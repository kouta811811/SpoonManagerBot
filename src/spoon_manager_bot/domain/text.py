"""チャット本文の長さ計算。

Spoonのチャット上限はUTF-16 code unit基準で数える（絵文字1つが2文字になることがある）。
"""

CHAT_MAX_LENGTH = 200


def utf16_len(text: str) -> int:
    """UTF-16 code unit で数えた文字数を返す。"""
    return len(text.encode("utf-16-le")) // 2


def fits_chat(text: str) -> bool:
    """空・空白のみでなく、チャット上限に収まるか。"""
    return bool(text.strip()) and utf16_len(text) <= CHAT_MAX_LENGTH
