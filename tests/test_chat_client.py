from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message
from typing import Any

import pytest

from spoon_manager_bot.spoon.chat_client import (
    ChatOutcome,
    SpoonChatClient,
    validate_base_url,
)


class FakeResponse:
    def __init__(self, status: int, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.headers = headers or {}

    def read(self) -> bytes:
        return b""


def http_error(status: int, body: bytes = b"", headers: dict[str, str] | None = None) -> Any:
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://x", status, "err", message, io.BytesIO(body))


def client_with(response: Any) -> tuple[SpoonChatClient, list[urllib.request.Request]]:
    requests: list[urllib.request.Request] = []

    def opener(request: urllib.request.Request, timeout: float) -> Any:
        requests.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    return SpoonChatClient(
        "https://jp-openapi.spooncast.net", lambda: "tok", opener=opener
    ), requests


def test_sends_official_request_shape() -> None:
    client, requests = client_with(FakeResponse(204, {"x-ratelimit-remaining": "3"}))
    result = client.send("こんにちは")
    assert result.ok
    assert result.rate_limit == {"x-ratelimit-remaining": "3"}
    request = requests[0]
    assert request.full_url == "https://jp-openapi.spooncast.net/v1/live/chat"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer tok"
    assert request.get_header("Content-type") == "application/json"
    assert json.loads(request.data) == {"message": "こんにちは"}  # type: ignore[arg-type]


def test_validates_length_before_sending() -> None:
    client, requests = client_with(FakeResponse(204))
    assert client.send("a" * 199 + "💕").outcome is ChatOutcome.INVALID
    assert client.send(" ").outcome is ChatOutcome.INVALID
    assert requests == []


def body(code: str) -> bytes:
    return json.dumps({"status": 0, "detailCode": code, "message": "x"}).encode()


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (http_error(400, body("OAPI_MNGR_0109")), ChatOutcome.INVALID),
        (http_error(401), ChatOutcome.UNAUTHORIZED),
        (http_error(403), ChatOutcome.FORBIDDEN),
        (http_error(403, body("OAPI_MNGR_0208")), ChatOutcome.CHAT_BLOCKED),
        (http_error(403, body("OAPI_MNGR_0209")), ChatOutcome.DJ_BLOCKED),
        (http_error(404, body("OAPI_MNGR_0301")), ChatOutcome.NOT_LIVE),
        (http_error(429, body("OAPI_MNGR_0302")), ChatOutcome.CHAT_BUSY),
        (http_error(429), ChatOutcome.RATE_LIMITED),
        (http_error(502, body("OAPI_MNGR_2303")), ChatOutcome.SERVER_ERROR),
        (http_error(500, b"not json"), ChatOutcome.SERVER_ERROR),
        (urllib.error.URLError("timeout"), ChatOutcome.NETWORK_ERROR),
        (TimeoutError(), ChatOutcome.NETWORK_ERROR),
    ],
)
def test_classifies_errors(error: Exception, outcome: ChatOutcome) -> None:
    client, _ = client_with(error)
    assert client.send("hi").outcome is outcome


def test_daily_limit_retry_after() -> None:
    client, _ = client_with(http_error(429, b"", {"Retry-After": "3600"}))
    result = client.send("hi")
    assert result.outcome is ChatOutcome.RATE_LIMITED
    assert result.retry_after == 3600.0


@pytest.mark.parametrize(
    "url",
    [
        "http://jp-openapi.spooncast.net",
        "https://evil.example.com",
        "https://jp-openapi.spooncast.net.evil.example",
        "https://jp-openapi.spooncast.net/v1",
        "https://jp-openapi.spooncast.net:8443",
    ],
)
def test_rejects_non_allowlisted_base_url(url: str) -> None:
    with pytest.raises(ValueError):
        validate_base_url(url)
