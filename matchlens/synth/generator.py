"""Seeded, football-realistic match event simulator.

The simulator plays a match as a chain of ball actions. Each team follows a
hidden *tactic script* (press intensity, directness, ball security, tempo) that
changes over the match. The script is written to a separate ground-truth file
and is never shown to the pipeline, so detectors such as "press shift" can be
scored against what the simulator actually did.

Coordinates are normalized for the whole match: the home team attacks towards
x = 105, the away team towards x = 0. Pitch is 105 x 68 metres.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple

from ..models import PITCH_X, PITCH_Y, Player, Team
from .teams import AWAY, HOME

GENERATOR_VERSION = "1.0"


class Tactic(NamedTuple):
    press: float  # 0 low block .. 1 full high press
    directness: float  # 0 patient .. 1 vertical
    control: float  # additive pass-completion modifier
    tempo: float  # 0 slow .. 1 fast


# (from_minute, tactic). The interesting part of the story: the away side sit
# deep early, grow into the game, then switch to a high press on the hour.
DEFAULT_SCRIPT: dict[str, list[tuple[int, Tactic, str]]] = {
    "home": [
        (0, Tactic(0.45, 0.35, 0.05, 0.45), "patient_control"),
        (25, Tactic(0.40, 0.40, 0.03, 0.50), "balanced"),
        (60, Tactic(0.30, 0.55, -0.03, 0.55), "tiring_under_pressure"),
        (78, Tactic(0.33, 0.60, -0.04, 0.60), "holding_on"),
    ],
    "away": [
        (0, Tactic(0.22, 0.60, -0.03, 0.50), "low_block"),
        (25, Tactic(0.42, 0.50, 0.00, 0.50), "mid_block"),
        (45, Tactic(0.48, 0.50, 0.01, 0.55), "mid_block_plus"),
        (60, Tactic(0.88, 0.65, 0.02, 0.75), "high_press"),
        (78, Tactic(0.72, 0.60, 0.02, 0.70), "sustained_press"),
    ],
}

# (clock_minute, side, shirt number off, shirt number on)
DEFAULT_SUBS = [(60, "away", 9, 20), (70, "home", 8, 14)]

# Average running rate in m/s by position, before press-intensity scaling.
RUN_RATE = {
    "GK": 0.85,
    "CB": 1.70,
    "RB": 1.95,
    "LB": 1.95,
    "DM": 2.08,
    "CM": 2.12,
    "AM": 2.02,
    "RW": 1.98,
    "LW": 1.98,
    "ST": 1.86,
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def format_clock(clock_s: float) -> str:
    return f"{int(clock_s // 60):02d}:{int(clock_s % 60):02d}"


def minute_label(period: int, clock_s: float) -> str:
    """Broadcast-style minute: 12', 45+2', 90+4'."""
    regulation = 2700 if period == 1 else 5400
    if clock_s >= regulation:
        return f"{regulation // 60}+{int((clock_s - regulation) // 60) + 1}'"
    return f"{int(clock_s // 60) + 1}'"


