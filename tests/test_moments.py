from matchlens.pipeline.moments import _z_two_proportions
from matchlens.pipeline.orchestrator import run_match
from matchlens.synth import generate_match


def minute_of(insight):
    return int(insight.minute.split("'")[0].split("+")[0])


def test_every_goal_becomes_an_insight(run, match):
    _, insights, _ = run
    goals = [i for i in insights if i.kind == "goal"]
    assert len(goals) == sum(match[2]["final_score"].values())
    assert all(i.importance == 1.0 for i in goals)


def test_insights_carry_evidence(run):
    _, insights, _ = run
    for i in insights:
        assert 0 <= i.importance <= 1
        assert i.facts, i.id
        assert i.kind == "recap" or i.source_event_ids or i.kind in ("press_shift", "momentum_swing", "rhythm_shift", "distance_alert")


def test_press_shift_is_found_near_the_scripted_switch(run, match):
    """Ground truth: the away side switch to a high press at 60'."""
    _, insights, truth = run[0], run[1], match[2]
    switch = next(p for p in truth["tactic_phases"] if p["label"] == "high_press")
    ups = [i for i in insights if i.kind == "press_shift" and i.team == switch["team"] and i.facts["direction"] == "up"]
    assert any(switch["from_minute"] <= minute_of(i) <= switch["from_minute"] + 12 for i in ups)


def test_press_shift_explains_itself(run):
    _, insights, _ = run
    shift = next(i for i in insights if i.kind == "press_shift" and i.facts["direction"] == "up" and minute_of(i) >= 60)
    assert shift.facts["press_now_pct"] > shift.facts["press_before_pct"]
    assert shift.facts["evidence_score"] >= shift.facts["evidence_threshold"]
    assert "coincides_with_sub" in shift.reasons and "chasing_game" in shift.reasons


def test_goal_after_the_press_is_linked_to_it(run):
    _, insights, _ = run
    late_goals = [i for i in insights if i.kind == "goal" and i.team == "CAL"]
    assert "press_paying_off" in late_goals[0].reasons
    assert "super_sub" in late_goals[0].reasons


def test_recaps_at_half_time_and_full_time(run):
    _, insights, _ = run
    recaps = [i for i in insights if i.kind == "recap"]
    assert [r.facts["phase"] for r in recaps] == ["half_time", "full_time"]
    assert recaps[1].facts["comeback"] is True
    assert recaps[1].facts["turning_point"]["kind"] == "press_shift"


def test_no_press_alarm_when_nothing_changes():
    """A match where both teams keep one tactic all game should stay quiet."""
    from matchlens.synth.generator import Tactic

    flat = {"home": [(0, Tactic(0.4, 0.45, 0.02, 0.5), "flat")], "away": [(0, Tactic(0.4, 0.45, 0.02, 0.5), "flat")]}
    alarms = 0
    for seed in range(2000, 2010):
        meta, events, _ = generate_match(seed, script=flat, subs=[])
        _, insights, _ = run_match(meta, events, [])
        alarms += sum(i.kind == "press_shift" for i in insights)
    assert alarms <= 2  # measured rate is about 1 per 8 quiet matches; see scripts/eval_detectors.py


def test_z_score_sign_and_zero():
    assert _z_two_proportions(30, 50, 15, 50) > 0
    assert _z_two_proportions(15, 50, 30, 50) < 0
    assert _z_two_proportions(1, 0, 1, 10) == 0.0
