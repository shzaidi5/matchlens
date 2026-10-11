import json
from pathlib import Path

import jsonschema

from matchlens.models import Insight, Profile
from matchlens.pipeline.personalize import KIND_LEVEL, Director, relevance
from matchlens.pipeline.render import to_webvtt

SCHEMA = json.loads((Path(__file__).parent.parent / "schemas" / "overlay_cue.schema.json").read_text())


def insight(kind="press_shift", importance=0.6, team="CAL", players=(), t=100.0):
    return Insight(id=f"{kind}-1", t=t, period=1, clock="01:40", minute="2'", kind=kind,
                   importance=importance, team=team, player_ids=list(players), facts={"x": 1})


def lower_thirds(cues):
    return [c for c in cues if c.slot == "lower_third"]


def test_same_match_two_different_shows(run):
    _, _, tracks = run
    analyst, fan = lower_thirds(tracks["analyst"]), lower_thirds(tracks["fan"])
    assert len(analyst) > len(fan) >= 5
    assert {c.lang for c in fan} == {"es"} and {c.lang for c in analyst} == {"en"}
    assert {c.kind for c in analyst} - {c.kind for c in fan}  # analyst-only detail exists


def test_fan_never_sees_specialist_detail_unless_it_is_their_player(run):
    _, insights, tracks = run
    by_id = {i.id: i for i in insights}
    for pid, profile_player in (("fan", None), ("focus", "NBR-10")):
        for cue in lower_thirds(tracks[pid]):
            ins = by_id[cue.insight_id]
            if KIND_LEVEL[ins.kind] == 2:
                assert profile_player in ins.player_ids


def test_favourites_raise_relevance_and_say_why():
    plain = relevance(insight(), Profile(id="p", mode="fan"))
    fav = relevance(insight(), Profile(id="p", mode="fan", favorite_team="CAL"))
    assert fav[0] > plain[0] and fav[1] == ["favorite_team"]
    player = relevance(insight("pass_milestone", 0.28, "CAL", ["CAL-10"]), Profile(id="p", mode="fan", favorite_player="CAL-10"))
    assert player and "favorite_player" in player[1]
    assert relevance(insight("pass_milestone", 0.28, "CAL", ["CAL-10"]), Profile(id="p", mode="fan")) is None
    focus = relevance(insight(), Profile(id="p", mode="analyst", focus_metric="pressing"))
    assert "focus:pressing" in focus[1]


def test_player_focus_follows_one_player(run):
    _, _, tracks = run
    follow = [c for c in tracks["focus"] if c.kind == "player_focus"]
    assert len(follow) > 20
    assert {c.payload["player"]["id"] for c in follow} == {"NBR-10"}
    assert all(b.t_in - a.t_in >= 6.0 for a, b in zip(follow, follow[1:]))
    assert not [c for c in tracks["analyst"] if c.kind == "player_focus"]


def test_director_paces_low_priority_and_lets_goals_cut_in():
    d = Director(Profile(id="p", mode="fan"))
    assert d.schedule(insight(t=100.0), 0.6) is not None
    assert d.schedule(insight("speed_alert", t=105.0), 0.47) is None  # busy, minor
    held = d.schedule(insight("momentum_swing", t=150.0), 0.6)  # busy, worth a short hold
    assert held is not None and held.t_in > 150.0
    goal = d.schedule(insight("goal", t=held.t_in + 1), 1.0)
    assert goal is not None and goal.t_in == held.t_in + 1
    assert d.suppressed == 1


def test_every_cue_matches_the_published_schema(run):
    _, _, tracks = run
    validator = jsonschema.Draft202012Validator(SCHEMA)
    n = 0
    for cues in tracks.values():
        for cue in cues:
            validator.validate(cue.to_dict())
            assert cue.t_out > cue.t_in
            n += 1
    assert n > 100


def test_cues_trace_back_to_insights_and_events(run):
    _, insights, tracks = run
    ids = {i.id for i in insights}
    for cue in tracks["analyst"]:
        assert cue.insight_id in ids


def test_webvtt_export_is_well_formed(run):
    _, _, tracks = run
    vtt = to_webvtt(tracks["fan"])
    blocks = [b for b in vtt.split("\n\n") if b.strip()]
    assert blocks[0] == "WEBVTT" and len(blocks) == len(tracks["fan"]) + 1
    cue_id, timing, payload = blocks[1].split("\n")
    assert " --> " in timing and timing[2] == ":" and json.loads(payload)["slot"]
