"""Synthesise the demo voiceover with Azure AI Speech (Entra auth, no keys).

    python scripts/video/voiceover.py   # writes build/video/vo_*.wav and durations.json
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
import wave
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "video"
VOICE = "en-GB-RyanNeural"
RESOURCE = "matchlens-ai-554"
RG = "matchlens-rg"

LINES = {
    "intro": "One football match feed. Two viewers. MatchLens gives each of them a different broadcast, and it can tell you why every overlay is on screen.",
    "live": "This feed is arriving live through Azure Event Hubs. The app runs on Azure Container Apps, and the writing is done by agents on Microsoft Foundry. All the data is synthetic, and every club and player is fictional.",
    "goal": "Same goal, two broadcasts. The analyst gets the expected goals, the shot speed and the five-pass build-up. The fan gets two plain sentences, in Spanish, written for a Calderwick supporter.",
    "shift": "Now the moment that decides the match. Sixty-one minutes: Calderwick bring on a striker. Two minutes later, their press jumps from thirty-three to seventy percent.",
    "why": "Every overlay shows its evidence. The analyst agent called match tools and briefed two writer agents, and a fact checker rejects any number the pipeline didn't compute.",
    "equaliser": "And it pays off. The substitute scores, and MatchLens explains why: the press won the ball high.",
    "focus": "Every viewer can personalise. Follow the substitute, and a tag tracks him with his own live stats.",
    "ask": "Fans can also just ask. The answer comes from the same tools, and every number is checked.",
    "outro": "One feed, a broadcast for every fan. Explainable, personal, and live on Azure. That's MatchLens.",
}


def az(*args: str) -> str:
    return subprocess.run(["az", *args, "-o", "tsv"], capture_output=True, text=True, check=True, shell=True).stdout.strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    token = az("account", "get-access-token", "--resource", "https://cognitiveservices.azure.com", "--query", "accessToken")
    endpoint = az("cognitiveservices", "account", "show", "-n", RESOURCE, "-g", RG, "--query", "properties.endpoint").rstrip("/")
    durations = {}
    for name, text in LINES.items():
        ssml = (
            f'<speak version="1.0" xml:lang="en-GB"><voice name="{VOICE}">'
            f'<prosody rate="+4%">{escape(text)}</prosody></voice></speak>'
        )
        req = urllib.request.Request(
            f"{endpoint}/tts/cognitiveservices/v1",
            data=ssml.encode(),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/ssml+xml",
                "X-Microsoft-OutputFormat": "riff-48khz-16bit-mono-pcm",
                "User-Agent": "matchlens-demo",
            },
        )
        path = OUT / f"vo_{name}.wav"
        path.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        with wave.open(str(path)) as w:
            durations[name] = round(w.getnframes() / w.getframerate(), 2)
        print(f"{name:>10}: {durations[name]:5.2f}s")
    (OUT / "durations.json").write_text(json.dumps(durations, indent=2))
    print(f"total speech: {sum(durations.values()):.1f}s")


if __name__ == "__main__":
    main()
