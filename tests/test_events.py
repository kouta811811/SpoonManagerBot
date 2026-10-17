"""公式ドキュメント（イベントストリーム）のサンプルでの解析。"""

from __future__ import annotations

import pytest

from spoon_manager_bot.domain.events import (
    ChatEvent,
    DonationEvent,
    EventValidationError,
    LikeEvent,
    PresenceEvent,
    StreamEndEvent,
    parse_event,
)

CHAT = (
    '{"user":{"id":"Ab1Cd2Ef3Gh4Ij5Kl6Mn7O","nickname":"スプーンリスナー"},'
    '"isDj":false,"message":"こんにちは","sentTime":"2026-09-02T05:47:26.114Z"}'
)
PRESENCE = (
    '{"user":{"id":"Pq8Rs9Tu0Vw1Xy2Za3Bc4D","nickname":"ラジオファン"},"type":"JOIN",'
    '"fanRank":1,"isManager":false,"favoriteTemperature":36.5,"time":"2026-09-02T05:47:32.169Z"}'
)
LIKE = (
    '{"user":{"id":"Pq8Rs9Tu0Vw1Xy2Za3Bc4D","nickname":"ラジオファン"},"type":"PAID",'
    '"totalAmount":30,"amount":10,"extraAmount":5,"combo":2,"time":"2026-09-02T05:47:41.044Z"}'
)
DONATION = (
    '{"user":{"id":"Ab1Cd2Ef3Gh4Ij5Kl6Mn7O","nickname":"スプーンリスナー"},"amount":300,'
    '"message":"応援しています","time":"2026-09-02T05:47:50.512Z"}'
)


def test_parses_official_samples() -> None:
    chat = parse_event("chat", CHAT)
    assert isinstance(chat, ChatEvent)
    assert chat.user.id == "Ab1Cd2Ef3Gh4Ij5Kl6Mn7O"
    assert chat.is_dj is False
    assert chat.message == "こんにちは"

    presence = parse_event("presence", PRESENCE)
    assert isinstance(presence, PresenceEvent)
    assert presence.fan_rank == 1
    assert presence.favorite_temperature == 36.5

    like = parse_event("like", LIKE)
    assert isinstance(like, LikeEvent)
    assert (like.total_amount, like.amount, like.extra_amount, like.combo) == (30, 10, 5, 2)

    donation = parse_event("donation", DONATION)
    assert isinstance(donation, DonationEvent)
    assert donation.amount == 300

    end = parse_event("end", '{"reason":"LIVE_ENDED"}')
    assert end == StreamEndEvent("LIVE_ENDED")


def test_accepts_nullable_fields_and_unknown_fields() -> None:
    free_like = parse_event(
        "like",
        '{"user":{"id":"u1","nickname":null},"type":"FREE","totalAmount":1,"amount":1,'
        '"extraAmount":0,"combo":null,"time":"2026-09-03T02:59:03Z","newField":true}',
    )
    assert isinstance(free_like, LikeEvent)
    assert free_like.user.nickname is None
    assert free_like.combo is None

    donation = parse_event(
        "donation",
        '{"user":{"id":"u1","nickname":null},"amount":10,"message":null,'
        '"time":"2026-09-02T05:47:50.5123456Z"}',
    )
    assert isinstance(donation, DonationEvent)
    assert donation.message is None


def test_chat_message_that_looks_like_json_stays_text() -> None:
    event = parse_event(
        "chat",
        '{"user":{"id":"u1","nickname":"a"},"isDj":false,"message":"{\\"a\\":1}",'
        '"sentTime":"2026-09-02T05:47:26.114Z"}',
    )
    assert isinstance(event, ChatEvent)
    assert event.message == '{"a":1}'


def test_unknown_event_is_ignored() -> None:
    assert parse_event("studio_notice", "{}") is None


@pytest.mark.parametrize(
    ("name", "data"),
    [
        (
            "like",
            '{"user":{"id":"u1"},"type":"PAID","amount":1,"extraAmount":0,'
            '"time":"2026-09-02T05:47:26Z"}',
        ),
        ("donation", '{"user":{"id":"u1"},"amount":"300","time":"2026-09-02T05:47:26Z"}'),
        ("chat", "not json"),
        ("chat", "[]"),
        ("donation", '{"user":{"id":""},"amount":1,"time":"2026-09-02T05:47:26Z"}'),
    ],
)
def test_rejects_invalid_payloads(name: str, data: str) -> None:
    with pytest.raises(EventValidationError):
        parse_event(name, data)


def test_validation_error_does_not_include_payload_values() -> None:
    with pytest.raises(EventValidationError) as excinfo:
        parse_event(
            "donation",
            '{"user":{"id":"secret-id","nickname":"ひみつ"},"amount":"x",'
            '"time":"2026-09-02T05:47:26Z"}',
        )
    assert "secret-id" not in str(excinfo.value)
    assert "ひみつ" not in str(excinfo.value)
