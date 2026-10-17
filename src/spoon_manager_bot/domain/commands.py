"""チャットコマンドの解析。

`!` または `/` で始まる発言をコマンドとして扱う。スマホの日本語入力で打ちやすいよう、
全角の `！` `／` も受け付け、コマンド名はNFKC正規化と小文字化をする。
引数（コマンド名より後ろ）は加工せずに渡す。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

COMMAND_PREFIXES = frozenset({"!", "/", "！", "／"})


@dataclass(frozen=True, slots=True)
class Command:
    name: str
    rest: str


def parse_command(message: str) -> Command | None:
    """発言からコマンド名と引数を取り出す。コマンドでなければ None。"""
    text = message.lstrip()
    if not text or text[0] not in COMMAND_PREFIXES:
        return None
    parts = text[1:].split(maxsplit=1)
    if not parts:
        return None
    name = unicodedata.normalize("NFKC", parts[0]).lower()
    rest = parts[1] if len(parts) > 1 else ""
    return Command(name=name, rest=rest)
