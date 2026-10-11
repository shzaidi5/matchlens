"""Stage 3b - Explain: say why a moment matters, for a given audience.

`TemplateNarrator` is the deterministic narrator: every sentence is a template
filled from the insight's facts and picked by its reason codes. It is also the
safety net for the agent desk in `matchlens.agents.desk`, which writes the
same copy with models and falls back to this whenever a draft fails its checks.
"""

from __future__ import annotations

from typing import Any

from ..i18n import has_language, render, tr
from ..models import Insight, Narrative, Profile

# Optional sentences tried after the base body (analyst only).
EXTRA_CLAUSES = {"goal": ["move", "assist"], "press_shift": ["ppda"], "rhythm_shift": ["before"]}
MAX_REASONS = {"analyst": 2, "fan": 2}

# (label key, value template). A chip is dropped if any fact it needs is None.
ANALYST_CHIPS: dict[str, list[tuple[str, str]]] = {
    "goal": [
        ("label.xg", "{xg}"),
        ("label.shot_speed", "{speed_kmh} km/h"),
        ("label.distance", "{distance_m} m"),
        ("label.buildup", "{sequence_passes} {unit_passes}"),
        ("label.threat_share_10", "{threat_share_pct}%"),
    ],
    "big_chance": [("label.xg", "{xg}"), ("label.distance", "{distance_m} m"), ("label.team_xg", "{team_xg}")],
    "shot_speed": [("label.shot_speed", "{speed_kmh} km/h"), ("label.xg", "{xg}"), ("label.distance", "{distance_m} m")],
    "line_breaking_pass": [
        ("label.difficulty", "{difficulty}/99"),
        ("label.pass_length", "{distance_m} m"),
        ("label.ball_speed", "{ball_speed_kmh} km/h"),
    ],
    "pass_milestone": [
        ("label.completed", "{completed}/{attempted}"),
        ("label.accuracy", "{accuracy_pct}%"),
        ("label.avg_difficulty", "{avg_difficulty}/99"),
    ],
    "speed_alert": [("label.top_speed", "{speed_kmh} km/h"), ("label.sprint", "{sprint_m} m")],
    "distance_alert": [("label.covered", "{distance_km} km")],
    "press_shift": [
        ("label.press_now", "{press_now_pct}%"),
        ("label.press_before", "{press_before_pct}%"),
        ("label.ppda", "{ppda_now}"),
        ("label.opp_accuracy", "{opp_accuracy_now_pct}%"),
    ],
    "momentum_swing": [
        ("label.threat_share_8", "{share_now_pct}%"),
        ("label.shots", "{shots}"),
        ("label.box_entries", "{box_entries}"),
    ],
    "rhythm_shift": [
        ("label.chaos", "{chaos_now}/100"),
        ("label.turnovers", "{turnovers_per_min_now}"),
        ("label.passes_per_poss", "{passes_per_possession_now}"),
    ],
}
FAN_CHIPS: dict[str, list[tuple[str, str]]] = {
    "speed_alert": [("label.top_speed", "{speed_kmh} km/h")],
    "shot_speed": [("label.shot_speed", "{speed_kmh} km/h")],
    "distance_alert": [("label.covered", "{distance_km} km")],
}

