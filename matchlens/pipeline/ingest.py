"""Stage 1 - Ingest: validate and order the raw event feed.

The feed is JSON lines. In production the same generator shape maps onto an
Azure Event Hubs consumer; here `replay` paces a recorded match at any speed.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Iterator

from ..models import EVENT_TYPES, PITCH_X, PITCH_Y

REQUIRED = ("id", "t", "period", "clock", "minute", "type")
ON_BALL = {"pass", "carry", "shot", "tackle", "interception", "pressure", "foul", "goal"}


class EventError(ValueError):
    """Raised when an event cannot be trusted enough to interpret."""


def validate_event(ev: dict[str, Any]) -> dict[str, Any]:
    """Return a clean copy of `ev` or raise EventError.

    Coordinates slightly outside the pitch are clamped rather than rejected:
    real feeds overshoot the touchline all the time.
    """
    if not isinstance(ev, dict):
        raise EventError("event is not an object")
    for key in REQUIRED:
        if key not in ev:
            raise EventError(f"event {ev.get('id', '?')} missing '{key}'")
    if ev["type"] not in EVENT_TYPES:
        raise EventError(f"event {ev['id']} has unknown type '{ev['type']}'")
    if not isinstance(ev["t"], (int, float)) or ev["t"] < 0:
        raise EventError(f"event {ev['id']} has invalid time {ev['t']!r}")
    if ev["period"] not in (1, 2):
        raise EventError(f"event {ev['id']} has invalid period {ev['period']!r}")
    if ev["type"] in ON_BALL and ("team" not in ev or "player_id" not in ev):
        raise EventError(f"event {ev['id']} ({ev['type']}) needs team and player_id")
    out = dict(ev)
    for key, limit in (("x", PITCH_X), ("end_x", PITCH_X), ("y", PITCH_Y), ("end_y", PITCH_Y)):
        if key in out:
            if not isinstance(out[key], (int, float)):
                raise EventError(f"event {ev['id']} has non-numeric {key}")
            if out[key] < -5 or out[key] > limit + 5:
                raise EventError(f"event {ev['id']} has {key}={out[key]} far outside the pitch")
            out[key] = max(0.0, min(limit, float(out[key])))
    return out


def ordered(events: Iterable[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    """Validate a feed and reject out-of-order delivery."""
    last_t = -1.0
    for raw in events:
        ev = validate_event(raw)
        if ev["t"] < last_t:
            raise EventError(f"event {ev['id']} arrived out of order ({ev['t']} < {last_t})")
        last_t = ev["t"]
        yield ev


def load_events(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def load_match(data_dir: str | Path, match_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    base = Path(data_dir)
    meta = json.loads((base / f"{match_id}.meta.json").read_text(encoding="utf-8"))
    return meta, load_events(base / f"{match_id}.events.jsonl")


async def replay(
    events: list[dict[str, Any]], speed: float = 1.0, start: float = 0.0, tick_s: float = 0.25
) -> AsyncIterator[tuple[float, list[dict[str, Any]]]]:
    """Yield (match_time, events) batches paced at `speed` x real time.

    Every tick is yielded, even an empty one, so a consumer can keep its clock
    moving between events.
    """
    pending = [e for e in events if e["t"] >= start]
    if not pending:
        return
    now, end, i = start, pending[-1]["t"], 0
    while i < len(pending):
        now = min(end, now + tick_s * speed)
        batch = []
        while i < len(pending) and pending[i]["t"] <= now:
            batch.append(pending[i])
            i += 1
        yield now, batch
        await asyncio.sleep(tick_s)
