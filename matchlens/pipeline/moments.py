"""Stage 3a - Detect: find the moments worth talking about.

Every detector returns an `Insight`: what happened, how important it is, the
numeric *facts* behind it and a list of *reason codes* that say why it matters.
Explanations are written only from those facts and codes, which is what makes
them checkable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from ..models import PITCH_X, Insight
from .interpret import CHAOS_WINDOW_S, MOMENTUM_WINDOW_S, OPEN_PLAY_TURNOVERS, MatchState


@dataclass
class Thresholds:
    """Every trigger level in one place, so a producer can tune them."""

    big_chance_xg: float = 0.25
    shot_speed_kmh: float = 115.0
    line_break_difficulty: int = 82
    line_break_forward_m: float = 15.0
    pass_milestones: tuple[int, ...] = (30, 50, 75)
    speed_alert_kmh: float = 34.0
    distance_marks_km: tuple[float, ...] = (5.0, 10.0)
    press_window_s: float = 360.0
    press_ratio: float = 1.7
    press_cusum_h: float = 7.0
    press_min_baseline: int = 100
    momentum_dominant: float = 0.66
    momentum_min_threat: float = 8.0
    chaos_high: float = 62.0
    chaos_low: float = 30.0
    periodic_from_s: float = 600.0


def _minute_number(minute: str) -> int:
    return int(minute.split("'")[0].split("+")[0])


def _z_two_proportions(x1: float, n1: float, x2: float, n2: float) -> float:
    """z-score for rate x1/n1 being different from x2/n2."""
    if n1 <= 0 or n2 <= 0:
        return 0.0
    p = (x1 + x2) / (n1 + n2)
    se = math.sqrt(max(p * (1 - p) * (1 / n1 + 1 / n2), 1e-12))
    return (x1 / n1 - x2 / n2) / se


class MomentDetector:
    def __init__(self, state: MatchState, thresholds: Thresholds | None = None) -> None:
        self.s = state
        self.th = thresholds or Thresholds()
        self.history: list[Insight] = []
        self._n = 0
        self._cooldown: dict[str, float] = {}
        self._next_eval = self.th.periodic_from_s
        self._dominant: str | None = None
        self._rhythm: str | None = None
        self._hit: set[tuple] = set()
        self._press_up_at: dict[str, float] = {}
        self._press_regime: dict[str, dict[str, float]] = {}

    # ----------------------------------------------------------------- helpers
    def _ready(self, key: str, cooldown_s: float) -> bool:
        if self.s.t < self._cooldown.get(key, -1.0):
            return False
        self._cooldown[key] = self.s.t + cooldown_s
        return True

    def _make(
        self,
        kind: str,
        importance: float,
        team: str | None = None,
        players: list[str] | None = None,
        facts: dict[str, Any] | None = None,
        reasons: list[str] | None = None,
        ev: dict[str, Any] | None = None,
    ) -> Insight:
        self._n += 1
        s = self.s
        base: dict[str, Any] = {"minute": s.minute}
        if team:
            base["team_name"] = s.team_name(team)
            base["opp_name"] = s.team_name(s.other(team))
        if players:
            base["player_name"] = s.player_name(players[0])
            base["player_number"] = s.players.get(players[0], {}).get("number")
        anchor = None
        if ev is not None and "x" in ev:
            anchor = {"x": ev["x"], "y": ev["y"]}
        insight = Insight(
            id=f"{kind}-{self._n:03d}",
            t=s.t,
            period=s.period,
            clock=s.clock,
            minute=s.minute,
            kind=kind,
            importance=round(max(0.0, min(1.0, importance)), 2),
            team=team,
            player_ids=list(players or []),
            facts={**base, **(facts or {})},
            reasons=list(reasons or []),
            anchor=anchor,
            source_event_ids=[ev["id"]] if ev is not None else [],
        )
        self.history.append(insight)
        return insight

    # ------------------------------------------------------------------ router
    def process(self, ev: dict[str, Any]) -> list[Insight]:
        out: list[Insight] = []
        etype = ev["type"]
        if etype == "shot":
            out += self._shot(ev)
        elif etype == "goal":
            out += self._goal(ev)
        elif etype == "pass":
            out += self._pass(ev)
        elif etype == "sprint":
            out += self._sprint(ev)
        elif etype == "physical_update":
            out += self._physical(ev)
        elif etype == "period_end":
            out.append(self._recap(ev))
        if self.s.status == "live":
            while ev["t"] >= self._next_eval:
                out += self._press_shift(self._next_eval) + self._momentum() + self._rhythm_shift()
                self._next_eval += 30.0
        return out

    # ------------------------------------------------------------ event-driven
    def _goal(self, ev: dict[str, Any]) -> list[Insight]:
        s, team, pid = self.s, ev["team"], ev["player_id"]
        opp = s.other(team)
        shot = s.last_shot or {}
        seq = s.sequence or {"passes": 0, "start_t": ev["t"], "start_x": 0.0, "origin": "unknown"}
        before = s.window(600, end=max(0.0, ev["t"] - 1))
        share = before[team]["threat_share"]
        mine, theirs = s.score[team], s.score[opp]
        if mine == 1 and theirs == 0:
            change = "opener"
        elif mine - 1 == theirs:
            change = "go_ahead"
        elif mine == theirs:
            change = "equaliser"
        elif mine - 1 > theirs:
            change = "extends_lead"
        else:
            change = "pulls_one_back"
        minute = _minute_number(ev["minute"])
        duration = ev["t"] - seq["start_t"]
        regain_high = seq["origin"] in OPEN_PLAY_TURNOVERS and seq["start_x"] >= PITCH_X / 2
        player_goals = int(s.player_stats[pid]["goals"])
        sub = next((x for x in s.substitutions if x["on"] == pid), None)
        max_deficit = self._max_deficit(team)

        reasons: list[str] = []
        if max_deficit >= 2 and change == "go_ahead":
            reasons.append("completes_comeback")
        if minute >= 80 and abs(mine - theirs) <= 1:
            reasons.append("late_drama")
        if share is not None and share <= 0.40:
            reasons.append("against_run_of_play")
        elif share is not None and share >= 0.62:
            reasons.append("had_been_coming")
        if s.t - self._press_up_at.get(team, -1e9) <= 1200:
            reasons.append("press_paying_off")
        if regain_high and duration <= 20:
            reasons.append("high_regain")
        elif seq["passes"] >= 6:
            reasons.append("patient_buildup")
        elif duration <= 12 and seq["start_x"] < PITCH_X / 2:
            reasons.append("direct_attack")
        if sub:
            reasons.append("super_sub")
        if player_goals == 2:
            reasons.append("brace")
        if shot.get("xg", 1.0) < 0.08:
            reasons.append("low_xg_finish")
        elif shot.get("xg", 0.0) >= 0.30:
            reasons.append("big_chance_taken")

        facts = {
            "score_home": s.score[s.home_id],
            "score_away": s.score[s.away_id],
            "home_name": s.team_name(s.home_id),
            "away_name": s.team_name(s.away_id),
            "state_change": change,
            "xg": round(shot.get("xg", 0.0), 2),
            "distance_m": round(shot.get("distance_m", 0.0)),
            "speed_kmh": round(shot.get("speed_kmh", 0.0)),
            "body_part": shot.get("body_part"),
            "sequence_passes": int(seq["passes"]),
            "sequence_seconds": round(duration),
            "threat_share_pct": round(share * 100) if share is not None else None,
            "lookback_min": 10,
            "player_goals": player_goals,
            "team_shots": int(s.counters[team]["shots"]),
            "team_xg": round(s.counters[team]["xg"], 2),
            "max_deficit": max_deficit,
        }
        if ev.get("assist_id"):
            facts["assist_name"] = s.player_name(ev["assist_id"])
        if sub:
            facts["sub_minute"] = sub["minute"]
        players = [pid] + ([ev["assist_id"]] if ev.get("assist_id") else [])
        return [self._make("goal", 1.0, team, players, facts, reasons, ev)]

    def _max_deficit(self, team: str) -> int:
        opp = self.s.other(team)
        return max([g["score"][opp] - g["score"][team] for g in self.s.goals] + [0])

    def _shot(self, ev: dict[str, Any]) -> list[Insight]:
        if ev["outcome"] == "goal":
            return []  # the goal insight carries the shot facts
        s, team, th = self.s, ev["team"], self.th
        c = s.counters[team]
        out: list[Insight] = []
        if ev["xg"] >= th.big_chance_xg:
            gap = c["xg"] - c["goals"]
            reasons = []
            if ev["outcome"] == "saved":
                reasons.append("keeper_stood_up")
            if gap >= 1.0:
                reasons.append("wasteful")
            if s.score[team] < s.score[s.other(team)]:
                reasons.append("chasing_game")
            out.append(
                self._make(
                    "big_chance",
                    0.5 + 0.5 * ev["xg"],
                    team,
                    [ev["player_id"]],
                    {
                        "xg": round(ev["xg"], 2),
                        "outcome": ev["outcome"],
                        "distance_m": round(ev["distance_m"]),
                        "team_xg": round(c["xg"], 2),
                        "team_goals": int(c["goals"]),
                        "team_shots": int(c["shots"]),
                    },
                    reasons,
                    ev,
                )
            )
        elif ev["speed_kmh"] >= th.shot_speed_kmh and ev["body_part"] != "head":
            out.append(
                self._make(
                    "shot_speed",
                    0.35 + (0.12 if ev["outcome"] == "saved" else 0.0),
                    team,
                    [ev["player_id"]],
                    {
                        "speed_kmh": round(ev["speed_kmh"]),
                        "outcome": ev["outcome"],
                        "distance_m": round(ev["distance_m"]),
                        "xg": round(ev["xg"], 2),
                    },
                    ["tested_keeper"] if ev["outcome"] == "saved" else [],
                    ev,
                )
            )
        return out

    def _pass(self, ev: dict[str, Any]) -> list[Insight]:
        if ev["outcome"] != "complete":
            return []
        s, th, team, pid = self.s, self.th, ev["team"], ev["player_id"]
        d = ev.get("derived", {})
        out: list[Insight] = []
        if (
            d.get("difficulty", 0) >= th.line_break_difficulty
            and d.get("forward_m", 0) >= th.line_break_forward_m
            and d.get("end_zone") in ("final_third", "box")
            and self._ready(f"line_break:{team}", 240)
        ):
            reasons = []
            if ev.get("under_pressure"):
                reasons.append("under_pressure")
            if d["end_zone"] == "box":
                reasons.append("into_box")
            if d["distance_m"] >= 30:
                reasons.append("long_range")
            out.append(
                self._make(
                    "line_breaking_pass",
                    0.32 + (d["difficulty"] - th.line_break_difficulty) / 100.0,
                    team,
                    [pid, ev["receiver_id"]],
                    {
                        "difficulty": d["difficulty"],
                        "distance_m": round(d["distance_m"]),
                        "forward_m": round(d["forward_m"]),
                        "ball_speed_kmh": round(d["ball_speed_kmh"]),
                        "receiver_name": s.player_name(ev["receiver_id"]),
                        "end_zone": d["end_zone"],
                    },
                    reasons,
                    ev,
                )
            )
        ps = s.player_stats[pid]
        done = int(ps["passes_completed"])
        if done in th.pass_milestones and ("passes", pid, done) not in self._hit:
            self._hit.add(("passes", pid, done))
            accuracy = ps["passes_completed"] / ps["passes"]
            avg_difficulty = ps["difficulty_sum"] / ps["passes_completed"]
            reasons = []
            if accuracy >= 0.90:
                reasons.append("metronome")
            if avg_difficulty >= 48:
                reasons.append("risk_taker")
            out.append(
                self._make(
                    "pass_milestone",
                    0.28 + (0.07 if accuracy >= 0.90 else 0.0) + (0.05 if done >= 75 else 0.0),
                    team,
                    [pid],
                    {
                        "completed": done,
                        "attempted": int(ps["passes"]),
                        "accuracy_pct": round(accuracy * 100),
                        "avg_difficulty": round(avg_difficulty),
                        "hardest_pass": int(ps["hardest_pass"]),
                    },
                    reasons,
                    ev,
                )
            )
        return out

    def _sprint(self, ev: dict[str, Any]) -> list[Insight]:
        speed = ev["top_speed_kmh"]
        record = bool(ev.get("derived", {}).get("match_record"))
        if speed < self.th.speed_alert_kmh or not record:
            return []
        if not self._ready("speed_record", 300):
            return []
        return [
            self._make(
                "speed_alert",
                0.36,
                ev["team"],
                [ev["player_id"]],
                {"speed_kmh": round(speed, 1), "sprint_m": round(ev["distance_m"]), "threshold_kmh": self.th.speed_alert_kmh},
                ["match_record"],
                ev,
            )
        ]

    def _physical(self, ev: dict[str, Any]) -> list[Insight]:
        out: list[Insight] = []
        for row in ev["players"]:
            km = row["distance_m"] / 1000.0
            for mark in self.th.distance_marks_km:
                key = ("distance", row["player_id"], mark)
                if km < mark or key in self._hit:
                    continue
                self._hit.add(key)
                first = ("distance_first", ev["team"], mark) not in self._hit
                self._hit.add(("distance_first", ev["team"], mark))
                if not first:
                    continue  # only the first player per team to each mark is news
                out.append(
                    self._make(
                        "distance_alert",
                        0.38 if mark >= 10 else 0.22,
                        ev["team"],
                        [row["player_id"]],
                        {"distance_km": round(km, 1), "mark_km": mark},
                        ["first_to_mark"],
                    )
                )
        return out

    # ---------------------------------------------------------------- periodic
    def _press_shift(self, at: float) -> list[Insight]:
        """Sequential change detector (binomial CUSUM) on pressing rate.

        Every 30 seconds each team's last bin (pressures k out of n opponent
        actions on the ball) updates two running log-likelihood ratios: "the
        rate has moved to baseline x ratio" and "...to baseline / ratio",
        against "the rate is still the baseline". An alarm fires when a sum
        crosses `press_cusum_h`. The baseline is everything since the current
        regime began, excluding the most recent minutes so a shift in progress
        does not pollute it. The threshold is calibrated on synthetic matches
        with known tactic scripts (see scripts/eval_detectors.py).
        """
        s, th = self.s, self.th
        out: list[Insight] = []
        latest = s.window(30.0, end=at)
        for team in s.teams:
            opp = s.other(team)
            regime = self._press_regime.setdefault(team, {"start": 0.0, "up": 0.0, "down": 0.0})
            base_end = at - th.press_window_s
            if base_end - regime["start"] < 240:
                continue
            base = s.window(base_end - regime["start"], end=base_end)
            n_base = base[opp]["passes"] + base[opp]["carries"]
            if n_base < th.press_min_baseline or base[team]["press_rate"] is None:
                continue
            p0 = min(0.85, max(0.05, base[team]["press_rate"]))
            n = latest[opp]["passes"] + latest[opp]["carries"]
            k = min(n, latest[team]["pressures"])
            for key, p1 in (("up", min(0.95, p0 * th.press_ratio)), ("down", p0 / th.press_ratio)):
                step = k * math.log(p1 / p0) + (n - k) * math.log((1 - p1) / (1 - p0))
                regime[key] = max(0.0, regime[key] + step)
            now = s.window(th.press_window_s, end=at)
            r_now = now[team]["press_rate"]
            if r_now is None:
                continue
            up = regime["up"] >= th.press_cusum_h and r_now >= p0 * 1.25
            down = regime["down"] >= th.press_cusum_h and r_now <= p0 / 1.25
            if not (up or down):
                continue
            evidence = regime["up"] if up else regime["down"]
            # A new regime starts here: relearn the baseline from the shift on.
            self._press_regime[team] = {"start": base_end, "up": 0.0, "down": 0.0}

            n_now = now[opp]["passes"] + now[opp]["carries"]
            z = _z_two_proportions(now[team]["pressures"], n_now, base[team]["pressures"], n_base)
            acc_now, acc_base = now[opp]["pass_accuracy"], base[opp]["pass_accuracy"]
            sub = next((x for x in reversed(s.substitutions) if x["team"] == team and s.t - x["t"] <= 720), None)
            diff = s.score[team] - s.score[opp]
            reasons: list[str] = []
            if up:
                if acc_now is not None and acc_base is not None and acc_base - acc_now >= 0.05:
                    reasons.append("opp_accuracy_dropped")
                if now[team]["high_recoveries_per_min"] > base[team]["high_recoveries_per_min"] * 1.3:
                    reasons.append("winning_ball_high")
                if sub:
                    reasons.append("coincides_with_sub")
                if diff < 0:
                    reasons.append("chasing_game")
                self._press_up_at[team] = s.t
            else:
                if diff > 0:
                    reasons.append("protecting_lead")
                if _minute_number(s.minute) >= 60:
                    reasons.append("fatigue")
            facts = {
                "direction": "up" if up else "down",
                "press_now_pct": round(r_now * 100),
                "press_before_pct": round(p0 * 100),
                "window_min": round(th.press_window_s / 60),
                "opp_accuracy_now_pct": round(acc_now * 100) if acc_now is not None else None,
                "opp_accuracy_before_pct": round(acc_base * 100) if acc_base is not None else None,
                "high_recoveries": int(now[team]["high_recoveries"]),
                "ppda_now": round(now[team]["ppda"], 1) if now[team]["ppda"] is not None else None,
                "ppda_before": round(base[team]["ppda"], 1) if base[team]["ppda"] is not None else None,
                "evidence_score": round(evidence, 1),
                "evidence_threshold": th.press_cusum_h,
                "z_score": round(z, 1),
                "score_diff": diff,
            }
            if sub and up:
                facts["sub_name"] = s.player_name(sub["on"])
                facts["sub_minute"] = sub["minute"]
            importance = 0.62 + (0.08 if "opp_accuracy_dropped" in reasons else 0.0)
            importance += 0.05 if "coincides_with_sub" in reasons else 0.0
            out.append(self._make("press_shift", importance if up else 0.48, team, [], facts, reasons))
        return out

    def _momentum(self) -> list[Insight]:
        s, th = self.s, self.th
        now = s.window(MOMENTUM_WINDOW_S)
        if now["match"]["total_threat"] < th.momentum_min_threat:
            return []
        share_home = now[s.home_id]["threat_share"]
        if share_home >= th.momentum_dominant:
            team = s.home_id
        elif share_home <= 1 - th.momentum_dominant:
            team = s.away_id
        else:
            return []
        if team == self._dominant:
            return []
        previous = self._dominant
        if previous is None:
            self._dominant = team  # first spell of dominance is not a swing
            return []
        if not self._ready("momentum", 480):
            return []
        self._dominant = team
        opp = s.other(team)
        before = s.window(MOMENTUM_WINDOW_S, end=now["match"]["start_t"])
        share_before = before[team]["threat_share"]
        diff = s.score[team] - s.score[opp]
        conceded = next((g for g in reversed(s.goals) if g["team"] == opp and s.t - g["t"] <= 600), None)
        reasons: list[str] = []
        if share_before is not None and share_before <= 0.45:
            reasons.append("sharp_turnaround")
        if now[team]["shots"] >= 3:
            reasons.append("sustained_pressure")
        if diff < 0:
            reasons.append("against_scoreline")
        if conceded:
            reasons.append("response_to_goal")
        facts = {
            "share_now_pct": round(now[team]["threat_share"] * 100),
            "share_before_pct": round(share_before * 100) if share_before is not None else None,
            "window_min": round(MOMENTUM_WINDOW_S / 60),
            "shots": int(now[team]["shots"]),
            "opp_shots": int(now[opp]["shots"]),
            "final_third_entries": int(now[team]["final_third_entries"]),
            "box_entries": int(now[team]["box_entries"]),
            "score_diff": diff,
        }
        importance = 0.62 if "sharp_turnaround" in reasons else 0.5
        return [self._make("momentum_swing", importance, team, [], facts, reasons)]

    def _rhythm_shift(self) -> list[Insight]:
        s, th = self.s, self.th
        now = s.window(CHAOS_WINDOW_S)
        chaos = now["match"]["chaos"]
        label = "chaotic" if chaos >= th.chaos_high else "controlled" if chaos <= th.chaos_low else None
        if label is None or label == self._rhythm:
            return []
        first = self._rhythm is None
        if not self._ready("rhythm", 600):
            return []
        self._rhythm = label
        before = s.window(CHAOS_WINDOW_S, end=now["match"]["start_t"])
        has_before = before["match"]["span_min"] >= 5.0
        controller = None
        for team in s.teams:
            poss = now[team]["possession_pct"]
            acc = now[team]["pass_accuracy"]
            if poss is not None and acc is not None and poss >= 0.58 and acc >= 0.82:
                controller = team
        reasons: list[str] = []
        presser = None
        for team in s.teams:
            a, b = now[team]["press_rate"], before[team]["press_rate"]
            if has_before and a is not None and b is not None and a >= b * 1.3 and a - b >= 0.08:
                presser = team
        if label == "chaotic":
            if presser:
                reasons.append("press_driven")
            if _minute_number(s.minute) >= 75:
                reasons.append("late_game")
        elif controller:
            reasons.append("one_team_dictating")
        team = presser if label == "chaotic" and presser else controller
        seq = [now[t]["avg_sequence_passes"] for t in s.teams if now[t]["avg_sequence_passes"] is not None]
        seq_before = [before[t]["avg_sequence_passes"] for t in s.teams if before[t]["avg_sequence_passes"] is not None]
        facts = {
            "state": label,
            "chaos_now": round(chaos),
            "chaos_before": round(before["match"]["chaos"]) if has_before else None,
            "turnovers_per_min_now": round(now["match"]["turnovers_per_min"], 1),
            "turnovers_per_min_before": round(before["match"]["turnovers_per_min"], 1) if has_before else None,
            "passes_per_possession_now": round(sum(seq) / len(seq), 1) if seq else None,
            "passes_per_possession_before": (
                round(sum(seq_before) / len(seq_before), 1) if seq_before and has_before else None
            ),
            "window_min": round(CHAOS_WINDOW_S / 60),
        }
        if controller and label == "controlled":
            facts["possession_pct"] = round(now[controller]["possession_pct"] * 100)
            facts["accuracy_pct"] = round(now[controller]["pass_accuracy"] * 100)
        importance = 0.55 if label == "chaotic" else 0.42
        if first:
            importance -= 0.08
        return [self._make("rhythm_shift", importance, team, [], facts, reasons)]

    # ------------------------------------------------------------------- recap
    def _recap(self, ev: dict[str, Any]) -> Insight:
        s = self.s
        full = s.window()
        phase = "half_time" if ev["period"] == 1 else "full_time"
        teams = {}
        for tid in s.teams:
            f = full[tid]
            teams[tid] = {
                "name": s.team_name(tid),
                "goals": s.score[tid],
                "possession_pct": round((f["possession_pct"] or 0) * 100),
                "shots": int(f["shots"]),
                "shots_on_target": int(f["shots_on_target"]),
                "xg": round(f["xg"], 2),
                "pass_accuracy_pct": round((f["pass_accuracy"] or 0) * 100),
                "ppda": round(f["ppda"], 1) if f["ppda"] is not None else None,
                "high_recoveries": int(f["high_recoveries"]),
            }
        scorers = [
            {"minute": g["minute"], "team": g["team"], "name": s.player_name(g["player_id"])} for g in s.goals
        ]
        tactical = [i for i in self.history if i.kind in ("press_shift", "momentum_swing", "rhythm_shift")]
        turning = max(tactical, key=lambda i: (i.importance, i.t), default=None)
        best_id, best_score = None, -1.0
        for pid in s.players:
            ps = s.player_stats[pid]
            rating = (
                3.0 * ps["goals"]
                + 2.0 * ps["assists"]
                + ps["xg"]
                + 0.02 * ps["passes_completed"]
                + 0.2 * (ps["tackles_won"] + ps["interceptions"])
            )
            if rating > best_score:
                best_id, best_score = pid, rating
        home, away = s.score[s.home_id], s.score[s.away_id]
        leader = s.home_id if home > away else s.away_id if away > home else None
        facts: dict[str, Any] = {
            "phase": phase,
            "home_id": s.home_id,
            "away_id": s.away_id,
            "leader": leader,
            "teams": teams,
            "scorers": scorers,
            "standout": s.player_card(best_id) if best_id else None,
        }
        if turning:
            facts["turning_point"] = {
                "kind": turning.kind,
                "minute": turning.minute,
                "team": turning.team,
                "insight_id": turning.id,
                "facts": turning.facts,
                "reasons": turning.reasons,
            }
        if leader:
            facts["comeback"] = self._max_deficit(leader) >= 1
        return self._make("recap", 1.0, leader, [best_id] if best_id else [], facts, [phase])
