"""The Agent Framework layer, driven by a scripted chat client (no network)."""

import asyncio
import json

import pytest
from fakes import ScriptedChat, call, last_kinds, payload_of

from matchlens import server
from matchlens.agents import AskAnalyst, EditorialDesk, MatchTools, NarrationCache
from matchlens.agents.desk import Audience, Brief, Draft, FactChecker
from matchlens.models import Profile
from matchlens.pipeline.orchestrator import Pipeline, arun_match

ANALYST = Profile(id="a", mode="analyst", lang="en")
FAN_ES = Profile(id="b", mode="fan", lang="es", favorite_team="CAL")
FAN_FR = Profile(id="c", mode="fan", lang="fr")


@pytest.fixture()
def at_press_shift(match):
    """A pipeline advanced to the 63' pressing shift, and that insight."""
    meta, events, _ = match
    pipeline = Pipeline(meta, [ANALYST, FAN_ES, FAN_FR])
    for ev in events:
        for insight in pipeline.plan(ev, render=False).insights:
            if insight.kind == "press_shift" and insight.facts["direction"] == "up" and insight.minute.startswith("6"):
                return pipeline, insight
    raise AssertionError("no press shift in the sample match")


def honest_script(agent, messages):
    """Every agent behaves: one tool call, grounded brief, grounded copy."""
    if agent == "analyst":
        if "function_result" in last_kinds(messages):
            return "Calderwick have changed the game: Northbridge can no longer play out."
        return call("get_team_stats", team="Northbridge", last_minutes=10)
    if agent == "ask":
        return "Calderwick are on top."
    p = payload_of(messages)
    if agent == "studio":
        pct = p["MOMENT"]["facts"].get("press_now_pct")
        body = f"Press rate now {pct}%." if pct is not None else f"A key moment at {p['MOMENT']['minute']}."
        return {"headline": "Calderwick turn the screw", "body": body}
    return {"headline": f"[{p['AUDIENCE']['language']}] game on", "body": "They are hunting the ball now."}


def test_match_tools(run):
    tools = MatchTools(run[0])
    assert tools.get_match_state()["score"] == {"Northbridge": 1, "Calderwick": 2}
    assert tools.get_team_stats("calderwick")["shots"] == 9
    assert tools.get_team_stats("CAL", last_minutes=10)["window"] == "last 10 min"
    assert tools.get_player_stats("gallagher")["goals"] == 2
    assert "error" in tools.get_team_stats("Nonexistent FC")
    assert "get_team_stats" in tools.get_player_stats("Calderwick")["hint"]  # a club name, not a player
    assert "hint" not in tools.get_player_stats("Nobody")
    assert all(m["kind"] == "press_shift" for m in tools.list_key_moments(kind="press_shift"))
    assert [c["tool"] for c in tools.calls][:2] == ["get_match_state", "get_team_stats"]
    assert {9.0, 2.0} <= tools.evidence
    assert len(tools.as_tools()) == 4


# -------------------------------------------------------------------- desk
def test_desk_writes_for_every_audience_from_one_brief(at_press_shift):
    pipeline, shift = at_press_shift
    chat = ScriptedChat(honest_script)
    desk = EditorialDesk(chat, pipeline)
    out = asyncio.run(desk.anarrate_many(shift, [ANALYST, FAN_ES, FAN_FR]))

    assert {n.source for n in out.values()} == {"agents"}
    assert out["a"].headline == "Calderwick turn the screw" and out["a"].stats  # stat chips stay deterministic
    assert out["b"].headline.startswith("[Spanish]") and out["c"].headline.startswith("[French]")
    assert out["a"].trace["tools"] == [{"tool": "get_team_stats", "arguments": {"team": "Northbridge", "last_minutes": 10}}]
    # one analyst (tool call + brief), one studio draft, two fan drafts
    assert (chat.count("analyst"), chat.count("studio"), chat.count("fan")) == (2, 1, 2)
    assert desk.stats["briefs"] == 1 and desk.stats["accepted"] == 3 and desk.stats["revisions"] == 0


def test_writers_receive_the_brief_the_audience_and_the_stance(at_press_shift):
    pipeline, shift = at_press_shift
    chat = ScriptedChat(honest_script)
    asyncio.run(EditorialDesk(chat, pipeline).anarrate_many(shift, [ANALYST, FAN_ES]))
    fan_payload = next(payload_of(m) for a, m in chat.requests if a == "fan")
    assert fan_payload["ANALYST_BRIEF"].startswith("Calderwick have changed the game")
    assert fan_payload["AUDIENCE"] == {
        "language": "Spanish", "type": "fan", "supports": "Calderwick", "moment_favours_their_club": True,
    }
    assert fan_payload["REFERENCE_COPY"]["headline"] == "Calderwick are going for it!"


