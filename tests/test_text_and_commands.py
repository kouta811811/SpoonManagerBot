from __future__ import annotations

from spoon_manager_bot.domain.commands import Command, parse_command
from spoon_manager_bot.domain.text import fits_chat, utf16_len


def test_utf16_length_counts_emoji_as_two() -> None:
    assert utf16_len("あいう") == 3
    assert utf16_len("💕") == 2
    assert fits_chat("a" * 200)
    assert not fits_chat("a" * 199 + "💕")
    assert not fits_chat("   ")


def test_parse_command_prefixes_and_normalization() -> None:
    assert parse_command("!setheart ありがとう") == Command("setheart", "ありがとう")
    assert parse_command("/msglist") == Command("msglist", "")
    assert parse_command("！ＳｅｔＳｐｏｏｎ 100 ありがとう") == Command(
        "setspoon", "100 ありがとう"
    )
    assert parse_command("  !setheart") == Command("setheart", "")


def test_non_commands() -> None:
    assert parse_command("こんにちは") is None
    assert parse_command("!") is None
    assert parse_command("") is None
