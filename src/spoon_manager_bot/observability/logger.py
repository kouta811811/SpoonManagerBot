"""allowlist方式の構造化ログ（要件R11-2、設計§12）。

許可していない項目は出力しない。チャット本文・ニックネーム・リスナーIDを
誤って渡しても、ログには残らない。
"""

from __future__ import annotations

import json
import logging
from typing import Any

ALLOWED_FIELDS = frozenset(
    {
        "event",
        "level",
        "task_id",
        "session_hash",
        "dj_hash",
        "connection_state",
        "event_type",
        "event_id_hash",
        "result",
        "duration_ms",
        "reconnect_count",
        "error_code",
        "lane",
        "label",
        "thanks_kind",
        "tier_min",
        "special",
        "recipient_count",
        "text_length",
        "status",
        "detail_code",
        "pending",
    }
)

_logger = logging.getLogger("spoon_manager_bot")


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> dict[str, Any]:
    record: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        if key in ALLOWED_FIELDS and isinstance(value, str | int | float | bool | None):
            record[key] = value
    _logger.log(level, json.dumps(record, ensure_ascii=False, sort_keys=True))
    return record
