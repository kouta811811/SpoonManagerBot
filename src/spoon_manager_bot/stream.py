"""イベントストリームの接続ループ（要件R4、設計§5.4、契約§4.4〜4.5）。

`end.reason` で次の動きを決める:

| 状況 | 動き |
|---|---|
| `LIVE_ENDED` | 終了 |
| `TOKEN_EXPIRED` | トークンを更新してすぐ再接続（1時間ごとに起きる正常系） |
| `RECONNECT` | 待たずに再接続 |
| `DUPLICATE_CONNECTION` | 再接続せず終了（別の接続が引き継いだ） |
| endなしの切断・45秒無受信 | 1→2→4…最大30秒（jitter付き）で再接続 |
| 404 `0301`（配信中でない） | 5〜30秒おきに再試行。待機上限（既定10分）で終了 |
| 401 | 1回だけ更新して再接続。それでも401なら終了（再連携が必要） |
| 403 detailCodeなし | 終了（権限不足、再同意が必要） |
| 403 `0209` | 終了（DJがBotをブロック） |
| 429・5xx・接続失敗 | バックオフして再試行。連続失敗の上限で終了 |
"""

from __future__ import annotations

import contextlib
import http.client
import random
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from typing import Protocol

from spoon_manager_bot.domain.events import (
    EventValidationError,
    SpoonEvent,
    StreamEndEvent,
    parse_event,
)
from spoon_manager_bot.observability.logger import log_event
from spoon_manager_bot.spoon.sse_client import (
    ConnectError,
    ConnectFailure,
    StreamConnection,
    StreamConnector,
)
from spoon_manager_bot.spoon.sse_parser import SseParser, SseTooLargeError
from spoon_manager_bot.tokens import ReauthRequired, TransientTokenError

LIVE_WAIT_LIMIT = 600.0
MAX_CONSECUTIVE_FAILURES = 20
MAX_BACKOFF = 30.0
NOT_LIVE_DELAY = (5.0, 30.0)
STABLE_CONNECTION = 60.0  # これ以上続いた接続のあとは失敗回数を数え直す


class StopReason(StrEnum):
    LIVE_ENDED = "live_ended"
    DUPLICATE_CONNECTION = "duplicate_connection"
    REAUTH_REQUIRED = "reauth_required"
    CONSENT_REQUIRED = "consent_required"
    BLOCKED_BY_DJ = "blocked_by_dj"
    LIVE_WAIT_TIMEOUT = "live_wait_timeout"
    TOO_MANY_FAILURES = "too_many_failures"
    LEASE_LOST = "lease_lost"
    SHUTDOWN = "shutdown"

    @property
    def needs_operator(self) -> bool:
        """人の対応（再連携・再同意・ブロック解除・調査）が必要な終了か。"""
        return self in (
            StopReason.REAUTH_REQUIRED,
            StopReason.CONSENT_REQUIRED,
            StopReason.BLOCKED_BY_DJ,
            StopReason.TOO_MANY_FAILURES,
            StopReason.LEASE_LOST,
        )


class TokenSource(Protocol):
    def access_token(self) -> str: ...

    def refresh(self, *, used: str | None = None) -> object: ...


