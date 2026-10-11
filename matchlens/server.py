"""HTTP API and live viewer.

    GET  /api/match      match metadata, capabilities
    GET  /api/stream     Server-Sent Events: live state + overlay cues per profile
                         (source=replay paces the recorded match; source=live follows Event Hubs)
    GET  /api/insights   every detected moment (replay markers)
    GET  /api/overlay    one profile's full overlay track as JSON or WebVTT
    POST /api/ask        ask-the-match agent (needs a Foundry or Azure OpenAI model)
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from .agents import AskAnalyst, EditorialDesk, NarrationCache, chat_client_from_env, describe_client
from .agents.desk import new_stats
from .i18n import LANGUAGE_NAMES, LANGUAGES
from .models import Cue, Profile
from .pipeline.explain import REASON_MEANINGS
from .pipeline.eventhub import LiveFeed, eventhub_source, settings as eventhub_settings
from .pipeline.ingest import load_match, replay
from .pipeline.orchestrator import NarrationJob, Pipeline, arun_match, run_match
from .pipeline.render import to_webvtt
from .synth import write_match

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MATCHLENS_DATA", ROOT / "data"))
MATCH_ID = os.getenv("MATCHLENS_MATCH", "sample")
WEB_DIR = ROOT / "web"
UI_EVENT_KEYS = ("id", "t", "type", "team", "player_id", "x", "y", "end_x", "end_y", "outcome")



@asynccontextmanager
async def lifespan(_app: FastAPI):
    feed = live_feed()
    if feed is not None:  # start consuming at boot so the first live viewer has the run so far
        feed.start()
    yield


app = FastAPI(title="MatchLens", version="0.3.0", lifespan=lifespan)


class Match:
    """Loaded once at startup; every stream gets its own Pipeline over it."""

    def __init__(self) -> None:
        if not (DATA_DIR / f"{MATCH_ID}.events.jsonl").exists():
            write_match(DATA_DIR, seed=int(os.getenv("MATCHLENS_SEED", "898")), name=MATCH_ID)
        self.meta, self.events = load_match(DATA_DIR, MATCH_ID)
        self.duration = self.events[-1]["t"]
        self.client = self._client()
        self.cache = NarrationCache(DATA_DIR / f"{MATCH_ID}.narration.json") if self.client else None
        self.desk_stats = new_stats()
        self.periods = [
            {"period": e["period"], "start_t": e["t"]} for e in self.events if e["type"] == "period_start"
        ]
        _, insights, _ = run_match(self.meta, self.events, [])
        self.markers = [
            {"id": i.id, "t": i.t, "minute": i.minute, "kind": i.kind, "team": i.team, "importance": i.importance}
            for i in insights
        ]

    @staticmethod
    def _client() -> Any | None:
        try:
            return chat_client_from_env()
        except Exception as exc:  # misconfigured credentials should not stop the demo
            print(f"[matchlens] agents disabled: {exc}")
            return None

    def pipeline(self, profiles: list[Profile]) -> Pipeline:
        pipe = Pipeline(self.meta, profiles)
        if self.client is not None:
            pipe.narrator = EditorialDesk(self.client, pipe, self.cache, self.desk_stats)
        return pipe


_match: Match | None = None


def match() -> Match:
    global _match
    if _match is None:
        _match = Match()
    return _match


_live: LiveFeed | None = None


def live_feed() -> LiveFeed | None:
    """The Event Hubs consumer, or None when EVENTHUB_NAMESPACE is unset."""
    global _live
    if _live is None and (cfg := eventhub_settings()) is not None:
        _live = LiveFeed(match().meta["match_id"], eventhub_source(cfg))
    return _live


def parse_profiles(raw: str | None) -> list[Profile]:
    if not raw:
        return [Profile(id="a", mode="analyst"), Profile(id="b", mode="fan")]
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        profiles = [Profile.from_dict(d) for d in data][:4]
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(400, f"profiles must be a JSON list of profile objects: {exc}") from exc
    if not profiles:
        raise HTTPException(400, "at least one profile is required")
    return profiles


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True}


@app.get("/api/match")
def get_match() -> dict[str, Any]:
    m = match()
    agents = m.client is not None
    return {
        **m.meta,
        "duration_t": m.duration,
        "periods": m.periods,
        "reason_meanings": REASON_MEANINGS,
        "agents_enabled": agents,
        "agent_backend": describe_client() if agents else None,
        "template_languages": sorted(LANGUAGES),
        "languages": LANGUAGE_NAMES if agents else {k: LANGUAGE_NAMES[k] for k in LANGUAGES},
        "live": {"enabled": True, **feed.status()} if (feed := live_feed()) else {"enabled": False},
    }


@app.get("/api/insights")
def get_insights() -> list[dict[str, Any]]:
    return match().markers


@app.get("/api/overlay")
async def get_overlay(profile: str | None = None, format: str = Query("json", pattern="^(json|vtt)$")) -> Any:
    m = match()
    profiles = parse_profiles(profile)[:1]
    pipe = m.pipeline(profiles)
    _, tracks = await arun_match(pipe, m.events)
    if m.cache:
        m.cache.save()
    cues = tracks[profiles[0].id]
    if format == "vtt":
        return PlainTextResponse(to_webvtt(cues), media_type="text/vtt")
    return JSONResponse({"match_id": m.meta["match_id"], "profile": profiles[0].__dict__, "cues": [c.to_dict() for c in cues]})


def _sse(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _late(cues: dict[str, list[Cue]], now: float) -> dict[str, list[Cue]]:
    """Agent copy that arrived after its slot opened airs now, for its full duration."""
    for items in cues.values():
        for cue in items:
            delay = max(0.0, now - cue.t_in)
            cue.t_in, cue.t_out = round(cue.t_in + delay, 2), round(cue.t_out + delay, 2)
    return cues


@app.get("/api/stream")
async def stream(
    profiles: str | None = None,
    speed: float = Query(30.0, ge=0.5, le=240.0),
    start: float = Query(0.0, ge=0.0),
    source: str = Query("replay", pattern="^(replay|live)$"),
) -> StreamingResponse:
    m = match()
    viewer_profiles = parse_profiles(profiles)
    feed = live_feed() if source == "live" else None
    if source == "live" and feed is None:
        raise HTTPException(503, "Live ingest is off. Set EVENTHUB_NAMESPACE (see .env.example).")

    async def generate():
        pipe = m.pipeline(viewer_profiles)
        desk = pipe.narrator if isinstance(pipe.narrator, EditorialDesk) else None
        if feed is not None:  # join the live run where it is: catch up silently, then follow
            feed.start()
            backlog, queue = feed.subscribe()
            if backlog and backlog[-1]["type"] == "match_end":
                backlog = []  # that match is over: wait for the next kick-off
            pipe.fast_forward(backlog, float("inf"))
            now = backlog[-1]["t"] if backlog else 0.0
            batches = feed.follow(queue, now, fresh=not backlog)
        else:
            skipped = pipe.fast_forward(m.events, start)
            now = start
            batches = replay(m.events[skipped:], speed=speed, start=start)
        yield _sse("init", {"state": pipe.state.snapshot(), "timeline": pipe.state.timeline, "start": now, "source": source})
        sent_points = len(pipe.state.timeline)
        writing: list[tuple[asyncio.Task, NarrationJob]] = []

        def collect(out: dict[str, list[dict[str, Any]]], cues: dict[str, list[Cue]]) -> None:
            for pid, items in cues.items():
                out.setdefault(pid, []).extend(c.to_dict() for c in items)

        def flush(out: dict[str, list[dict[str, Any]]], now: float) -> None:
            for task, job in [w for w in writing if w[0].done()]:
                writing.remove((task, job))
                narratives = pipe.narrate_sync(job) if task.cancelled() or task.exception() else task.result()
                collect(out, _late(pipe.finish(job, narratives), now))

        try:
            async for now, batch in batches:
                cues: dict[str, list[dict[str, Any]]] = {}
                insights: list[dict[str, Any]] = []
                for ev in batch:
                    step = pipe.plan(ev)
                    collect(cues, step.cues)
                    insights += [i.to_dict() for i in step.insights]
                    for job in step.jobs:
                        if desk is None or desk.cached(job.insight, [t.profile for t in job.targets]):
                            collect(cues, pipe.finish(job, pipe.narrate_sync(job)))
                        else:  # the agents write in the background; the match clock keeps running
                            writing.append((asyncio.create_task(pipe.narrate(job)), job))
                flush(cues, now)
                tick: dict[str, Any] = {"t": round(now, 2), "state": pipe.state.snapshot()}
                if batch:
                    tick["events"] = [{k: ev[k] for k in UI_EVENT_KEYS if k in ev} for ev in batch]
                if cues:
                    tick["cues"] = cues
                if insights:
                    tick["insights"] = insights
                if len(pipe.state.timeline) > sent_points:
                    tick["timeline"] = pipe.state.timeline[sent_points:]
                    sent_points = len(pipe.state.timeline)
                if desk is not None:
                    tick["agents"] = {**m.desk_stats, "writing": len(writing)}
                yield _sse("tick", tick)
            if writing:  # let the desk finish what it started (the full-time recap, usually)
                await asyncio.wait([t for t, _ in writing], timeout=60)
                late: dict[str, list[dict[str, Any]]] = {}
                flush(late, now)
                if late:
                    yield _sse("tick", {"t": round(now, 2), "state": pipe.state.snapshot(), "cues": late})
            yield _sse("end", {"state": pipe.state.snapshot()})
        finally:
            if feed is not None:
                feed.unsubscribe(queue)
            for task, _ in writing:
                task.cancel()
            if m.cache:
                m.cache.save()

    return StreamingResponse(
        generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


class Ask(BaseModel):
    question: str
    t: float = 0.0
    profile: dict[str, Any] = {}


@app.post("/api/ask")
async def ask(body: Ask) -> dict[str, Any]:
    m = match()
    if m.client is None:
        raise HTTPException(503, "Ask needs a model. Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL (see .env.example).")
    question = body.question.strip()
    if not question or len(question) > 500:
        raise HTTPException(400, "question must be 1-500 characters")
    profile = Profile.from_dict({"id": "ask", **body.profile})
    pipe = Pipeline(m.meta, [])
    pipe.fast_forward(m.events, body.t if body.t > 0 else m.duration + 1)
    try:
        return await AskAnalyst(m.client, pipe).ask(question, profile)
    except Exception as exc:
        raise HTTPException(502, f"model call failed: {type(exc).__name__}: {exc}") from exc


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")
