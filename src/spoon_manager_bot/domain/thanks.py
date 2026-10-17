"""ハート・スプーンのお礼文（要件R13、設計§6.3）。

- お礼文は種別（ハート／スプーン）ごとに、下限つきの段を最大5段まで持つ。
- 送り主の量以下の下限のうち、最大の段の文面を使う。
- 最も小さい下限の段を「通常文」、それより上の段を「特別文」と呼ぶ。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from spoon_manager_bot.domain.text import CHAT_MAX_LENGTH, utf16_len

MIN_AMOUNT_LOWER = 1
MIN_AMOUNT_UPPER = 1_000_000
MAX_TIERS_PER_KIND = 5
TEMPLATE_MAX_LENGTH = 150

VAR_NAME = "{名前}"
VAR_AMOUNT = "{数}"
VAR_TOTAL = "{累計}"

ANONYMOUS_NAME = "リスナー"
HONORIFIC = "さん"
NAME_SEPARATOR = "、"
ELLIPSIS = "…"


class ThanksKind(StrEnum):
    HEART = "heart"
    SPOON = "spoon"

    @property
    def label(self) -> str:
        return "ハート" if self is ThanksKind.HEART else "スプーン"


@dataclass(frozen=True, slots=True)
class ThanksTemplate:
    kind: ThanksKind
    min_amount: int
    text: str


@dataclass(frozen=True, slots=True)
class Recipient:
    """お礼の相手。ニックネームはメモリ上だけで扱う。"""

    nickname: str | None
    amount: int


class TemplateSet:
    """登録済みのお礼文をメモリ上で保持する。"""

    def __init__(self, templates: Iterable[ThanksTemplate] = ()) -> None:
        self._by_kind: dict[ThanksKind, dict[int, ThanksTemplate]] = {k: {} for k in ThanksKind}
        for template in templates:
            self.upsert(template)

    def upsert(self, template: ThanksTemplate) -> bool:
        """登録または上書きする。上書きならTrue。"""
        tiers = self._by_kind[template.kind]
        existed = template.min_amount in tiers
        tiers[template.min_amount] = template
        return existed

    def remove(self, kind: ThanksKind, min_amount: int) -> bool:
        return self._by_kind[kind].pop(min_amount, None) is not None

    def has(self, kind: ThanksKind, min_amount: int) -> bool:
        return min_amount in self._by_kind[kind]

    def tiers(self, kind: ThanksKind) -> list[ThanksTemplate]:
        """下限の小さい順。"""
        tiers = self._by_kind[kind]
        return [tiers[m] for m in sorted(tiers)]

    def is_empty(self, kind: ThanksKind) -> bool:
        return not self._by_kind[kind]

    def lowest_min(self, kind: ThanksKind) -> int | None:
        tiers = self._by_kind[kind]
        return min(tiers) if tiers else None

    def select(self, kind: ThanksKind, amount: int) -> ThanksTemplate | None:
        """量以下の下限のうち最大の段。どの段にも届かなければNone。"""
        eligible = [m for m in self._by_kind[kind] if m <= amount]
        return self._by_kind[kind][max(eligible)] if eligible else None

    def is_special(self, template: ThanksTemplate) -> bool:
        lowest = self.lowest_min(template.kind)
        return lowest is not None and template.min_amount > lowest


def display_name(nickname: str | None) -> str:
    return f"{nickname or ANONYMOUS_NAME}{HONORIFIC}"


def render(text: str, *, names: str, amount: int, total: int) -> str:
    """変数を展開する。登録されていない `{...}` はそのまま残す。"""
    return (
        text.replace(VAR_NAME, names)
        .replace(VAR_AMOUNT, f"{amount:,}")
        .replace(VAR_TOTAL, f"{total:,}")
    )


def compose(text: str, recipients: Sequence[Recipient], *, total: int) -> str | None:
    """お礼の本文を作る。200文字（UTF-16）に収まらなければNone。

    recipientsは並べたい順（量の多い順）で渡す。収まらない人は「ほかN名」にまとめ、
    1人目だけでも収まらない場合は1人目の名前を切り詰める。
    """
    if not recipients:
        return None
    amount = sum(r.amount for r in recipients)
    count = len(recipients)

    def build(names: str) -> str | None:
        message = render(text, names=names, amount=amount, total=total)
        if message.strip() and utf16_len(message) <= CHAT_MAX_LENGTH:
            return message
        return None

    for shown in range(count, 0, -1):
        names = NAME_SEPARATOR.join(display_name(r.nickname) for r in recipients[:shown])
        if shown < count:
            names += f"{NAME_SEPARATOR}ほか{count - shown}名"
        message = build(names)
        if message is not None:
            return message

    first = recipients[0].nickname or ANONYMOUS_NAME
    others = f"{NAME_SEPARATOR}ほか{count - 1}名" if count > 1 else ""
    for cut in range(len(first) - 1, -1, -1):
        message = build(f"{first[:cut]}{ELLIPSIS}{HONORIFIC}{others}")
        if message is not None:
            return message
    return None
