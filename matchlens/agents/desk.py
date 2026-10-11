"""The editorial desk: a multi-agent workflow that writes overlay copy.

Built on Microsoft Agent Framework. Three model-backed agents and one
deterministic checker, wired as a workflow graph:

    tactical_analyst ──► studio_writer ────┐
                    └──► fan_commentator ──┴──► fact_checker ──► accepted copy
                              ▲                      │
                              └──── revise (once) ───┘

* tactical_analyst looks at the detected moment, calls read-only match tools
  for context (the scorer's match so far, the last ten minutes, earlier
  moments) and writes a short brief on why it matters.
* studio_writer and fan_commentator turn that one brief into copy for their
  audience, in every language and supporter stance watching right now, in
  parallel.
* fact_checker is not a model. It rejects any draft that quotes a number the
  pipeline or a tool did not produce, runs over length, or uses analyst jargon
  in fan copy. A rejected draft goes back to its writer once with the reasons.

If a draft fails twice, a model call errors, or the desk times out, that
viewer gets the deterministic template copy instead. The show never waits.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agent_framework import Agent, Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from pydantic import BaseModel

from ..grounding import numbers_in, parse_json_object, ungrounded_numbers
from ..i18n import LANGUAGE_NAMES
from ..models import Insight, Narrative, Profile
from ..pipeline.explain import REASON_MEANINGS, TemplateNarrator, stance_for
from ..pipeline.orchestrator import Pipeline
from .tools import MatchTools

MAX_HEADLINE = 90
MAX_BODY = 480
FAN_JARGON = re.compile(r"\b(xG|PPDA)\b")

ANALYST_INSTRUCTIONS = """You are the tactical analyst on a live football broadcast desk.

You receive one detected match moment as JSON: its facts and the reason codes \
that say why it matters. Use your tools to check context that would sharpen the \
explanation, for example the player's match so far, a team's last ten minutes, \
or earlier key moments. Make at most three tool calls.

Then write a brief for the writers: two to four short sentences in English \
that say why this moment matters right now and which numbers prove it. Use only \
numbers from the moment JSON or from your tool results. No headline, no preamble."""

WRITER_RULES = """You receive one match moment as JSON: MOMENT (facts and reasons), \
ANALYST_BRIEF, the AUDIENCE to write for, and REFERENCE_COPY showing the intended \
meaning in English.

