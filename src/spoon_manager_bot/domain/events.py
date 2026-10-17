"""イベントストリーム（`GET /v1/live/events`）の型付きモデル。

公式schemaは `docs/spoon-api-contract.md` §4.4 を正とする。
- 未知のフィールドは無視する（前方互換）。
- 未知のイベント種別は `parse_event` が None を返し、副作用なしで無視できるようにする。
- 例外メッセージにはフィールド名だけを入れ、ペイロードの値（チャット本文・ニックネーム）を含めない。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class EventValidationError(ValueError):
    """公式schemaに合わないイベント。"""


@dataclass(frozen=True, slots=True)
class EventUser:
    id: str
    nickname: str | None


@dataclass(frozen=True, slots=True)
class ChatEvent:
    user: EventUser
    is_dj: bool
    message: str
    sent_time: datetime


@dataclass(frozen=True, slots=True)
class PresenceEvent:
    user: EventUser
    type: str
    fan_rank: int | None
    is_manager: bool
    favorite_temperature: float | None
    time: datetime


@dataclass(frozen=True, slots=True)
class LikeEvent:
    user: EventUser
    type: str
    total_amount: int
    amount: int
    extra_amount: int
    combo: int | None
    time: datetime


@dataclass(frozen=True, slots=True)
class DonationEvent:
    user: EventUser
    amount: int
    message: str | None
    time: datetime


@dataclass(frozen=True, slots=True)
class StreamEndEvent:
    reason: str


SpoonEvent = ChatEvent | PresenceEvent | LikeEvent | DonationEvent | StreamEndEvent


def _require(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise EventValidationError(f"missing field: {key}")
    return payload[key]


def _req_str(payload: dict[str, Any], key: str) -> str:
    value = _require(payload, key)
    if not isinstance(value, str):
        raise EventValidationError(f"field must be string: {key}")
    return value


def _opt_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EventValidationError(f"field must be string or null: {key}")
    return value


def _req_bool(payload: dict[str, Any], key: str) -> bool:
    value = _require(payload, key)
    if not isinstance(value, bool):
        raise EventValidationError(f"field must be boolean: {key}")
    return value


def _to_int(value: Any, key: str) -> int:
    if isinstance(value, bool):
        raise EventValidationError(f"field must be integer: {key}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise EventValidationError(f"field must be integer: {key}")


def _req_int(payload: dict[str, Any], key: str) -> int:
    return _to_int(_require(payload, key), key)


def _opt_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return None if value is None else _to_int(value, key)


def _opt_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise EventValidationError(f"field must be number or null: {key}")
    return float(value)


def _req_time(payload: dict[str, Any], key: str) -> datetime:
    raw = _req_str(payload, key)
    try:
        # Python 3.11以降は末尾Zと任意桁の小数秒を受け付ける
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EventValidationError(f"field must be ISO-8601 time: {key}") from exc
    if parsed.tzinfo is None:
        raise EventValidationError(f"time must include timezone: {key}")
    return parsed


def _user(payload: dict[str, Any]) -> EventUser:
    raw = _require(payload, "user")
    if not isinstance(raw, dict):
        raise EventValidationError("field must be object: user")
    user_id = _req_str(raw, "id")
    if not user_id:
        raise EventValidationError("field must not be empty: user.id")
    return EventUser(id=user_id, nickname=_opt_str(raw, "nickname"))


def _parse_chat(payload: dict[str, Any]) -> ChatEvent:
    return ChatEvent(
        user=_user(payload),
        is_dj=_req_bool(payload, "isDj"),
        message=_req_str(payload, "message"),
        sent_time=_req_time(payload, "sentTime"),
    )


def _parse_presence(payload: dict[str, Any]) -> PresenceEvent:
    return PresenceEvent(
        user=_user(payload),
        type=_req_str(payload, "type"),
        fan_rank=_opt_int(payload, "fanRank"),
        is_manager=_req_bool(payload, "isManager"),
        favorite_temperature=_opt_float(payload, "favoriteTemperature"),
        time=_req_time(payload, "time"),
    )


def _parse_like(payload: dict[str, Any]) -> LikeEvent:
    return LikeEvent(
        user=_user(payload),
        type=_req_str(payload, "type"),
        total_amount=_req_int(payload, "totalAmount"),
        amount=_req_int(payload, "amount"),
        extra_amount=_req_int(payload, "extraAmount"),
        combo=_opt_int(payload, "combo"),
        time=_req_time(payload, "time"),
    )


def _parse_donation(payload: dict[str, Any]) -> DonationEvent:
    return DonationEvent(
        user=_user(payload),
        amount=_req_int(payload, "amount"),
        message=_opt_str(payload, "message"),
        time=_req_time(payload, "time"),
    )


def _parse_end(payload: dict[str, Any]) -> StreamEndEvent:
    reason = _req_str(payload, "reason")
    if not reason:
        raise EventValidationError("field must not be empty: reason")
    return StreamEndEvent(reason=reason)


_PARSERS: dict[str, Callable[[dict[str, Any]], SpoonEvent]] = {
    "chat": _parse_chat,
    "presence": _parse_presence,
    "like": _parse_like,
    "donation": _parse_donation,
    "end": _parse_end,
}


def parse_event(event_name: str, data: str) -> SpoonEvent | None:
    """SSEフレームの `event:` と `data:` から型付きイベントを作る。

    未知のイベント種別は None を返す。schemaに合わない場合は EventValidationError。
    """
    parser = _PARSERS.get(event_name)
    if parser is None:
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise EventValidationError(f"invalid JSON in {event_name} event") from exc
    if not isinstance(payload, dict):
        raise EventValidationError(f"{event_name} event must be a JSON object")
    return parser(payload)
