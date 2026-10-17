"""ランタイム（ストリーム・トークン・AWS）のテスト用フェイク。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from spoon_manager_bot.spoon.oauth import OAuthError, TokenResponse
from spoon_manager_bot.spoon.sse_client import ConnectError, StreamConnection
from spoon_manager_bot.tokens import StoredToken


class ClientError(Exception):
    """botocoreの ClientError と同じ形の `response` を持つ例外。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class ScriptedStream:
    """決められたチャンクを返し、最後に b"" か例外で終わる接続。"""

    def __init__(self, chunks: list[bytes], end: BaseException | None = None) -> None:
        self._chunks = list(chunks)
        self._end = end
        self.closed = False

    def read(self) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        if self._end is not None:
            raise self._end
        return b""

    def close(self) -> None:
        self.closed = True


class ScriptedConnector:
    def __init__(self, results: list[StreamConnection | ConnectError]) -> None:
        self._results = list(results)
        self.tokens: list[str] = []

    def open(self, access_token: str) -> StreamConnection | ConnectError:
        self.tokens.append(access_token)
        if not self._results:
            raise AssertionError("connector called more times than scripted")
        return self._results.pop(0)


def frame(event: str, data: str) -> bytes:
    return f"event:{event}\ndata:{data}\n\n".encode()


class FakeTokens:
    """StreamSupervisor用の最小のトークン源。"""

    def __init__(self, refresh_errors: list[BaseException] | None = None) -> None:
        self.version = 1
        self.refresh_calls = 0
        self._errors = list(refresh_errors or [])

    def access_token(self) -> str:
        return f"at{self.version}"

    def refresh(self, *, used: str | None = None) -> object:
        self.refresh_calls += 1
        if self._errors:
            raise self._errors.pop(0)
        self.version += 1
        return None


class MemoryTokenStore:
    def __init__(self, token: StoredToken | None = None, redirect_uri: str = "https://cb") -> None:
        self.token = token
        self.redirect_uri = redirect_uri
        self.saved: list[StoredToken] = []
        self.fail_save = False

    def load_credentials(self) -> Any:
        from spoon_manager_bot.spoon.oauth import ClientCredentials

        return ClientCredentials("00000000-0000-0000-0000-000000000000", "secret")

    def load_redirect_uri(self) -> str:
        return self.redirect_uri

    def load_token(self) -> StoredToken | None:
        return self.token

    def save_token(self, token: StoredToken) -> None:
        from spoon_manager_bot.tokens import TokenStoreError

        if self.fail_save:
            raise TokenStoreError("ssm put_parameter failed")
        self.token = token
        self.saved.append(token)

    def delete_token(self) -> None:
        self.token = None


class FakeRefresher:
    def __init__(self, errors: list[BaseException] | None = None) -> None:
        self.calls: list[str] = []
        self._errors = list(errors or [])

    def refresh(self, refresh_token: str) -> TokenResponse:
        self.calls.append(refresh_token)
        if self._errors:
            raise self._errors.pop(0)
        n = len(self.calls)
        return TokenResponse(
            access_token=f"at{n + 1}",
            refresh_token=f"rt{n + 1}",
            expires_in=3600,
            scope="events.chat events.like events.donation chat.send",
            token_type="Bearer",
        )


def oauth_error(error: str, status: int = 400) -> OAuthError:
    return OAuthError(error, status)


class FakeSsm:
    def __init__(self) -> None:
        self.params: dict[str, str] = {}
        self.types: dict[str, str] = {}
        self.fail = False

    def get_parameter(self, **kwargs: Any) -> Mapping[str, Any]:
        self._maybe_fail()
        name = kwargs["Name"]
        assert kwargs.get("WithDecryption") is True
        if name not in self.params:
            raise ClientError("ParameterNotFound")
        return {"Parameter": {"Name": name, "Value": self.params[name]}}

    def put_parameter(self, **kwargs: Any) -> Mapping[str, Any]:
        self._maybe_fail()
        self.params[kwargs["Name"]] = kwargs["Value"]
        self.types[kwargs["Name"]] = kwargs["Type"]
        return {}

    def delete_parameters(self, **kwargs: Any) -> Mapping[str, Any]:
        self._maybe_fail()
        for name in kwargs["Names"]:
            self.params.pop(name, None)
        return {}

    def _maybe_fail(self) -> None:
        if self.fail:
            raise ClientError("ThrottlingException")


class ConditionalDynamoDb:
    """LeaseRepository / OAuthStateRepository が使う条件式だけを評価するフェイク。"""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(key: Mapping[str, Any]) -> tuple[str, str]:
        return key["PK"]["S"], key["SK"]["S"]

    def _check(
        self, item: dict[str, Any] | None, condition: str | None, values: Mapping[str, Any]
    ) -> None:
        if condition is None:
            return
        ok: bool
        if condition == "attribute_not_exists(PK) OR expires_at < :now":
            ok = item is None or float(item["expires_at"]["N"]) < float(values[":now"]["N"])
        elif condition == "attribute_not_exists(PK)":
            ok = item is None
        elif condition == "lease_token = :t":
            ok = item is not None and item["lease_token"]["S"] == values[":t"]["S"]
        elif condition == "attribute_exists(PK) AND used = :false AND expires_at > :now":
            ok = (
                item is not None
                and item["used"]["BOOL"] is False
                and float(item["expires_at"]["N"]) > float(values[":now"]["N"])
            )
        else:
            raise AssertionError(f"unsupported condition: {condition}")
        if not ok:
            raise ClientError("ConditionalCheckFailedException")

    def put_item(self, **kwargs: Any) -> Mapping[str, Any]:
        item = kwargs["Item"]
        key = self._key(item)
        self._check(
            self.items.get(key),
            kwargs.get("ConditionExpression"),
            kwargs.get("ExpressionAttributeValues", {}),
        )
        self.items[key] = dict(item)
        return {}

    def update_item(self, **kwargs: Any) -> Mapping[str, Any]:
        key = self._key(kwargs["Key"])
        values = kwargs.get("ExpressionAttributeValues", {})
        item = self.items.get(key)
        self._check(item, kwargs.get("ConditionExpression"), values)
        assert item is not None
        expression = kwargs["UpdateExpression"].removeprefix("SET ")
        for assignment in expression.split(","):
            field, placeholder = (part.strip() for part in assignment.split("="))
            item[field] = values[placeholder]
        return {}

    def delete_item(self, **kwargs: Any) -> Mapping[str, Any]:
        key = self._key(kwargs["Key"])
        self._check(
            self.items.get(key),
            kwargs.get("ConditionExpression"),
            kwargs.get("ExpressionAttributeValues", {}),
        )
        self.items.pop(key, None)
        return {}

    def get_item(self, **kwargs: Any) -> Mapping[str, Any]:
        item = self.items.get(self._key(kwargs["Key"]))
        return {"Item": item} if item else {}
