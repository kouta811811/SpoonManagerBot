from __future__ import annotations

from spoon_manager_bot.domain.text import utf16_len
from spoon_manager_bot.domain.thanks import (
    Recipient,
    TemplateSet,
    ThanksKind,
    ThanksTemplate,
    compose,
    render,
)

SPOON = ThanksKind.SPOON


def make_set() -> TemplateSet:
    return TemplateSet(
        [
            ThanksTemplate(SPOON, 1, "{名前}、{数}スプーンありがとう"),
            ThanksTemplate(SPOON, 100, "{名前}！{数}スプーン本当にありがとう"),
            ThanksTemplate(SPOON, 1000, "🎉{名前}🎉"),
        ]
    )


def test_select_picks_largest_tier_not_above_amount() -> None:
    templates = make_set()

    def selected_min(amount: int) -> int | None:
        template = templates.select(SPOON, amount)
        return None if template is None else template.min_amount

    assert [selected_min(a) for a in (1, 99, 100, 5000)] == [1, 1, 100, 1000]
    assert templates.select(ThanksKind.HEART, 5) is None


def test_select_returns_none_below_lowest_tier() -> None:
    templates = TemplateSet([ThanksTemplate(SPOON, 100, "x")])
    assert templates.select(SPOON, 99) is None
    template = templates.select(SPOON, 100)
    assert template is not None
    assert not templates.is_special(template)


def test_special_is_any_tier_above_lowest() -> None:
    templates = make_set()
    assert not templates.is_special(ThanksTemplate(SPOON, 1, ""))
    assert templates.is_special(ThanksTemplate(SPOON, 100, ""))


def test_render_variables_and_unknown_braces() -> None:
    text = render("{名前}{数}/{累計} {未知}", names="Aさん", amount=1500, total=12000)
    assert text == "Aさん1,500/12,000 {未知}"


def test_compose_single_and_multiple_with_honorific() -> None:
    one = compose("{名前}、{数}個ありがとう", [Recipient("A", 3)], total=3)
    assert one == "Aさん、3個ありがとう"
    many = compose("{名前}、{数}個ありがとう", [Recipient("A", 10), Recipient(None, 5)], total=15)
    assert many == "Aさん、リスナーさん、15個ありがとう"


def test_compose_collapses_overflow_into_others() -> None:
    recipients = [Recipient(f"リスナー{i:02d}番のとても長いニックネーム", 1) for i in range(20)]
    message = compose("{名前}ありがとう", recipients, total=20)
    assert message is not None
    assert utf16_len(message) <= 200
    assert "ほか" in message
    assert message.endswith("名ありがとう")


def test_compose_truncates_single_long_name() -> None:
    message = compose("{名前}ありがとう" + "!" * 140, [Recipient("あ" * 100, 1)], total=1)
    assert message is not None
    assert utf16_len(message) <= 200
    assert "…さん" in message


def test_compose_returns_none_when_template_alone_is_too_long() -> None:
    assert compose("あ" * 201, [Recipient("A", 1)], total=1) is None
