import pytest

from matchlens.pipeline.ingest import EventError, ordered, validate_event

BASE = {"id": 1, "t": 10.0, "period": 1, "clock": "00:10", "minute": "1'", "type": "pass",
        "team": "NBR", "player_id": "NBR-4", "x": 50.0, "y": 30.0, "end_x": 60.0, "end_y": 31.0,
        "outcome": "complete", "receiver_id": "NBR-8"}


def test_valid_event_passes_through():
    assert validate_event(BASE)["type"] == "pass"


@pytest.mark.parametrize("change", [{"type": "teleport"}, {"t": -1}, {"period": 3}, {"x": 400}, {"x": "left"}])
def test_bad_events_are_rejected(change):
    with pytest.raises(EventError):
        validate_event({**BASE, **change})


def test_missing_field_is_rejected():
    with pytest.raises(EventError):
        validate_event({k: v for k, v in BASE.items() if k != "clock"})


def test_on_ball_event_needs_a_player():
    with pytest.raises(EventError):
        validate_event({k: v for k, v in BASE.items() if k != "player_id"})


def test_small_overshoot_is_clamped_not_rejected():
    assert validate_event({**BASE, "end_x": 106.2})["end_x"] == 105.0


def test_out_of_order_delivery_is_rejected():
    with pytest.raises(EventError):
        list(ordered([BASE, {**BASE, "id": 2, "t": 5.0}]))
