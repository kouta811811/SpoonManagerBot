"""OAuthクライアント情報とトークンの保存（SSM SecureString）と更新。

SSMの配置（`{prefix}` は `/spoon-manager-bot/dev/` など）:

| パラメータ | 内容 |
|---|---|
| `{prefix}oauth/client_id` | Client ID |
| `{prefix}oauth/client_secret` | Client Secret |
| `{prefix}oauth/redirect_uri` | Developer Appに登録したログインリダイレクトURL |
| `{prefix}token/refresh` | refresh token |
| `{prefix}token/access` | access token・有効期限・scope（JSON） |

- refresh tokenはローテーションする。更新のたびに新しい値を先に保存してからaccess tokenを保存する
  （途中で落ちても、次回は新しいrefresh tokenで更新できる）。
- トークンを更新するのはleaseを持つWorkerだけなので、プロセス間の更新は1つに限られる。
  同じWorker内のスレッド間は threading.Lock で直列にする。
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from spoon_manager_bot.aws import error_code
from spoon_manager_bot.observability.logger import log_event
from spoon_manager_bot.spoon.oauth import (
    ClientCredentials,
    OAuthError,
    OAuthNetworkError,
    TokenResponse,
)

REFRESH_MARGIN = 300.0  # 期限の5分前に更新する
MIN_REFRESH_INTERVAL = 60.0


class TokenStoreError(RuntimeError):
    """保存先への読み書きに失敗した。"""


class ReauthRequired(RuntimeError):
    """DJに連携し直してもらう必要がある（トークンなし・invalid_grant・invalid_client）。"""


class TransientTokenError(RuntimeError):
    """一時的に更新できなかった（後で再試行する）。"""


@dataclass(frozen=True, slots=True)
class StoredToken:
    access_token: str
    refresh_token: str
    expires_at: float
    scope: str

    @property
    def scopes(self) -> frozenset[str]:
        return frozenset(self.scope.split())

    @classmethod
    def from_response(cls, response: TokenResponse, now: float) -> StoredToken:
        return cls(
            access_token=response.access_token,
            refresh_token=response.refresh_token,
            expires_at=now + response.expires_in,
            scope=response.scope,
        )


class TokenStore(Protocol):
    def load_credentials(self) -> ClientCredentials: ...

    def load_redirect_uri(self) -> str: ...

    def load_token(self) -> StoredToken | None: ...

    def save_token(self, token: StoredToken) -> None: ...

    def delete_token(self) -> None: ...


class SsmTokenStore:
    def __init__(self, ssm: Any, prefix: str) -> None:
        if not prefix.startswith("/") or not prefix.endswith("/"):
            raise ValueError("SSM prefix must start and end with '/'")
        self._ssm = ssm
        self._prefix = prefix

    def name(self, suffix: str) -> str:
        return self._prefix + suffix

    def load_credentials(self) -> ClientCredentials:
        client_id = self._get("oauth/client_id")
        client_secret = self._get("oauth/client_secret")
        if client_id is None or client_secret is None:
            raise TokenStoreError("OAuth client credentials are not configured")
        return ClientCredentials(client_id=client_id, client_secret=client_secret)

    def load_redirect_uri(self) -> str:
        value = self._get("oauth/redirect_uri")
        if value is None:
            raise TokenStoreError("OAuth redirect URI is not configured")
        return value

    def load_token(self) -> StoredToken | None:
        refresh = self._get("token/refresh")
        access_raw = self._get("token/access")
        if refresh is None or access_raw is None:
            return None
        try:
            access = json.loads(access_raw)
            return StoredToken(
                access_token=str(access["access_token"]),
                refresh_token=refresh,
                expires_at=float(access["expires_at"]),
                scope=str(access.get("scope", "")),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def save_token(self, token: StoredToken) -> None:
        self._put("token/refresh", token.refresh_token)
        self._put(
            "token/access",
            json.dumps(
                {
                    "access_token": token.access_token,
                    "expires_at": token.expires_at,
                    "scope": token.scope,
                }
            ),
        )

    def delete_token(self) -> None:
        try:
            self._ssm.delete_parameters(
                Names=[self.name("token/refresh"), self.name("token/access")]
            )
        except Exception as exc:
            raise TokenStoreError("ssm delete_parameters failed") from exc

    def _get(self, suffix: str) -> str | None:
        try:
            response = self._ssm.get_parameter(Name=self.name(suffix), WithDecryption=True)
        except Exception as exc:
            if error_code(exc) == "ParameterNotFound":
                return None
            raise TokenStoreError("ssm get_parameter failed") from exc
        value = response.get("Parameter", {}).get("Value")
        return value if isinstance(value, str) and value else None

    def _put(self, suffix: str, value: str) -> None:
        try:
            self._ssm.put_parameter(
                Name=self.name(suffix),
                Value=value,
                Type="SecureString",
                Overwrite=True,
                Tier="Standard",
            )
        except Exception as exc:
            raise TokenStoreError("ssm put_parameter failed") from exc


class TokenRefresher(Protocol):
    def refresh(self, refresh_token: str) -> TokenResponse: ...


class TokenManager:
    """Workerが使うトークン。期限前の先行更新と、401・TOKEN_EXPIRED時の更新を行う。"""

    def __init__(
        self,
        store: TokenStore,
        oauth: TokenRefresher,
        *,
        clock: Callable[[], float] = time.time,
        margin: float = REFRESH_MARGIN,
    ) -> None:
        self._store = store
        self._oauth = oauth
        self._clock = clock
        self._margin = margin
        self._lock = threading.Lock()
        self._token: StoredToken | None = None
        self._last_refresh: float | None = None

    def load(self) -> StoredToken:
        token = self._store.load_token()
        if token is None:
            raise ReauthRequired("no token stored")
        with self._lock:
            self._token = token
        return token

    @property
    def scopes(self) -> frozenset[str]:
        return self._current().scopes

    def access_token(self) -> str:
        """有効なaccess token。期限が近ければ先に更新する（失敗しても今の値を返す）。"""
        token = self._current()
        if token.expires_at - self._margin <= self._clock():
            try:
                return self.refresh(used=token.access_token).access_token
            except TransientTokenError:
                return self._current().access_token
        return token.access_token

    def refresh(self, *, used: str | None = None) -> StoredToken:
        """更新する。`used` が今の値と違えば、別の処理が更新済みなのでそのまま返す。

        `used` を渡さない場合は、直前（60秒以内）に更新していれば更新しない。
        """
        with self._lock:
            token = self._token
            if token is None:
                raise ReauthRequired("no token loaded")
            now = self._clock()
            if used is not None and used != token.access_token:
                return token
            recently = self._last_refresh is not None and now - self._last_refresh < (
                MIN_REFRESH_INTERVAL
            )
            if used is None and recently:
                return token
            try:
                response = self._oauth.refresh(token.refresh_token)
            except OAuthError as exc:
                log_event("token_refresh_failed", error_code=exc.error, status=exc.status)
                if exc.reauth_required or exc.error == "invalid_client":
                    raise ReauthRequired(exc.error) from exc
                raise TransientTokenError(exc.error) from exc
            except OAuthNetworkError as exc:
                log_event("token_refresh_failed", error_code="network")
                raise TransientTokenError("network") from exc
            new = StoredToken.from_response(response, now)
            self._token = new
            self._last_refresh = now
            self._save(new)
            log_event("token_refreshed", result="ok")
            return new

    def _save(self, token: StoredToken) -> None:
        for attempt in range(3):
            try:
                self._store.save_token(token)
                return
            except TokenStoreError:
                log_event("token_save_failed", reconnect_count=attempt + 1)
                time.sleep(0.5 * (attempt + 1))
        # メモリ上の新しいトークンでこのTaskは動き続ける。次のTaskは再連携が必要になり得る。
        log_event("token_save_gave_up", result="error")

    def _current(self) -> StoredToken:
        with self._lock:
            if self._token is None:
                raise ReauthRequired("no token loaded")
            return self._token
