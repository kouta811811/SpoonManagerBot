"""お礼を送るまで送り主をためる（メモリのみ）。

- 送り主は種別ごとに `user.id` 単位で合算する。
- `user.id` とニックネームはこのプロセスのメモリにだけ置き、DBやログへは出さない（要件R10-6）。
- 最後に届いてから `expire_after` 秒を超えた送り主は捨てる。
  続けて送ってくれている人は、最後の分から数えるので捨てられない。
"""

from __future__ import annotations

from dataclasses import dataclass

from spoon_manager_bot.domain.thanks import (
    Recipient,
    TemplateSet,
    ThanksKind,
    ThanksTemplate,
    compose,
)

DEFAULT_EXPIRE_AFTER = 60.0
DEFAULT_MAX_SPECIAL = 5

# 1通の枠で選ぶ順番。スプーンをハートより優先する。
KIND_PRIORITY: tuple[ThanksKind, ...] = (ThanksKind.SPOON, ThanksKind.HEART)


@dataclass(slots=True)
class _Pending:
    nickname: str | None
    amount: int
    first_seen: float
    last_seen: float


@dataclass(frozen=True, slots=True)
class ThanksMessage:
    """送信する1通。ログ用の情報（文面以外）も持つ。"""

    kind: ThanksKind
    tier_min: int
    special: bool
    recipient_count: int
    text: str


class ThanksBuffer:
    def __init__(
        self,
        *,
        expire_after: float = DEFAULT_EXPIRE_AFTER,
        max_special: int = DEFAULT_MAX_SPECIAL,
    ) -> None:
        self._expire_after = expire_after
        self._max_special = max_special
        self._pending: dict[ThanksKind, dict[str, _Pending]] = {k: {} for k in ThanksKind}

    def add(
        self,
        kind: ThanksKind,
        user_id: str,
        nickname: str | None,
        amount: int,
        now: float,
    ) -> None:
        if amount <= 0:
            return
        pending = self._pending[kind].get(user_id)
        if pending is None:
            self._pending[kind][user_id] = _Pending(nickname, amount, now, now)
            return
        pending.amount += amount
        pending.last_seen = now
        if nickname is not None:
            pending.nickname = nickname

    def pending_count(self, kind: ThanksKind | None = None) -> int:
        kinds = (kind,) if kind is not None else tuple(ThanksKind)
        return sum(len(self._pending[k]) for k in kinds)

    def clear(self) -> None:
        for pending in self._pending.values():
            pending.clear()

    def take_next(
        self,
        templates: TemplateSet,
        totals: dict[ThanksKind, int],
        now: float,
        enabled: frozenset[ThanksKind] = frozenset(ThanksKind),
    ) -> ThanksMessage | None:
        """次に送る1通を選び、選んだ送り主をバッファから外す。送るものがなければNone。

        優先順: 特別文（1人ずつ、スプーン→ハート、量の多い人から）→ スプーン通常文 → ハート通常文。
        """
        self._expire(now)
        for kind in ThanksKind:
            if kind not in enabled or templates.is_empty(kind):
                # 登録がない、または権限がない種別はためない（要件R13-7、R13-17）
                self._pending[kind].clear()

        self._limit_special(templates)

        for kind in KIND_PRIORITY:
            special = self._special_candidates(kind, templates)
            while special:
                user_id, pending, template = special.pop(0)
                del self._pending[kind][user_id]
                message = self._compose(template, [pending], totals, special=True)
                if message is not None:
                    return message

        for kind in KIND_PRIORITY:
            members: list[tuple[str, _Pending]] = []
            normal: ThanksTemplate | None = None
            for user_id, pending in self._pending[kind].items():
                selected = templates.select(kind, pending.amount)
                if selected is None or templates.is_special(selected):
                    # 下限に届かない人は次の分と合算できるよう残す
                    continue
                members.append((user_id, pending))
                normal = selected
            if normal is None:
                continue
            members.sort(key=lambda item: (-item[1].amount, item[1].first_seen))
            for user_id, _ in members:
                del self._pending[kind][user_id]
            message = self._compose(normal, [p for _, p in members], totals, special=False)
            if message is not None:
                return message
        return None

    def _expire(self, now: float) -> None:
        limit = now - self._expire_after
        for pending in self._pending.values():
            for user_id in [uid for uid, p in pending.items() if p.last_seen < limit]:
                del pending[user_id]

    def _special_candidates(
        self, kind: ThanksKind, templates: TemplateSet
    ) -> list[tuple[str, _Pending, ThanksTemplate]]:
        candidates: list[tuple[str, _Pending, ThanksTemplate]] = []
        for user_id, pending in self._pending[kind].items():
            template = templates.select(kind, pending.amount)
            if template is not None and templates.is_special(template):
                candidates.append((user_id, pending, template))
        candidates.sort(key=lambda item: (-item[1].amount, item[1].first_seen))
        return candidates

    def _limit_special(self, templates: TemplateSet) -> None:
        """特別文の待ちは全種別で最大 max_special 件。超えたら古いものから捨てる。"""
        waiting = [
            (pending.first_seen, kind, user_id)
            for kind in ThanksKind
            for user_id, pending, _ in self._special_candidates(kind, templates)
        ]
        overflow = len(waiting) - self._max_special
        if overflow <= 0:
            return
        waiting.sort()
        for _, kind, user_id in waiting[:overflow]:
            del self._pending[kind][user_id]

    @staticmethod
    def _compose(
        template: ThanksTemplate,
        members: list[_Pending],
        totals: dict[ThanksKind, int],
        *,
        special: bool,
    ) -> ThanksMessage | None:
        recipients = [Recipient(nickname=p.nickname, amount=p.amount) for p in members]
        text = compose(template.text, recipients, total=totals.get(template.kind, 0))
        if text is None:
            return None
        return ThanksMessage(
            kind=template.kind,
            tier_min=template.min_amount,
            special=special,
            recipient_count=len(recipients),
            text=text,
        )
