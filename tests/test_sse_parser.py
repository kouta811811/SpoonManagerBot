from __future__ import annotations

import pytest

from spoon_manager_bot.spoon.errors import detail_code
from spoon_manager_bot.spoon.sse_parser import SseMessage, SseParser, SseTooLargeError

# 公式ドキュメントのレスポンス例
OFFICIAL = (
    ":heartbeat\n\n"
    "id:1788328046114-0\nevent:chat\n"
    'data:{"user":{"id":"Ab1","nickname":"スプーンリスナー"},"isDj":false,'
    '"message":"こんにちは","sentTime":"2026-09-02T05:47:26.114Z"}\n\n'
    'event:end\ndata:{"reason":"LIVE_ENDED"}\n\n'
).encode()


def test_official_sample_in_one_chunk() -> None:
    parser = SseParser()
    messages = parser.feed(OFFICIAL)
    assert [m.event for m in messages] == ["chat", "end"]
    assert messages[0].id == "1788328046114-0"
    assert "こんにちは" in messages[0].data
    assert parser.comments == 1


def test_byte_by_byte_including_split_multibyte_characters() -> None:
    parser = SseParser()
    messages: list[SseMessage] = []
    for i in range(len(OFFICIAL)):
        messages += parser.feed(OFFICIAL[i : i + 1])
    assert [m.event for m in messages] == ["chat", "end"]
    assert "スプーンリスナー" in messages[0].data


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_line_endings(newline: str) -> None:
    text = f"event:like{newline}data:{{}}{newline}{newline}"
    parser = SseParser()
    messages = parser.feed(text.encode())
    if newline == "\r":
        # 末尾のCRはLFが続くか分からないので、次のバイトが来るまで待つ
        messages += parser.feed(b"x")
    assert messages == [SseMessage("like", "{}", None)]


def test_crlf_split_across_chunks_is_one_line_end() -> None:
    parser = SseParser()
    assert parser.feed(b"event:chat\r") == []
    assert parser.feed(b"\ndata:a\r") == []
    assert parser.feed(b"\n\r\n") == [SseMessage("chat", "a", None)]


def test_multiple_data_lines_space_and_unknown_fields() -> None:
    parser = SseParser()
    messages = parser.feed(b"event: chat\ndata: a\ndata:b\nfoo:bar\nretry:3000\n\n")
    assert messages == [SseMessage("chat", "a\nb", None)]
    assert parser.retry_ms == 3000


def test_frames_without_data_are_not_dispatched() -> None:
    parser = SseParser()
    assert parser.feed(b":heartbeat\n\nevent:chat\n\n\n") == []


def test_oversized_event_and_line_raise() -> None:
    parser = SseParser(max_event_bytes=32)
    with pytest.raises(SseTooLargeError):
        parser.feed(b"data:" + b"a" * 20 + b"\ndata:" + b"b" * 20 + b"\n")
    with pytest.raises(SseTooLargeError):
        SseParser(max_event_bytes=32).feed(b"data:" + b"a" * 64)


def test_error_body_in_sse_format() -> None:
    body = b'data:{"status":404,"detailCode":"OAPI_MNGR_0301","message":"x"}\n\n'
    assert detail_code(body, sse=True) == "OAPI_MNGR_0301"
    assert detail_code(b"", sse=True) is None
    assert detail_code(b"data:not json", sse=True) is None
