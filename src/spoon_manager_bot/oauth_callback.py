"""OAuth callback Lambda（`GET /oauth/callback`、要件R2、設計§7）。

DJが同意画面から戻ってきたときに呼ばれる。stateを1回だけ使えるように確認し、
codeをすぐ（60秒以内）トークンに交換してSSMへ保存する。
画面にはcode・state・トークン・エラー詳細を出さない。
"""

from __future__ import annotations

import html
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from spoon_manager_bot import aws
from spoon_manager_bot.observability.logger import log_event
from spoon_manager_bot.repositories.state import OAuthStateRepository, StateError
from spoon_manager_bot.spoon.oauth import OAuthError, OAuthNetworkError, TokenResponse
from spoon_manager_bot.spoon.region import resolve_region
from spoon_manager_bot.tokens import StoredToken, TokenStore, TokenStoreError

MAX_PARAM_LENGTH = 2048


@dataclass(frozen=True, slots=True)
class CallbackDeps:
    states: OAuthStateRepository
    store: TokenStore
    exchange: Callable[[str, str], TokenResponse]
    clock: Callable[[], float] = time.time


def _page(status: int, title: str, message: str) -> dict[str, Any]:
    body = (
        "<!doctype html><html lang='ja'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        f"<body style='font-family:sans-serif;padding:2em'><h1>{html.escape(title)}</h1>"
        f"<p>{html.escape(message)}</p></body></html>"
    )
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
        },
        "body": body,
    }


def handle_callback(params: dict[str, str], deps: CallbackDeps) -> dict[str, Any]:
    state = params.get("state", "")
    if not state or len(state) > MAX_PARAM_LENGTH:
        log_event("oauth_callback", result="missing_state")
        return _page(400, "連携できませんでした", "もう一度、連携用のURLから開き直してください。")
    try:
        valid = deps.states.consume(state)
    except StateError:
        log_event("oauth_callback", result="state_store_error", level=40)
        return _page(500, "連携できませんでした", "時間をおいてもう一度お試しください。")
    if not valid:
        log_event("oauth_callback", result="invalid_state")
        return _page(400, "連携できませんでした", "このURLは期限切れか使用済みです。")

    error = params.get("error")
    if error:
        code = error if error.isascii() and len(error) <= 64 else "unknown"
        log_event("oauth_callback", result="denied", error_code=code)
        if code == "access_denied":
            return _page(200, "連携をやめました", "許可されなかったため、Botは連携していません。")
        return _page(400, "連携できませんでした", "Botの設定を確認してください。")

    code_value = params.get("code", "")
    if not code_value or len(code_value) > MAX_PARAM_LENGTH:
        log_event("oauth_callback", result="missing_code")
        return _page(400, "連携できませんでした", "もう一度、連携用のURLから開き直してください。")

    try:
        redirect_uri = deps.store.load_redirect_uri()
        # codeは1回限り。失敗しても同じcodeで再試行しない。
        response = deps.exchange(code_value, redirect_uri)
        deps.store.save_token(StoredToken.from_response(response, deps.clock()))
    except OAuthError as exc:
        log_event("oauth_callback", result="exchange_failed", error_code=exc.error, level=40)
        return _page(400, "連携できませんでした", "もう一度、連携用のURLから開き直してください。")
    except (OAuthNetworkError, TokenStoreError) as exc:
        log_event("oauth_callback", result="error", error_code=type(exc).__name__, level=40)
        return _page(500, "連携できませんでした", "時間をおいてもう一度お試しください。")

    log_event("oauth_callback", result="connected", pending=len(response.scopes))
    return _page(200, "連携しました", "Botとの連携が完了しました。このページは閉じてかまいません。")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    from spoon_manager_bot.spoon.oauth import SpoonOAuthClient
    from spoon_manager_bot.tokens import SsmTokenStore

    region = os.environ.get("AWS_REGION")
    store = SsmTokenStore(aws.client("ssm", region), os.environ["SSM_PREFIX"])
    spoon = resolve_region(os.environ.get("SPOON_REGION", "jp"))

    def exchange(code: str, redirect_uri: str) -> TokenResponse:
        client = SpoonOAuthClient(spoon.api_base_url, store.load_credentials())
        return client.exchange_code(code, redirect_uri)

    deps = CallbackDeps(
        states=OAuthStateRepository(aws.client("dynamodb", region), os.environ["STATE_TABLE"]),
        store=store,
        exchange=exchange,
    )
    raw = event.get("queryStringParameters") or {}
    params = {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
    return handle_callback(params, deps)
