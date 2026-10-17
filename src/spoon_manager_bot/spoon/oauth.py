"""トークンの発行・更新・失効（契約 §2、RFC 6749 / RFC 7009）。

- `application/x-www-form-urlencoded`、クライアント認証はBasicヘッダー。
- 認可コードは1回限り。ネットワークエラーでも同じcodeで再試行しない（二重使用で全トークン失効）。
- refresh tokenはローテーションする。返ってきた新しい値を呼び出し側が必ず保存する。
- 例外にはRFC 6749の `error` コードだけを入れ、`error_description` やトークンは入れない。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from spoon_manager_bot.spoon.http import Opener, default_open
from spoon_manager_bot.spoon.region import validate_base_url

TOKEN_PATH = "/v1/oauth/token"
REVOKE_PATH = "/v1/oauth/revoke"
DEFAULT_TIMEOUT = 10.0


class OAuthError(RuntimeError):
    """OAuth endpointがエラーを返した。`error` はRFC 6749のコード。"""

    def __init__(self, error: str, status: int | None) -> None:
        super().__init__(f"oauth error: {error} (status {status})")
        self.error = error
        self.status = status

    @property
    def reauth_required(self) -> bool:
        """DJに連携し直してもらう必要があるか。"""
        return self.error == "invalid_grant"


class OAuthNetworkError(RuntimeError):
    """応答を受け取れなかった。"""


@dataclass(frozen=True, slots=True)
class TokenResponse:
    access_token: str
    refresh_token: str
    expires_in: int
    scope: str
    token_type: str

    @property
    def scopes(self) -> frozenset[str]:
        return frozenset(self.scope.split())


@dataclass(frozen=True, slots=True)
class ClientCredentials:
    client_id: str
    client_secret: str


class SpoonOAuthClient:
    def __init__(
        self,
        base_url: str,
        credentials: ClientCredentials,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Opener = default_open,
    ) -> None:
        self._base = validate_base_url(base_url)
        self._credentials = credentials
        self._timeout = timeout
        self._open = opener

    def exchange_code(self, code: str, redirect_uri: str) -> TokenResponse:
        return self._token(
            {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}
        )

    def refresh(self, refresh_token: str) -> TokenResponse:
        return self._token({"grant_type": "refresh_token", "refresh_token": refresh_token})

    def revoke(self, token: str) -> None:
        self._post(REVOKE_PATH, {"token": token})

    def _token(self, form: dict[str, str]) -> TokenResponse:
        body = self._post(TOKEN_PATH, form)
        try:
            payload = json.loads(body.decode("utf-8"))
            return TokenResponse(
                access_token=str(payload["access_token"]),
                refresh_token=str(payload["refresh_token"]),
                expires_in=int(payload.get("expires_in", 3600)),
                scope=str(payload.get("scope", "")),
                token_type=str(payload.get("token_type", "Bearer")),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise OAuthError("invalid_response", 200) from exc

    def _post(self, path: str, form: dict[str, str]) -> bytes:
        basic = base64.b64encode(
            f"{self._credentials.client_id}:{self._credentials.client_secret}".encode()
        ).decode("ascii")
        request = urllib.request.Request(
            self._base + path,
            data=urllib.parse.urlencode(form).encode("ascii"),
            method="POST",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        try:
            response = self._open(request, self._timeout)
        except urllib.error.HTTPError as error:
            raise OAuthError(_error_code(error.read() if error.fp else b""), error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise OAuthNetworkError("oauth endpoint unreachable") from exc
        return response.read()


def _error_code(body: bytes) -> str:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unknown_error"
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, str) and error.isascii() else "unknown_error"
