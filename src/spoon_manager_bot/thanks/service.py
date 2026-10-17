"""お礼の集計と15秒ごとの送信判断（要件R13-7〜17）。

時刻は呼び出し側から単調増加の秒（`time.monotonic()` など）で渡す。
"""

from __future__ import annotations

from spoon_manager_bot.domain.events import DonationEvent, LikeEvent
from spoon_manager_bot.domain.thanks import TemplateSet, ThanksKind
from spoon_manager_bot.thanks.buffer import ThanksBuffer, ThanksMessage

THANKS_INTERVAL = 15.0


class ThanksService:
    def __init__(
        self,
        templates: TemplateSet,
        *,
        buffer: ThanksBuffer | None = None,
        interval: float = THANKS_INTERVAL,
        enabled: frozenset[ThanksKind] = frozenset(ThanksKind),
    ) -> None:
        self._templates = templates
        self._buffer = buffer or ThanksBuffer()
        self._interval = interval
        self._last_sent: float | None = None
        self.enabled = enabled
        # 配信全体の総数（{累計}）。個人別の累計は持たない（要件R10-7）。
        self.totals: dict[ThanksKind, int] = dict.fromkeys(ThanksKind, 0)

    @property
    def templates(self) -> TemplateSet:
        return self._templates

    def on_like(self, event: LikeEvent, now: float) -> None:
        # 集計は FREE / PAID ともに totalAmount を使う
        self._record(ThanksKind.HEART, event.user.id, event.user.nickname, event.total_amount, now)

    def on_donation(self, event: DonationEvent, now: float) -> None:
        # amount は掛け算済みの合計スプーン数
        self._record(ThanksKind.SPOON, event.user.id, event.user.nickname, event.amount, now)

    def due(self, now: float) -> ThanksMessage | None:
        """送信枠が来ていれば次の1通を返す。返した時点で枠を使ったものとする。"""
        if self._last_sent is not None and now - self._last_sent < self._interval:
            return None
        message = self._buffer.take_next(self._templates, self.totals, now, self.enabled)
        if message is not None:
            self._last_sent = now
        return message

    def pending_count(self) -> int:
        return self._buffer.pending_count()

    def reset_session(self) -> None:
        """配信が変わったときに集計と待ちを捨てる。"""
        self.totals = dict.fromkeys(ThanksKind, 0)
        self._buffer.clear()
        self._last_sent = None

    def _record(
        self, kind: ThanksKind, user_id: str, nickname: str | None, amount: int, now: float
    ) -> None:
        if amount <= 0:
            return
        self.totals[kind] += amount
        if kind in self.enabled and not self._templates.is_empty(kind):
            self._buffer.add(kind, user_id, nickname, amount, now)
