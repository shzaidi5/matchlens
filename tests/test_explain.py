import pytest

from matchlens.i18n import EN, ES
from matchlens.grounding import numbers_in, ungrounded_numbers
from matchlens.models import Profile
from matchlens.pipeline.explain import REASON_MEANINGS, TemplateNarrator

MODES = ("analyst", "fan")
LANGS = ("en", "es")


def test_languages_have_identical_keys():
    assert set(EN) == set(ES)


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", MODES)
def test_every_insight_gets_complete_copy(run, lang, mode):
    _, insights, _ = run
    narrator = TemplateNarrator()
    for team in (None, "NBR", "CAL"):
        profile = Profile(id="x", mode=mode, lang=lang, favorite_team=team)
        for insight in insights:
            n = narrator.narrate(insight, profile)
            text = n.headline + " " + n.body
            assert n.headline and n.body, insight.id
            assert "{" not in text and "None" not in text, text


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("mode", MODES)
def test_template_copy_only_quotes_computed_numbers(run, lang, mode):
    """The guard used on model output must also pass on our own templates."""
    _, insights, _ = run
    narrator = TemplateNarrator()
    for insight in insights:
        n = narrator.narrate(insight, Profile(id="x", mode=mode, lang=lang))
        allowed = numbers_in(insight.facts) | numbers_in(insight.minute)
        assert ungrounded_numbers(n.headline + " " + n.body, allowed) == [], (insight.id, n.body)


def test_analyst_and_fan_read_differently(run):
    _, insights, _ = run
    shift = next(i for i in insights if i.kind == "press_shift")
    narrator = TemplateNarrator()
    analyst = narrator.narrate(shift, Profile(id="a", mode="analyst"))
    fan = narrator.narrate(shift, Profile(id="f", mode="fan"))
    assert "PPDA" in analyst.body and "%" in analyst.body
    assert "PPDA" not in fan.body and not any(ch.isdigit() for ch in fan.body)
    assert analyst.stats and not fan.stats


def test_supporter_stance_changes_the_headline(run):
    _, insights, _ = run
    goal = next(i for i in insights if i.kind == "goal" and i.team == "CAL")
    narrator = TemplateNarrator()
    cheer = narrator.narrate(goal, Profile(id="f", mode="fan", favorite_team="CAL")).headline
    groan = narrator.narrate(goal, Profile(id="f", mode="fan", favorite_team="NBR")).headline
    assert cheer != groan and cheer.startswith("YES!")


def test_every_reason_code_has_a_meaning_and_copy(run):
    _, insights, _ = run
    recap_phases = {"half_time", "full_time"}
    for insight in insights:
        for code in insight.reasons:
            if code in recap_phases:
                continue
            assert code in REASON_MEANINGS, code
            if code not in ("match_record", "first_to_mark"):  # stated in the body itself
                assert f"reason.{insight.kind}.{code}.analyst" in EN, (insight.kind, code)


def test_grounding_guard_details():
    allowed = numbers_in({"xg": 0.26, "minute": "45+2'", "nested": [{"speed": 101.4}]})
    assert {0.26, 45.0, 2.0, 101.4} <= allowed
    assert ungrounded_numbers("0,26 xG at 101 km/h in the 45th", allowed) == []
    assert ungrounded_numbers("He ran 12 km", allowed) == ["12"]
