"""The five-stage pipeline, wired together.

    ingest -> interpret -> detect -> personalize -> explain -> render

A step is split in two so narration never blocks the match clock:

* `plan(event)` runs everything deterministic and returns the narration jobs
  it created (one per insight that at least one viewer will see).
* `narrate(job)` writes the copy, with templates or with the agent desk, and
  `finish(job, narratives)` turns it into overlay cues.

`process` (sync) and `aprocess` (async) do both in one call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from ..models import Cue, Insight, Narrative, Profile
from .explain import TemplateNarrator
from .ingest import validate_event
from .interpret import MatchState
from .moments import MomentDetector, Thresholds
from .personalize import Director, Slot, relevance
from .render import CueBuilder


class Narrator(Protocol):
    def narrate(self, insight: Insight, profile: Profile) -> Narrative: ...


@dataclass
class Target:
    """One viewer an insight will be shown to, and when."""

    profile: Profile
    score: float
    why_shown: list[str]
    slot: Slot


@dataclass
class NarrationJob:
    insight: Insight
    targets: list[Target]


@dataclass
class StepResult:
    event: dict[str, Any]
    insights: list[Insight] = field(default_factory=list)
    cues: dict[str, list[Cue]] = field(default_factory=dict)
    jobs: list[NarrationJob] = field(default_factory=list)


class Pipeline:
    def __init__(
        self,
        meta: dict[str, Any],
        profiles: Iterable[Profile] = (),
        narrator: Narrator | None = None,
        thresholds: Thresholds | None = None,
    ) -> None:
        self.state = MatchState(meta)
        self.detector = MomentDetector(self.state, thresholds)
        self.narrator: Narrator = narrator or TemplateNarrator()
        self.builder = CueBuilder(self.state)
        self.profiles: list[Profile] = []
        self.directors: dict[str, Director] = {}
        self.set_profiles(profiles)

    def set_profiles(self, profiles: Iterable[Profile]) -> None:
        self.profiles = list(profiles)
        self.directors = {p.id: Director(p) for p in self.profiles}

    # ------------------------------------------------------------ plan/finish
    def plan(self, raw: dict[str, Any], render: bool = True) -> StepResult:
        """Ingest, interpret, detect and personalize one event.

        `render=False` updates state and detectors only. Used to fast-forward
        to a seek point without scheduling moments nobody will see.
        """
        event = self.state.apply(validate_event(raw))  # ingest + interpret
        insights = self.detector.process(event)  # detect
        result = StepResult(event=event, insights=insights)
        if not render:
            return result
        for profile in self.profiles:
            focus = self.builder.for_focus_player(event, profile)
            if focus:
                result.cues.setdefault(profile.id, []).append(focus)
        for insight in insights:
            targets = []
            for profile in self.profiles:
                scored = relevance(insight, profile)  # personalize
                if scored is None:
                    continue
                slot = self.directors[profile.id].schedule(insight, scored[0])
                if slot is not None:
                    targets.append(Target(profile, scored[0], scored[1], slot))
            if targets:
                result.jobs.append(NarrationJob(insight, targets))
        return result

    def finish(self, job: NarrationJob, narratives: dict[str, Narrative]) -> dict[str, list[Cue]]:
        """Render a narrated job into cues, keyed by profile id."""
        cues: dict[str, list[Cue]] = {}
        for target in job.targets:
            narrative = narratives.get(target.profile.id)
            if narrative is None:
                continue
            cues[target.profile.id] = self.builder.for_insight(
                job.insight, narrative, target.profile, target.slot, target.score, target.why_shown
            )
        return cues

    # ---------------------------------------------------------------- explain
    def narrate_sync(self, job: NarrationJob) -> dict[str, Narrative]:
        return {t.profile.id: self.narrator.narrate(job.insight, t.profile) for t in job.targets}

    async def narrate(self, job: NarrationJob) -> dict[str, Narrative]:
        """Use the narrator's async multi-audience path when it has one."""
        many = getattr(self.narrator, "anarrate_many", None)
        if many is None:
            return self.narrate_sync(job)
        return await many(job.insight, [t.profile for t in job.targets])

    # --------------------------------------------------------------- one call
    @staticmethod
    def _merge(result: StepResult, cues: dict[str, list[Cue]]) -> None:
        for pid, items in cues.items():
            result.cues.setdefault(pid, []).extend(items)

    def process(self, raw: dict[str, Any], render: bool = True) -> StepResult:
        result = self.plan(raw, render)
        for job in result.jobs:
            self._merge(result, self.finish(job, self.narrate_sync(job)))
        return result

    async def aprocess(self, raw: dict[str, Any], render: bool = True) -> StepResult:
        result = self.plan(raw, render)
        for job in result.jobs:
            self._merge(result, self.finish(job, await self.narrate(job)))
        return result

    def fast_forward(self, events: Iterable[dict[str, Any]], until_t: float) -> int:
        """Apply every event with t < until_t without rendering. Returns the count."""
        n = 0
        for ev in events:
            if ev["t"] >= until_t:
                break
            self.plan(ev, render=False)
            n += 1
        return n


def _collect(pipeline: Pipeline, step: StepResult, insights: list[Insight], tracks: dict[str, list[Cue]]) -> None:
    insights += step.insights
    for pid, cues in step.cues.items():
        tracks[pid] += cues


def run_match(
    meta: dict[str, Any],
    events: Iterable[dict[str, Any]],
    profiles: Iterable[Profile],
    narrator: Narrator | None = None,
) -> tuple[Pipeline, list[Insight], dict[str, list[Cue]]]:
    """Batch helper: run a whole match and collect insights and overlay tracks."""
    pipeline = Pipeline(meta, profiles, narrator)
    insights: list[Insight] = []
    tracks: dict[str, list[Cue]] = {p.id: [] for p in pipeline.profiles}
    for ev in events:
        _collect(pipeline, pipeline.process(ev), insights, tracks)
    return pipeline, insights, tracks


async def arun_match(pipeline: Pipeline, events: Iterable[dict[str, Any]]) -> tuple[list[Insight], dict[str, list[Cue]]]:
    """Async batch helper for narrators that call models."""
    insights: list[Insight] = []
    tracks: dict[str, list[Cue]] = {p.id: [] for p in pipeline.profiles}
    for ev in events:
        _collect(pipeline, await pipeline.aprocess(ev), insights, tracks)
    return insights, tracks
