"""Server-Sent Events の逐次パーサー（`docs/spoon-api-contract.md` §4.3）。

- 受信チャンクの境界と行の境界は一致しない前提で、バイト列を少しずつ渡せる。
- 行末は CRLF / LF / CR のどれでもよい（チャンク境界で CR と LF が分かれても正しく扱う）。
- `:` で始まる行はコメント（`:heartbeat`）。`data:` のないフレームは返さない。
- 1フレーム（または1行）が上限を超えたら SseTooLargeError で止める。
"""

from __future__ import annotations

import codecs
from dataclasses import dataclass

DEFAULT_MAX_EVENT_BYTES = 64 * 1024


class SseTooLargeError(ValueError):
    """フレームが上限を超えた。接続を閉じて再接続する。"""


@dataclass(frozen=True, slots=True)
class SseMessage:
    event: str
    data: str
    id: str | None


class SseParser:
    def __init__(self, max_event_bytes: int = DEFAULT_MAX_EVENT_BYTES) -> None:
        self._max = max_event_bytes
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""
        self._started = False
        self._event = ""
        self._data: list[str] = []
        self._data_size = 0
        self._last_id: str | None = None
        self.retry_ms: int | None = None
        self.comments = 0  # heartbeatを数える（受信が途絶えていないかの確認用）

    def feed(self, chunk: bytes) -> list[SseMessage]:
        text = self._decoder.decode(chunk)
        if not self._started and text:
            self._started = True
            text = text.removeprefix("\ufeff")
        self._buffer += text
        messages: list[SseMessage] = []
        while True:
            index = _line_end(self._buffer)
            if index is None:
                break
            line = self._buffer[:index]
            skip = 2 if self._buffer.startswith("\r\n", index) else 1
            self._buffer = self._buffer[index + skip :]
            message = self._line(line)
            if message is not None:
                messages.append(message)
        if len(self._buffer.encode("utf-8")) > self._max:
            raise SseTooLargeError("SSE line exceeds the size limit")
        return messages

    def _line(self, line: str) -> SseMessage | None:
        if line == "":
            return self._dispatch()
        if line.startswith(":"):
            self.comments += 1
            return None
        field, sep, value = line.partition(":")
        if sep:
            value = value.removeprefix(" ")
        if field == "event":
            self._event = value
        elif field == "data":
            self._data.append(value)
            self._data_size += len(value.encode("utf-8")) + 1
            if self._data_size > self._max:
                raise SseTooLargeError("SSE event exceeds the size limit")
        elif field == "id":
            if "\x00" not in value:
                self._last_id = value
        elif field == "retry" and value.isascii() and value.isdigit():
            self.retry_ms = int(value)
        return None

    def _dispatch(self) -> SseMessage | None:
        event = self._event or "message"
        has_data = bool(self._data)
        data = "\n".join(self._data)
        self._event = ""
        self._data = []
        self._data_size = 0
        if not has_data:
            return None
        return SseMessage(event=event, data=data, id=self._last_id)


def _line_end(buffer: str) -> int | None:
    """最初の行末の位置。末尾が CR だけのときは LF が続くか分からないので待つ。"""
    for index, char in enumerate(buffer):
        if char == "\n":
            return index
        if char == "\r":
            return index if index + 1 < len(buffer) else None
    return None


def strip_sse_data(body: bytes) -> str:
    """SSE形式で返るエラー本文（`data:{json}`）から `data:` を取り除く。"""
    text = body.decode("utf-8", errors="replace")
    lines = [line[5:].removeprefix(" ") for line in text.splitlines() if line.startswith("data:")]
    return "\n".join(lines) if lines else text
