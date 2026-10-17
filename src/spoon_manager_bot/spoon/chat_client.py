"""`POST /v1/live/chat` クライアント（`docs/spoon-api-contract.md` §6・§7）。

- 送信先は地域別の公式ホストだけ。redirectは追わない（Bearer tokenを別ホストへ送らない）。
- 成功は `204` ボディなし。失敗はHTTPステータスで先に分け、`detailCode` はあるときだけ読む。
- 呼び出し上限の429はボディが空なので、ボディを前提にしない。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from spoon_manager_bot.domain.text import fits_chat
from spoon_manager_bot.spoon.errors import detail_code
from spoon_manager_bot.spoon.http import Opener, default_open, header, retry_after_seconds
from spoon_manager_bot.spoon.region import validate_base_url

__all__ = [
    "ChatOutcome",
    "ChatResult",
    "ChatSender",
    "SpoonChatClient",
    "validate_base_url",
]

CHAT_PATH = "/v1/live/chat"
DEFAULT_TIMEOUT = 5.0
RATE_LIMIT_HEADERS = (
    "x-ratelimit-remaining",
    "x-ratelimit-replenish-rate",
    "x-ratelimit-burst-capacity",
    "x-ratelimit-daily-limit",
    "x-ratelimit-daily-remaining",
    "x-ratelimit-daily-reset",
    "x-ratelimit-daily-scope",
)


class ChatOutcome(StrEnum):
    SENT = "sent"
    INVALID = "invalid"  # 400、または送信前の検証で不可
    UNAUTHORIZED = "unauthorized"  # 401: 再連携が必要
    FORBIDDEN = "forbidden"  # 403 detailCodeなし: 権限不足（再同意）
    CHAT_BLOCKED = "chat_blocked"  # 403 0208: 凍結またはBotがチャット禁止
    DJ_BLOCKED = "dj_blocked"  # 403 0209/0211: DJがBotをブロック
    NOT_LIVE = "not_live"  # 404 0301
    CHAT_BUSY = "chat_busy"  # 429 0302: チャット送信が多すぎる（配信全体の混雑を含む）
    RATE_LIMITED = "rate_limited"  # 429 ボディなし: 呼び出し上限
    SERVER_ERROR = "server_error"  # 5xx
    NETWORK_ERROR = "network_error"  # タイムアウト・接続失敗


@dataclass(frozen=True, slots=True)
class ChatResult:
    outcome: ChatOutcome
    status: int | None = None
    detail_code: str | None = None
    retry_after: float | None = None
    rate_limit: Mapping[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.outcome is ChatOutcome.SENT


class ChatSender(Protocol):
    def send(self, message: str) -> ChatResult: ...


class SpoonChatClient:
    def __init__(
        self,
        base_url: str,
        access_token: Callable[[], str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Opener = default_open,
    ) -> None:
        self._url = validate_base_url(base_url) + CHAT_PATH
        self._access_token = access_token
        self._timeout = timeout
        self._open = opener

    def send(self, message: str) -> ChatResult:
        if not fits_chat(message):
            return ChatResult(ChatOutcome.INVALID)
        request = urllib.request.Request(
            self._url,
            data=json.dumps({"message": message}, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
            },
        )
        try:
            response = self._open(request, self._timeout)
        except urllib.error.HTTPError as error:
            body = error.read() if error.fp is not None else b""
            return _classify(error.code, error.headers, body)
        except (urllib.error.URLError, TimeoutError, OSError):
            return ChatResult(ChatOutcome.NETWORK_ERROR)
        return _classify(response.status, response.headers, b"")


def _classify(status: int, headers: Any, body: bytes) -> ChatResult:
    rate_limit = {
        name: value for name in RATE_LIMIT_HEADERS if (value := header(headers, name)) is not None
    }
    code = detail_code(body)

    def result(outcome: ChatOutcome, retry_after: float | None = None) -> ChatResult:
        return ChatResult(outcome, status, code, retry_after, rate_limit)

    if 200 <= status < 300:
        return result(ChatOutcome.SENT)
    if status == 400:
        return result(ChatOutcome.INVALID)
    if status == 401:
        return result(ChatOutcome.UNAUTHORIZED)
    if status == 403:
        if code == "OAPI_MNGR_0208":
            return result(ChatOutcome.CHAT_BLOCKED)
        if code in ("OAPI_MNGR_0209", "OAPI_MNGR_0211"):
            return result(ChatOutcome.DJ_BLOCKED)
        return result(ChatOutcome.FORBIDDEN)
    if status == 404:
        return result(ChatOutcome.NOT_LIVE)
    if status == 429:
        if code == "OAPI_MNGR_0302":
            return result(ChatOutcome.CHAT_BUSY)
        return result(ChatOutcome.RATE_LIMITED, retry_after_seconds(headers))
    if status >= 500:
        return result(ChatOutcome.SERVER_ERROR)
    return result(ChatOutcome.INVALID)
