"""お礼の送信判断（15秒に1通・まとめ送信・優先順・期限）。"""

from __future__ import annotations

from spoon_manager_bot.domain.thanks import TemplateSet, ThanksKind, ThanksTemplate
from spoon_manager_bot.thanks.buffer import ThanksBuffer
from spoon_manager_bot.thanks.service import ThanksService
from tests.helpers import donation, like

HEART, SPOON = ThanksKind.HEART, ThanksKind.SPOON


def service(*templates: ThanksTemplate, **kwargs: object) -> ThanksService:
    return ThanksService(TemplateSet(templates), **kwargs)  # type: ignore[arg-type]


def test_nothing_is_sent_without_templates() -> None:
    svc = service()
    svc.on_like(like("a", "A", 5), 0)
    svc.on_donation(donation("a", "A", 100), 0)
    assert svc.due(0) is None
    assert svc.pending_count() == 0
    # 集計（{累計}）は続ける
    assert svc.totals == {HEART: 5, SPOON: 100}


def test_free_and_paid_hearts_are_merged_per_user() -> None:
    svc = service(ThanksTemplate(HEART, 1, "{名前}ハート{数}個ありがとう"))
    svc.on_like(like("a", "A", 3, "FREE"), 0)
    svc.on_like(like("b", "B", 10), 1)
    svc.on_like(like("a", "A", 2, "PAID"), 2)
    message = svc.due(2)
    assert message is not None
    # 量の多い順（B=10, A=5）
    assert message.text == "Bさん、Aさんハート15個ありがとう"
    assert message.recipient_count == 2


def test_one_message_per_fifteen_seconds() -> None:
    svc = service(ThanksTemplate(HEART, 1, "{名前}ありがとう"))
    svc.on_like(like("a", "A", 1), 0)
    assert svc.due(0) is not None
    svc.on_like(like("b", "B", 1), 1)
    assert svc.due(1) is None
    assert svc.due(14.9) is None
    message = svc.due(15)
    assert message is not None
    assert message.text == "Bさんありがとう"


def test_spoon_is_sent_before_heart_and_heart_is_kept() -> None:
    svc = service(
        ThanksTemplate(HEART, 1, "{名前}ハートありがとう"),
        ThanksTemplate(SPOON, 1, "{名前}{数}スプーンありがとう"),
    )
    svc.on_like(like("a", "A", 3), 0)
    svc.on_like(like("b", "B", 10), 4)
    svc.on_like(like("a", "A", 2), 9)
    svc.on_donation(donation("c", "C", 100), 12)
    first = svc.due(15)
    assert first is not None
    assert first.text == "Cさん100スプーンありがとう"
    second = svc.due(30)
    assert second is not None
    assert second.text == "Bさん、Aさんハートありがとう"
    assert svc.due(45) is None


def test_special_tier_is_sent_alone_first_largest_amount_first() -> None:
    svc = service(
        ThanksTemplate(SPOON, 1, "{名前}ありがとう"),
        ThanksTemplate(SPOON, 1000, "🎉{名前}{数}スプーン🎉"),
        ThanksTemplate(HEART, 1, "{名前}ハート"),
        ThanksTemplate(HEART, 100, "{名前}ハート{数}個も！"),
    )
    svc.on_donation(donation("a", "A", 10), 0)
    svc.on_like(like("h", "H", 500), 0)
    svc.on_donation(donation("b", "B", 1000), 1)
    svc.on_donation(donation("c", "C", 3000), 2)
    texts = [m.text for t in (15, 30, 45, 60) if (m := svc.due(t)) is not None]
    assert texts == [
        "🎉Cさん3,000スプーン🎉",
        "🎉Bさん1,000スプーン🎉",
        "Hさんハート500個も！",
        "Aさんありがとう",
    ]


def test_below_lowest_tier_waits_and_accumulates() -> None:
    svc = service(ThanksTemplate(HEART, 10, "{名前}{数}個"))
    svc.on_like(like("a", "A", 4), 0)
    assert svc.due(0) is None
    svc.on_like(like("a", "A", 6), 5)
    message = svc.due(5)
    assert message is not None
    assert message.text == "Aさん10個"


def test_expired_after_sixty_seconds_since_last_event() -> None:
    svc = service(ThanksTemplate(SPOON, 1, "{名前}"), ThanksTemplate(HEART, 1, "{名前}"))
    svc.on_donation(donation("s", "S", 1), 0)
    svc.on_like(like("a", "A", 1), 0)
    assert svc.due(0) is not None  # Sさん
    assert svc.due(61) is None  # Aさんは60秒を超えたので捨てる


def test_special_queue_is_capped_dropping_oldest() -> None:
    buffer = ThanksBuffer(max_special=2)
    svc = service(
        ThanksTemplate(SPOON, 1, "{名前}"),
        ThanksTemplate(SPOON, 100, "特別{名前}"),
        buffer=buffer,
    )
    svc.on_donation(donation("a", "A", 900), 0)
    svc.on_donation(donation("b", "B", 100), 1)
    svc.on_donation(donation("c", "C", 100), 2)
    texts = [m.text for t in (15, 30, 45) if (m := svc.due(t)) is not None]
    assert texts == ["特別Bさん", "特別Cさん"]


def test_total_variable_is_whole_live_total() -> None:
    svc = service(ThanksTemplate(SPOON, 1, "{名前}{数}（累計{累計}）"))
    svc.on_donation(donation("a", "A", 100), 0)
    svc.on_donation(donation("b", "B", 50), 1)
    message = svc.due(1)
    assert message is not None
    assert message.text == "Aさん、Bさん150（累計150）"


def test_disabled_kind_is_not_sent() -> None:
    svc = service(
        ThanksTemplate(HEART, 1, "{名前}"),
        ThanksTemplate(SPOON, 1, "{名前}"),
        enabled=frozenset({HEART}),
    )
    svc.on_donation(donation("a", "A", 100), 0)
    assert svc.due(0) is None
    assert svc.totals[SPOON] == 100


def test_empty_slot_does_not_consume_interval() -> None:
    svc = service(ThanksTemplate(HEART, 1, "{名前}"))
    assert svc.due(0) is None
    svc.on_like(like("a", "A", 1), 3)
    assert svc.due(3) is not None


def test_reset_session_clears_state() -> None:
    svc = service(ThanksTemplate(HEART, 1, "{名前}"))
    svc.on_like(like("a", "A", 1), 0)
    svc.reset_session()
    assert svc.pending_count() == 0
    assert svc.totals[HEART] == 0
