"""Stage 4 - Render: turn narratives into timed, machine-readable overlay cues.

A cue says *what* to draw, in *which* slot, *when* (match seconds) and for
*whom*. It carries no styling: a graphics engine, a web player or a studio
tool can each render the same track. `schemas/overlay_cue.schema.json` is the
contract; `to_webvtt` gives a subtitle-style export for players that take VTT.
"""

from __future__ import annotations

import json
from typing import Any

from ..i18n import fmt_value, has_language, tr
from ..models import Cue, Insight, Narrative, Profile
from .interpret import MatchState
from .personalize import Slot

TAG_KINDS = {"goal", "big_chance", "shot_speed", "line_breaking_pass", "speed_alert", "pass_milestone"}
FOCUS_EVENTS = {"pass", "carry", "shot", "tackle", "interception", "sprint"}
TAG_METRIC = {
    "goal": ("label.xg", "xg", ""),
    "big_chance": ("label.xg", "xg", ""),
    "shot_speed": ("label.shot_speed", "speed_kmh", " km/h"),
    "line_breaking_pass": ("label.difficulty", "difficulty", "/99"),
    "speed_alert": ("label.top_speed", "speed_kmh", " km/h"),
    "pass_milestone": ("label.completed", "completed", ""),
}


class CueBuilder:
    def __init__(self, state: MatchState) -> None:
        self.state = state
        self._n = 0
        self._last_focus_t: dict[str, float] = {}

    def _id(self, profile: Profile) -> str:
        self._n += 1
        return f"cue-{profile.id}-{self._n:04d}"

    def _team(self, team_id: str | None) -> dict[str, Any] | None:
        team = self.state.teams.get(team_id or "")
        if not team:
            return None
        return {"id": team["id"], "name": team["short"], "color": team["color"]}

    def _player(self, player_id: str | None) -> dict[str, Any] | None:
        p = self.state.players.get(player_id or "")
        if not p:
            return None
        return {"id": p["id"], "name": p["name"], "number": p["number"], "team": p["team"]}

    def for_insight(
        self,
        insight: Insight,
        narrative: Narrative,
        profile: Profile,
        slot: Slot,
        score: float,
        why_shown: list[str],
    ) -> list[Cue]:
        lang = profile.lang if narrative.source == "agents" or has_language(profile.lang) else "en"
        common = {
            "profile_id": profile.id,
            "t_in": slot.t_in,
            "t_out": slot.t_out,
            "clock": insight.clock,
            "priority": score,
            "lang": lang,
            "audience": profile.mode,
            "kind": insight.kind,
            "insight_id": insight.id,
        }
        base_payload = {
            "minute": insight.minute,
            "team": self._team(insight.team),
            "why_shown": why_shown,
            "source": narrative.source,
        }
        if narrative.trace:
            base_payload["trace"] = narrative.trace
        if insight.kind == "recap":
            payload = {
                **base_payload,
                "headline": narrative.headline,
                "body": narrative.body,
                "phase": insight.facts["phase"],
                "table": narrative.stats,
                "home": self._team(insight.facts["home_id"]),
                "away": self._team(insight.facts["away_id"]),
                "scorers": insight.facts["scorers"],
            }
            return [Cue(id=self._id(profile), slot="recap", payload=payload, **common)]

        player = self._player(insight.player_ids[0]) if insight.player_ids else None
        cues = [
            Cue(
                id=self._id(profile),
                slot="lower_third",
                payload={**base_payload, "headline": narrative.headline, "body": narrative.body, "player": player,
                         "stats": narrative.stats if profile.mode == "fan" else []},
                **common,
            )
        ]
        if profile.mode == "analyst" and narrative.stats:
            cues.append(
                Cue(
                    id=self._id(profile),
                    slot="stat_card",
                    payload={**base_payload, "title": narrative.headline, "stats": narrative.stats},
                    **common,
                )
            )
        if insight.kind in TAG_KINDS and player and insight.anchor:
            label_key, fact, unit = TAG_METRIC[insight.kind]
            value = insight.facts.get(fact)
            metric = None
            if value is not None:
                metric = {"label": tr(lang, label_key) or label_key, "value": fmt_value(value, lang) + unit}
            cues.append(
                Cue(
                    id=self._id(profile),
                    slot="player_tag",
                    payload={**base_payload, "player": player, "anchor": insight.anchor, "stats": [metric] if metric else []},
                    **{**common, "t_out": min(slot.t_out, slot.t_in + 8.0)},
                )
            )
        return cues

    def for_focus_player(self, ev: dict[str, Any], profile: Profile) -> Cue | None:
        """Follow-cam tag for player-focus mode: fires whenever the viewer's
        chosen player is on the ball, with a card sized to the audience."""
        pid = profile.favorite_player
        if not (profile.player_focus and pid and ev.get("player_id") == pid and ev["type"] in FOCUS_EVENTS):
            return None
        if "x" not in ev or ev["t"] - self._last_focus_t.get(profile.id, -1e9) < 6.0:
            return None
        self._last_focus_t[profile.id] = ev["t"]
        lang = profile.lang if has_language(profile.lang) else "en"
        card = self.state.player_card(pid)

        def chip(label: str, value: Any, unit: str = "") -> dict[str, str]:
            return {"label": tr(lang, label) or label, "value": fmt_value(value, lang) + unit}

        stats = [chip("label.touches", card["touches"]), chip("label.passes", f"{card['passes_completed']}/{card['passes']}")]
        if profile.mode == "analyst":
            if card["avg_pass_difficulty"] is not None:
                stats.append(chip("label.avg_difficulty", round(card["avg_pass_difficulty"]), "/99"))
            if card["top_speed_kmh"]:
                stats.append(chip("label.top_speed", card["top_speed_kmh"], " km/h"))
            if card["distance_km"]:
                stats.append(chip("label.covered", card["distance_km"], " km"))
        elif card["goals"]:
            stats.append(chip("label.goals", card["goals"]))
        x, y = (ev.get("end_x"), ev.get("end_y")) if ev["type"] == "carry" else (ev["x"], ev["y"])
        return Cue(
            id=self._id(profile),
            profile_id=profile.id,
            slot="player_tag",
            t_in=ev["t"],
            t_out=round(ev["t"] + 5.0, 2),
            clock=ev["clock"],
            priority=0.4,
            lang=lang,
            audience=profile.mode,
            kind="player_focus",
            payload={
                "minute": ev["minute"],
                "team": self._team(ev.get("team")),
                "player": self._player(pid),
                "anchor": {"x": x, "y": y},
                "stats": stats,
                "why_shown": ["player_focus"],
                "source": "template",
            },
        )


def _vtt_time(seconds: float) -> str:
    h, rem = divmod(max(0.0, seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:06.3f}"


def to_webvtt(cues: list[Cue]) -> str:
    """Overlay track as WebVTT. Each cue body is the JSON payload, the pattern
    web players use for timed metadata tracks."""
    lines = ["WEBVTT", ""]
    for cue in sorted(cues, key=lambda c: (c.t_in, c.id)):
        lines += [
            cue.id,
            f"{_vtt_time(cue.t_in)} --> {_vtt_time(cue.t_out)}",
            json.dumps({"slot": cue.slot, "kind": cue.kind, "lang": cue.lang, **cue.payload}, ensure_ascii=False),
            "",
        ]
    return "\n".join(lines)
