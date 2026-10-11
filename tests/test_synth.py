from matchlens.models import EVENT_TYPES, PITCH_X, PITCH_Y
from matchlens.pipeline.ingest import ordered
from matchlens.synth import generate_match


def test_same_seed_is_reproducible():
    assert generate_match(5)[1] == generate_match(5)[1]


def test_different_seeds_differ():
    assert generate_match(5)[1] != generate_match(6)[1]


def test_feed_is_valid_ordered_and_on_the_pitch(match):
    _, events, _ = match
    clean = list(ordered(events))
    assert len(clean) == len(events)
    assert {e["type"] for e in clean} <= EVENT_TYPES
    for e in clean:
        for key, limit in (("x", PITCH_X), ("end_x", PITCH_X), ("y", PITCH_Y), ("end_y", PITCH_Y)):
            if key in e:
                assert 0 <= e[key] <= limit


def test_feed_covers_the_required_event_families(match):
    _, events, _ = match
    kinds = {e["type"] for e in events}
    assert {"pass", "shot", "tackle", "possession_change", "pressure", "sprint", "substitution"} <= kinds


def test_numbers_look_like_football():
    """Averages over several matches stay inside plausible ranges."""
    passes, completed, shots, goals, n = 0, 0, 0, 0, 6
    for seed in range(n):
        _, events, truth = generate_match(seed)
        p = [e for e in events if e["type"] == "pass"]
        passes += len(p)
        completed += sum(e["outcome"] == "complete" for e in p)
        shots += sum(e["type"] == "shot" for e in events)
        goals += sum(truth["final_score"].values())
    assert 700 <= passes / n <= 1300
    assert 0.70 <= completed / passes <= 0.90
    assert 14 <= shots / n <= 36
    assert 1.0 <= goals / n <= 5.0


def test_metadata_is_marked_synthetic_and_hides_the_script(match):
    meta, events, truth = match
    assert meta["synthetic"] is True
    assert "tactic_phases" in truth
    assert "tactic" not in str(meta).lower()
    assert all("press" not in e for e in events)  # hidden tactic values never leak into the feed
    assert "pace" not in meta["teams"][0]["players"][0]