REASON_MEANINGS = {
    "completes_comeback": "the scoring team were at least two goals down and now lead",
    "late_drama": "scored after the 80th minute with one goal between the teams",
    "against_run_of_play": "the scoring team had created little threat in the previous 10 minutes",
    "had_been_coming": "the scoring team had created most of the threat in the previous 10 minutes",
    "press_paying_off": "the scoring team recently raised their pressing intensity",
    "high_regain": "the ball was won in the attacking half shortly before the goal",
    "patient_buildup": "the goal ended a long passing move",
    "direct_attack": "the attack went from the team's own half to goal very quickly",
    "super_sub": "the scorer came on as a substitute",
    "brace": "the scorer's second goal of the match",
    "low_xg_finish": "the chance was low quality, so the finish was exceptional",
    "big_chance_taken": "the chance was high quality",
    "keeper_stood_up": "the goalkeeper saved a high-quality chance",
    "wasteful": "the team has far more expected goals than goals",
    "chasing_game": "the team is behind on the scoreboard",
    "tested_keeper": "the shot was on target",
    "under_pressure": "the passer had a defender closing him down",
    "into_box": "the pass was received inside the penalty area",
    "long_range": "the pass travelled 30 metres or more",
    "metronome": "pass accuracy of at least 90 percent",
    "risk_taker": "a high average pass difficulty",
    "match_record": "fastest sprint of the match so far",
    "first_to_mark": "first player in the team to reach this distance",
    "opp_accuracy_dropped": "the opponent's pass accuracy fell after the press increased",
    "winning_ball_high": "more ball recoveries in the attacking half",
    "coincides_with_sub": "the shift started around a substitution by the same team",
    "protecting_lead": "the team is ahead",
    "fatigue": "it is late in the match",
    "sharp_turnaround": "the same team had a minority of the threat in the previous window",
    "sustained_pressure": "three or more shots in the window",
    "against_scoreline": "the team on top is behind on the scoreboard",
    "response_to_goal": "the team on top conceded in the last 10 minutes",
    "press_driven": "one team's pressing rate rose at the same time",
    "late_game": "after the 75th minute",
    "one_team_dictating": "one team has most of the ball at high accuracy",
}


def stance_for(insight: Insight, profile: Profile) -> str | None:
    if not profile.favorite_team or not insight.team:
        return None
    return "for" if insight.team == profile.favorite_team else "against"


