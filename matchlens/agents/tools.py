"""Read-only tools the agents use to look at the live match.

One `MatchTools` instance is created per agent run. It records every call and
every number it returned, so the grounding guard can accept a figure the agent
fetched itself and reject one it made up.
"""

from __future__ import annotations

from typing import Annotated, Any

from ..grounding import numbers_in
from ..pipeline.orchestrator import Pipeline

MOMENT_KINDS = (
    "goal", "big_chance", "press_shift", "momentum_swing", "rhythm_shift",
    "line_breaking_pass", "pass_milestone", "speed_alert", "shot_speed", "distance_alert",
)


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


class MatchTools:
    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline
        self.calls: list[dict[str, Any]] = []
        self.evidence: set[float] = set()

    def _record(self, name: str, args: dict[str, Any], result: Any) -> Any:
        self.calls.append({"tool": name, "arguments": args})
        self.evidence |= numbers_in(result)
        return result

    def as_tools(self) -> list[Any]:
        return [self.get_match_state, self.get_team_stats, self.get_player_stats, self.list_key_moments]

    def _team_id(self, team: str) -> str | None:
        needle = team.strip().lower()
        for tid, t in self.pipeline.state.teams.items():
            if needle in (tid.lower(), t["short"].lower()) or needle in t["name"].lower():
                return tid
        return None

    # ------------------------------------------------------------------ tools
    def get_match_state(self) -> dict[str, Any]:
        """Score, minute, goals so far, which team has the momentum and how chaotic the match is right now."""
        s = self.pipeline.state
        snap = s.snapshot()
        home_share = round(snap["live"]["momentum_home"] * 100)
        result = {
            "minute": snap["minute"],
            "status": snap["status"],
            "score": {s.team_name(tid): goals for tid, goals in snap["score"].items()},
            "momentum_share_last_8_min_pct": {s.team_name(s.home_id): home_share, s.team_name(s.away_id): 100 - home_share},
            "chaos_index_0_to_100": snap["live"]["chaos"],
            "goals": [
                {"minute": g["minute"], "team": s.team_name(g["team"]), "scorer": s.player_name(g["player_id"])}
                for g in s.goals
            ],
        }
        return self._record("get_match_state", {}, result)

    def get_team_stats(
        self,
        team: Annotated[str, "Team name or id."],
        last_minutes: Annotated[int | None, "Optional rolling window in minutes (1-45). Omit for the whole match."] = None,
    ) -> dict[str, Any]:
        """Team statistics for the whole match or the last N minutes: passing, shots, xG, pressing, recoveries."""
        s = self.pipeline.state
        args = {"team": team, "last_minutes": last_minutes}
        tid = self._team_id(team)
        if tid is None:
            return self._record("get_team_stats", args, {"error": f"unknown team '{team}'", "teams": [t["name"] for t in s.teams.values()]})
        seconds = None if not last_minutes else max(1, min(45, int(last_minutes))) * 60
        w = s.window(seconds)[tid]
        keep = ("passes", "passes_completed", "shots", "shots_on_target", "goals", "xg", "pressures",
                "tackles_won", "interceptions", "high_recoveries", "final_third_entries", "box_entries")
        out: dict[str, Any] = {k: w[k] for k in keep}
        for k in ("possession_pct", "pass_accuracy", "press_rate", "threat_share"):
            out[f"{k.removesuffix('_pct')}_pct"] = None if w[k] is None else round(w[k] * 100)
        out["ppda"] = w["ppda"]
        window = f"last {int(seconds / 60)} min" if seconds else "match"
        return self._record("get_team_stats", args, _round({"team": s.team_name(tid), "window": window, **out}))

    def get_player_stats(self, player: Annotated[str, "Player name or part of it."]) -> dict[str, Any]:
        """One player's match so far: touches, passing, shots, goals, top speed, distance."""
        s = self.pipeline.state
        needle = player.strip().lower()
        matches = [pid for pid, p in s.players.items() if needle and needle in p["name"].lower()]
        if not matches:
            return self._record("get_player_stats", {"player": player}, {"error": f"no player matching '{player}'"})
        card = s.player_card(matches[0])
        card["team"] = s.team_name(card["team"])
        for key in ("pass_accuracy", "avg_pass_difficulty"):
            if card[key] is not None:
                card[key] = round(card[key] * 100) if key == "pass_accuracy" else round(card[key])
        card["pass_accuracy_pct"] = card.pop("pass_accuracy")
        card.pop("id", None)
        return self._record("get_player_stats", {"player": player}, _round(card))

    def list_key_moments(
        self,
        kind: Annotated[str | None, f"Optional filter. One of: {', '.join(MOMENT_KINDS)}."] = None,
        limit: Annotated[int, "Maximum number of moments (1-20)."] = 8,
    ) -> list[dict[str, Any]]:
        """Key moments detected so far, each with the facts and reason codes that explain why it mattered."""
        s = self.pipeline.state
        history = [i for i in self.pipeline.detector.history if i.kind != "recap" and (not kind or i.kind == kind)]
        top = sorted(history, key=lambda i: (-i.importance, i.t))[: max(1, min(20, int(limit)))]
        result = [
            {
                "minute": i.minute,
                "kind": i.kind,
                "team": s.team_name(i.team) if i.team else None,
                "why_it_matters": i.reasons,
                "facts": _round(i.facts),
            }
            for i in sorted(top, key=lambda i: i.t)
        ]
        return self._record("list_key_moments", {"kind": kind, "limit": limit}, result)
