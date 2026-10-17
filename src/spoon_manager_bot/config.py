"""環境変数からの設定（Worker・Lambda・CLI 共通）。

秘密（Client Secret・トークン）は環境変数に入れず、SSMから読む。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from spoon_manager_bot.spoon.region import SpoonRegion, resolve_region

DEFAULT_SCOPES = ("events.chat", "events.like", "events.donation", "chat.send")


class ConfigError(ValueError):
    """必須の設定がない、または不正。"""


@dataclass(frozen=True, slots=True)
class Settings:
    aws_region: str
    state_table: str
    ssm_prefix: str
    spoon_region: SpoonRegion
    ignored_user_ids: frozenset[str]
    lease_token: str | None
    live_wait_limit: float
    scopes: tuple[str, ...]


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"environment variable {name} is required")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    prefix = _required(env, "SSM_PREFIX")
    if not prefix.startswith("/"):
        raise ConfigError("SSM_PREFIX must start with '/'")
    if not prefix.endswith("/"):
        prefix += "/"
    try:
        region = resolve_region(env.get("SPOON_REGION", "jp"))
    except ValueError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        live_wait = float(env.get("LIVE_WAIT_LIMIT_SECONDS", "600"))
    except ValueError as exc:
        raise ConfigError("LIVE_WAIT_LIMIT_SECONDS must be a number") from exc
    scopes = tuple(env.get("SPOON_SCOPES", " ".join(DEFAULT_SCOPES)).split())
    ignored = frozenset(v.strip() for v in env.get("IGNORED_USER_IDS", "").split(",") if v.strip())
    return Settings(
        aws_region=env.get("AWS_REGION", env.get("AWS_DEFAULT_REGION", "ap-northeast-1")),
        state_table=_required(env, "STATE_TABLE"),
        ssm_prefix=prefix,
        spoon_region=region,
        ignored_user_ids=ignored,
        lease_token=env.get("LEASE_TOKEN") or None,
        live_wait_limit=live_wait,
        scopes=scopes,
    )
