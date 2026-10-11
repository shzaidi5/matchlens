"""Stage 2 - Interpret: turn events into football state and statistics.

`MatchState` keeps cumulative counters per team plus a snapshot of those
counters every 30 match-seconds. Any rolling window (last 6, 8, 10 minutes, or
"the 20 minutes before that") is then just the difference of two snapshots, so
every number an explanation quotes can be reproduced from the event feed.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from typing import Any

from ..models import BOX_X, BOX_Y_MAX, BOX_Y_MIN, FINAL_THIRD_X, PITCH_X, PITCH_Y

SNAPSHOT_EVERY_S = 30.0
MOMENTUM_WINDOW_S = 480.0
CHAOS_WINDOW_S = 600.0
OPEN_PLAY_TURNOVERS = {"tackle", "interception", "loose_ball", "block"}

TEAM_COUNTERS = (
    "passes",
    "passes_completed",
    "carries",
    "shots",
    "shots_on_target",
    "goals",
    "xg",
    "tackles",
    "tackles_won",
    "interceptions",
    "fouls",
    "pressures",
    "possession_s",
    "threat",
    "final_third_entries",
    "box_entries",
    "turnovers",
    "recoveries",
    "high_recoveries",
    "recovery_x",
    "def_actions_high",
    "passes_own60",
    "sequences",
    "sequence_passes",
)


def pass_difficulty(
    distance: float, forward: float, under_pressure: bool, end_x_own: float, end_y: float
) -> int:
    """Transparent 1-99 difficulty rating for a pass.

    Longer, more forward, pressured passes into dangerous areas score higher.
    The weights are deliberately simple so an analyst can argue with them.
    """
    score = 16.0
    score += min(distance, 45.0) / 45.0 * 30.0
    score += max(0.0, min(forward, 30.0)) / 30.0 * 15.0
    if under_pressure:
        score += 20.0
    if end_x_own >= BOX_X and BOX_Y_MIN <= end_y <= BOX_Y_MAX:
        score += 17.0
    elif end_x_own >= FINAL_THIRD_X:
        score += 10.0
    return int(max(1, min(99, round(score))))


def _ratio(a: float, b: float) -> float | None:
    return a / b if b else None


class MatchState:
    def __init__(self, meta: dict[str, Any]) -> None:
        self.meta = meta
        self.teams: dict[str, dict[str, Any]] = {t["id"]: t for t in meta["teams"]}
        self.home_id = next(t["id"] for t in meta["teams"] if t["side"] == "home")
        self.away_id = next(t["id"] for t in meta["teams"] if t["side"] == "away")
        self.players: dict[str, dict[str, Any]] = {}
        for team in meta["teams"]:
            for p in team["players"] + team.get("bench", []):
                self.players[p["id"]] = p
        self.counters: dict[str, dict[str, float]] = {
            tid: {k: 0.0 for k in TEAM_COUNTERS} for tid in self.teams
        }
        self.player_stats: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self.score = {tid: 0 for tid in self.teams}
        self.t = 0.0
        self.period = 1
        self.clock = "00:00"
        self.minute = "1'"
        self.status = "pre"  # pre | live | half_time | full_time
        self.ball = (PITCH_X / 2, PITCH_Y / 2)
        self.possession: str | None = None
        self.sequence: dict[str, Any] | None = None
        self.substitutions: list[dict[str, Any]] = []
        self.goals: list[dict[str, Any]] = []
        self.last_shot: dict[str, Any] | None = None
        self.match_top_speed = 0.0
        self.timeline: list[dict[str, Any]] = []
        self._last_t: float | None = None
        self._snap_t: list[float] = [0.0]
        self._snaps: list[dict[str, dict[str, float]]] = [self._copy_counters()]
        self._next_snap = SNAPSHOT_EVERY_S
        self._next_timeline = 60.0

    # ----------------------------------------------------------------- helpers
    def other(self, team_id: str) -> str:
        return self.away_id if team_id == self.home_id else self.home_id

    def own_x(self, team_id: str, x: float) -> float:
        """x in the team's attacking frame (0 = own goal line)."""
        return x if team_id == self.home_id else PITCH_X - x

    def player_name(self, player_id: str | None) -> str:
        return self.players.get(player_id or "", {}).get("name", "Unknown")

    def team_name(self, team_id: str | None, short: bool = True) -> str:
        team = self.teams.get(team_id or "", {})
        return team.get("short" if short else "name", "Unknown")

    def clock_seconds(self) -> float:
        mm, ss = self.clock.split(":")
        return int(mm) * 60 + int(ss)

    def _copy_counters(self) -> dict[str, dict[str, float]]:
        return {tid: dict(c) for tid, c in self.counters.items()}

    # ------------------------------------------------------------------ update
    def apply(self, ev: dict[str, Any]) -> dict[str, Any]:
        """Fold one validated event into the state. Returns the event enriched
        with a `derived` block (pass distance, difficulty, zones...)."""
        etype = ev["type"]
        derived: dict[str, Any] = {}

        # Snapshots are taken on the way *past* each boundary, before this event.
        while ev["t"] >= self._next_snap:
            self._snap_t.append(self._next_snap)
            self._snaps.append(self._copy_counters())
            self._next_snap += SNAPSHOT_EVERY_S

        if self._last_t is not None and self.possession and etype != "period_start":
            dt = ev["t"] - self._last_t
            if 0 < dt <= 20:  # longer gaps are dead ball
                self.counters[self.possession]["possession_s"] += dt
        self._last_t = ev["t"]
        self.t, self.period, self.clock, self.minute = ev["t"], ev["period"], ev["clock"], ev["minute"]

        team = ev.get("team")
        pid = ev.get("player_id")
        c = self.counters.get(team) if team else None
        ps = self.player_stats[pid] if pid else None
        if "x" in ev and etype in ("pass", "carry", "shot", "tackle", "interception", "possession_change"):
            self.ball = (ev["x"], ev["y"])

        if etype == "match_start":
            self.status = "live"
        elif etype == "period_start":
            self.status = "live"
            self.possession = None
        elif etype == "period_end":
            self.status = "half_time" if ev["period"] == 1 else "full_time"
            self._close_sequence()
        elif etype == "match_end":
            self.status = "full_time"
        elif etype == "pass":
            derived = self._on_pass(ev, c, ps)
        elif etype == "carry":
            c["carries"] += 1
            ps["touches"] += 1
            before, after = self.own_x(team, ev["x"]), self.own_x(team, ev["end_x"])
            if before < FINAL_THIRD_X <= after:
                c["final_third_entries"] += 1
                c["threat"] += 1.0
            self.ball = (ev["end_x"], ev["end_y"])
        elif etype == "shot":
            c["shots"] += 1
            c["xg"] += ev["xg"]
            c["threat"] += 3.0 + 12.0 * ev["xg"]
            ps["touches"] += 1
            ps["shots"] += 1
            ps["xg"] += ev["xg"]
            if ev["outcome"] in ("goal", "saved"):
                c["shots_on_target"] += 1
            self.last_shot = ev
        elif etype == "goal":
            c["goals"] += 1
            ps["goals"] += 1
            if ev.get("assist_id"):
                self.player_stats[ev["assist_id"]]["assists"] += 1
            self.score = {tid: int(v) for tid, v in ev["score"].items()}
            self.goals.append(
                {"t": ev["t"], "minute": ev["minute"], "team": team, "player_id": pid, "score": dict(self.score)}
            )
        elif etype == "pressure":
            c["pressures"] += 1
            ps["pressures"] += 1
        elif etype in ("tackle", "interception", "foul"):
            if etype == "tackle":
                c["tackles"] += 1
                ps["touches"] += 1
                if ev["outcome"] == "won":
                    c["tackles_won"] += 1
                    ps["tackles_won"] += 1
            elif etype == "interception":
                c["interceptions"] += 1
                ps["interceptions"] += 1
                ps["touches"] += 1
            else:
                c["fouls"] += 1
            # "High" = in the opponent's defensive 60% (classic PPDA zone).
            if self.own_x(team, ev["x"]) >= PITCH_X * 0.4:
                c["def_actions_high"] += 1
        elif etype == "possession_change":
            self._on_possession_change(ev)
        elif etype == "sprint":
            ps["sprints"] += 1
            ps["top_speed"] = max(ps["top_speed"], ev["top_speed_kmh"])
            derived["match_record"] = ev["top_speed_kmh"] > self.match_top_speed
            self.match_top_speed = max(self.match_top_speed, ev["top_speed_kmh"])
        elif etype == "physical_update":
            for row in ev["players"]:
                st = self.player_stats[row["player_id"]]
                st["distance_m"] = row["distance_m"]
                st["top_speed"] = max(st["top_speed"], row["top_speed_kmh"])
        elif etype == "substitution":
            self.substitutions.append(
                {"t": ev["t"], "minute": ev["minute"], "team": team, "off": ev["player_off_id"], "on": ev["player_on_id"]}
            )

        while self.status == "live" and ev["t"] >= self._next_timeline:
            self.timeline.append(self._timeline_point(self._next_timeline))
            self._next_timeline += 60.0

        return {**ev, "derived": derived} if derived else ev

    def _on_pass(self, ev: dict[str, Any], c: dict[str, float], ps: dict[str, float]) -> dict[str, Any]:
        team = ev["team"]
        sx, ex = self.own_x(team, ev["x"]), self.own_x(team, ev["end_x"])
        ey_own = ev["end_y"] if team == self.home_id else PITCH_Y - ev["end_y"]
        distance = math.hypot(ev["end_x"] - ev["x"], ev["end_y"] - ev["y"])
        forward = ex - sx
        complete = ev["outcome"] == "complete"
        in_box = ex >= BOX_X and BOX_Y_MIN <= ey_own <= BOX_Y_MAX
        difficulty = pass_difficulty(distance, forward, bool(ev.get("under_pressure")), ex, ey_own)
        c["passes"] += 1
        ps["passes"] += 1
        ps["touches"] += 1
        if sx <= PITCH_X * 0.6:
            c["passes_own60"] += 1
        if complete:
            c["passes_completed"] += 1
            ps["passes_completed"] += 1
            ps["difficulty_sum"] += difficulty
            if difficulty > ps["hardest_pass"]:
                ps["hardest_pass"] = difficulty
            if sx < FINAL_THIRD_X <= ex:
                c["final_third_entries"] += 1
                c["threat"] += 1.0
            if in_box and not (sx >= BOX_X):
                c["box_entries"] += 1
                c["threat"] += 2.5
            if self.sequence and self.sequence["team"] == team:
                self.sequence["passes"] += 1
            self.ball = (ev["end_x"], ev["end_y"])
        return {
            "distance_m": round(distance, 1),
            "forward_m": round(forward, 1),
            "difficulty": difficulty,
            "ball_speed_kmh": round(ev.get("ball_speed_ms", 0.0) * 3.6, 1),
            "end_zone": "box" if in_box else "final_third" if ex >= FINAL_THIRD_X else "other",
        }

    def _close_sequence(self) -> None:
        if self.sequence:
            c = self.counters[self.sequence["team"]]
            c["sequences"] += 1
            c["sequence_passes"] += self.sequence["passes"]
        self.sequence = None

    def _on_possession_change(self, ev: dict[str, Any]) -> None:
        new, old, reason = ev["team"], ev.get("from_team"), ev["reason"]
        self._close_sequence()
        start_x = self.own_x(new, ev["x"])
        if old and old != new and reason in OPEN_PLAY_TURNOVERS:
            self.counters[old]["turnovers"] += 1
            won = self.counters[new]
            won["recoveries"] += 1
            won["recovery_x"] += start_x
            if start_x >= PITCH_X / 2:
                won["high_recoveries"] += 1
        self.possession = new
        self.sequence = {
            "team": new,
            "start_t": ev["t"],
            "start_x": start_x,
            "passes": 0,
            "origin": reason,
        }

    # ----------------------------------------------------------------- queries
    def _snapshot_at(self, t: float) -> tuple[float, dict[str, dict[str, float]]]:
        i = max(0, bisect.bisect_right(self._snap_t, t) - 1)
        return self._snap_t[i], self._snaps[i]

    def window(self, seconds: float | None = None, end: float | None = None) -> dict[str, Any]:
        """Metrics over [end - seconds, end]. `seconds=None` means whole match.

        Returns {team_id: {...}, "match": {...}}. Rates are None when there is
        no denominator rather than a misleading zero.
        """
        if end is None or end >= self.t:
            end_t, end_c = self.t, self.counters
        else:
            end_t, end_c = self._snapshot_at(end)
        if seconds is None:
            start_t, start_c = self._snap_t[0], self._snaps[0]
        else:
            start_t, start_c = self._snapshot_at(max(0.0, end_t - seconds))
        raw = {
            tid: {k: end_c[tid][k] - start_c[tid][k] for k in TEAM_COUNTERS} for tid in self.teams
        }
        span_min = max((end_t - start_t) / 60.0, 1e-6)
        out: dict[str, Any] = {}
        total_poss = sum(r["possession_s"] for r in raw.values())
        total_threat = sum(r["threat"] for r in raw.values())
        for tid, r in raw.items():
            opp = raw[self.other(tid)]
            out[tid] = {
                **r,
                "possession_pct": _ratio(r["possession_s"], total_poss),
                "pass_accuracy": _ratio(r["passes_completed"], r["passes"]),
                "press_rate": _ratio(r["pressures"], opp["passes"] + opp["carries"]),
                "ppda": _ratio(opp["passes_own60"], r["def_actions_high"]),
                "threat_share": _ratio(r["threat"], total_threat),
                "avg_sequence_passes": _ratio(r["sequence_passes"], r["sequences"]),
                "avg_recovery_x": _ratio(r["recovery_x"], r["recoveries"]),
                "high_recoveries_per_min": r["high_recoveries"] / span_min,
            }
        passes = sum(r["passes"] for r in raw.values())
        completed = sum(r["passes_completed"] for r in raw.values())
        turnovers = sum(r["turnovers"] for r in raw.values())
        accuracy = _ratio(completed, passes)
        tpm = turnovers / span_min
        out["match"] = {
            "span_min": span_min,
            "start_t": start_t,
            "end_t": end_t,
            "total_threat": total_threat,
            "turnovers_per_min": tpm,
            "pass_accuracy": accuracy,
            "chaos": chaos_index(tpm, accuracy),
        }
        return out

    def _timeline_point(self, t: float) -> dict[str, Any]:
        w8 = self.window(MOMENTUM_WINDOW_S, end=t)
        w10 = self.window(CHAOS_WINDOW_S, end=t)
        share = w8[self.home_id]["threat_share"]
        return {
            "t": t,
            "momentum_home": round(share, 3) if share is not None else 0.5,
            "chaos": round(w10["match"]["chaos"]),
            "press": {tid: round(w8[tid]["press_rate"] or 0.0, 3) for tid in self.teams},
        }

    def player_card(self, player_id: str) -> dict[str, Any]:
        p = self.players.get(player_id, {})
        s = self.player_stats[player_id]
        return {
            "id": player_id,
            "name": p.get("name", "Unknown"),
            "number": p.get("number"),
            "position": p.get("position"),
            "team": p.get("team"),
            "touches": int(s["touches"]),
            "passes": int(s["passes"]),
            "passes_completed": int(s["passes_completed"]),
            "pass_accuracy": _ratio(s["passes_completed"], s["passes"]),
            "avg_pass_difficulty": _ratio(s["difficulty_sum"], s["passes_completed"]),
            "hardest_pass": int(s["hardest_pass"]),
            "shots": int(s["shots"]),
            "goals": int(s["goals"]),
            "assists": int(s["assists"]),
            "xg": round(s["xg"], 2),
            "tackles_won": int(s["tackles_won"]),
            "interceptions": int(s["interceptions"]),
            "pressures": int(s["pressures"]),
            "top_speed_kmh": round(s["top_speed"], 1),
            "distance_km": round(s["distance_m"] / 1000.0, 2),
        }

    def snapshot(self) -> dict[str, Any]:
        """Compact live state for a renderer or UI."""
        full = self.window()
        w8 = self.window(MOMENTUM_WINDOW_S)
        w10 = self.window(CHAOS_WINDOW_S)
        teams = {}
        for tid in self.teams:
            f = full[tid]
            teams[tid] = {
                "possession_pct": _pct(f["possession_pct"]),
                "pass_accuracy": _pct(f["pass_accuracy"]),
                "passes": int(f["passes"]),
                "shots": int(f["shots"]),
                "shots_on_target": int(f["shots_on_target"]),
                "xg": round(f["xg"], 2),
                "ppda": round(f["ppda"], 1) if f["ppda"] is not None else None,
                "press_rate_8": _pct(w8[tid]["press_rate"]),
            }
        share = w8[self.home_id]["threat_share"]
        return {
            "t": round(self.t, 2),
            "period": self.period,
            "clock": self.clock,
            "minute": self.minute,
            "status": self.status,
            "score": dict(self.score),
            "possession": self.possession,
            "ball": {"x": round(self.ball[0], 1), "y": round(self.ball[1], 1)},
            "teams": teams,
            "live": {
                "momentum_home": round(share, 3) if share is not None else 0.5,
                "chaos": round(w10["match"]["chaos"]),
            },
        }


def _pct(v: float | None) -> int | None:
    return None if v is None else round(v * 100)


def chaos_index(turnovers_per_min: float, pass_accuracy: float | None) -> float:
    """0 (one team calmly in control) .. 100 (end-to-end, the ball is nobody's).

    60% open-play turnover rate, 40% combined passing inaccuracy. Calibrated on
    the synthetic generator: about 1.2 open-play turnovers a minute is a quiet
    spell, about 3.0 is frantic.
    """
    tpm = max(0.0, min(1.0, (turnovers_per_min - 1.2) / 1.8))
    acc = pass_accuracy if pass_accuracy is not None else 0.8
    loose = max(0.0, min(1.0, (0.88 - acc) / 0.20))
    return 100.0 * (0.6 * tpm + 0.4 * loose)
