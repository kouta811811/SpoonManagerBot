"""接続ループ（end.reason・HTTPステータス別の動き）。"""

from __future__ import annotations

import random

from spoon_manager_bot.domain.events import ChatEvent, SpoonEvent
from spoon_manager_bot.spoon.sse_client import ConnectError, ConnectFailure
from spoon_manager_bot.stream import StopReason, StreamSupervisor
from spoon_manager_bot.tokens import ReauthRequired, TransientTokenError
from tests.fakes import FakeTokens, ScriptedConnector, ScriptedStream, frame

CHAT = (
    '{"user":{"id":"u1","nickname":"A"},"isDj":false,"message":"hi",'
    '"sentTime":"2026-09-02T05:47:26.114Z"}'
)


def end(reason: str) -> bytes:
    return frame("end", f'{{"reason":"{reason}"}}')


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def supervisor(
    connector: ScriptedConnector,
    tokens: FakeTokens | None = None,
    *,
    clock: Clock | None = None,
    **kwargs: object,
) -> tuple[StreamSupervisor, list[SpoonEvent], list[float], FakeTokens]:
    events: list[SpoonEvent] = []
    waits: list[float] = []
    tokens = tokens or FakeTokens()
    clock = clock or Clock()
    sup = StreamSupervisor(
        connector,
        tokens,
        events.append,
        clock=clock,
        rng=random.Random(0),
        **kwargs,  # type: ignore[arg-type]
    )

    def fake_wait(seconds: float) -> None:
        waits.append(seconds)
        clock.now += seconds

    sup._wait = fake_wait  # type: ignore[method-assign]
    return sup, events, waits, tokens


def test_events_are_delivered_and_live_end_stops() -> None:
    connector = ScriptedConnector(
        [ScriptedStream([b":heartbeat\n\n", frame("chat", CHAT), end("LIVE_ENDED")])]
    )
    sup, events, waits, _ = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert len(events) == 1 and isinstance(events[0], ChatEvent)
    assert waits == []


def test_token_expired_refreshes_and_reconnects_immediately() -> None:
    connector = ScriptedConnector(
        [ScriptedStream([end("TOKEN_EXPIRED")]), ScriptedStream([end("LIVE_ENDED")])]
    )
    sup, _, waits, tokens = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert connector.tokens == ["at1", "at2"]
    assert tokens.refresh_calls == 1
    assert waits == []


def test_reconnect_reason_has_no_backoff_and_keeps_token() -> None:
    connector = ScriptedConnector(
        [ScriptedStream([end("RECONNECT")]), ScriptedStream([end("LIVE_ENDED")])]
    )
    sup, _, waits, tokens = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert connector.tokens == ["at1", "at1"]
    assert tokens.refresh_calls == 0
    assert waits == []


def test_duplicate_connection_does_not_reconnect() -> None:
    connector = ScriptedConnector([ScriptedStream([end("DUPLICATE_CONNECTION")])])
    sup, _, _, _ = supervisor(connector)
    assert sup.run() is StopReason.DUPLICATE_CONNECTION


def test_disconnect_without_end_backs_off_and_reconnects() -> None:
    connector = ScriptedConnector(
        [
            ScriptedStream([frame("chat", CHAT)]),
            ScriptedStream([], end=TimeoutError()),
            ScriptedStream([end("LIVE_ENDED")]),
        ]
    )
    sup, events, waits, _ = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert len(events) == 1
    assert len(waits) == 2
    assert all(0.5 <= w <= 30 for w in waits)


def test_not_live_waits_then_times_out() -> None:
    not_live = ConnectError(ConnectFailure.NOT_LIVE, 404, "OAPI_MNGR_0301")
    connector = ScriptedConnector([not_live] * 100)
    sup, _, waits, _ = supervisor(connector, live_wait_limit=60)
    assert sup.run() is StopReason.LIVE_WAIT_TIMEOUT
    assert all(5 <= w <= 30 for w in waits)
    assert sum(waits) >= 60


def test_not_live_then_live_starts_streaming() -> None:
    not_live = ConnectError(ConnectFailure.NOT_LIVE, 404, "OAPI_MNGR_0301")
    connector = ScriptedConnector([not_live, not_live, ScriptedStream([end("LIVE_ENDED")])])
    sup, _, waits, _ = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert len(waits) == 2


def test_401_refreshes_once_then_stops() -> None:
    unauthorized = ConnectError(ConnectFailure.UNAUTHORIZED, 401)
    connector = ScriptedConnector([unauthorized, unauthorized])
    sup, _, _, tokens = supervisor(connector)
    assert sup.run() is StopReason.REAUTH_REQUIRED
    assert tokens.refresh_calls == 1
    assert connector.tokens == ["at1", "at2"]


def test_401_recovers_after_refresh() -> None:
    connector = ScriptedConnector(
        [ConnectError(ConnectFailure.UNAUTHORIZED, 401), ScriptedStream([end("LIVE_ENDED")])]
    )
    sup, _, _, _ = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED


def test_refresh_reauth_required_stops() -> None:
    connector = ScriptedConnector([ScriptedStream([end("TOKEN_EXPIRED")])])
    sup, _, _, _ = supervisor(connector, FakeTokens([ReauthRequired("invalid_grant")]))
    assert sup.run() is StopReason.REAUTH_REQUIRED


def test_transient_refresh_failure_retries() -> None:
    connector = ScriptedConnector(
        [ScriptedStream([end("TOKEN_EXPIRED")]), ScriptedStream([end("LIVE_ENDED")])]
    )
    sup, _, waits, tokens = supervisor(connector, FakeTokens([TransientTokenError("network")]))
    assert sup.run() is StopReason.LIVE_ENDED
    assert tokens.refresh_calls == 2
    assert len(waits) == 1


def test_403_variants_stop() -> None:
    for error, reason in [
        (ConnectError(ConnectFailure.FORBIDDEN, 403), StopReason.CONSENT_REQUIRED),
        (ConnectError(ConnectFailure.DJ_BLOCKED, 403, "OAPI_MNGR_0209"), StopReason.BLOCKED_BY_DJ),
    ]:
        sup, _, _, _ = supervisor(ScriptedConnector([error]))
        assert sup.run() is reason
        assert reason.needs_operator


def test_rate_limit_uses_retry_after() -> None:
    connector = ScriptedConnector(
        [
            ConnectError(ConnectFailure.RATE_LIMITED, 429, retry_after=7.0),
            ScriptedStream([end("LIVE_ENDED")]),
        ]
    )
    sup, _, waits, _ = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert waits == [7.0]


def test_too_many_consecutive_failures() -> None:
    connector = ScriptedConnector([ConnectError(ConnectFailure.SERVER_ERROR, 502)] * 10)
    sup, _, _, _ = supervisor(connector, max_failures=3)
    assert sup.run() is StopReason.TOO_MANY_FAILURES


def test_invalid_event_and_oversize_are_survived() -> None:
    connector = ScriptedConnector(
        [
            ScriptedStream([frame("chat", '{"user":{}}'), b"data:" + b"a" * 70_000]),
            ScriptedStream([frame("chat", CHAT), end("LIVE_ENDED")]),
        ]
    )
    sup, events, _, _ = supervisor(connector)
    assert sup.run() is StopReason.LIVE_ENDED
    assert len(events) == 1


def test_stop_closes_current_connection() -> None:
    stream = ScriptedStream([])
    connector = ScriptedConnector([stream])
    sup, _, _, _ = supervisor(connector)
    sup.stop()
    assert sup.run() is StopReason.SHUTDOWN
