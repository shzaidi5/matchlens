"""Shared data shapes used by every pipeline stage.

Events stay plain dicts (they arrive as JSON lines); everything the pipeline
*produces* is a dataclass so the contract between stages is explicit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PITCH_X = 105.0
PITCH_Y = 68.0
FINAL_THIRD_X = 70.0
BOX_X = 88.5
BOX_Y_MIN = 13.84
BOX_Y_MAX = 54.16

EVENT_TYPES = {
    "match_start",
    "period_start",
    "period_end",
    "match_end",
    "pass",
    "carry",
    "shot",
    "goal",
    "tackle",
    "interception",
    "pressure",
    "foul",
    "possession_change",
    "sprint",
    "physical_update",
    "substitution",
}


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    number: int
    position: str
    team: str
    base_x: float = 0.0
    base_y: float = 0.0
    pace: float = 31.0
    passing: float = 0.75
    finishing: float = 0.6
    tackling: float = 0.6

    def public(self) -> dict[str, Any]:
        """What a data provider would publish (no hidden simulation skills)."""
        return {
            "id": self.id,
            "name": self.name,
            "number": self.number,
            "position": self.position,
            "team": self.team,
        }


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    short: str
    color: str
    formation: str
    side: str  # "home" | "away"
    players: tuple[Player, ...] = ()
    bench: tuple[Player, ...] = ()

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "short": self.short,
            "color": self.color,
            "formation": self.formation,
            "side": self.side,
            "players": [p.public() for p in self.players],
            "bench": [p.public() for p in self.bench],
        }


@dataclass
class Insight:
    """A detected moment plus the evidence that makes it explainable."""

    id: str
    t: float
    period: int
    clock: str
    minute: str
    kind: str
    importance: float
    team: str | None = None
    player_ids: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    anchor: dict[str, float] | None = None
    source_event_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Narrative:
    headline: str
    body: str
    stats: list[dict[str, str]] = field(default_factory=list)
    source: str = "template"  # "template" | "agents"
    trace: dict[str, Any] | None = None  # how the agent desk produced it

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Profile:
    """One viewer. The same insights are re-ranked and re-voiced per profile."""

    id: str
    mode: str = "fan"  # "analyst" | "fan"
    lang: str = "en"
    favorite_team: str | None = None
    favorite_player: str | None = None
    player_focus: bool = False
    focus_metric: str | None = None  # pressing | passing | speed | shooting

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Profile":
        mode = d.get("mode", "fan")
        if mode not in ("analyst", "fan"):
            mode = "fan"
        return cls(
            id=str(d.get("id", "viewer")),
            mode=mode,
            lang=str(d.get("lang", "en")),
            favorite_team=d.get("favorite_team") or None,
            favorite_player=d.get("favorite_player") or None,
            player_focus=bool(d.get("player_focus", False)),
            focus_metric=d.get("focus_metric") or None,
        )


@dataclass
class Cue:
    """A timed, machine-readable overlay instruction for a graphics renderer."""

    id: str
    profile_id: str
    slot: str  # lower_third | stat_card | player_tag | recap
    t_in: float
    t_out: float
    clock: str
    priority: float
    lang: str
    audience: str
    kind: str
    payload: dict[str, Any]
    insight_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