class TemplateNarrator:
    def narrate(self, insight: Insight, profile: Profile) -> Narrative:
        lang = profile.lang if has_language(profile.lang) else "en"
        if insight.kind == "recap":
            return self._recap(insight, profile, lang)
        headline, sentences, facts = self._parts(insight, profile.mode, lang, stance_for(insight, profile))
        chips = (ANALYST_CHIPS if profile.mode == "analyst" else FAN_CHIPS).get(insight.kind, [])
        stats = []
        for label_key, template in chips:
            value = render(template, facts, lang)
            if value is not None:
                stats.append({"label": tr(lang, label_key) or label_key, "value": value})
        return Narrative(headline=headline, body=" ".join(sentences), stats=stats, source="template")

    def _parts(
        self, insight: Insight, mode: str, lang: str, stance: str | None
    ) -> tuple[str, list[str], dict[str, Any]]:
        kind = insight.kind
        facts = dict(insight.facts)
        facts["unit_passes"] = tr(lang, "unit.passes")
        variant = facts.get("direction") if kind == "press_shift" else facts.get("state") if kind == "rhythm_shift" else None
        prefix = f"{kind}.{variant}" if variant else kind
        if "outcome" in facts:
            facts["outcome_txt"] = tr(lang, f"outcome.{facts['outcome']}")
        if kind == "goal":
            facts["state"] = tr(lang, f"goal.state.{facts['state_change']}.{mode}", facts)
        if isinstance(facts.get("mark_km"), float) and facts["mark_km"].is_integer():
            facts["mark_km"] = int(facts["mark_km"])

        headline = None
        if stance:
            headline = tr(lang, f"{prefix}.{mode}.headline.{stance}", facts)
        headline = headline or tr(lang, f"{prefix}.{mode}.headline", facts) or kind

        sentences = [tr(lang, f"{prefix}.{mode}.body", facts)]
        if mode == "analyst":
            for clause in EXTRA_CLAUSES.get(kind, []):
                if clause == "move" and facts.get("sequence_passes", 0) < 2:
                    continue
                if clause == "ppda" and not (
                    facts.get("ppda_now") is not None
                    and facts.get("ppda_before") is not None
                    and facts["ppda_now"] < facts["ppda_before"]
                ):
                    continue
                sentences.append(tr(lang, f"{prefix}.{mode}.{clause}", facts))
        used = 0
        for code in insight.reasons:
            if used >= MAX_REASONS[mode]:
                break
            sentence = tr(lang, f"reason.{kind}.{code}.{mode}", facts)
            if sentence:
                sentences.append(sentence)
                used += 1
        return headline, [s for s in sentences if s], facts

    # ------------------------------------------------------------------ recap
    def _recap(self, insight: Insight, profile: Profile, lang: str) -> Narrative:
        f, mode = insight.facts, profile.mode
        home, away = f["teams"][f["home_id"]], f["teams"][f["away_id"]]
        leader = f.get("leader")
        base: dict[str, Any] = {
            "home_name": home["name"],
            "away_name": away["name"],
            "hg": home["goals"],
            "ag": away["goals"],
            "hxg": home["xg"],
            "axg": away["xg"],
            "hposs": home["possession_pct"],
            "aposs": away["possession_pct"],
            "hshots": home["shots"],
            "ashots": away["shots"],
        }
        if leader:
            win, lose = f["teams"][leader], f["teams"][f["away_id"] if leader == f["home_id"] else f["home_id"]]
            base.update(winner=win["name"], wg=win["goals"], lg=lose["goals"])
        full_time = f["phase"] == "full_time"
        sentences: list[str | None] = []

        fav = profile.favorite_team
        if mode == "fan" and fav in f["teams"]:
            if full_time:
                key = "draw" if not leader else "won" if leader == fav else "lost"
            else:
                key = "ht_level" if not leader else "ht_lead" if leader == fav else "ht_behind"
            sentences.append(tr(lang, f"recap.stance.{key}", {"fav": f["teams"][fav]["name"]}))

        if not leader:
            sentences.append(tr(lang, "recap.result.draw" if full_time else "recap.result.level_ht", base))
        elif not full_time:
            sentences.append(tr(lang, "recap.result.lead_ht", base))
        else:
            sentences.append(tr(lang, "recap.result.comeback" if f.get("comeback") else "recap.result.win", base))

        if f["scorers"]:
            scorer_list = ", ".join(f"{g['name']} {g['minute']}" for g in f["scorers"])
            sentences.append(tr(lang, "recap.scorers", {"scorer_list": scorer_list}))
        else:
            sentences.append(tr(lang, "recap.scorers.none"))

        if mode == "analyst":
            sentences.append(tr(lang, "recap.stats", base))

        tp = f.get("turning_point")
        if tp:
            turning = Insight(
                id=tp["insight_id"], t=insight.t, period=insight.period, clock=insight.clock,
                minute=tp["minute"], kind=tp["kind"], importance=0.0, team=tp["team"],
                facts=tp["facts"], reasons=tp["reasons"],
            )
            tp_headline, tp_sentences, _ = self._parts(turning, mode, lang, None)
            tp_text = tp_sentences[0] if mode == "analyst" and tp_sentences else tp_headline.rstrip(".!") + "."
            sentences.append(tr(lang, f"recap.turning.{mode}", {"tp_minute": tp["minute"], "tp_text": tp_text}))

        if f.get("standout"):
            sentences.append(tr(lang, f"recap.standout.{mode}", f["standout"]))

        rows = [("label.goals", "goals", ""), ("label.shots", "shots", ""), ("label.possession", "possession_pct", "%")]
        if mode == "analyst":
            rows = [
                ("label.xg", "xg", ""),
                ("label.shots", "shots", ""),
                ("label.shots_on_target", "shots_on_target", ""),
                ("label.possession", "possession_pct", "%"),
                ("label.pass_accuracy", "pass_accuracy_pct", "%"),
                ("label.ppda", "ppda", ""),
                ("label.high_regains", "high_recoveries", ""),
            ]
        table = [
            {
                "label": tr(lang, label) or label,
                "home": render("{v}" + unit, {"v": home[key]}, lang) or "-",
                "away": render("{v}" + unit, {"v": away[key]}, lang) or "-",
            }
            for label, key, unit in rows
        ]
        headline = tr(lang, f"recap.headline.{f['phase']}", base) or f["phase"]
        narrative = Narrative(headline=headline, body=" ".join(s for s in sentences if s), source="template")
        narrative.stats = table  # recap uses home/away rows instead of chips
        return narrative
