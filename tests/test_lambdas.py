"""OAuth callback、Control Lambda、lease・state、同意URL。"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from spoon_manager_bot.cli import build_authorize_url
from spoon_manager_bot.control import Controller
from spoon_manager_bot.oauth_callback import CallbackDeps, handle_callback
from spoon_manager_bot.repositories.state import LeaseRepository, OAuthStateRepository
from spoon_manager_bot.spoon.oauth import OAuthError, TokenResponse
from tests.fakes import ConditionalDynamoDb, MemoryTokenStore

TOKEN = TokenResponse("at", "rt", 3600, "events.chat chat.send", "Bearer")


def callback_deps(
    exchange_result: Any = TOKEN,
) -> tuple[CallbackDeps, OAuthStateRepository, MemoryTokenStore, list[tuple[str, str]]]:
    db = ConditionalDynamoDb()
    states = OAuthStateRepository(db, "t", clock=lambda: 1000.0)
    store = MemoryTokenStore(redirect_uri="https://api.example/oauth/callback")
    calls: list[tuple[str, str]] = []

    def exchange(code: str, redirect_uri: str) -> TokenResponse:
        calls.append((code, redirect_uri))
        if isinstance(exchange_result, Exception):
            raise exchange_result
        result: TokenResponse = exchange_result
        return result

    deps = CallbackDeps(states=states, store=store, exchange=exchange, clock=lambda: 1000.0)
    return deps, states, store, calls


def test_callback_success_saves_token_once() -> None:
    deps, states, store, calls = callback_deps()
    states.create("state-1")
    response = handle_callback({"code": "c1", "state": "state-1"}, deps)
    assert response["statusCode"] == 200
    assert calls == [("c1", "https://api.example/oauth/callback")]
    assert store.token is not None and store.token.expires_at == 4600.0
    # 同じstateは2回使えない（codeの二重使用も防ぐ）
    again = handle_callback({"code": "c1", "state": "state-1"}, deps)
    assert again["statusCode"] == 400
    assert len(calls) == 1


def test_callback_rejects_unknown_or_missing_state() -> None:
    deps, _, store, calls = callback_deps()
    assert handle_callback({"code": "c1", "state": "nope"}, deps)["statusCode"] == 400
    assert handle_callback({"code": "c1"}, deps)["statusCode"] == 400
    assert calls == [] and store.token is None


def test_callback_expired_state() -> None:
    deps, _, _, calls = callback_deps()
    OAuthStateRepository(deps.states._db, "t", clock=lambda: 0.0).create("old", ttl=600)
    assert handle_callback({"code": "c1", "state": "old"}, deps)["statusCode"] == 400
    assert calls == []


def test_callback_access_denied() -> None:
    deps, states, _, calls = callback_deps()
    states.create("s")
    response = handle_callback({"error": "access_denied", "state": "s"}, deps)
    assert response["statusCode"] == 200
    assert "連携をやめました" in response["body"]
    assert calls == []


def test_callback_exchange_failure_does_not_leak() -> None:
    deps, states, store, _ = callback_deps(OAuthError("invalid_grant", 400))
    states.create("s")
    response = handle_callback({"code": "secret-code", "state": "s"}, deps)
    assert response["statusCode"] == 400
    assert "secret-code" not in response["body"]
    assert "invalid_grant" not in response["body"]
    assert store.token is None
    assert response["headers"]["Cache-Control"] == "no-store"


def test_state_is_stored_hashed() -> None:
    db = ConditionalDynamoDb()
    OAuthStateRepository(db, "t").create("plain-state")
    assert all("plain-state" not in sk for _, sk in db.items)


def test_lease_acquire_heartbeat_release() -> None:
    now = [0.0]
    lease = LeaseRepository(ConditionalDynamoDb(), "t", clock=lambda: now[0])
    assert lease.acquire("a", 120)
    assert not lease.acquire("b", 120)
    assert lease.heartbeat("a", 120)
    assert not lease.heartbeat("b", 120)
    now[0] = 500  # 期限切れなら別のTaskが取れる
    assert lease.acquire("b", 120)
    assert not lease.heartbeat("a", 120)
    assert not lease.release("a")
    assert lease.release("b")
    assert lease.get() is None


class FakeEcs:
    def __init__(self, run_result: dict[str, Any] | None = None) -> None:
        self.run_calls: list[dict[str, Any]] = []
        self.stopped: list[str] = []
        self.status = "RUNNING"
        self._run_result = run_result or {"tasks": [{"taskArn": "arn:task/1"}]}

    def run_task(self, **kwargs: Any) -> dict[str, Any]:
        self.run_calls.append(kwargs)
        return self._run_result

    def stop_task(self, **kwargs: Any) -> dict[str, Any]:
        self.stopped.append(kwargs["task"])
        return {}

    def describe_tasks(self, **kwargs: Any) -> dict[str, Any]:
        return {"tasks": [{"taskArn": t, "lastStatus": self.status} for t in kwargs["tasks"]]}


def controller(ecs: FakeEcs) -> tuple[Controller, LeaseRepository]:
    lease = LeaseRepository(ConditionalDynamoDb(), "t")
    return (
        Controller(
            ecs=ecs,
            lease=lease,
            cluster="c",
            task_definition="td",
            container="worker",
            subnets=["s1", "s2"],
            security_group="sg",
        ),
        lease,
    )


def test_control_start_is_exclusive_and_passes_lease_token() -> None:
    ecs = FakeEcs()
    ctl, lease = controller(ecs)
    assert ctl.handle({"action": "start"}) == {"result": "started", "task_arn": "arn:task/1"}
    assert ctl.handle({"action": "start"})["result"] == "already_running"
    assert len(ecs.run_calls) == 1
    call = ecs.run_calls[0]
    env = call["overrides"]["containerOverrides"][0]["environment"]
    current = lease.get()
    assert current is not None
    assert env == [{"name": "LEASE_TOKEN", "value": current.lease_token}]
    assert call["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "ENABLED"
    assert ctl.handle({"action": "status"})["result"] == "running"


def test_control_start_failure_releases_lease() -> None:
    ecs = FakeEcs({"tasks": [], "failures": [{"reason": "RESOURCE:CPU"}]})
    ctl, lease = controller(ecs)
    assert ctl.handle({"action": "start"})["result"] == "failed"
    assert lease.get() is None


def test_control_stop_and_reset() -> None:
    ecs = FakeEcs()
    ctl, lease = controller(ecs)
    assert ctl.handle({"action": "stop"}) == {"result": "not_running"}
    ctl.handle({"action": "start"})
    assert ctl.handle({"action": "stop"})["result"] == "stopping"
    assert ecs.stopped == ["arn:task/1"]
    assert ctl.handle({"action": "reset"})["result"] == "task_still_running"
    ecs.status = "STOPPED"
    assert ctl.handle({"action": "reset"}) == {"result": "reset"}
    assert lease.get() is None


def test_authorize_url_encodes_scopes_with_percent_20() -> None:
    url = build_authorize_url(
        "https://developers.spooncast.net/jp/oauth/authorize",
        "cid",
        "https://api.example/oauth/callback",
        ("events.chat", "chat.send"),
        "st",
    )
    assert "scope=events.chat%20chat.send" in url
    query = parse_qs(urlsplit(url).query)
    assert query["redirect_uri"] == ["https://api.example/oauth/callback"]
    assert query["response_type"] == ["code"]
    assert urlsplit(url).netloc == "developers.spooncast.net"


@pytest.mark.parametrize("region", ["kr", "us", ""])
def test_only_japan_region_is_allowed_by_default(region: str) -> None:
    from spoon_manager_bot.spoon.region import resolve_region

    with pytest.raises(ValueError):
        resolve_region(region)
