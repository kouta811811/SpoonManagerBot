"""運用者用のコマンド（AWSの認証情報が必要）。

    python -m spoon_manager_bot.cli auth-url   DJに送る同意画面のURLを作る（10分有効・1回限り）
    python -m spoon_manager_bot.cli revoke     トークンを失効させて削除する（連携の解除）

環境変数: STATE_TABLE、SSM_PREFIX、SPOON_REGION（既定jp）、SPOON_SCOPES、AWS_REGION
"""

from __future__ import annotations

import argparse
import secrets
import sys
from urllib.parse import quote, urlencode

from spoon_manager_bot import aws
from spoon_manager_bot.config import Settings, load_settings
from spoon_manager_bot.repositories.state import OAuthStateRepository
from spoon_manager_bot.spoon.oauth import OAuthError, OAuthNetworkError, SpoonOAuthClient
from spoon_manager_bot.thanks.repository import DynamoDbThanksRepository
from spoon_manager_bot.tokens import SsmTokenStore


def build_authorize_url(
    authorize_url: str, client_id: str, redirect_uri: str, scopes: tuple[str, ...], state: str
) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        },
        quote_via=quote,  # scopeの区切りを %20 にする（+ではなく）
    )
    return f"{authorize_url}?{query}"


def auth_url(settings: Settings) -> int:
    store = SsmTokenStore(aws.client("ssm", settings.aws_region), settings.ssm_prefix)
    states = OAuthStateRepository(aws.client("dynamodb", settings.aws_region), settings.state_table)
    credentials = store.load_credentials()
    state = secrets.token_urlsafe(32)
    states.create(state)
    url = build_authorize_url(
        settings.spoon_region.authorize_url,
        credentials.client_id,
        store.load_redirect_uri(),
        settings.scopes,
        state,
    )
    print("DJにこのURLを開いてもらってください（10分有効・1回限り）:")
    print(url)
    return 0


def revoke(settings: Settings, *, delete_templates: bool) -> int:
    dynamodb = aws.client("dynamodb", settings.aws_region)
    store = SsmTokenStore(aws.client("ssm", settings.aws_region), settings.ssm_prefix)
    token = store.load_token()
    if token is not None:
        client = SpoonOAuthClient(settings.spoon_region.api_base_url, store.load_credentials())
        try:
            client.revoke(token.refresh_token)  # refreshを失効させるとaccessも失効する
        except (OAuthError, OAuthNetworkError) as exc:
            print(f"失効APIの呼び出しに失敗しました: {type(exc).__name__}", file=sys.stderr)
            return 1
        store.delete_token()
    if delete_templates:
        DynamoDbThanksRepository(dynamodb, settings.state_table).delete_all()
    print("トークンを失効・削除しました。" + ("お礼文も削除しました。" if delete_templates else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spoon_manager_bot.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("auth-url", help="同意画面のURLを作る")
    revoke_parser = sub.add_parser("revoke", help="トークンを失効・削除する")
    revoke_parser.add_argument("--delete-templates", action="store_true", help="お礼文も削除する")
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "auth-url":
        return auth_url(settings)
    return revoke(settings, delete_templates=args.delete_templates)


if __name__ == "__main__":
    sys.exit(main())
