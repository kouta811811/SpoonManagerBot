from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spoon_manager_bot.domain.thanks import ThanksKind, ThanksTemplate
from spoon_manager_bot.thanks.repository import (
    DynamoDbThanksRepository,
    ThanksRepositoryError,
    sort_key,
)
from tests.helpers import FakeDynamoDb


def make_repo(db: FakeDynamoDb) -> DynamoDbThanksRepository:
    return DynamoDbThanksRepository(
        db, "spoon-dev-state", clock=lambda: datetime(2026, 10, 17, tzinfo=UTC)
    )


def test_sort_key_format() -> None:
    assert sort_key(ThanksKind.HEART, 1) == "THANKS#HEART#0000001"
    assert sort_key(ThanksKind.SPOON, 1000) == "THANKS#SPOON#0001000"


def test_put_load_delete_roundtrip_with_paging() -> None:
    db = FakeDynamoDb(page_size=2)
    repo = make_repo(db)
    templates = [
        ThanksTemplate(ThanksKind.HEART, 1, "ハート"),
        ThanksTemplate(ThanksKind.SPOON, 1, "スプーン"),
        ThanksTemplate(ThanksKind.SPOON, 1000, "特別"),
    ]
    for template in templates:
        repo.put(template)
    # THANKS# 以外の項目は読まない
    db.items[("DJ#SINGLE", "LEASE")] = {"PK": {"S": "DJ#SINGLE"}, "SK": {"S": "LEASE"}}

    assert sorted(repo.load_all(), key=lambda t: (t.kind, t.min_amount)) == sorted(
        templates, key=lambda t: (t.kind, t.min_amount)
    )
    item = db.items[("DJ#SINGLE", "THANKS#SPOON#0001000")]
    assert item["updated_at"]["S"] == "2026-10-17T00:00:00+00:00"

    repo.delete(ThanksKind.SPOON, 1000)
    assert len(repo.load_all()) == 2
    repo.delete_all()
    assert repo.load_all() == []
    assert ("DJ#SINGLE", "LEASE") in db.items


def test_corrupted_items_are_skipped() -> None:
    db = FakeDynamoDb()
    db.items[("DJ#SINGLE", "THANKS#HEART#0000001")] = {
        "PK": {"S": "DJ#SINGLE"},
        "SK": {"S": "THANKS#HEART#0000001"},
        "kind": {"S": "unknown"},
    }
    assert make_repo(db).load_all() == []


def test_errors_do_not_leak_details() -> None:
    repo = make_repo(FakeDynamoDb(fail=True))
    with pytest.raises(ThanksRepositoryError) as excinfo:
        repo.load_all()
    assert str(excinfo.value) == "dynamodb query failed"
