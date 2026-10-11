import pytest

from matchlens.models import Profile
from matchlens.pipeline.orchestrator import run_match
from matchlens.synth import generate_match

SEED = 898


@pytest.fixture(scope="session")
def match():
    return generate_match(SEED)


@pytest.fixture(scope="session")
def profiles():
    return [
        Profile(id="analyst", mode="analyst", lang="en"),
        Profile(id="fan", mode="fan", lang="es", favorite_team="CAL"),
        Profile(id="focus", mode="fan", lang="en", favorite_team="NBR", favorite_player="NBR-10", player_focus=True),
    ]


@pytest.fixture(scope="session")
def run(match, profiles):
    meta, events, _ = match
    pipeline, insights, tracks = run_match(meta, events, profiles)
    return pipeline, insights, tracks
