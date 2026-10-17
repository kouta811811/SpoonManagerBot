"""Spoonの地域別の公式アドレス（`docs/spoon-api-contract.md` §1）。

Bearer tokenやClient Secretは、ここに載っているホスト以外へ送らない。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class SpoonRegion:
    code: str
    api_base_url: str
    authorize_url: str


REGIONS: dict[str, SpoonRegion] = {
    "jp": SpoonRegion(
        code="jp",
        api_base_url="https://jp-openapi.spooncast.net",
        authorize_url="https://developers.spooncast.net/jp/oauth/authorize",
    ),
    "kr": SpoonRegion(
        code="kr",
        api_base_url="https://kr-openapi.spooncast.net",
        authorize_url="https://developers.spooncast.net/kr/oauth/authorize",
    ),
}

ALLOWED_API_HOSTS = frozenset(urlsplit(r.api_base_url).hostname or "" for r in REGIONS.values())

# MVPは日本のアプリ・DJだけを対象にする
DEFAULT_ALLOWED_REGIONS = frozenset({"jp"})


def resolve_region(code: str, allowed: frozenset[str] = DEFAULT_ALLOWED_REGIONS) -> SpoonRegion:
    normalized = code.strip().lower()
    if normalized not in allowed or normalized not in REGIONS:
        raise ValueError("Spoon region is not allowed")
    return REGIONS[normalized]


def validate_base_url(base_url: str) -> str:
    """許可リストのhttpsホストだけを受け付け、`https://host` の形に揃える。"""
    parts = urlsplit(base_url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_API_HOSTS:
        raise ValueError("base_url must be an allowlisted https Spoon API host")
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.port:
        raise ValueError("base_url must not contain a path, query, fragment, or port")
    return f"https://{parts.hostname}"
