"""DJの登録コマンド → ハート・スプーン → お礼送信までの通し確認。"""

from __future__ import annotations

import logging

import pytest

from spoon_manager_bot.bot.router import EventRouter, enabled_kinds
from spoon_manager_bot.chat.outbox import Lane, Outbox
from spoon_manager_bot.domain.thanks import TemplateSet, ThanksKind
from spoon_manager_bot.spoon.chat_client import ChatOutcome, ChatResult
from spoon_manager_bot.thanks.commands import ThanksCommandHandler
from spoon_manager_bot.thanks.repository import InMemoryThanksRepository
from spoon_manager_bot.thanks.service import ThanksService
from tests.helpers import RecordingSender, chat, donation, like

MVP_SCOPES = ("events.chat", "events.like", "events.donation", "chat.send")


def build(
    scopes: tuple[str, ...] = MVP_SCOPES, ignored: tuple[str, ...] = ()
) -> tuple[EventRouter, Outbox, InMemoryThanksRepository]:
    templates = TemplateSet()
    repo = InMemoryThanksRepository()
    outbox = Outbox()
    router = EventRouter(
        thanks=ThanksService(templates),
        thanks_commands=ThanksCommandHandler(templates, repo),
        outbox=outbox,
        scopes=scopes,
        ignored_user_ids=ignored,
    )
    return router, outbox, repo


def drain(router: EventRouter, sender: RecordingSender, now: float) -> None:
    while router.flush(sender, now) is not None:
        pass


def test_enabled_kinds_from_scopes() -> None:
    assert enabled_kinds(MVP_SCOPES) == frozenset(ThanksKind)
    assert enabled_kinds(("events.like", "chat.send")) == frozenset({ThanksKind.HEART})
    assert enabled_kinds(("events.like", "events.donation")) == frozenset()


def test_end_to_end_register_then_thank() -> None:
    router, _, repo = build()
    sender = RecordingSender()

    # 登録前はお礼を送らない
    router.handle(like("a", "A", 3), 0)
    drain(router, sender, 0)
    assert sender.sent == []

    router.handle(chat("!setheart {名前}ハート{数}個ありがとう💕", is_dj=True), 1)
    router.handle(chat("!setspoon {名前}、{数}スプーンありがとう✨", is_dj=True), 1)
    drain(router, sender, 1)
    assert sender.sent == [
        "ハートのお礼を登録しました（1〜）",
        "スプーンのお礼を登録しました（1〜）",
    ]
    assert len(repo.items) == 2

    router.handle(like("a", "A", 3, "FREE"), 2)  # 最初のお礼はすぐ送る
    drain(router, sender, 2)
    router.handle(like("b", "B", 10), 4)
    router.handle(like("a", "A", 2), 9)
    router.handle(donation("c", "C", 100), 12)
    router.tick(17)
    drain(router, sender, 17)
    router.tick(32)
    drain(router, sender, 32)
    assert sender.sent[2:] == [
        "Aさんハート3個ありがとう💕",
        "Cさん、100スプーンありがとう✨",
        "Bさん、Aさんハート12個ありがとう💕",
    ]


def test_listener_cannot_register() -> None:
    router, _, repo = build()
    sender = RecordingSender()
    router.handle(chat("!setheart 乗っ取り", is_dj=False, user_id="listener"), 0)
    drain(router, sender, 0)
    assert sender.sent == []
    assert repo.items == {}


def test_reply_lane_is_sent_before_thanks() -> None:
    router, outbox, _ = build()
    router.handle(chat("!setheart {名前}", is_dj=True), 0)
    sender = RecordingSender()
    drain(router, sender, 0)
    router.handle(like("a", "A", 1), 1)
    router.handle(chat("!msglist", is_dj=True), 1)
    assert outbox.pending(Lane.THANKS) == 1
    assert outbox.pending(Lane.REPLY) == 1
    drain(router, sender, 1)
    assert sender.sent[1:] == ["お礼文 ハート: 1 ／ スプーン: 未登録", "Aさん"]


def test_failed_thanks_is_not_retried() -> None:
    router, _, _ = build()
    router.handle(chat("!setheart {名前}", is_dj=True), 0)
    drain(router, RecordingSender(), 0)
    sender = RecordingSender([ChatResult(ChatOutcome.CHAT_BUSY, 429, "OAPI_MNGR_0302")])
    router.handle(like("a", "A", 1), 1)
    drain(router, sender, 1)
    router.tick(20)
    drain(router, sender, 20)
    assert sender.sent == ["Aさん"]


def test_daily_limit_suspends_sending() -> None:
    router, outbox, _ = build()
    router.handle(chat("!setheart {名前}", is_dj=True), 0)
    drain(router, RecordingSender(), 0)
    sender = RecordingSender([ChatResult(ChatOutcome.RATE_LIMITED, 429, None, 100.0)])
    router.handle(like("a", "A", 1), 1)
    drain(router, sender, 1)
    router.handle(chat("!msglist", is_dj=True), 2)
    drain(router, sender, 2)
    assert sender.sent == ["Aさん"]
    assert outbox.suspended(50)
    drain(router, sender, 102)
    assert sender.sent[-1].startswith("お礼文")


def test_without_chat_send_nothing_is_queued() -> None:
    router, outbox, repo = build(scopes=("events.chat", "events.like"))
    router.handle(chat("!setheart {名前}", is_dj=True), 0)
    router.handle(like("a", "A", 1), 1)
    assert outbox.pending() == 0
    assert len(repo.items) == 1  # 登録自体はできる


def test_ignored_users_are_skipped() -> None:
    router, outbox, _ = build(ignored=("other-bot",))
    router.handle(chat("!setheart {名前}", is_dj=True), 0)
    drain(router, RecordingSender(), 0)
    router.handle(like("other-bot", "Bot", 5), 1)
    assert outbox.pending() == 0


def test_logs_do_not_contain_nickname_or_text(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="spoon_manager_bot")
    router, _, _ = build()
    router.handle(chat("!setheart {名前}ありがとう秘密の文面", is_dj=True), 0)
    router.handle(like("secret-user-id", "ひみつのニックネーム", 5), 1)
    drain(router, RecordingSender(), 1)
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "thanks_ready" in logged
    assert "ひみつのニックネーム" not in logged
    assert "secret-user-id" not in logged
    assert "秘密の文面" not in logged
