"""テスト用のイベント生成とフェイク。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from spoon_manager_bot.domain.events import ChatEvent, DonationEvent, EventUser, LikeEvent
from spoon_manager_bot.spoon.chat_client import ChatOutcome, ChatResult

T0 = datetime(2026, 9, 2, 5, 47, 26, tzinfo=UTC)


def like(user_id: str, nickname: str | None, total: int, kind: str = "PAID") -> LikeEvent:
    return LikeEvent(
        user=EventUser(user_id, nickname),
        type=kind,
        total_amount=total,
        amount=total,
        extra_amount=0,
        combo=None if kind == "FREE" else 1,
        time=T0,
    )


def donation(user_id: str, nickname: str | None, amount: int) -> DonationEvent:
    return DonationEvent(user=EventUser(user_id, nickname), amount=amount, message=None, time=T0)


def chat(message: str, *, is_dj: bool, user_id: str = "dj-id") -> ChatEvent:
    return ChatEvent(
        user=EventUser(user_id, "スプーンDJ"), is_dj=is_dj, message=message, sent_time=T0
    )


class RecordingSender:
    def __init__(self, outcomes: list[ChatResult] | None = None) -> None:
        self.sent: list[str] = []
        self._outcomes = outcomes or []

    def send(self, message: str) -> ChatResult:
        self.sent.append(message)
        if self._outcomes:
            return self._outcomes.pop(0)
        return ChatResult(ChatOutcome.SENT, 204)


class FakeDynamoDb:
    """put_item / delete_item / query だけを持つ最小のフェイク。"""

    def __init__(self, page_size: int = 100, fail: bool = False) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.page_size = page_size
        self.fail = fail

    def put_item(self, **kwargs: Any) -> Mapping[str, Any]:
        self._maybe_fail()
        item = kwargs["Item"]
        self.items[(item["PK"]["S"], item["SK"]["S"])] = item
        return {}

    def delete_item(self, **kwargs: Any) -> Mapping[str, Any]:
        self._maybe_fail()
        key = kwargs["Key"]
        self.items.pop((key["PK"]["S"], key["SK"]["S"]), None)
        return {}

    def query(self, **kwargs: Any) -> Mapping[str, Any]:
        self._maybe_fail()
        values = kwargs["ExpressionAttributeValues"]
        pk, prefix = values[":pk"]["S"], values[":prefix"]["S"]
        keys = sorted(k for k in self.items if k[0] == pk and k[1].startswith(prefix))
        start = kwargs.get("ExclusiveStartKey")
        if start is not None:
            keys = [k for k in keys if k[1] > start["SK"]["S"]]
        page = keys[: self.page_size]
        response: dict[str, Any] = {"Items": [self.items[k] for k in page]}
        if len(keys) > self.page_size:
            response["LastEvaluatedKey"] = {"PK": {"S": pk}, "SK": {"S": page[-1][1]}}
        return response

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("secret お礼文 in error")