class MatchSimulator:
    def __init__(
        self,
        seed: int = 11,
        match_id: str | None = None,
        home: Team = HOME,
        away: Team = AWAY,
        script: dict[str, list[tuple[int, Tactic, str]]] | None = None,
        subs: list[tuple[int, str, int, int]] | None = None,
    ) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.match_id = match_id or f"SYN-{seed:04d}"
        self.home, self.away = home, away
        self.script = script or DEFAULT_SCRIPT
        self.subs = list(DEFAULT_SUBS if subs is None else subs)
        self.lineup: dict[str, list[Player]] = {
            home.id: list(home.players),
            away.id: list(away.players),
        }
        self.bench: dict[str, list[Player]] = {
            home.id: list(home.bench),
            away.id: list(away.bench),
        }
        self.events: list[dict[str, Any]] = []
        self.t = 0.0
        self.period = 1
        self.period_start_t = 0.0
        self.score = {home.id: 0, away.id: 0}
        self.ball = (PITCH_X / 2, PITCH_Y / 2)
        self.poss = home.id
        self.carrier: Player = home.players[-2]
        self.distance: dict[str, float] = {}
        self.top_speed: dict[str, float] = {}
        self._next_physical = 300.0
        self._subs_done: set[int] = set()
        self._last_pass: tuple[str, str] | None = None  # (passer_id, receiver_id)

    # ------------------------------------------------------------------ helpers
    def _other(self, team_id: str) -> str:
        return self.away.id if team_id == self.home.id else self.home.id

    def _side(self, team_id: str) -> str:
        return "home" if team_id == self.home.id else "away"

    def _clock_s(self) -> float:
        return (0 if self.period == 1 else 2700) + (self.t - self.period_start_t)

    def _tactic(self, team_id: str) -> Tactic:
        minute = self._clock_s() / 60
        current = self.script[self._side(team_id)][0][1]
        for start, tactic, _label in self.script[self._side(team_id)]:
            if minute >= start:
                current = tactic
        return current

    def _own(self, team_id: str, x: float, y: float) -> tuple[float, float]:
        """Convert between pitch frame and a team's own attacking frame (involution)."""
        if team_id == self.home.id:
            return x, y
        return PITCH_X - x, PITCH_Y - y

    def _pos(self, p: Player, jitter: float = 2.5) -> tuple[float, float]:
        """Where a player roughly is right now, in pitch coordinates."""
        bx, by = self._own(p.team, *self.ball)
        if p.position == "GK":
            x = p.base_x + (bx - 52.5) * 0.08
            y = 34 + (by - 34) * 0.10
        else:
            x = p.base_x + (bx - 52.5) * 0.50 + (7 if p.team == self.poss else -5)
            y = p.base_y + (by - 34) * 0.30
        x = _clamp(x + self.rng.gauss(0, jitter), 1, PITCH_X - 6)
        y = _clamp(y + self.rng.gauss(0, jitter), 1, PITCH_Y - 1)
        return self._own(p.team, x, y)

    def _nearest(
        self, team_id: str, x: float, y: float, exclude: str | None = None, allow_gk: bool = False
    ) -> Player:
        best, best_d = None, 1e9
        for p in self.lineup[team_id]:
            if p.id == exclude or (p.position == "GK" and not allow_gk):
                continue
            px, py = self._pos(p)
            d = math.hypot(px - x, py - y)
            if d < best_d:
                best, best_d = p, d
        assert best is not None
        return best

    def _gk(self, team_id: str) -> Player:
        return next(p for p in self.lineup[team_id] if p.position == "GK")

    def _emit(
        self, etype: str, team: str | None = None, player: Player | None = None, **extra: Any
    ) -> dict[str, Any]:
        clock_s = self._clock_s()
        ev: dict[str, Any] = {
            "id": len(self.events) + 1,
            "t": round(self.t, 2),
            "period": self.period,
            "clock": format_clock(clock_s),
            "minute": minute_label(self.period, clock_s),
            "type": etype,
        }
        if team is not None:
            ev["team"] = team
        if player is not None:
            ev["player_id"] = player.id
        ev.update(extra)
        self.events.append(ev)
        return ev

    def _xy(self) -> dict[str, float]:
        return {"x": round(self.ball[0], 1), "y": round(self.ball[1], 1)}

    def _change_possession(
        self,
        team_id: str,
        reason: str,
        carrier: Player | None = None,
        at: tuple[float, float] | None = None,
    ) -> None:
        prev = self.poss
        if at is not None:
            self.ball = at
        self.poss = team_id
        self.carrier = carrier or self._nearest(team_id, *self.ball)
        self._last_pass = None
        self._emit(
            "possession_change", team_id, self.carrier, from_team=prev, reason=reason, **self._xy()
        )

    # ------------------------------------------------------------- side events
    def _accumulate_physical(self, dt: float) -> None:
        for team_id, players in self.lineup.items():
            press = self._tactic(team_id).press
            for p in players:
                rate = RUN_RATE.get(p.position, 1.9) * (0.98 + 0.38 * press)
                self.distance[p.id] = self.distance.get(p.id, 0.0) + dt * rate * self.rng.uniform(
                    0.9, 1.1
                )

    def _emit_physical(self) -> None:
        for team_id, players in self.lineup.items():
            self._emit(
                "physical_update",
                team_id,
                players=[
                    {
                        "player_id": p.id,
                        "distance_m": round(self.distance.get(p.id, 0.0)),
                        "top_speed_kmh": round(self.top_speed.get(p.id, 0.0), 1),
                    }
                    for p in players
                ],
            )

    def _maybe_physical_update(self) -> None:
        if self._clock_s() >= self._next_physical:
            self._emit_physical()
            self._next_physical += 300.0

    def _sprint(self, p: Player, at: tuple[float, float] | None = None) -> None:
        v = min(p.pace + 0.6, self.rng.gauss(p.pace - 2.6, 1.7))
        if v < 25:
            return
        x, y = at or self._pos(p)
        self.top_speed[p.id] = max(self.top_speed.get(p.id, 0.0), v)
        self._emit(
            "sprint",
            p.team,
            p,
            x=round(x, 1),
            y=round(y, 1),
            top_speed_kmh=round(v, 1),
            distance_m=round(self.rng.uniform(12, 38), 1),
        )

    def _maybe_sprint(self) -> None:
        if self.rng.random() >= 0.075:
            return
        team_id = self.rng.choice([self.home.id, self.away.id])
        outfield = [p for p in self.lineup[team_id] if p.position != "GK"]
        weights = [max(0.5, p.pace - 28) for p in outfield]
        self._sprint(self.rng.choices(outfield, weights)[0])

    def _maybe_sub(self) -> None:
        minute = self._clock_s() / 60
        for i, (at_min, side, off_no, on_no) in enumerate(self.subs):
            if i in self._subs_done or minute < at_min:
                continue
            self._subs_done.add(i)
            team_id = self.home.id if side == "home" else self.away.id
            off = next((p for p in self.lineup[team_id] if p.number == off_no), None)
            on = next((p for p in self.bench[team_id] if p.number == on_no), None)
            if off is None or on is None:
                continue
            on = replace(on, base_x=off.base_x, base_y=off.base_y)
            idx = self.lineup[team_id].index(off)
            self.lineup[team_id][idx] = on
            self.bench[team_id] = [p for p in self.bench[team_id] if p.number != on_no]
            if self.carrier.id == off.id:
                self.carrier = on
            self._emit("substitution", team_id, player_off_id=off.id, player_on_id=on.id)

    # ----------------------------------------------------------------- actions
    def _step(self) -> None:
        rng = self.rng
        att, dfn = self.poss, self._other(self.poss)
        ta, td = self._tactic(att), self._tactic(dfn)
        dt = rng.uniform(2.0, 4.6) / (0.80 + 0.40 * ta.tempo)
        self.t += dt
        self._accumulate_physical(dt)
        self._maybe_sub()
        self._maybe_physical_update()
        self._maybe_sprint()

        carrier = self.carrier
        bx, by = self._own(att, *self.ball)
        depth = bx / PITCH_X
        pressured = False

        p_press = min(0.92, 0.10 + td.press * (0.80 - 0.50 * depth) + 0.5 * max(0.0, depth - 0.7))
        if carrier.position == "GK":
            p_press *= 0.5
        if rng.random() < p_press:
            pressured = True
            presser = self._nearest(dfn, *self.ball)
            self._emit(
                "pressure",
                dfn,
                presser,
                target_player_id=carrier.id,
                intensity=round(rng.uniform(0.4, 1.0) * (0.5 + 0.5 * td.press), 2),
                **self._xy(),
            )
            if rng.random() < 0.10 + 0.20 * td.press:
                p_win = 0.40 + 0.5 * (presser.tackling - 0.6) - 0.15 * (carrier.passing - 0.75)
                if rng.random() < p_win:
                    self._emit(
                        "tackle", dfn, presser, outcome="won", opponent_id=carrier.id, **self._xy()
                    )
                    self._change_possession(dfn, "tackle", carrier=presser)
                    return
                if rng.random() < 0.18:
                    self._emit("foul", dfn, presser, fouled_player_id=carrier.id, **self._xy())
                    self.t += rng.uniform(8, 20)
                    pressured = False
                else:
                    self._emit(
                        "tackle", dfn, presser, outcome="lost", opponent_id=carrier.id, **self._xy()
                    )

        dist_goal = math.hypot(PITCH_X - bx, PITCH_Y / 2 - by)
        p_shot = 0.0
        if dist_goal < 28 and carrier.position != "GK":
            p_shot = (0.012 + 0.24 * (28 - dist_goal) / 28) * (0.7 if pressured else 1.0)
        p_carry = 0.02 if carrier.position == "GK" else 0.12 + 0.10 * ta.directness
        r = rng.random()
        if r < p_shot:
            self._shot(carrier, pressured, bx, by, dist_goal)
        elif r < p_shot + p_carry:
            self._carry(carrier, pressured, bx, by)
        else:
            self._pass(carrier, ta, pressured, bx, by)

    def _pass(self, carrier: Player, ta: Tactic, pressured: bool, bx: float, by: float) -> None:
        rng = self.rng
        att, dfn = self.poss, self._other(self.poss)
        cands = []
        for p in self.lineup[att]:
            if p.id == carrier.id:
                continue
            ox, oy = self._own(att, *self._pos(p))
            d = math.hypot(ox - bx, oy - by)
            if d < 4:
                continue
            fwd = ox - bx
            w = math.exp(-((d - 16) ** 2) / (2 * 13**2))
            w *= math.exp(_clamp(ta.directness * fwd / 14, -1.5, 1.5))
            if p.position == "GK":
                w *= 0.25 if bx < 35 else 0.02
            cands.append((w, p, ox, oy))
        if not cands:
            self._carry(carrier, pressured, bx, by)
            return
        _, recv, ox, oy = rng.choices(cands, [c[0] for c in cands])[0]
        tx = _clamp(ox + rng.gauss(0, 1.2), 0.5, PITCH_X - 5)
        ty = _clamp(oy + rng.gauss(0, 1.2), 0.5, PITCH_Y - 0.5)
        d = math.hypot(tx - bx, ty - by)
        fwd = tx - bx
        p_ok = (
            0.945
            + ta.control
            - 0.0065 * max(0.0, d - 12)
            - (0.13 if pressured else 0.0)
            - 0.20 * max(0.0, tx / PITCH_X - 0.66) / 0.34
            - 0.03 * max(0.0, fwd) / 20
            + (carrier.passing - 0.78) * 0.35
        )
        complete = rng.random() < _clamp(p_ok, 0.28, 0.985)
        speed = _clamp(rng.gauss(9 + 0.32 * d, 1.5), 5, 32)
        end = self._own(att, tx, ty)
        self._emit(
            "pass",
            att,
            carrier,
            **self._xy(),
            end_x=round(end[0], 1),
            end_y=round(end[1], 1),
            receiver_id=recv.id,
            outcome="complete" if complete else "incomplete",
            under_pressure=pressured,
            height="high" if d > 30 and rng.random() < 0.7 else "ground",
            ball_speed_ms=round(speed, 1),
        )
        self.ball = end
        if complete:
            self._last_pass = (carrier.id, recv.id)
            self.carrier = recv
            return
        r = rng.random()
        if r < 0.36:
            opp = self._nearest(dfn, *end)
            self._emit("interception", dfn, opp, **self._xy())
            self._change_possession(dfn, "interception", carrier=opp)
        elif r < 0.60:
            self.t += rng.uniform(5, 12)
            self._change_possession(
                dfn, "out_of_play", at=(end[0], 0.5 if end[1] < PITCH_Y / 2 else PITCH_Y - 0.5)
            )
        else:
            self._change_possession(dfn, "loose_ball")

    def _carry(self, carrier: Player, pressured: bool, bx: float, by: float) -> None:
        rng = self.rng
        dx = rng.uniform(4, 13) * (0.6 if pressured else 1.0)
        nx = _clamp(bx + dx, 1, PITCH_X - 6)
        ny = _clamp(by + rng.gauss(0, 4), 1, PITCH_Y - 1)
        end = self._own(self.poss, nx, ny)
        self._emit(
            "carry",
            self.poss,
            carrier,
            **self._xy(),
            end_x=round(end[0], 1),
            end_y=round(end[1], 1),
            under_pressure=pressured,
        )
        self.ball = end
        if dx > 9 and rng.random() < 0.45:
            self._sprint(carrier, at=end)

    def _shot(
        self, carrier: Player, pressured: bool, bx: float, by: float, dist: float
    ) -> None:
        rng = self.rng
        att, dfn = self.poss, self._other(self.poss)
        off_centre = min(1.0, abs(PITCH_Y / 2 - by) / max(dist, 1e-6))
        xg = 0.62 * math.exp(-dist / 9.0) * (1 - 0.55 * off_centre) * (0.75 if pressured else 1.0)
        xg = _clamp(xg, 0.01, 0.92)
        p_goal = _clamp(xg * (0.75 + 0.6 * (carrier.finishing - 0.5)), 0.0, 0.95)
        body_part = rng.choices(["right_foot", "left_foot", "head"], [0.55, 0.30, 0.15])[0]
        speed = _clamp(rng.gauss(98, 14), 55, 131) * (0.62 if body_part == "head" else 1.0)
        r = rng.random()
        if r < p_goal:
            outcome = "goal"
        else:
            outcome = rng.choices(["saved", "off_target", "blocked"], [0.38, 0.37, 0.25])[0]
        shot = self._emit(
            "shot",
            att,
            carrier,
            **self._xy(),
            xg=round(xg, 3),
            outcome=outcome,
            speed_kmh=round(speed, 1),
            distance_m=round(dist, 1),
            body_part=body_part,
            under_pressure=pressured,
        )
        gk = self._gk(dfn)
        goal_mouth = self._own(dfn, 6.0, PITCH_Y / 2)
        if outcome == "goal":
            self.score[att] += 1
            extra: dict[str, Any] = {}
            if self._last_pass and self._last_pass[1] == carrier.id:
                extra["assist_id"] = self._last_pass[0]
            self._emit(
                "goal",
                att,
                carrier,
                shot_id=shot["id"],
                score=dict(self.score),
                **extra,
                **self._xy(),
            )
            self.t += rng.uniform(45, 70)
            self._change_possession(dfn, "kickoff", at=(PITCH_X / 2, PITCH_Y / 2))
        elif outcome == "saved":
            self.t += rng.uniform(4, 10)
            self._change_possession(dfn, "save", carrier=gk, at=goal_mouth)
        elif outcome == "off_target":
            self.t += rng.uniform(15, 30)
            self._change_possession(dfn, "goal_kick", carrier=gk, at=goal_mouth)
        elif rng.random() < 0.5:
            self.t += rng.uniform(10, 22)
            self.carrier = self._nearest(att, *self.ball, exclude=carrier.id)
        else:
            self._change_possession(dfn, "block")

    # -------------------------------------------------------------------- run
    def _play_period(self, period: int, kickoff: str) -> None:
        self.period = period
        self.period_start_t = self.t
        self._emit("period_start")
        self._change_possession(kickoff, "kickoff", at=(PITCH_X / 2, PITCH_Y / 2))
        end = self.t + 2700 + self.rng.uniform(60, 170 if period == 1 else 300)
        while self.t < end:
            self._step()
        self._emit_physical()
        self._emit("period_end", score=dict(self.score))

    def run(self) -> list[dict[str, Any]]:
        self._emit("match_start", match_id=self.match_id)
        self._play_period(1, self.home.id)
        self.t += 1.0
        self._next_physical = 3000.0
        self._play_period(2, self.away.id)
        self._emit("match_end", score=dict(self.score))
        return self.events

    # ------------------------------------------------------------- descriptors
    def meta(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "competition": "Synthetic Super League",
            "venue": "Northbridge Park",
            "synthetic": True,
            "notice": "Fully synthetic data. Clubs, players and events are fictional.",
            "pitch": {
                "length_m": PITCH_X,
                "width_m": PITCH_Y,
                "orientation": "Home attacks towards x=105 for the whole match.",
            },
            "teams": [self.home.public(), self.away.public()],
            "generator": {"name": "matchlens.synth", "version": GENERATOR_VERSION, "seed": self.seed},
        }

    def truth(self) -> dict[str, Any]:
        """Labels the pipeline never sees; used to score detectors."""
        return {
            "match_id": self.match_id,
            "seed": self.seed,
            "final_score": dict(self.score),
            "tactic_phases": [
                {
                    "team": self.home.id if side == "home" else self.away.id,
                    "from_minute": start,
                    "label": label,
                    **tactic._asdict(),
                }
                for side, phases in self.script.items()
                for start, tactic, label in phases
            ],
            "substitutions": [
                {"minute": m, "team": self.home.id if s == "home" else self.away.id, "off": o, "on": n}
                for m, s, o, n in self.subs
            ],
        }


def generate_match(seed: int = 11, **kwargs: Any) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Return (meta, events, ground_truth) for one synthetic match."""
    sim = MatchSimulator(seed=seed, **kwargs)
    events = sim.run()
    return sim.meta(), events, sim.truth()


def write_match(out_dir: str | Path, seed: int = 11, name: str | None = None) -> dict[str, Path]:
    meta, events, truth = generate_match(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = name or meta["match_id"]
    paths = {
        "meta": out / f"{stem}.meta.json",
        "events": out / f"{stem}.events.jsonl",
        "truth": out / f"{stem}.truth.json",
    }
    paths["meta"].write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    paths["truth"].write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
    with paths["events"].open("w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
    return paths
