"""お礼文の保存先（DynamoDB `THANKS#...`、設計§9）。

boto3には依存せず、`put_item` / `delete_item` / `query` を持つDynamoDB低レベルクライアント
（boto3の `client("dynamodb")` など）を受け取る。テストではフェイクを渡す。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from spoon_manager_bot.domain.thanks import (
    MIN_AMOUNT_LOWER,
    MIN_AMOUNT_UPPER,
    ThanksKind,
    ThanksTemplate,
)

DEFAULT_PARTITION = "DJ#SINGLE"
SORT_PREFIX = "THANKS#"


class ThanksRepositoryError(RuntimeError):
    """保存先への読み書きに失敗した。"""


class ThanksRepository(Protocol):
    def load_all(self) -> list[ThanksTemplate]: ...

    def put(self, template: ThanksTemplate) -> None: ...

    def delete(self, kind: ThanksKind, min_amount: int) -> None: ...

    def delete_all(self) -> None: ...


class InMemoryThanksRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[ThanksKind, int], ThanksTemplate] = {}

    def load_all(self) -> list[ThanksTemplate]:
        return list(self.items.values())

    def put(self, template: ThanksTemplate) -> None:
        self.items[(template.kind, template.min_amount)] = template

    def delete(self, kind: ThanksKind, min_amount: int) -> None:
        self.items.pop((kind, min_amount), None)

    def delete_all(self) -> None:
        self.items.clear()


class DynamoDbClient(Protocol):
    def put_item(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def delete_item(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def query(self, **kwargs: Any) -> Mapping[str, Any]: ...


def sort_key(kind: ThanksKind, min_amount: int) -> str:
    return f"{SORT_PREFIX}{kind.name}#{min_amount:07d}"


class DynamoDbThanksRepository:
    def __init__(
        self,
        client: DynamoDbClient,
        table_name: str,
        *,
        partition: str = DEFAULT_PARTITION,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self._table = table_name
        self._partition = partition
        self._clock = clock

    def load_all(self) -> list[ThanksTemplate]:
        templates: list[ThanksTemplate] = []
        request: dict[str, Any] = {
            "TableName": self._table,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :prefix)",
            "ExpressionAttributeValues": {
                ":pk": {"S": self._partition},
                ":prefix": {"S": SORT_PREFIX},
            },
        }
        while True:
            response = self._call("query", request)
            for item in response.get("Items", []):
                template = _from_item(item)
                if template is not None:
                    templates.append(template)
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return templates
            request["ExclusiveStartKey"] = last_key

    def put(self, template: ThanksTemplate) -> None:
        self._call(
            "put_item",
            {
                "TableName": self._table,
                "Item": {
                    "PK": {"S": self._partition},
                    "SK": {"S": sort_key(template.kind, template.min_amount)},
                    "kind": {"S": template.kind.value},
                    "min_amount": {"N": str(template.min_amount)},
                    "text": {"S": template.text},
                    "updated_at": {"S": self._clock().isoformat()},
                },
            },
        )

    def delete(self, kind: ThanksKind, min_amount: int) -> None:
        self._call(
            "delete_item",
            {
                "TableName": self._table,
                "Key": {
                    "PK": {"S": self._partition},
                    "SK": {"S": sort_key(kind, min_amount)},
                },
            },
        )

    def delete_all(self) -> None:
        """連携解除時の明示削除（要件R13-6）。"""
        for template in self.load_all():
            self.delete(template.kind, template.min_amount)

    def _call(self, operation: str, request: dict[str, Any]) -> Mapping[str, Any]:
        try:
            result: Mapping[str, Any] = getattr(self._client, operation)(**request)
        except Exception as exc:
            # 例外の中身（文面を含み得る）は伝えず、操作名だけを残す
            raise ThanksRepositoryError(f"dynamodb {operation} failed") from exc
        return result


def _from_item(item: Mapping[str, Any]) -> ThanksTemplate | None:
    """壊れた項目は読み飛ばす。"""
    try:
        kind = ThanksKind(item["kind"]["S"])
        min_amount = int(item["min_amount"]["N"])
        text = item["text"]["S"]
    except (KeyError, TypeError, ValueError):
        return None
    if not (MIN_AMOUNT_LOWER <= min_amount <= MIN_AMOUNT_UPPER) or not text.strip():
        return None
    return ThanksTemplate(kind=kind, min_amount=min_amount, text=text)
