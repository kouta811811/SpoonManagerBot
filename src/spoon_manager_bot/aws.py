"""AWS SDK（boto3）の薄い入口。

boto3はLambdaの実行環境とコンテナに入っている。テストやローカル検証では
フェイクのクライアントを渡すため、ここで遅延importする。
"""

from __future__ import annotations

import importlib
from typing import Any


def client(service: str, region: str | None = None) -> Any:
    boto3 = importlib.import_module("boto3")
    return boto3.client(service, region_name=region)


def error_code(exc: BaseException) -> str | None:
    """botocoreの ClientError からエラーコードを取り出す（それ以外はNone）。"""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        return code if isinstance(code, str) else None
    return None