def test_fact_checker_sends_an_invented_number_back_once(at_press_shift):
    pipeline, shift = at_press_shift

    def script(agent, messages):
        if agent == "fan":
            p = payload_of(messages)
            if "REVISION" not in p:
                return {"headline": "¡A por ellos!", "body": "Ya han robado 41 balones."}
            assert "41" in p["REVISION"]["problems"][0]
            return {"headline": "¡A por ellos!", "body": "Calderwick aprieta y Northbridge sufre."}
        return honest_script(agent, messages)

    desk = EditorialDesk(ScriptedChat(script), pipeline)
    out = asyncio.run(desk.anarrate_many(shift, [FAN_ES]))
    assert out["b"].source == "agents" and "41" not in out["b"].body
    assert out["b"].trace["revisions"] == 1 and desk.stats["revisions"] == 1


def test_twice_rejected_copy_falls_back_to_the_template(at_press_shift):
    pipeline, shift = at_press_shift

    def script(agent, messages):
        if agent == "fan":
            return {"headline": "Pressing", "body": "PPDA is collapsing."}  # jargon in fan copy, every time
        return honest_script(agent, messages)

    desk = EditorialDesk(ScriptedChat(script), pipeline)
    out = asyncio.run(desk.anarrate_many(shift, [ANALYST, FAN_ES]))
    assert out["a"].source == "agents"
    assert out["b"].source == "template" and out["b"].headline == "¡Calderwick va a por el partido!"
    assert out["b"].trace == {"rejected": ["fan copy must not use xG or PPDA"]}
    assert desk.stats["rejected"] == 1 and desk.stats["template_fallbacks"] == 1


def test_desk_survives_model_failures(at_press_shift):
    pipeline, shift = at_press_shift

    def script(agent, messages):
        if agent == "analyst":
            raise RuntimeError("content filter")
        if agent == "studio":
            raise TimeoutError("model timeout")
        return honest_script(agent, messages)

    desk = EditorialDesk(ScriptedChat(script), pipeline)
    out = asyncio.run(desk.anarrate_many(shift, [ANALYST, FAN_ES]))
    assert out["a"].source == "template" and "model call failed" in out["a"].trace["rejected"][0]
    assert out["b"].source == "agents" and out["b"].trace["brief"] == ""  # writers worked without a brief
    assert desk.stats["errors"] == 1


def test_an_ungrounded_brief_never_reaches_the_writers(at_press_shift):
    pipeline, shift = at_press_shift

    def script(agent, messages):
        if agent == "analyst":
            return "Calderwick have made 512 pressures already."
        return honest_script(agent, messages)

    chat = ScriptedChat(script)
    desk = EditorialDesk(chat, pipeline)
    asyncio.run(desk.anarrate_many(shift, [ANALYST]))
    assert desk.stats["briefs_rejected"] == 1
    assert next(payload_of(m) for a, m in chat.requests if a == "studio")["ANALYST_BRIEF"] is None


def test_desk_times_out_to_templates(at_press_shift):
    pipeline, shift = at_press_shift
    desk = EditorialDesk(ScriptedChat(honest_script), pipeline, timeout_s=0.0)
    out = asyncio.run(desk.anarrate_many(shift, [ANALYST]))
    assert out["a"].source == "template" and desk.stats["timeouts"] == 1


def test_cache_makes_the_second_run_free_and_survives_a_restart(at_press_shift, tmp_path):
    pipeline, shift = at_press_shift
    chat = ScriptedChat(honest_script)
    cache = NarrationCache(tmp_path / "narration.json")
    desk = EditorialDesk(chat, pipeline, cache)
    first = asyncio.run(desk.anarrate_many(shift, [ANALYST, FAN_ES]))
    calls = len(chat.requests)
    assert asyncio.run(desk.anarrate_many(shift, [ANALYST, FAN_ES]))["b"].body == first["b"].body
    assert len(chat.requests) == calls and desk.stats["cache_hits"] == 2
    assert desk.cached(shift, [ANALYST, FAN_ES]) and not desk.cached(shift, [FAN_FR])
    cache.save()
    reloaded = EditorialDesk(ScriptedChat(honest_script), pipeline, NarrationCache(tmp_path / "narration.json"))
    assert reloaded.narrate(shift, FAN_ES).source == "agents"  # the sync path serves cached agent copy


