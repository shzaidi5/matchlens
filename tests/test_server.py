import json

import pytest
from fastapi.testclient import TestClient

from matchlens import server


@pytest.fixture(scope="module")
def client(tmp_path_factory, monkeypatch_module):
    monkeypatch_module.setattr(server, "DATA_DIR", tmp_path_factory.mktemp("data"))
    for var in ("FOUNDRY_PROJECT_ENDPOINT", "FOUNDRY_MODEL", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"):
        monkeypatch_module.delenv(var, raising=False)
    monkeypatch_module.setattr(server, "_match", None)
    return TestClient(server.app)


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_match_metadata(client):
    data = client.get("/api/match").json()
    assert data["synthetic"] is True and data["agents_enabled"] is False
    assert len(data["teams"]) == 2 and data["periods"][1]["period"] == 2
    assert set(data["languages"]) == {"en", "es"}


def test_insight_markers(client):
    markers = client.get("/api/insights").json()
    assert sum(m["kind"] == "goal" for m in markers) == 3


def test_overlay_export_json_and_vtt(client):
    profile = json.dumps({"id": "x", "mode": "fan", "lang": "es", "favorite_team": "CAL"})
    data = client.get("/api/overlay", params={"profile": profile}).json()
    assert data["cues"] and data["cues"][0]["lang"] == "es"
    vtt = client.get("/api/overlay", params={"profile": profile, "format": "vtt"})
    assert vtt.text.startswith("WEBVTT") and vtt.headers["content-type"].startswith("text/vtt")


def test_bad_profile_is_a_400(client):
    assert client.get("/api/overlay", params={"profile": "{broken"}).status_code == 400


def test_stream_seeks_and_ends_with_the_final_score(client):
    profiles = json.dumps([{"id": "a", "mode": "analyst"}, {"id": "b", "mode": "fan", "lang": "es"}])
    kinds, last, cue_profiles = [], None, set()
    with client.stream("GET", "/api/stream", params={"speed": 240, "start": 5200, "profiles": profiles}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
                kinds.append(event)
            elif line.startswith("data: "):
                last = json.loads(line[6:])
                cue_profiles |= set(last.get("cues", {}))
    assert kinds[0] == "init" and kinds[-1] == "end" and "tick" in kinds
    assert last["state"]["score"] == {"NBR": 1, "CAL": 2} and last["state"]["status"] == "full_time"
    assert cue_profiles == {"a", "b"}


def test_ask_without_a_model_explains_what_is_missing(client):
    r = client.post("/api/ask", json={"question": "Who is on top?"})
    assert r.status_code == 503 and "FOUNDRY_PROJECT_ENDPOINT" in r.json()["detail"]


def test_index_serves_the_viewer(client):
    assert "MatchLens" in client.get("/").text