class StreamSupervisor:
    def __init__(
        self,
        connector: StreamConnector,
        tokens: TokenSource,
        on_event: Callable[[SpoonEvent], None],
        *,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
        live_wait_limit: float = LIVE_WAIT_LIMIT,
        max_failures: int = MAX_CONSECUTIVE_FAILURES,
    ) -> None:
        self._connector = connector
        self._tokens = tokens
        self._on_event = on_event
        self._clock = clock
        self._rng = rng or random.Random()
        self._live_wait_limit = live_wait_limit
        self._max_failures = max_failures
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._connection: StreamConnection | None = None
        self.reconnects = 0

    def stop(self) -> None:
        """別スレッドから止める。読み取り中の接続も閉じる。"""
        self._stop.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            with contextlib.suppress(OSError):
                connection.close()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def run(self) -> StopReason:
        failures = 0
        not_live_since: float | None = None
        refreshed_for_401 = False

        while not self._stop.is_set():
            token = self._tokens.access_token()
            opened = self._connector.open(token)

            if isinstance(opened, ConnectError):
                log_event(
                    "stream_connect_failed",
                    connection_state=opened.failure.value,
                    status=opened.status,
                    detail_code=opened.detail_code,
                    reconnect_count=failures,
                )
                failure = opened.failure
                if failure is ConnectFailure.UNAUTHORIZED:
                    if refreshed_for_401:
                        return StopReason.REAUTH_REQUIRED
                    refreshed_for_401 = True
                    reason = self._refresh(token)
                    if reason is not None:
                        return reason
                    continue
                if failure is ConnectFailure.FORBIDDEN:
                    return StopReason.CONSENT_REQUIRED
                if failure is ConnectFailure.DJ_BLOCKED:
                    return StopReason.BLOCKED_BY_DJ
                if failure is ConnectFailure.NOT_LIVE:
                    now = self._clock()
                    not_live_since = not_live_since if not_live_since is not None else now
                    if now - not_live_since >= self._live_wait_limit:
                        return StopReason.LIVE_WAIT_TIMEOUT
                    self._wait(self._rng.uniform(*NOT_LIVE_DELAY))
                    continue
                failures += 1
                if failures > self._max_failures:
                    return StopReason.TOO_MANY_FAILURES
                delay = opened.retry_after if opened.retry_after is not None else None
                self._wait(delay if delay is not None else self._backoff(failures))
                continue

            # 接続できた
            not_live_since = None
            refreshed_for_401 = False
            log_event("stream_connected", connection_state="streaming", reconnect_count=failures)
            started = self._clock()
            end_reason = self._consume(opened)
            if self._clock() - started >= STABLE_CONNECTION:
                failures = 0
            log_event(
                "stream_closed",
                connection_state=end_reason or "disconnected",
                reconnect_count=failures,
            )
            if self._stop.is_set():
                break
            self.reconnects += 1

            if end_reason == "LIVE_ENDED":
                return StopReason.LIVE_ENDED
            if end_reason == "DUPLICATE_CONNECTION":
                return StopReason.DUPLICATE_CONNECTION
            if end_reason == "TOKEN_EXPIRED":
                reason = self._refresh(token)
                if reason is not None:
                    return reason
                continue
            if end_reason == "RECONNECT":
                continue
            # endなしの切断、または未知のreason
            failures += 1
            if failures > self._max_failures:
                return StopReason.TOO_MANY_FAILURES
            self._wait(self._backoff(failures))

        return StopReason.SHUTDOWN

    def _consume(self, connection: StreamConnection) -> str | None:
        with self._lock:
            self._connection = connection
        parser = SseParser()
        try:
            while not self._stop.is_set():
                chunk = connection.read()
                if not chunk:
                    return None
                for message in parser.feed(chunk):
                    try:
                        event = parse_event(message.event, message.data)
                    except EventValidationError:
                        log_event("event_invalid", event_type=message.event)
                        continue
                    if event is None:
                        continue
                    if isinstance(event, StreamEndEvent):
                        return event.reason
                    self._on_event(event)
            return None
        except SseTooLargeError:
            log_event("event_too_large", result="reconnect")
            return None
        except (OSError, http.client.HTTPException, ValueError):
            return None
        finally:
            with self._lock:
                self._connection = None
            with contextlib.suppress(OSError):
                connection.close()

    def _refresh(self, used: str) -> StopReason | None:
        while not self._stop.is_set():
            try:
                self._tokens.refresh(used=used)
                return None
            except ReauthRequired:
                return StopReason.REAUTH_REQUIRED
            except TransientTokenError:
                self._wait(self._backoff(3))
        return StopReason.SHUTDOWN

    def _backoff(self, failures: int) -> float:
        ceiling = min(MAX_BACKOFF, 2.0 ** (failures - 1))
        return max(0.5, self._rng.uniform(0, ceiling))

    def _wait(self, seconds: float) -> None:
        self._stop.wait(seconds)
