"""Bot API のエラー本文（RFC 7807）から `detailCode` を読む（契約 §7）。"""

from __future__ import annotations

import json

from spoon_manager_bot.spoon.sse_parser import strip_sse_data


def detail_code(body: bytes, *, sse: bool = False) -> str | None:
    """`detailCode` があれば返す。ゲートウェイ由来の401/403/429など、無い場合はNone。"""
    if not body:
        return None
    text = strip_sse_data(body) if sse else body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    code = payload.get("detailCode") if isinstance(payload, dict) else None
    return code if isinstance(code, str) else None
