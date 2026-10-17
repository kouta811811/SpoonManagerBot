from __future__ import annotations

import pytest

from spoon_manager_bot.domain.commands import parse_command
from spoon_manager_bot.domain.thanks import TemplateSet, ThanksKind, ThanksTemplate
from spoon_manager_bot.thanks.commands import CommandResult, ThanksCommandHandler
from spoon_manager_bot.thanks.repository import DynamoDbThanksRepository, InMemoryThanksRepository
from tests.helpers import FakeDynamoDb

ALL = frozenset(ThanksKind)
Setup = tuple[ThanksCommandHandler, TemplateSet, InMemoryThanksRepository]


def run(
    handler: ThanksCommandHandler,
    message: str,
    *,
    is_dj: bool = True,
    enabled: frozenset[ThanksKind] = ALL,
) -> CommandResult | None:
    command = parse_command(message)
    assert command is not None
    return handler.handle(command, is_dj=is_dj, enabled=enabled)


def ok(handler: ThanksCommandHandler, message: str, **kwargs: object) -> CommandResult:
    result = run(handler, message, **kwargs)  # type: ignore[arg-type]
    assert result is not None
    return result


def template_text(templates: TemplateSet, kind: ThanksKind, amount: int) -> str:
    template = templates.select(kind, amount)
    assert template is not None
    return template.text


@pytest.fixture
def setup() -> Setup:
    templates = TemplateSet()
    repo = InMemoryThanksRepository()
    return ThanksCommandHandler(templates, repo), templates, repo


def test_set_without_min_uses_one(setup: Setup) -> None:
    handler, templates, repo = setup
    assert ok(handler, "!setheart {名前}ハートありがとう💕").reply == (
        "ハートのお礼を登録しました（1〜）"
    )
    assert templates.select(ThanksKind.HEART, 1) == ThanksTemplate(
        ThanksKind.HEART, 1, "{名前}ハートありがとう💕"
    )
    assert repo.items[(ThanksKind.HEART, 1)].text == "{名前}ハートありがとう💕"


def test_set_with_min_and_overwrite(setup: Setup) -> None:
    handler, templates, _ = setup
    assert ok(handler, "!setspoon 1000 🎉{名前}🎉").reply == (
        "スプーンのお礼を登録しました（1,000〜）"
    )
    assert ok(handler, "!setspoon 1000 新しい文").reply == (
        "スプーンのお礼を更新しました（1,000〜）"
    )
    assert template_text(templates, ThanksKind.SPOON, 1000) == "新しい文"


def test_number_followed_by_text_is_part_of_message(setup: Setup) -> None:
    handler, templates, _ = setup
    ok(handler, "!setspoon 100円分ありがとう")
    assert template_text(templates, ThanksKind.SPOON, 1) == "100円分ありがとう"


def test_non_dj_is_ignored_silently(setup: Setup) -> None:
    handler, templates, repo = setup
    assert run(handler, "!setheart 乗っ取り", is_dj=False) is None
    assert run(handler, "!msglist", is_dj=False) is None
    assert templates.is_empty(ThanksKind.HEART)
    assert not repo.items


def test_unrelated_command_is_not_handled(setup: Setup) -> None:
    handler, _, _ = setup
    assert run(handler, "!help") is None


@pytest.mark.parametrize(
    ("message", "result"),
    [
        ("!setheart", "empty_text"),
        ("!setheart 100", "empty_text"),
        ("!setheart 0 ありがとう", "invalid_min"),
        ("!setheart 1000001 ありがとう", "invalid_min"),
        ("!setheart " + "あ" * 151, "too_long"),
    ],
)
def test_set_validation(setup: Setup, message: str, result: str) -> None:
    handler, templates, repo = setup
    assert ok(handler, message).result == result
    assert templates.is_empty(ThanksKind.HEART)
    assert not repo.items


def test_max_five_tiers_but_overwrite_allowed(setup: Setup) -> None:
    handler, _, _ = setup
    for minimum in (1, 10, 100, 1000, 10000):
        assert ok(handler, f"!setspoon {minimum} ありがとう").result == "created"
    assert ok(handler, "!setspoon 50 ありがとう").result == "too_many_tiers"
    assert ok(handler, "!setspoon 100 上書き").result == "updated"


def test_delete(setup: Setup) -> None:
    handler, templates, repo = setup
    ok(handler, "!setspoon ありがとう")
    ok(handler, "!setspoon 1000 特別")
    assert ok(handler, "!delspoon 1000").reply == "スプーンのお礼を削除しました（1,000〜）"
    assert ok(handler, "!delspoon").result == "deleted"
    assert ok(handler, "!delspoon").result == "not_found"
    assert ok(handler, "!delspoon abc").result == "invalid_min"
    assert templates.is_empty(ThanksKind.SPOON)
    assert not repo.items


def test_list_shows_minimums_and_missing_permission(setup: Setup) -> None:
    handler, _, _ = setup
    ok(handler, "!setspoon ありがとう")
    ok(handler, "!setspoon 1000 特別")
    result = ok(handler, "!msglist", enabled=frozenset({ThanksKind.SPOON}))
    assert result.reply == "お礼文 ハート: 未登録（権限なし） ／ スプーン: 1, 1,000"


def test_storage_failure_keeps_memory_unchanged() -> None:
    templates = TemplateSet()
    repo = DynamoDbThanksRepository(FakeDynamoDb(fail=True), "table")
    handler = ThanksCommandHandler(templates, repo)
    assert ok(handler, "!setheart ありがとう").result == "error"
    assert templates.is_empty(ThanksKind.HEART)
