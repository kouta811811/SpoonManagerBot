"""送信キュー（設計§10）。

- 返信レーン（コマンド返信・登録結果）を優先し、お礼レーンは返信レーンが空のときに送る。
- お礼は失敗しても再送しない（要件R13-16）。
- 1日上限（429 + Retry-After）を受けたら、その秒数だけ全レーンの送信を止める。
- 返信の限定再送や `x-ratelimit-*` による事前の速度調整は tasks 6.2 で追加する。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from spoon_manager_bot.spoon.chat_client import ChatOutcome, ChatResult, ChatSender

DEFAULT_MAX_THANKS = 2
DEFAULT_MAX_REPLIES = 10
DEFAULT_THANKS_TTL = 60.0


class Lane(StrEnum):
    REPLY = "reply"
    THANKS = "thanks"


@dataclass(frozen=True, slots=True)
class Outgoing:
    lane: Lane
    text: str
    enqueued_at: float
    label: str  # ログ用の識別子（本文は入れない）


@dataclass(frozen=True, slots=True)
class Delivery:
    item: Outgoing
    result: ChatResult


class Outbox:
    def __init__(
        self,
        *,
        max_replies: int = DEFAULT_MAX_REPLIES,
        max_thanks: int = DEFAULT_MAX_THANKS,
        thanks_ttl: float = DEFAULT_THANKS_TTL,
    ) -> None:
        self._replies: deque[Outgoing] = deque(maxlen=max_replies)
        self._thanks: deque[Outgoing] = deque(maxlen=max_thanks)
        self._thanks_ttl = thanks_ttl
        self._suspended_until: float | None = None

    def push(self, lane: Lane, text: str, now: float, label: str) -> None:
        queue = self._replies if lane is Lane.REPLY else self._thanks
        queue.append(Outgoing(lane, text, now, label))

    def pending(self, lane: Lane | None = None) -> int:
        if lane is Lane.REPLY:
            return len(self._replies)
        if lane is Lane.THANKS:
            return len(self._thanks)
        return len(self._replies) + len(self._thanks)

    def suspended(self, now: float) -> bool:
        return self._suspended_until is not None and now < self._suspended_until

    def send_next(self, sender: ChatSender, now: float) -> Delivery | None:
        """1件送る。送るものがない、または停止中ならNone。"""
        if self.suspended(now):
            return None
        self._drop_stale_thanks(now)
        if self._replies:
            item = self._replies.popleft()
        elif self._thanks:
            item = self._thanks.popleft()
        else:
            return None
        result = sender.send(item.text)
        if result.outcome is ChatOutcome.RATE_LIMITED and result.retry_after is not None:
            self._suspended_until = now + result.retry_after
        return Delivery(item, result)

    def _drop_stale_thanks(self, now: float) -> None:
        while self._thanks and now - self._thanks[0].enqueued_at > self._thanks_ttl:
            self._thanks.popleft()
