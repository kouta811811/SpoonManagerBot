"""redirectを追わないHTTPクライアント（短い REST 呼び出し用）。"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable
from typing import Any, Protocol


class HttpResponse(Protocol):
    status: int

    @property
    def headers(self) -> Any: ...

    def read(self) -> bytes: ...


Opener = Callable[[urllib.request.Request, float], HttpResponse]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        # 3xxはHTTPErrorとして呼び出し側へ返す（tokenを別ホストへ送らない）
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def default_open(request: urllib.request.Request, timeout: float) -> HttpResponse:
    response: HttpResponse = _OPENER.open(request, timeout=timeout)
    return response


def header(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    value = headers.get(name)
    return None if value is None else str(value)


def retry_after_seconds(headers: Any) -> float | None:
    raw = header(headers, "Retry-After")
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None