Rules:
- Write in the AUDIENCE language.
- Explain why the moment matters. Do not just restate the event.
- Never invent a number, name, minute or event. Every number you write must \
appear in MOMENT or ANALYST_BRIEF.
- Keep player and club names exactly as given.
- If AUDIENCE.supports is set, write from that supporter's side: share their \
excitement when the moment favours their club, their worry when it does not.
- If REVISION is present, your last draft was rejected. Fix every listed problem.
- Reply with a JSON object: {"headline": "...", "body": "..."}. Headline at most \
70 characters."""

STUDIO_INSTRUCTIONS = (
    "You write on-screen copy for the analyst desk of a live football broadcast. "
    "Your reader is tactically literate: use the numbers and the proper terms "
    "(xG, PPDA, press rate). Body: at most three sentences.\n\n" + WRITER_RULES
)
FAN_INSTRUCTIONS = (
    "You write on-screen copy for casual fans watching a live football match. "
    "Plain, warm, spoken language. No jargon: never write xG or PPDA. "
    "At most one number. Body: at most two sentences.\n\n" + WRITER_RULES
)


class Copy(BaseModel):
    headline: str
    body: str


# --------------------------------------------------------------- messages
@dataclass
class Audience:
    key: str
    mode: str
    lang: str
    supports: str | None  # club name the viewer supports
    stance: str | None  # "for" | "against" | None


@dataclass
class Assignment:
    insight: dict[str, Any]
    audiences: list[Audience]
    references: dict[str, dict[str, str]]


@dataclass
class Brief:
    assignment: Assignment
    text: str
    evidence: set[float] = field(default_factory=set)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Draft:
    audience: Audience
    brief: Brief
    headline: str = ""
    body: str = ""
    attempt: int = 0
    error: str | None = None


@dataclass
class Revision:
    previous: Draft
    problems: list[str]


@dataclass
class Accepted:
    key: str
    headline: str
    body: str
    revisions: int
    brief: str
    tool_calls: list[dict[str, Any]]


@dataclass
class Rejected:
    key: str
    problems: list[str]


# -------------------------------------------------------------- executors
class TacticalAnalyst(Executor):
    def __init__(self, agent: Agent, pipeline: Pipeline, stats: dict[str, int]) -> None:
        super().__init__(id="tactical_analyst")
        self.agent, self.pipeline, self.stats = agent, pipeline, stats

    @handler
    async def on_assignment(self, assignment: Assignment, ctx: WorkflowContext[Brief]) -> None:
        insight = assignment.insight
        tools = MatchTools(self.pipeline)
        moment = {
            "kind": insight["kind"],
            "minute": insight["minute"],
            "facts": insight["facts"],
            "reasons": {code: REASON_MEANINGS.get(code, code) for code in insight["reasons"]},
        }
        text = ""
        try:
            response = await self.agent.run(json.dumps(moment, ensure_ascii=False), tools=tools.as_tools())
            text = (response.text or "").strip()
        except Exception:  # the writers can work from the facts alone
            self.stats["errors"] += 1
        evidence = numbers_in(insight["facts"]) | numbers_in(insight["minute"]) | tools.evidence
        if text and ungrounded_numbers(text, evidence):
            self.stats["briefs_rejected"] += 1
            text = ""
        elif text:
            self.stats["briefs"] += 1
        self.stats["tool_calls"] += len(tools.calls)
        ctx.set_state("brief", text)
        await ctx.send_message(Brief(assignment, text, evidence, tools.calls))


class Writer(Executor):
    """One voice (analyst desk or fan). Writes every audience of its mode in parallel."""

    def __init__(self, id: str, agent: Agent, mode: str, stats: dict[str, int]) -> None:
        super().__init__(id=id)
        self.agent, self.mode, self.stats = agent, mode, stats

    @handler
    async def on_brief(self, brief: Brief, ctx: WorkflowContext[Draft]) -> None:
        mine = [a for a in brief.assignment.audiences if a.mode == self.mode]
        for draft in await asyncio.gather(*(self._write(brief, a, 0, None) for a in mine)):
            await ctx.send_message(draft)

    @handler
    async def on_revision(self, revision: Revision, ctx: WorkflowContext[Draft]) -> None:
        self.stats["revisions"] += 1
        previous = revision.previous
        await ctx.send_message(await self._write(previous.brief, previous.audience, previous.attempt + 1, revision))

    async def _write(self, brief: Brief, audience: Audience, attempt: int, revision: Revision | None) -> Draft:
        insight = brief.assignment.insight
        payload: dict[str, Any] = {
            "MOMENT": {
                "kind": insight["kind"],
                "minute": insight["minute"],
                "facts": insight["facts"],
                "reasons": {code: REASON_MEANINGS.get(code, code) for code in insight["reasons"]},
            },
            "ANALYST_BRIEF": brief.text or None,
            "AUDIENCE": {
                "language": LANGUAGE_NAMES.get(audience.lang, audience.lang),
                "type": audience.mode,
                "supports": audience.supports,
                "moment_favours_their_club": {"for": True, "against": False}.get(audience.stance or ""),
            },
            "REFERENCE_COPY": brief.assignment.references.get(audience.key),
        }
        if revision is not None:
            payload["REVISION"] = {
                "problems": revision.problems,
                "previous": {"headline": revision.previous.headline, "body": revision.previous.body},
            }
        draft = Draft(audience=audience, brief=brief, attempt=attempt)
        try:
            response = await self.agent.run(
                json.dumps(payload, ensure_ascii=False), options={"response_format": Copy}
            )
            data = parse_json_object(response.text)
            if not data or not isinstance(data.get("headline"), str) or not isinstance(data.get("body"), str):
                draft.error = "reply was not a JSON object with headline and body"
            else:
                draft.headline, draft.body = data["headline"].strip(), data["body"].strip()
        except Exception as exc:
            draft.error = f"model call failed: {type(exc).__name__}"
        return draft


class FactChecker(Executor):
    """Deterministic gate. One revision, then the template takes over."""

    def __init__(self, stats: dict[str, int]) -> None:
        super().__init__(id="fact_checker")
        self.stats = stats

    @staticmethod
    def problems(draft: Draft) -> list[str]:
        if draft.error:
            return [f"previous attempt failed: {draft.error}"]
        found: list[str] = []
        if not draft.headline or not draft.body:
            found.append("headline or body is empty")
        bad = ungrounded_numbers(f"{draft.headline} {draft.body}", draft.brief.evidence)
        if bad:
            found.append(f"numbers not in the facts: {', '.join(bad)}")
        if len(draft.headline) > MAX_HEADLINE:
            found.append(f"headline is longer than {MAX_HEADLINE} characters")
        if len(draft.body) > MAX_BODY:
            found.append(f"body is longer than {MAX_BODY} characters")
        if draft.audience.mode == "fan" and FAN_JARGON.search(f"{draft.headline} {draft.body}"):
            found.append("fan copy must not use xG or PPDA")
        return found

    @handler
    async def on_draft(self, draft: Draft, ctx: WorkflowContext[Revision, Accepted | Rejected]) -> None:
        problems = self.problems(draft)
        if not problems:
            self.stats["accepted"] += 1
            await ctx.yield_output(
                Accepted(draft.audience.key, draft.headline, draft.body, draft.attempt, draft.brief.text, draft.brief.tool_calls)
            )
        elif draft.attempt == 0:
            await ctx.send_message(Revision(draft, problems))
        else:
            self.stats["rejected"] += 1
            await ctx.yield_output(Rejected(draft.audience.key, problems))


# ------------------------------------------------------------------ cache
class NarrationCache:
    """Accepted copy keyed by (insight id, audience). A replay of the same match
    is then instant, and the cache file doubles as an audit log of what aired."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.items: dict[str, dict[str, Any]] = {}
        if self.path and self.path.exists():
            self.items = json.loads(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def key(insight_id: str, audience_key: str) -> str:
        return f"{insight_id}|{audience_key}"

    def get(self, insight_id: str, audience_key: str) -> dict[str, Any] | None:
        return self.items.get(self.key(insight_id, audience_key))

    def put(self, insight_id: str, audience_key: str, value: dict[str, Any]) -> None:
        self.items[self.key(insight_id, audience_key)] = value

    def save(self) -> None:
        if self.path:
            self.path.write_text(json.dumps(self.items, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------- desk
def new_stats() -> dict[str, int]:
    return {k: 0 for k in ("runs", "briefs", "briefs_rejected", "tool_calls", "accepted", "revisions",
                           "rejected", "errors", "timeouts", "cache_hits", "template_fallbacks")}


class EditorialDesk:
    """Narrator backed by the agent workflow. Drop-in for `TemplateNarrator`."""

    def __init__(
        self,
        client: Any,
        pipeline: Pipeline,
        cache: NarrationCache | None = None,
        stats: dict[str, int] | None = None,
        timeout_s: float = 45.0,
    ) -> None:
        self.pipeline = pipeline
        self.cache = cache if cache is not None else NarrationCache()
        self.stats = stats if stats is not None else new_stats()
        self.timeout_s = timeout_s
        self.templates = TemplateNarrator()
        self.analyst = Agent(client, ANALYST_INSTRUCTIONS, name="tactical_analyst")
        self.studio = Agent(client, STUDIO_INSTRUCTIONS, name="studio_writer")
        self.fan = Agent(client, FAN_INSTRUCTIONS, name="fan_commentator")

    def build_workflow(self) -> Workflow:
        """A fresh graph per run, so concurrent narration jobs never share run state."""
        analyst = TacticalAnalyst(self.analyst, self.pipeline, self.stats)
        studio = Writer("studio_writer", self.studio, "analyst", self.stats)
        fan = Writer("fan_commentator", self.fan, "fan", self.stats)
        checker = FactChecker(self.stats)
        return (
            WorkflowBuilder(start_executor=analyst, name="editorial_desk")
            .add_fan_out_edges(analyst, [studio, fan])
            .add_edge(studio, checker)
            .add_edge(fan, checker)
            .add_edge(checker, studio, condition=lambda m: isinstance(m, Revision) and m.previous.audience.mode == "analyst")
            .add_edge(checker, fan, condition=lambda m: isinstance(m, Revision) and m.previous.audience.mode == "fan")
            .build()
        )

    def audience_for(self, insight: Insight, profile: Profile) -> Audience:
        teams = self.pipeline.state.teams
        supports = teams[profile.favorite_team]["short"] if profile.favorite_team in teams else None
        return Audience(
            key=f"{profile.mode}|{profile.lang}|{profile.favorite_team or '-'}",
            mode=profile.mode,
            lang=profile.lang,
            supports=supports,
            stance=stance_for(insight, profile),
        )

    def _from_cache(self, insight: Insight, audience: Audience, template: Narrative) -> Narrative | None:
        hit = self.cache.get(insight.id, audience.key)
        if hit is None:
            return None
        return Narrative(hit["headline"], hit["body"], template.stats, "agents", hit.get("trace"))

    def narrate(self, insight: Insight, profile: Profile) -> Narrative:
        """Sync path: cached agent copy if there is any, otherwise the template."""
        template = self.templates.narrate(insight, profile)
        return self._from_cache(insight, self.audience_for(insight, profile), template) or template

    def cached(self, insight: Insight, profiles: list[Profile]) -> bool:
        return all(self.cache.get(insight.id, self.audience_for(insight, p).key) for p in profiles)

    async def anarrate_many(self, insight: Insight, profiles: list[Profile]) -> dict[str, Narrative]:
        templates = {p.id: self.templates.narrate(insight, p) for p in profiles}
        audiences = {p.id: self.audience_for(insight, p) for p in profiles}
        out: dict[str, Narrative] = {}
        todo: dict[str, Audience] = {}
        references: dict[str, dict[str, str]] = {}
        rejected: dict[str, list[str]] = {}
        for p in profiles:
            audience = audiences[p.id]
            hit = self._from_cache(insight, audience, templates[p.id])
            if hit:
                self.stats["cache_hits"] += 1
                out[p.id] = hit
            elif audience.key not in todo:
                todo[audience.key] = audience
                english = self.templates.narrate(
                    insight, Profile(id="ref", mode=p.mode, lang="en", favorite_team=p.favorite_team)
                )
                references[audience.key] = {"headline": english.headline, "body": english.body}
        if todo:
            assignment = Assignment(insight=insight.to_dict(), audiences=list(todo.values()), references=references)
            self.stats["runs"] += 1
            try:
                result = await asyncio.wait_for(self.build_workflow().run(assignment), self.timeout_s)
                outputs = result.get_outputs()
            except asyncio.TimeoutError:
                self.stats["timeouts"] += 1
                outputs = []
            except Exception:
                self.stats["errors"] += 1
                outputs = []
            for item in outputs:
                if isinstance(item, Accepted):
                    trace = {"brief": item.brief, "tools": item.tool_calls, "revisions": item.revisions}
                    self.cache.put(insight.id, item.key, {"headline": item.headline, "body": item.body, "trace": trace})
                elif isinstance(item, Rejected):
                    rejected[item.key] = item.problems
        for p in profiles:
            if p.id in out:
                continue
            hit = self._from_cache(insight, audiences[p.id], templates[p.id])
            if hit is None:
                self.stats["template_fallbacks"] += 1
                problems = rejected.get(audiences[p.id].key, ["the agent desk did not return copy in time"])
                hit = replace(templates[p.id], trace={"rejected": problems})
            out[p.id] = hit
        return out
