"""DJ専用のお礼文コマンド（要件R13-1〜6）。

| コマンド | 動作 |
|---|---|
| `!setheart [下限] 文面` / `!setspoon [下限] 文面` | 登録・上書き |
| `!delheart [下限]` / `!delspoon [下限]` | 削除 |
| `!msglist` | 登録状況（下限の一覧） |

DJ（`chat.isDj=true`）以外が使ったときは何もしない（返信もしない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from spoon_manager_bot.domain.commands import Command
from spoon_manager_bot.domain.text import utf16_len
from spoon_manager_bot.domain.thanks import (
    MAX_TIERS_PER_KIND,
    MIN_AMOUNT_LOWER,
    MIN_AMOUNT_UPPER,
    TEMPLATE_MAX_LENGTH,
    TemplateSet,
    ThanksKind,
    ThanksTemplate,
)
from spoon_manager_bot.thanks.repository import ThanksRepository, ThanksRepositoryError


class ThanksCommandName(StrEnum):
    SET_HEART = "setheart"
    SET_SPOON = "setspoon"
    DEL_HEART = "delheart"
    DEL_SPOON = "delspoon"
    LIST = "msglist"


_KIND_OF: dict[ThanksCommandName, ThanksKind] = {
    ThanksCommandName.SET_HEART: ThanksKind.HEART,
    ThanksCommandName.DEL_HEART: ThanksKind.HEART,
    ThanksCommandName.SET_SPOON: ThanksKind.SPOON,
    ThanksCommandName.DEL_SPOON: ThanksKind.SPOON,
}

THANKS_COMMANDS = frozenset(c.value for c in ThanksCommandName)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """返信する本文と、ログ用の結果コード。"""

    reply: str
    result: str


def _tier_label(min_amount: int) -> str:
    return f"{min_amount:,}〜"


def _parse_min(token: str) -> int | None:
    """半角数字だけの語を下限として読む。数字以外が混ざれば下限ではない。"""
    if token.isascii() and token.isdigit():
        return int(token)
    return None


class ThanksCommandHandler:
    def __init__(self, templates: TemplateSet, repository: ThanksRepository) -> None:
        self._templates = templates
        self._repository = repository

    def handle(
        self,
        command: Command,
        *,
        is_dj: bool,
        enabled: frozenset[ThanksKind],
    ) -> CommandResult | None:
        """お礼文コマンドなら結果を返す。対象外のコマンドやDJ以外ならNone。"""
        if command.name not in THANKS_COMMANDS or not is_dj:
            return None
        name = ThanksCommandName(command.name)
        if name is ThanksCommandName.LIST:
            return self._list(enabled)
        kind = _KIND_OF[name]
        if name in (ThanksCommandName.SET_HEART, ThanksCommandName.SET_SPOON):
            return self._set(kind, command.rest)
        return self._delete(kind, command.rest)

    def _set(self, kind: ThanksKind, rest: str) -> CommandResult:
        min_amount = MIN_AMOUNT_LOWER
        text = rest.strip()
        parts = text.split(maxsplit=1)
        if parts:
            parsed = _parse_min(parts[0])
            if parsed is not None:
                min_amount = parsed
                text = parts[1].strip() if len(parts) > 1 else ""

        if not MIN_AMOUNT_LOWER <= min_amount <= MIN_AMOUNT_UPPER:
            return CommandResult(
                f"下限は{MIN_AMOUNT_LOWER}〜{MIN_AMOUNT_UPPER:,}で指定してください", "invalid_min"
            )
        if not text:
            return CommandResult(f"{kind.label}のお礼文を入力してください", "empty_text")
        if utf16_len(text) > TEMPLATE_MAX_LENGTH:
            return CommandResult(f"お礼文が長すぎます（{TEMPLATE_MAX_LENGTH}文字まで）", "too_long")
        exists = self._templates.has(kind, min_amount)
        if not exists and len(self._templates.tiers(kind)) >= MAX_TIERS_PER_KIND:
            return CommandResult(
                f"{kind.label}のお礼文は{MAX_TIERS_PER_KIND}つまでです。"
                f"!del{kind.value} で削除してから登録してください",
                "too_many_tiers",
            )

        template = ThanksTemplate(kind=kind, min_amount=min_amount, text=text)
        try:
            self._repository.put(template)
        except ThanksRepositoryError:
            return CommandResult("保存に失敗しました。少ししてからもう一度送ってください", "error")
        self._templates.upsert(template)
        verb = "更新" if exists else "登録"
        return CommandResult(
            f"{kind.label}のお礼を{verb}しました（{_tier_label(min_amount)}）",
            "updated" if exists else "created",
        )

    def _delete(self, kind: ThanksKind, rest: str) -> CommandResult:
        token = rest.strip()
        min_amount = MIN_AMOUNT_LOWER
        if token:
            parsed = _parse_min(token)
            if parsed is None:
                return CommandResult("削除する下限を半角数字で指定してください", "invalid_min")
            min_amount = parsed
        if not self._templates.has(kind, min_amount):
            return CommandResult(
                f"{kind.label}のお礼（{_tier_label(min_amount)}）は登録されていません",
                "not_found",
            )
        try:
            self._repository.delete(kind, min_amount)
        except ThanksRepositoryError:
            return CommandResult("削除に失敗しました。少ししてからもう一度送ってください", "error")
        self._templates.remove(kind, min_amount)
        return CommandResult(
            f"{kind.label}のお礼を削除しました（{_tier_label(min_amount)}）", "deleted"
        )

    def _list(self, enabled: frozenset[ThanksKind]) -> CommandResult:
        parts: list[str] = []
        for kind in (ThanksKind.HEART, ThanksKind.SPOON):
            tiers = self._templates.tiers(kind)
            value = ", ".join(f"{t.min_amount:,}" for t in tiers) if tiers else "未登録"
            if kind not in enabled:
                value += "（権限なし）"
            parts.append(f"{kind.label}: {value}")
        return CommandResult("お礼文 " + " ／ ".join(parts), "listed")
