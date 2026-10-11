"""Command line: generate data, run the pipeline, export overlays, serve the viewer.

    python -m matchlens generate --seed 898 --name sample
    python -m matchlens run --profile analyst:en --profile fan:es:CAL
    python -m matchlens export --profile fan:es:CAL --format vtt > fan.vtt
    python -m matchlens check                 # one live run of the agent desk
    python -m matchlens warm                  # pre-write agent copy for the demo profiles
    python -m matchlens serve
    python -m matchlens publish --speed 30    # send the match to Event Hubs as it happens
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from .models import Profile
from .pipeline.ingest import load_match
from .pipeline.orchestrator import Pipeline, arun_match
from .pipeline.render import to_webvtt
from .synth import write_match

ROOT = Path(__file__).resolve().parent.parent
DEMO_PROFILES = ["analyst:en", "fan:es:CAL", "fan:en:NBR:NBR-10:focus"]


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env without overriding real environment variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_profile(spec: str, index: int = 0) -> Profile:
    """mode[:lang[:favorite_team[:favorite_player[:focus]]]], e.g. fan:es:CAL:CAL-10:focus"""
    parts = spec.split(":")
    return Profile(
        id=f"p{index}",
        mode=parts[0] if parts[0] in ("analyst", "fan") else "fan",
        lang=parts[1] if len(parts) > 1 and parts[1] else "en",
        favorite_team=parts[2] if len(parts) > 2 and parts[2] else None,
        favorite_player=parts[3] if len(parts) > 3 and parts[3] else None,
        player_focus=len(parts) > 4 and parts[4] == "focus",
    )


def _desk_pipeline(meta: dict, profiles: list[Profile], cache_path: Path | None):
    """A pipeline narrated by the agent desk. Exits with guidance if no model is configured."""
    from .agents import EditorialDesk, NarrationCache, chat_client_from_env, describe_client

    client = chat_client_from_env()
    if client is None:
        sys.exit("No model configured. Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL in .env (see .env.example).")
    print(f"Agent backend: {describe_client()}", file=sys.stderr)
    pipeline = Pipeline(meta, profiles)
    pipeline.narrator = EditorialDesk(client, pipeline, NarrationCache(cache_path))
    return pipeline


async def _check(meta: dict, events: list[dict]) -> int:
    """Run the desk once, live, on the first big tactical moment. Proves auth, model and tools work."""
    profiles = [parse_profile("analyst:en", 0), parse_profile("fan:es:CAL", 1)]
    pipeline = _desk_pipeline(meta, profiles, None)
    desk = pipeline.narrator
    for ev in events:
        step = pipeline.plan(ev)
        job = next((j for j in step.jobs if j.insight.kind in ("press_shift", "goal") and len(j.targets) == 2), None)
        if job is None:
            continue
        print(f"Moment: {job.insight.kind} at {job.insight.minute}. Asking the desk...", file=sys.stderr)
        started = time.perf_counter()
        narratives = await pipeline.narrate(job)
        elapsed = time.perf_counter() - started
        for target in job.targets:
            n = narratives[target.profile.id]
            print(f"\n[{target.profile.mode}/{target.profile.lang}] written by: {n.source}")
            print(f"  {n.headline}\n  {n.body}")
            if n.trace:
                print(f"  trace: {json.dumps(n.trace, ensure_ascii=False)}")
        print(f"\n{elapsed:.1f}s. Desk stats: {desk.stats}")
        ok = all(n.source == "agents" for n in narratives.values())
        print("RESULT: agents are live." if ok else "RESULT: fell back to templates. See trace and stats above.")
        return 0 if ok else 1
    print("No suitable moment found in this match.")
    return 1


async def _publish(meta: dict, events: list[dict], speed: float, start: float) -> None:
    from .pipeline.eventhub import EventHubSender, publish, settings

    cfg = settings()
    if cfg is None:
        sys.exit("Live ingest is off. Set EVENTHUB_NAMESPACE in .env (see .env.example).")
    sender = EventHubSender(cfg, meta["match_id"])
    print(f"Publishing {meta['match_id']} to {cfg['namespace']}/{cfg['name']} at {speed}x. Ctrl+C to stop.", file=sys.stderr)
    try:
        run, sent = await publish(events, meta["match_id"], sender, speed=speed, start=start)
    finally:
        await sender.close()
    print(f"Run {run}: sent {sent} events.")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="matchlens", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="write a synthetic match")
    g.add_argument("--seed", type=int, default=898)
    g.add_argument("--out", default=str(ROOT / "data"))
    g.add_argument("--name", default=None, help="file stem (default: match id)")

    for name, help_ in (
        ("run", "print each viewer's overlay copy"),
        ("export", "write one overlay track"),
        ("warm", "pre-write agent copy so replays are instant"),
        ("check", "one live run of the agent desk"),
        ("publish", "send a match to Event Hubs, paced like a live feed"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--data", default=str(ROOT / "data"))
        p.add_argument("--match", default="sample")
        if name in ("run", "export", "warm"):
            p.add_argument("--profile", action="append", help=parse_profile.__doc__)
        if name in ("run", "export"):
            p.add_argument("--agents", action="store_true", help="narrate with the agent desk instead of templates")
        if name == "export":
            p.add_argument("--format", choices=("json", "vtt"), default="json")
        if name == "publish":
            p.add_argument("--speed", type=float, default=30.0, help="match seconds per real second")
            p.add_argument("--start", type=float, default=0.0, help="match time to start from (seconds)")

    s = sub.add_parser("serve", help="start the API and live viewer")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    if args.cmd == "generate":
        for kind, path in write_match(args.out, args.seed, args.name).items():
            print(f"{kind:>6}: {path}")
        return
    if args.cmd == "serve":
        import uvicorn

        uvicorn.run("matchlens.server:app", host=args.host, port=args.port)
        return

    meta, events = load_match(args.data, args.match)
    if args.cmd == "check":
        sys.exit(asyncio.run(_check(meta, events)))
    if args.cmd == "publish":
        try:
            asyncio.run(_publish(meta, events, args.speed, args.start))
        except KeyboardInterrupt:
            print("Stopped.", file=sys.stderr)
        return

    default = DEMO_PROFILES if args.cmd == "warm" else ["analyst:en", "fan:en"]
    profiles = [parse_profile(spec, i) for i, spec in enumerate(args.profile or default)]
    cache_path = Path(args.data) / f"{args.match}.narration.json"
    use_agents = args.cmd == "warm" or args.agents
    pipeline = _desk_pipeline(meta, profiles, cache_path) if use_agents else Pipeline(meta, profiles)
    started = time.perf_counter()
    insights, tracks = asyncio.run(arun_match(pipeline, events))
    if use_agents:
        pipeline.narrator.cache.save()

    if args.cmd == "warm":
        print(f"{len(pipeline.narrator.cache.items)} pieces of agent copy in {cache_path} "
              f"({time.perf_counter() - started:.0f}s). Desk stats: {pipeline.narrator.stats}")
        return
    if args.cmd == "export":
        cues = tracks[profiles[0].id]
        if args.format == "vtt":
            print(to_webvtt(cues))
        else:
            print(json.dumps([c.to_dict() for c in cues], indent=2, ensure_ascii=False))
        return
    print(f"{len(events)} events, {len(insights)} insights detected\n")
    for profile in profiles:
        cues = [c for c in tracks[profile.id] if c.slot in ("lower_third", "recap")]
        print(f"== {profile.mode} / {profile.lang} / team={profile.favorite_team} / player={profile.favorite_player} ({len(cues)} on-air moments)")
        for c in cues:
            print(f"  {c.payload['minute']:>6}  {c.payload['headline']}")
            print(f"          {c.payload['body']}")
        print()
    if use_agents:
        print("Agent desk:", pipeline.narrator.stats)


if __name__ == "__main__":
    main()
