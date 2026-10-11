import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from matchlens import server
from matchlens.pipeline import eventhub
from matchlens.pipeline.eventhub import LiveFeed, publish
from matchlens.synth import generate_match


@pytest.fixture(scope="module")
def match():
    meta, events, _ = generate_match(seed=898)
    return meta, events


def fake_source(items):
    async def source():
        for item in items:
            yield item
            await asyncio.sleep(0)

    return source


def test_publish_sends_every_event_in_order_under_one_run(match):
    meta, events = match
    sent = []

    async def send(items):
        sent.extend(items)

    run, n = asyncio.run(publish(events[:300], meta["match_id"], send, speed=10_000, start=0))
    assert n == 300 == len(sent)
    assert [json.loads(body)["id"] for body, _ in sent] == [e["id"] for e in events[:300]]
    assert {props["run"] for _, props in sent} == {run}
    assert {props["match_id"] for _, props in sent} == {meta["match_id"]}


def test_feed_keeps_the_current_run_and_drops_bad_events(match):
    meta, events = match
    feed = LiveFeed(meta["match_id"], fake_source([]))
    mine = {"match_id": meta["match_id"], "run": "r1"}
    feed.accept({"match_id": "other", "run": "x"}, events[0])  # another match on the hub
    for ev in events[:50]:
        feed.accept(mine, ev)
    feed.accept(mine, events[10])  # redelivered: out of order
    feed.accept(mine, {"id": 1, "t": 999})  # malformed
    assert len(feed.events) == 50 and feed.rejected == 2
    feed.accept({**mine, "run": "r2"}, events[0])  # a new kick-off replaces the old run
    assert feed.run == "r2" and len(feed.events) == 1


def test_follow_catches_up_then_streams_until_full_time(match):
    meta, events = match
    props = {"match_id": meta["match_id"], "run": "r1"}

    async def scenario():
        feed = LiveFeed(meta["match_id"], fake_source([(props, ev) for ev in events]))
        for ev in events[:100]:
            feed.accept(props, ev)
        backlog, q = feed.subscribe()
        for ev in events[100:]:
            feed.accept(props, ev)
        got = [ev async for _, batch in feed.follow(q, backlog[-1]["t"], tick_s=0) for ev in batch]
        return backlog, got, feed

    backlog, got, feed = asyncio.run(scenario())
    assert [e["id"] for e in backlog + got] == [e["id"] for e in events]
    assert got[-1]["type"] == "match_end" and not feed._followers


def test_follow_stops_at_a_new_kick_off(match):
    meta, events = match

    async def scenario():
        feed = LiveFeed(meta["match_id"], fake_source([]))
        feed.accept({"match_id": meta["match_id"], "run": "r1"}, events[0])
        _, q = feed.subscribe()
        feed.accept({"match_id": meta["match_id"], "run": "r1"}, events[1])
        feed.accept({"match_id": meta["match_id"], "run": "r2"}, events[0])
        return [b async for _, b in feed.follow(q, 0.0, tick_s=0)]

    assert [[e["id"] for e in b] for b in asyncio.run(scenario())] == [[events[1]["id"]]]


def test_a_viewer_waiting_for_kick_off_follows_the_first_run(match):
    meta, events = match

    async def scenario():
        feed = LiveFeed(meta["match_id"], fake_source([]))
        backlog, q = feed.subscribe()
        for ev in events:
            feed.accept({"match_id": meta["match_id"], "run": "r1"}, ev)
        return backlog, [e async for _, b in feed.follow(q, 0.0, tick_s=0) for e in b]

    backlog, got = asyncio.run(scenario())
    assert backlog == [] and len(got) == len(events)


def test_a_fresh_viewer_after_full_time_waits_for_the_next_run(match):
    meta, events = match

    async def scenario():
        feed = LiveFeed(meta["match_id"], fake_source([]))
        for ev in events:
            feed.accept({"match_id": meta["match_id"], "run": "r1"}, ev)
        _, q = feed.subscribe()
        for ev in events[:20]:
            feed.accept({"match_id": meta["match_id"], "run": "r2"}, ev)
        feed.accept({"match_id": meta["match_id"], "run": "r3"}, events[0])
        return [e["id"] async for _, b in feed.follow(q, 0.0, fresh=True, tick_s=0) for e in b]

    assert asyncio.run(scenario()) == [e["id"] for e in events[:20]]


@pytest.fixture()
def live_client(tmp_path, monkeypatch, match):
    meta, events = match
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    for var in ("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(server, "_match", None)
    props = {"match_id": meta["match_id"], "run": "r1"}
    monkeypatch.setattr(server, "_live", LiveFeed(meta["match_id"], fake_source([(props, ev) for ev in events])))
    return TestClient(server.app)


def test_live_stream_follows_the_hub_to_full_time(live_client):
    assert live_client.get("/api/match").json()["live"]["enabled"] is True
    kinds, last = [], None
    with live_client.stream("GET", "/api/stream", params={"source": "live"}) as r:
        for line in r.iter_lines():
            if line.startswith("event: "):
                kinds.append(line[7:])
            elif line.startswith("data: "):
                last = json.loads(line[6:])
    assert kinds[0] == "init" and kinds[-1] == "end"
    assert last["state"]["score"] == {"NBR": 1, "CAL": 2}


def test_live_stream_is_503_when_event_hubs_is_off(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "_match", None)
    monkeypatch.setattr(server, "_live", None)
    monkeypatch.delenv("EVENTHUB_NAMESPACE", raising=False)
    client = TestClient(server.app)
    assert client.get("/api/stream", params={"source": "live"}).status_code == 503
    assert client.get("/api/match").json()["live"] == {"enabled": False}


def test_settings_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("EVENTHUB_NAMESPACE", raising=False)
    assert eventhub.settings() is None
    monkeypatch.setenv("EVENTHUB_NAMESPACE", "ns.servicebus.windows.net")
    assert eventhub.settings() == {"namespace": "ns.servicebus.windows.net", "name": "match-events"}
