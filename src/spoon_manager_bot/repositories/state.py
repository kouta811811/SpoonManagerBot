"""起動lease と OAuth state（設計§9）。

| PK | SK | 用途 |
|---|---|---|
| `DJ#SINGLE` | `LEASE` | 起動中のTask（lease_token、task_arn、expires_at） |
| `OAUTH#SINGLE` | `STATE#{sha256(state)}` | 同意リクエストのstate（1回限り、10分） |

stateは値そのものではなくハッシュで保存する。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from spoon_manager_bot.aws import error_code

LEASE_PK = "DJ#SINGLE"
LEASE_SK = "LEASE"
OAUTH_PK = "OAUTH#SINGLE"
STATE_TTL = 600
CONDITION_FAILED = "ConditionalCheckFailedException"


class StateError(RuntimeError):
    """DynamoDBへの読み書きに失敗した。"""


@dataclass(frozen=True, slots=True)
class Lease:
    lease_token: str
    task_arn: str | None
    expires_at: float


def _key(pk: str, sk: str) -> dict[str, Any]:
    return {"PK": {"S": pk}, "SK": {"S": sk}}


class LeaseRepository:
    def __init__(self, dynamodb: Any, table: str, *, clock: Callable[[], float] = time.time):
        self._db = dynamodb
        self._table = table
        self._clock = clock

    def acquire(self, lease_token: str, duration: float) -> bool:
        """leaseがない、または期限切れのときだけ取る。"""
        now = self._clock()
        try:
            self._db.put_item(
                TableName=self._table,
                Item={
                    **_key(LEASE_PK, LEASE_SK),
                    "lease_token": {"S": lease_token},
                    "expires_at": {"N": str(int(now + duration))},
                    "ttl_timestamp": {"N": str(int(now + duration + 86400))},
                },
                ConditionExpression="attribute_not_exists(PK) OR expires_at < :now",
                ExpressionAttributeValues={":now": {"N": str(int(now))}},
            )
        except Exception as exc:
            if error_code(exc) == CONDITION_FAILED:
                return False
            raise StateError("dynamodb put_item failed") from exc
        return True

    def set_task(self, lease_token: str, task_arn: str) -> bool:
        return self._update(lease_token, "SET task_arn = :arn", {":arn": {"S": task_arn}})

    def heartbeat(self, lease_token: str, duration: float) -> bool:
        """自分のleaseなら期限を延ばす。他のTaskに移っていたらFalse。"""
        now = self._clock()
        return self._update(
            lease_token,
            "SET expires_at = :exp, ttl_timestamp = :ttl",
            {
                ":exp": {"N": str(int(now + duration))},
                ":ttl": {"N": str(int(now + duration + 86400))},
            },
        )

    def release(self, lease_token: str) -> bool:
        try:
            self._db.delete_item(
                TableName=self._table,
                Key=_key(LEASE_PK, LEASE_SK),
                ConditionExpression="lease_token = :t",
                ExpressionAttributeValues={":t": {"S": lease_token}},
            )
        except Exception as exc:
            if error_code(exc) == CONDITION_FAILED:
                return False
            raise StateError("dynamodb delete_item failed") from exc
        return True

    def force_release(self) -> None:
        try:
            self._db.delete_item(TableName=self._table, Key=_key(LEASE_PK, LEASE_SK))
        except Exception as exc:
            raise StateError("dynamodb delete_item failed") from exc

    def get(self) -> Lease | None:
        try:
            response = self._db.get_item(
                TableName=self._table, Key=_key(LEASE_PK, LEASE_SK), ConsistentRead=True
            )
        except Exception as exc:
            raise StateError("dynamodb get_item failed") from exc
        item = response.get("Item")
        if not item:
            return None
        task = item.get("task_arn", {}).get("S")
        return Lease(
            lease_token=item["lease_token"]["S"],
            task_arn=task if isinstance(task, str) else None,
            expires_at=float(item["expires_at"]["N"]),
        )

    def _update(self, lease_token: str, expression: str, values: dict[str, Any]) -> bool:
        try:
            self._db.update_item(
                TableName=self._table,
                Key=_key(LEASE_PK, LEASE_SK),
                UpdateExpression=expression,
                ConditionExpression="lease_token = :t",
                ExpressionAttributeValues={":t": {"S": lease_token}, **values},
            )
        except Exception as exc:
            if error_code(exc) == CONDITION_FAILED:
                return False
            raise StateError("dynamodb update_item failed") from exc
        return True


def state_hash(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


class OAuthStateRepository:
    def __init__(self, dynamodb: Any, table: str, *, clock: Callable[[], float] = time.time):
        self._db = dynamodb
        self._table = table
        self._clock = clock

    def create(self, state: str, ttl: int = STATE_TTL) -> None:
        now = self._clock()
        try:
            self._db.put_item(
                TableName=self._table,
                Item={
                    **_key(OAUTH_PK, f"STATE#{state_hash(state)}"),
                    "used": {"BOOL": False},
                    "expires_at": {"N": str(int(now + ttl))},
                    "ttl_timestamp": {"N": str(int(now + ttl))},
                },
                ConditionExpression="attribute_not_exists(PK)",
            )
        except Exception as exc:
            raise StateError("dynamodb put_item failed") from exc

    def consume(self, state: str) -> bool:
        """未使用・期限内のstateなら使用済みにしてTrue。それ以外はFalse。"""
        now = self._clock()
        try:
            self._db.update_item(
                TableName=self._table,
                Key=_key(OAUTH_PK, f"STATE#{state_hash(state)}"),
                UpdateExpression="SET used = :true",
                ConditionExpression="attribute_exists(PK) AND used = :false AND expires_at > :now",
                ExpressionAttributeValues={
                    ":true": {"BOOL": True},
                    ":false": {"BOOL": False},
                    ":now": {"N": str(int(now))},
                },
            )
        except Exception as exc:
            if error_code(exc) == CONDITION_FAILED:
                return False
            raise StateError("dynamodb update_item failed") from exc
        return True
