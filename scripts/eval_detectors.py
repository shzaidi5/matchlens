"""Score the press-shift detector against the simulator's hidden tactic script.

    python scripts/eval_detectors.py [--matches 30] [--h 5.0]

Two sets of synthetic matches the detector has never been tuned on by seed:
  scripted  the away side switch to a high press at 60' (ground truth label)
  flat      both sides keep one tactic all match (any alarm is a false alarm)
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from matchlens.pipeline.moments import Thresholds  # noqa: E402
from matchlens.pipeline.orchestrator import Pipeline  # noqa: E402
from matchlens.synth import generate_match  # noqa: E402
from matchlens.synth.generator import Tactic  # noqa: E402

FLAT = {
    "home": [(0, Tactic(0.4, 0.45, 0.02, 0.5), "flat")],
    "away": [(0, Tactic(0.4, 0.45, 0.02, 0.5), "flat")],
}


def minute(insight) -> int:
    return int(insight.minute.split("'")[0].split("+")[0])


def press_shifts(meta, events, thresholds):
    pipeline = Pipeline(meta, [], thresholds=thresholds)
    found = []
    for ev in events:
        found += [i for i in pipeline.process(ev, render=False).insights if i.kind == "press_shift"]
    return found


def evaluate(matches: int, thresholds: Thresholds, within: int = 12) -> dict:
    hits, latency, false_alarms = 0, [], 0
    for seed in range(1000, 1000 + matches):
        meta, events, truth = generate_match(seed)
        switch = next(p for p in truth["tactic_phases"] if p["label"] == "high_press")
        ups = [
            minute(i) - switch["from_minute"]
            for i in press_shifts(meta, events, thresholds)
            if i.team == switch["team"] and i.facts["direction"] == "up"
            and switch["from_minute"] <= minute(i) <= switch["from_minute"] + within
        ]
        if ups:
            hits += 1
            latency.append(min(ups))
    for seed in range(2000, 2000 + matches):
        meta, events, _ = generate_match(seed, script=FLAT, subs=[])
        false_alarms += len(press_shifts(meta, events, thresholds))
    return {
        "matches": matches,
        "detected": hits,
        "median_latency_min": statistics.median(latency) if latency else None,
        "false_alarms": false_alarms,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--matches", type=int, default=30)
    ap.add_argument("--h", type=float, default=None, help="CUSUM threshold (default: shipped value)")
    args = ap.parse_args()
    th = Thresholds() if args.h is None else Thresholds(press_cusum_h=args.h)
    r = evaluate(args.matches, th)
    print(f"CUSUM threshold h={th.press_cusum_h}, ratio={th.press_ratio}")
    print(f"scripted 60' press switch detected within 12 min: {r['detected']}/{r['matches']} matches"
          f" (median delay {r['median_latency_min']} min)")
    print(f"false alarms in {r['matches']} matches with no tactical change: {r['false_alarms']}")
