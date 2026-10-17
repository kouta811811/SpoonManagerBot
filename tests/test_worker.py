"""Workerの通し確認: ストリーム → お礼文の登録 → ハート → お礼の送信 → 配信終了。"""

from __future__ import annotations

import pytest

from spoon_manager_bot.repositories.state import LeaseRepository
from spoon_manager_bot.stream import StopReason
from spoon_manager_bot.thanks.repository import InMemoryThanksRepository
from spoon_manager_bot.tokens import StoredToken, TokenManager
from spoon_manager_bot.worker import EXIT_NEEDS_OPERATOR, EXIT_OK, Worker, WorkerDeps
from tests.fakes import (
    ConditionalDynamoDb,
    FakeRefresher,
    MemoryTokenStore,
    ScriptedConnector,
    ScriptedStream,
    frame,
)
from tests.helpers import RecordingSender

SCOPES = "events.chat events.like events.donation chat.send"


def chat(message: str, *, is_dj: bool) -> bytes:
    return frame(
        "chat",
        f'{{"user":{{"id":"dj","nickname":"DJ"}},"isDj":{str(is_dj).lower()},'
        f'"message":"{message}","sentTime":"2026-09-02T05:47:26.114Z"}}',
    )


def like(user: str, total: int) -> bytes:
    return frame(
        "like",
        f'{{"user":{{"id":"{user}","nickname":"{user.upper()}"}},"type":"FREE",'
        f'"totalAmount":{total},"amount":1,"extraAmount":0,"combo":null,'
        '"time":"2026-09-03T02:59:03.249Z"}',
    )


def build(
    chunks: list[bytes], *, scope: str = SCOPES, token: StoredToken | None = None
) -> tuple[Worker, RecordingSender, LeaseRepository, InMemoryThanksRepository]:
    store = MemoryTokenStore(token or StoredToken("at1", "rt1", 1e12, scope))
    tokens = TokenManager(store, FakeRefresher())
    lease = LeaseRepository(ConditionalDynamoDb(), "t")
    assert lease.acquire("lease-1", 120)
    sender = RecordingSender()
    repo = InMemoryThanksRepository()
    deps = WorkerDeps(
        tokens=tokens,
        connector=ScriptedConnector([ScriptedStream(chunks)]),
        sender=sender,
        thanks_repository=repo,
        lease=lease,
        lease_token="lease-1",
        ignored_user_ids=frozenset(),
        live_wait_limit=600,
    )
    return Worker(deps), sender, lease, repo


def test_end_to_end_live() -> None:
    worker, sender, lease, repo = build(
        [
            chat("!setheart {名前}ハート{数}個ありがとう", is_dj=True),
            like("a", 3),
            frame("end", '{"reason":"LIVE_ENDED"}'),
        ]
    )
    reason, code = worker.run()
    assert (reason, code) == (StopReason.LIVE_ENDED, EXIT_OK)
    assert sender.sent == ["ハートのお礼を登録しました（1〜）", "Aさんハート3個ありがとう"]
    assert len(repo.items) == 1
    assert lease.get() is None  # 終了時にleaseを外す


def test_missing_event_scope_stops_before_connecting() -> None:
    worker, _, _, _ = build([], scope="chat.send")
    assert worker.run() == (StopReason.CONSENT_REQUIRED, EXIT_NEEDS_OPERATOR)


def test_no_token_requires_reauth() -> None:
    worker, _, _, _ = build([])
    worker._deps.tokens = TokenManager(MemoryTokenStore(None), FakeRefresher())
    assert worker.run() == (StopReason.REAUTH_REQUIRED, EXIT_NEEDS_OPERATOR)


def test_lost_lease_stops_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    gate = threading.Event()

    class BlockingStream:
        def read(self) -> bytes:
            gate.wait(5)
            return b""

        def close(self) -> None:
            gate.set()

    worker, _, lease, _ = build([])
    worker._deps.connector = ScriptedConnector([BlockingStream()])
    worker.supervisor._connector = worker._deps.connector
    worker._heartbeat_interval = 0.0
    lease.force_release()
    assert lease.acquire("other-task", 120)
    reason, code = worker.run()
    assert (reason, code) == (StopReason.LEASE_LOST, EXIT_NEEDS_OPERATOR)
