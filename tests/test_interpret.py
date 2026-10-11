import pytest

from matchlens.pipeline.interpret import MatchState, chaos_index, pass_difficulty


@pytest.fixture(scope="module")
def state(match):
    meta, events, _ = match
    s = MatchState(meta)
    for e in events:
        s.apply(e)
    return s


def test_score_matches_ground_truth(state, match):
    assert state.score == match[2]["final_score"]
    assert state.status == "full_time"


def test_possession_and_threat_shares_sum_to_one(state):
    w = state.window()
    assert sum(w[t]["possession_pct"] for t in state.teams) == pytest.approx(1.0)
    assert sum(w[t]["threat_share"] for t in state.teams) == pytest.approx(1.0)


def test_counters_match_the_raw_feed(state, match):
    _, events, _ = match
    for tid in state.teams:
        passes = [e for e in events if e["type"] == "pass" and e["team"] == tid]
        assert state.counters[tid]["passes"] == len(passes)
        assert state.counters[tid]["passes_completed"] == sum(e["outcome"] == "complete" for e in passes)
        assert state.counters[tid]["xg"] == pytest.approx(
            sum(e["xg"] for e in events if e["type"] == "shot" and e["team"] == tid)
        )


def test_windows_add_up(state):
    """First half + second half window equals the whole match."""
    end = state.t
    half = state.window(end / 2, end=end / 2)
    rest = state.window(end - half["match"]["end_t"])
    for tid in state.teams:
        assert half[tid]["passes"] + rest[tid]["passes"] == state.counters[tid]["passes"]


def test_rolling_window_is_smaller_than_the_match(state):
    assert state.window(600)["NBR"]["passes"] < state.window()["NBR"]["passes"]


def test_pass_difficulty_orders_passes_sensibly():
    easy = pass_difficulty(8, 0, False, 30, 34)
    long_forward = pass_difficulty(40, 30, False, 60, 34)
    pressured_into_box = pass_difficulty(40, 30, True, 95, 34)
    assert easy < long_forward < pressured_into_box
    assert 1 <= easy and pressured_into_box <= 99


def test_chaos_index_is_bounded_and_monotonic():
    assert chaos_index(0.5, 0.95) == 0
    assert chaos_index(10, 0.4) == 100
    assert chaos_index(1.5, 0.85) < chaos_index(2.8, 0.75)


def test_player_card_reports_what_happened(state, match):
    _, events, _ = match
    card = state.player_card("CAL-20")
    assert card["goals"] == sum(1 for e in events if e["type"] == "goal" and e["player_id"] == "CAL-20")
    assert card["passes_completed"] <= card["passes"]