@pytest.mark.parametrize(
    ("headline", "body", "mode", "expected"),
    [
        ("Fine", "Press rate 70%.", "analyst", []),
        ("", "Something.", "analyst", ["headline or body is empty"]),
        ("H" * 91, "Body.", "analyst", ["headline is longer than 90 characters"]),
        ("Fine", "B" * 481, "analyst", ["body is longer than 480 characters"]),
        ("Fine", "A 0.4 xG chance.", "fan", ["numbers not in the facts: 0.4", "fan copy must not use xG or PPDA"]),
    ],
)
def test_fact_checker_rules(headline, body, mode, expected):
    audience = Audience(key="k", mode=mode, lang="en", supports=None, stance=None)
    brief = Brief(assignment=None, text="", evidence={70.0})
    assert FactChecker.problems(Draft(audience, brief, headline, body)) == expected


def test_whole_match_through_the_desk(match):
    """Every on-air moment for two viewers goes through the workflow and the cues stay valid."""
    meta, events, _ = match
    pipeline = Pipeline(meta, [ANALYST, FAN_ES])
    desk = EditorialDesk(ScriptedChat(honest_script), pipeline)
    pipeline.narrator = desk
    _, tracks = asyncio.run(arun_match(pipeline, events))
    lower = [c for c in tracks["a"] + tracks["b"] if c.slot in ("lower_third", "recap")]
    assert len(lower) > 30 and {c.payload["source"] for c in lower} == {"agents"}
    assert all("brief" in c.payload["trace"] for c in lower)
    assert {c.lang for c in tracks["b"] if c.slot == "lower_third"} == {"es"}
    assert desk.stats["rejected"] == 0 and desk.stats["errors"] == 0


# --------------------------------------------------------------------- ask
def test_ask_answers_from_tool_results(run):
    def script(agent, messages):
        if "function_result" in last_kinds(messages):
            return "Gallagher-Reyes scored 2 goals."
        return call("get_player_stats", player="Gallagher-Reyes")

    chat = ScriptedChat(script)
    result = asyncio.run(AskAnalyst(chat, run[0]).ask("Who won it?", Profile(id="q", mode="fan")))
    assert result["grounded"] and result["revisions"] == 0
    assert result["tools_used"] == [{"tool": "get_player_stats", "arguments": {"player": "Gallagher-Reyes"}}]
    assert json.loads(chat.requests[1][1][-1].contents[0].result)["goals"] == 2


def test_ask_critic_sends_back_an_invented_number_once(run):
    state = {"final": 0}

    def script(agent, messages):
        if "function_result" in last_kinds(messages):
            state["final"] += 1
            return "Calderwick had 31 shots." if state["final"] == 1 else "Calderwick had 9 shots."
        return call("get_team_stats", team="Calderwick")

    chat = ScriptedChat(script)
    result = asyncio.run(AskAnalyst(chat, run[0]).ask("How many shots did Calderwick have?", Profile(id="q", mode="analyst")))
    assert result["answer"] == "Calderwick had 9 shots." and result["grounded"] and result["revisions"] == 1
    assert "31" in chat.requests[2][1][0].text  # the retry prompt names the bad number


def test_ask_flags_persistent_invention(run):
    result = asyncio.run(
        AskAnalyst(ScriptedChat(lambda a, m: "They ran 140 km."), run[0]).ask("How far?", Profile(id="q", mode="fan"))
    )
    assert result["grounded"] is False and result["ungrounded_numbers"] == ["140"]


# ------------------------------------------------------------------ server
def test_stream_airs_agent_copy_without_blocking_the_clock(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "chat_client_from_env", lambda: ScriptedChat(honest_script))
    monkeypatch.setattr(server, "_match", None)
    client = TestClient(server.app)
    assert client.get("/api/match").json()["agents_enabled"] is True

    profiles = json.dumps([{"id": "a", "mode": "analyst"}, {"id": "b", "mode": "fan", "lang": "es", "favorite_team": "CAL"}])
    sources, agents, ticks = [], None, 0
    with client.stream("GET", "/api/stream", params={"speed": 240, "start": 5100, "profiles": profiles}) as r:
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            ticks += 1
            agents = data.get("agents", agents)
            for cues in data.get("cues", {}).values():
                sources += [c["payload"]["source"] for c in cues if c["slot"] in ("lower_third", "recap")]
    assert sources and set(sources) == {"agents"}
    assert agents["accepted"] >= len(sources) / 2 and ticks > 5
    assert (tmp_path / "sample.narration.json").exists()  # cache persisted for the next replay

    answer = client.post("/api/ask", json={"question": "Who is on top?", "profile": {"mode": "fan"}})
    assert answer.status_code == 200 and "answer" in answer.json()
    monkeypatch.setattr(server, "_match", None)
