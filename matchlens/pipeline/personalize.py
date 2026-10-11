"""Stage 5 - Personalize: one set of insights, a different show per viewer.

`relevance` re-scores an insight for a profile and records *why* it was kept
(favorite player, favorite club, chosen metric). `Director` then paces the
overlay track so a casual fan is not buried under analyst-grade detail.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models import Insight, Profile

# 0 = anyone enjoys it, 1 = tactical, 2 = specialist detail
KIND_LEVEL = {
    "goal": 0,
    "big_chance": 0,
    "shot_speed": 0,
    "speed_alert": 0,
    "distance_alert": 0,
    "recap": 0,
    "press_shift": 1,
    "momentum_swing": 1,
    "rhythm_shift": 1,
    "line_breaking_pass": 2,
    "pass_milestone": 2,
}
SPECTACLE = {"shot_speed", "speed_alert", "distance_alert"}
FOCUS_KINDS = {
    "pressing": {"press_shift", "rhythm_shift", "momentum_swing"},
    "passing": {"line_breaking_pass", "pass_milestone"},
    "speed": {"speed_alert", "shot_speed", "distance_alert"},
    "shooting": {"goal", "big_chance", "shot_speed"},
}
THRESHOLD = {"analyst": 0.30, "fan": 0.45}
MIN_GAP_S = {"analyst": 20.0, "fan": 45.0}
DURATION_S = {"goal": 14.0, "recap": 40.0, "press_shift": 12.0, "momentum_swing": 12.0, "rhythm_shift": 12.0}
DEFAULT_DURATION_S = 8.0


def relevance(insight: Insight, profile: Profile) -> tuple[float, list[str]] | None:
    """Score an insight for one viewer, or None to leave it off their screen.

    Returns (score 0..1, tags explaining the personalization).
    """
    if insight.kind == "recap":
        return 1.0, []
    score = insight.importance
    tags: list[str] = []
    level = KIND_LEVEL.get(insight.kind, 1)
    has_player = bool(profile.favorite_player) and profile.favorite_player in insight.player_ids
    has_team = bool(profile.favorite_team) and profile.favorite_team == insight.team

    if profile.mode == "fan":
        if level == 2 and not has_player:
            return None
        if level == 1:
            score *= 0.95
        if insight.kind in SPECTACLE:
            score *= 1.3
    if has_team:
        score *= 1.25
        tags.append("favorite_team")
    if has_player:
        score = max(score * 1.7, 0.55)
        tags.append("favorite_player")
    if profile.focus_metric and insight.kind in FOCUS_KINDS.get(profile.focus_metric, set()):
        score *= 1.5
        tags.append(f"focus:{profile.focus_metric}")
    if profile.player_focus and profile.favorite_player:
        if not has_player and insight.kind != "goal" and insight.importance < 0.8:
            return None
    score = min(1.0, score)
    if score < THRESHOLD[profile.mode]:
        return None
    return round(score, 2), tags


@dataclass
class Slot:
    t_in: float
    t_out: float


class Director:
    """Decides when (and whether) a relevant insight goes to air for one viewer."""

    def __init__(self, profile: Profile) -> None:
        self.profile = profile
        self._busy_until = -1.0
        self._on_air_score = 0.0
        self.suppressed = 0

    def schedule(self, insight: Insight, score: float) -> Slot | None:
        duration = DURATION_S.get(insight.kind, DEFAULT_DURATION_S)
        t = insight.t
        free_at = self._busy_until + MIN_GAP_S[self.profile.mode]
        if t >= free_at or score >= 0.8 or score >= self._on_air_score + 0.2:
            t_in = t  # clear air, or important enough to cut in
        elif score >= 0.55 and free_at - t <= 30.0:
            t_in = free_at  # worth holding for a few seconds
        else:
            self.suppressed += 1
            return None
        self._busy_until = t_in + duration
        self._on_air_score = score
        return Slot(round(t_in, 2), round(t_in + duration, 2))
