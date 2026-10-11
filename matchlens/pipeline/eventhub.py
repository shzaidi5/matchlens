"""Live ingest over Azure Event Hubs.

The producer (`python -m matchlens publish`) stands in for a data provider: it
sends a match's events paced by match time, keyed by match id so they stay in
order on one partition, and tags every message with a run id.

The server runs one `LiveFeed`: a background consumer that validates events,
keeps the current run in memory so late viewers can catch up, and fans each
event out to every live viewer. `follow` yields the same (match_time, events)
batches as `ingest.replay`, so the pipeline cannot tell the two apart.

Auth is Microsoft Entra ID (`DefaultAzureCredential`); the namespace has key
auth disabled.

    EVENTHUB_NAMESPACE   e.g. matchlens-eh-554.servicebus.windows.net
    EVENTHUB_NAME        default: match-events
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable

from .ingest import EventError, replay, validate_event

Message = tuple[str, dict[str, str]]  # (JSON body, application properties)
Source = Callable[[], AsyncIterator[tuple[dict[str, str], dict[str, Any]]]]


def settings() -> dict[str, str] | None:
    """Event Hubs settings from the environment, or None when live ingest is off."""
    namespace = os.getenv("EVENTHUB_NAMESPACE")
    if not namespace:
        return None
    return {"namespace": namespace, "name": os.getenv("EVENTHUB_NAME", "match-events")}


def messages(batch: Iterable[dict[str, Any]], match_id: str, run: str) -> list[Message]:
    return [(json.dumps(ev, ensure_ascii=False), {"match_id": match_id, "run": run}) for ev in batch]


async def publish(
    events: list[dict[str, Any]],
    match_id: str,
    send: Callable[[list[Message]], Awaitable[None]],
    speed: float = 30.0,
    start: float = 0.0,
) -> tuple[str, int]:
    """Send a match as it happens. Returns (run id, events sent)."""
    run, sent = uuid.uuid4().hex[:8], 0
    async for _, batch in replay(events, speed=speed, start=start):
        if batch:
            await send(messages(batch, match_id, run))
            sent += len(batch)
    return run, sent


class EventHubSender:
    """`send` for `publish`: one batch per tick, partition key = match id."""

    def __init__(self, cfg: dict[str, str], match_id: str) -> None:
        from azure.eventhub.aio import EventHubProducerClient
        from azure.identity.aio import DefaultAzureCredential

        self.credential = DefaultAzureCredential()
        self.client = EventHubProducerClient(cfg["namespace"], eventhub_name=cfg["name"], credential=self.credential)
        self.match_id = match_id

    async def __call__(self, items: list[Message]) -> None:
        from azure.eventhub import EventData

        batch = await self.client.create_batch(partition_key=self.match_id)
        for body, props in items:
            data = EventData(body)
            data.properties = props
            try:
                batch.add(data)
            except ValueError:  # batch full: send it and start another
                await self.client.send_batch(batch)
                batch = await self.client.create_batch(partition_key=self.match_id)
                batch.add(data)
        await self.client.send_batch(batch)

    async def close(self) -> None:
        await self.client.close()
        await self.credential.close()


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def eventhub_source(cfg: dict[str, str]) -> Source:
    """Every message in the hub, oldest first, so a restarted server rebuilds the current run."""

    async def source() -> AsyncIterator[tuple[dict[str, str], dict[str, Any]]]:
        from azure.eventhub.aio import EventHubConsumerClient
        from azure.identity.aio import DefaultAzureCredential

        inbox: asyncio.Queue = asyncio.Queue()

        async def on_event(_partition, event) -> None:
            if event is not None:
                props = {_text(k): _text(v) for k, v in (event.properties or {}).items()}
                inbox.put_nowait((props, json.loads(event.body_as_str())))

        async def on_error(_partition, error: Exception) -> None:  # the client retries on its own
            print(f"[matchlens] event hubs: {type(error).__name__}: {error}")

        credential = DefaultAzureCredential()
        client = EventHubConsumerClient(
            cfg["namespace"], eventhub_name=cfg["name"], consumer_group="$Default", credential=credential
        )
        receiving = asyncio.create_task(
            client.receive(on_event=on_event, on_error=on_error, starting_position="-1")
        )
        try:
            while True:
                getter = asyncio.create_task(inbox.get())
                done, _ = await asyncio.wait({getter, receiving}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    receiving.result()  # raises if the receiver died
                    return
                yield getter.result()
        finally:
            receiving.cancel()
            await client.close()
            await credential.close()

    return source


NEW_RUN = object()  # sent to followers when a new kick-off replaces the run they were watching


class LiveFeed:
    """One consumer per server, fanned out to any number of live viewers."""

    def __init__(self, match_id: str, source: Source) -> None:
        self.match_id = match_id
        self.source = source
        self.run: str | None = None
        self.events: list[dict[str, Any]] = []
        self.rejected = 0
        self.error: str | None = None
        self._followers: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self.error = None
            self._task = asyncio.create_task(self._pump())

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._task is not None and not self._task.done(),
            "run": self.run,
            "events": len(self.events),
            "last_t": self.events[-1]["t"] if self.events else None,
            "ended": self.ended,
            "rejected": self.rejected,
            "error": self.error,
        }

    @property
    def ended(self) -> bool:
        return bool(self.events) and self.events[-1]["type"] == "match_end"

    async def _pump(self) -> None:
        try:
            async for props, raw in self.source():
                self.accept(props, raw)
        except Exception as exc:  # surfaced in /api/match; a viewer reconnect restarts the consumer
            self.error = f"{type(exc).__name__}: {exc}"

    def accept(self, props: dict[str, str], raw: dict[str, Any]) -> None:
        if props.get("match_id") != self.match_id:
            return
        if props.get("run") != self.run:
            if self.events:  # viewers waiting for a first kick-off simply keep following
                for q in self._followers:
                    q.put_nowait(NEW_RUN)
            self.run, self.events = props.get("run"), []
        try:
            ev = validate_event(raw)
        except EventError:
            self.rejected += 1
            return
        if self.events and ev["t"] < self.events[-1]["t"]:
            self.rejected += 1  # duplicate or out of order: the hub redelivers on reconnect
            return
        self.events.append(ev)
        for q in self._followers:
            q.put_nowait(ev)

    def subscribe(self) -> tuple[list[dict[str, Any]], asyncio.Queue]:
        """The run so far, plus a queue of everything after it. No await between the two."""
        q: asyncio.Queue = asyncio.Queue()
        self._followers.add(q)
        return list(self.events), q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._followers.discard(q)

    async def follow(
        self, q: asyncio.Queue, now: float, fresh: bool = False, tick_s: float = 0.25
    ) -> AsyncIterator[tuple[float, list[dict[str, Any]]]]:
        """Yield (match_time, events) every tick until full time or a new kick-off.

        A `fresh` follower has seen nothing of the current run, so it waits for
        the next kick-off instead of stopping at it.
        """
        ended = False
        try:
            while not ended:
                await asyncio.sleep(tick_s)
                batch = []
                while not q.empty():
                    item = q.get_nowait()
                    if item is NEW_RUN:
                        if fresh:
                            continue
                        if batch:
                            yield now, batch
                        return
                    fresh = False
                    batch.append(item)
                    now = max(now, item["t"])
                    ended = item["type"] == "match_end"
                yield now, batch
        finally:
            self.unsubscribe(q)
