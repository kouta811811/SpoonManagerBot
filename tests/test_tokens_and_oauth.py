from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any

import pytest

from spoon_manager_bot.spoon.oauth import (
    ClientCredentials,
    OAuthError,
    OAuthNetworkError,
    SpoonOAuthClient,
)
from spoon_manager_bot.tokens import (
    ReauthRequired,
    SsmTokenStore,
    StoredToken,
    TokenManager,
    TransientTokenError,
)
from tests.fakes import FakeRefresher, FakeSsm, MemoryTokenStore, oauth_error

CREDS = ClientCredentials("00000000-0000-0000-0000-000000000000", "s3cr3t")
TOKEN_BODY = json.dumps(
    {
        "access_token": "at_new",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "rt_new",
        "scope": "events.chat chat.send",
    }
).encode()


class Response:
    status = 200

    def __init__(self, body: bytes) -> None:
        self.headers: dict[str, str] = {}
        self._body = body

    def read(self) -> bytes:
        return self._body


def http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", status, "err", Message(), io.BytesIO(body))


def oauth_client(result: Any) -> tuple[SpoonOAuthClient, list[urllib.request.Request]]:
    requests: list[urllib.request.Request] = []

    def opener(request: urllib.request.Request, timeout: float) -> Any:
        requests.append(request)
        if isinstance(result, Exception):
            raise result
        return result

    return SpoonOAuthClient("https://jp-openapi.spooncast.net", CREDS, opener=opener), requests


def test_exchange_code_request_shape() -> None:
    client, requests = oauth_client(Response(TOKEN_BODY))
    token = client.exchange_code("code123", "https://example.com/oauth/callback")
    assert token.refresh_token == "rt_new"
    assert token.scopes == {"events.chat", "chat.send"}
    request = requests[0]
    assert request.full_url == "https://jp-openapi.spooncast.net/v1/oauth/token"
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"
    expected = base64.b64encode(f"{CREDS.client_id}:{CREDS.client_secret}".encode()).decode()
    assert request.get_header("Authorization") == f"Basic {expected}"
    form = urllib.parse.parse_qs(request.data.decode())  # type: ignore[union-attr]
    assert form == {
        "grant_type": ["authorization_code"],
        "code": ["code123"],
        "redirect_uri": ["https://example.com/oauth/callback"],
    }


def test_refresh_and_revoke_forms() -> None:
    client, requests = oauth_client(Response(TOKEN_BODY))
    client.refresh("rt_old")
    client.revoke("rt_old")
    forms = [urllib.parse.parse_qs(r.data.decode()) for r in requests]  # type: ignore[union-attr]
    assert forms[0] == {"grant_type": ["refresh_token"], "refresh_token": ["rt_old"]}
    assert forms[1] == {"token": ["rt_old"]}
    assert requests[1].full_url.endswith("/v1/oauth/revoke")


def test_rfc6749_error_without_description() -> None:
    body = b'{"error":"invalid_grant","error_description":"secret detail rt_old"}'
    client, _ = oauth_client(http_error(400, body))
    with pytest.raises(OAuthError) as excinfo:
        client.refresh("rt_old")
    assert excinfo.value.error == "invalid_grant"
    assert excinfo.value.reauth_required
    assert "secret detail" not in str(excinfo.value)
    assert "rt_old" not in str(excinfo.value)


def test_network_error() -> None:
    client, _ = oauth_client(urllib.error.URLError("down"))
    with pytest.raises(OAuthNetworkError):
        client.refresh("rt")


def test_ssm_store_roundtrip_uses_secure_string() -> None:
    ssm = FakeSsm()
    store = SsmTokenStore(ssm, "/spoon-manager-bot/dev/")
    assert store.load_token() is None
    store.save_token(StoredToken("at", "rt", 1000.0, "events.chat chat.send"))
    assert store.load_token() == StoredToken("at", "rt", 1000.0, "events.chat chat.send")
    assert set(ssm.types.values()) == {"SecureString"}
    assert set(ssm.params) == {
        "/spoon-manager-bot/dev/token/refresh",
        "/spoon-manager-bot/dev/token/access",
    }
    store.delete_token()
    assert store.load_token() is None


def test_token_manager_refreshes_before_expiry_and_saves_rotation() -> None:
    now = [0.0]
    store = MemoryTokenStore(StoredToken("at1", "rt1", 3600.0, "events.chat"))
    refresher = FakeRefresher()
    manager = TokenManager(store, refresher, clock=lambda: now[0])
    manager.load()
    assert manager.access_token() == "at1"
    now[0] = 3600 - 299  # 期限の5分前を過ぎた
    assert manager.access_token() == "at2"
    assert refresher.calls == ["rt1"]
    assert store.saved[-1].refresh_token == "rt2"  # ローテーションした値を保存
    assert manager.scopes == {"events.chat", "events.like", "events.donation", "chat.send"}


def test_refresh_with_stale_used_token_does_not_refresh_twice() -> None:
    store = MemoryTokenStore(StoredToken("at1", "rt1", 1e12, "events.chat"))
    refresher = FakeRefresher()
    manager = TokenManager(store, refresher, clock=lambda: 0.0)
    manager.load()
    manager.refresh(used="at1")
    manager.refresh(used="at1")  # 別の処理が更新済み
    assert refresher.calls == ["rt1"]


def test_invalid_grant_requires_reauth_and_transient_errors_do_not() -> None:
    store = MemoryTokenStore(StoredToken("at1", "rt1", 1e12, "events.chat"))
    manager = TokenManager(store, FakeRefresher([oauth_error("invalid_grant")]))
    manager.load()
    with pytest.raises(ReauthRequired):
        manager.refresh(used="at1")
    manager = TokenManager(store, FakeRefresher([oauth_error("server_error", 500)]))
    manager.load()
    with pytest.raises(TransientTokenError):
        manager.refresh(used="at1")


def test_no_stored_token_requires_reauth() -> None:
    with pytest.raises(ReauthRequired):
        TokenManager(MemoryTokenStore(None), FakeRefresher()).load()
