"""`GET /v1/live/events` への接続（契約 §4.1・§4.5）。

接続の結果はHTTPステータスで先に分ける。エラー本文もSSE形式なので `data:` を外して読む。
読み取りのタイムアウトは45秒（heartbeat 15秒の3倍）。超えたら切断とみなす。
"""

from __future__ import annotations

import http.client
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from spoon_manager_bot.spoon.errors import detail_code
from spoon_manager_bot.spoon.http import retry_after_seconds
from spoon_manager_bot.spoon.region import validate_base_url

EVENTS_PATH = "/v1/live/events"
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 45.0
MAX_ERROR_BODY = 8 * 1024
READ_SIZE = 8 * 1024


class ConnectFailure(StrEnum):
    UNAUTHORIZED = "unauthorized"  # 401
    FORBIDDEN = "forbidden"  # 403 detailCodeなし: events.* の権限がない
    DJ_BLOCKED = "dj_blocked"  # 403 0209
    NOT_LIVE = "not_live"  # 404 0301
    RATE_LIMITED = "rate_limited"  # 429
    SERVER_ERROR = "server_error"  # 5xx
    BAD_RESPONSE = "bad_response"  # 想定外のstatus・content-type
    NETWORK_ERROR = "network_error"


@dataclass(frozen=True, slots=True)
class ConnectError:
    failure: ConnectFailure
    status: int | None = None
    detail_code: str | None = None
    retry_after: float | None = None


class StreamConnection(Protocol):
    def read(self) -> bytes:
        """届いたバイト列。接続が終わったら b""。タイムアウト・切断は OSError。"""
        ...

    def close(self) -> None: ...


class StreamConnector(Protocol):
    def open(self, access_token: str) -> StreamConnection | ConnectError: ...


def classify_status(status: int, body: bytes, retry_after: float | None) -> ConnectError:
    code = detail_code(body, sse=True)
    if status == 401:
        failure = ConnectFailure.UNAUTHORIZED
    elif status == 403:
        failure = ConnectFailure.DJ_BLOCKED if code else ConnectFailure.FORBIDDEN
    elif status == 404:
        failure = ConnectFailure.NOT_LIVE
    elif status == 429:
        failure = ConnectFailure.RATE_LIMITED
    elif status >= 500:
        failure = ConnectFailure.SERVER_ERROR
    else:
        failure = ConnectFailure.BAD_RESPONSE
    return ConnectError(failure, status, code, retry_after)


class _HttpStream:
    def __init__(self, connection: http.client.HTTPSConnection, response: http.client.HTTPResponse):
        self._connection = connection
        self._response = response

    def read(self) -> bytes:
        return self._response.read1(READ_SIZE)

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()


ConnectionFactory = Callable[[str, float], http.client.HTTPSConnection]


def _https(host: str, timeout: float) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(host, timeout=timeout, context=ssl.create_default_context())


class HttpStreamConnector:
    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = CONNECT_TIMEOUT,
        read_timeout: float = READ_TIMEOUT,
        factory: ConnectionFactory = _https,
    ) -> None:
        self._host = validate_base_url(base_url).removeprefix("https://")
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._factory = factory

    def open(self, access_token: str) -> StreamConnection | ConnectError:
        connection = self._factory(self._host, self._connect_timeout)
        try:
            connection.request(
                "GET",
                EVENTS_PATH,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "text/event-stream",
                    "Cache-Control": "no-cache",
                },
            )
            response = connection.getresponse()
        except (OSError, http.client.HTTPException):
            connection.close()
            return ConnectError(ConnectFailure.NETWORK_ERROR)

        content_type = (response.getheader("Content-Type") or "").lower()
        if response.status != 200 or not content_type.startswith("text/event-stream"):
            try:
                body = response.read(MAX_ERROR_BODY) if response.status != 200 else b""
            except (OSError, http.client.HTTPException):
                body = b""
            retry_after = retry_after_seconds(response.headers)
            connection.close()
            if response.status == 200:
                return ConnectError(ConnectFailure.BAD_RESPONSE, 200)
            return classify_status(response.status, body, retry_after)

        # 接続後は読み取りの待ち時間をheartbeat基準に切り替える
        if connection.sock is not None:
            connection.sock.settimeout(self._read_timeout)
        return _HttpStream(connection, response)
