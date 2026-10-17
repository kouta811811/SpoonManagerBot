"""イベントストリームの各イベントを処理に振り分ける。

Workerの読み取りループは、イベントを受け取るたびに `handle(event, now)` を呼び、
イベントが来ないときも1秒程度ごとに `tick(now)` を呼ぶ（15秒の枠を逃さないため）。
`now` は単調増加の秒（`time.monotonic()`）。
"""

from __future__ import annotations

from collections.abc import Iterable

from spoon_manager_bot.chat.outbox import Delivery, Lane, Outbox
from spoon_manager_bot.domain.commands import parse_command
from spoon_manager_bot.domain.events import ChatEvent, DonationEvent, LikeEvent, SpoonEvent
from spoon_manager_bot.domain.text import utf16_len
from spoon_manager_bot.domain.thanks import ThanksKind
from spoon_manager_bot.observability.logger import log_event
from spoon_manager_bot.spoon.chat_client import ChatSender
from spoon_manager_bot.thanks.commands import ThanksCommandHandler
from spoon_manager_bot.thanks.service import ThanksService

SCOPE_CHAT_SEND = "chat.send"
SCOPE_FOR_KIND = {ThanksKind.HEART: "events.like", ThanksKind.SPOON: "events.donation"}


def enabled_kinds(scopes: Iterable[str]) -> frozenset[ThanksKind]:
    """同意されたscopeから、お礼を送れる種別を決める（要件R13-17）。"""
    granted = set(scopes)
    if SCOPE_CHAT_SEND not in granted:
        return frozenset()
    return frozenset(kind for kind, scope in SCOPE_FOR_KIND.items() if scope in granted)


class EventRouter:
    def __init__(
        self,
        *,
        thanks: ThanksService,
        thanks_commands: ThanksCommandHandler,
        outbox: Outbox,
        scopes: Iterable[str],
        ignored_user_ids: Iterable[str] = (),
    ) -> None:
        self._thanks = thanks
        self._commands = thanks_commands
        self._outbox = outbox
        self._ignored = frozenset(ignored_user_ids)
        self.update_scopes(scopes)

    def update_scopes(self, scopes: Iterable[str]) -> None:
        """token発行・更新のたびに呼ぶ（要件R2-4a）。"""
        granted = frozenset(scopes)
        self._can_send = SCOPE_CHAT_SEND in granted
        self._thanks.enabled = enabled_kinds(granted)

    def handle(self, event: SpoonEvent, now: float) -> None:
        if isinstance(event, ChatEvent):
            self._on_chat(event, now)
        elif isinstance(event, LikeEvent | DonationEvent) and event.user.id not in self._ignored:
            if isinstance(event, LikeEvent):
                self._thanks.on_like(event, now)
            else:
                self._thanks.on_donation(event, now)
        self.tick(now)

    def tick(self, now: float) -> None:
        message = self._thanks.due(now)
        if message is None:
            return
        log_event(
            "thanks_ready",
            thanks_kind=message.kind.value,
            tier_min=message.tier_min,
            special=message.special,
            recipient_count=message.recipient_count,
            text_length=utf16_len(message.text),
        )
        if self._can_send:
            self._outbox.push(Lane.THANKS, message.text, now, f"thanks:{message.kind.value}")

    def flush(self, sender: ChatSender, now: float) -> Delivery | None:
        """送信キューから1件送る。"""
        delivery = self._outbox.send_next(sender, now)
        if delivery is not None:
            log_event(
                "chat_sent",
                lane=delivery.item.lane.value,
                label=delivery.item.label,
                result=delivery.result.outcome.value,
                status=delivery.result.status,
                detail_code=delivery.result.detail_code,
            )
        return delivery

    def _on_chat(self, event: ChatEvent, now: float) -> None:
        if event.user.id in self._ignored:
            return
        command = parse_command(event.message)
        if command is None:
            return
        result = self._commands.handle(command, is_dj=event.is_dj, enabled=self._thanks.enabled)
        if result is None:
            return
        log_event("thanks_command", label=command.name, result=result.result)
        if self._can_send:
            self._outbox.push(Lane.REPLY, result.reply, now, f"command:{command.name}")
