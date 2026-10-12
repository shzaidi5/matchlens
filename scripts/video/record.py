"""Record the demo: drive the live app through the shot list in docs/PITCH.md.

    python scripts/video/voiceover.py   # first: the voice lines and their lengths
    python scripts/video/record.py      # browser recording + cue times
    python scripts/video/assemble.py    # mp4 with voiceover, plus .srt captions

The live segment publishes the match to Event Hubs, so `az login` and the
"Azure Event Hubs Data Sender" role are needed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "video"
URL = os.getenv("MATCHLENS_URL", "https://matchlens.greenmoss-a99bb9ec.eastus2.azurecontainerapps.io")
SIZE = {"width": 1920, "height": 1080}
SCALE = 2  # render at 2x and capture 3840x2160: YouTube gives 4K uploads far more bitrate, so small text stays sharp
FRAMES = OUT / "frames"
GAP = 0.6  # silence between voice lines

CARD_CSS = """
#demo-card { position: fixed; z-index: 9999; font-family: Segoe UI, system-ui, sans-serif; transition: opacity .5s; }
#demo-card.full { inset: 0; display: grid; place-content: center; text-align: center; background: #0b1220; color: #f5f7fb; }
#demo-card.full h1 { font-size: 88px; margin: 0 0 12px; letter-spacing: -1px; }
#demo-card.full p { font-size: 30px; margin: 6px 0; color: #b8c2d6; }
#demo-card.term { right: 28px; bottom: 28px; width: 820px; background: #0d1117; color: #d1e7d1; border: 1px solid #30363d;
  border-radius: 10px; padding: 18px 22px; font: 21px/1.5 Cascadia Mono, Consolas, monospace; box-shadow: 0 12px 40px #0008; }
#demo-card.term b { color: #7ee787; font-weight: normal; }
.demo-spot { outline: 4px solid #ffd33d !important; outline-offset: 4px; border-radius: 6px; transition: outline-color .3s; }
"""


class Director:
    def __init__(self, page, durations: dict[str, float]) -> None:
        self.page, self.durations = page, durations
        self.t0 = time.monotonic()
        self.t0_wall = time.time()
        self.cues: list[dict] = []

    def now(self) -> float:
        return time.monotonic() - self.t0

    async def say(self, name: str, wait: bool = True) -> None:
        self.cues.append({"line": name, "at": round(self.now(), 2)})
        print(f"{self.now():6.1f}s  {name}", flush=True)
        if wait:
            await asyncio.sleep(self.durations[name] + GAP)

    async def js(self, code: str, arg=None):
        return await self.page.evaluate(code, arg)

    async def card(self, kind: str | None, html: str = "") -> None:
        await self.js(
            """([kind, html]) => {
                let el = document.getElementById("demo-card");
                if (!kind) { if (el) el.remove(); return; }
                if (!el) { el = document.createElement("div"); el.id = "demo-card"; document.body.appendChild(el); }
                el.className = kind; el.innerHTML = html;
            }""",
            [kind, html],
        )

    async def spot(self, selector: str | None) -> None:
        await self.js(
            """(sel) => { document.querySelectorAll(".demo-spot").forEach(e => e.classList.remove("demo-spot"));
                          if (sel) document.querySelector(sel)?.classList.add("demo-spot"); }""",
            selector,
        )

    async def seek(self, t: float, speed: str) -> None:
        await self.js("([t, speed]) => { document.querySelector('#speed').value = speed; seek(t); }", [t, speed])


class Screencast:
    """Lossless-quality frames straight from Chrome (Playwright's built-in recorder is ~1 Mbps VP8).

    Chrome sends a frame whenever the page repaints, stamped with wall-clock time;
    assemble.py turns them into constant-frame-rate video.
    """

    def __init__(self, cdp) -> None:
        self.cdp, self.frames = cdp, []
        cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(self._frame(p)))

    async def _frame(self, p: dict) -> None:
        name = f"{len(self.frames):05d}.jpg"
        (FRAMES / name).write_bytes(base64.b64decode(p["data"]))
        self.frames.append([p["metadata"]["timestamp"], name])
        await self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})

    async def start(self) -> None:
        w, h = SIZE["width"] * SCALE, SIZE["height"] * SCALE
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 95, "maxWidth": w, "maxHeight": h})

    async def stop(self) -> None:
        await self.cdp.send("Page.stopScreencast")


def marker(markers: list[dict], kind: str, minute: str) -> float:
    return next(m["t"] for m in markers if m["kind"] == kind and m["minute"] == minute)


def publisher() -> subprocess.Popen:
    env = {**os.environ, "PATH": os.environ["PATH"] + r";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"}
    return subprocess.Popen(
        [sys.executable, "-m", "matchlens", "publish", "--speed", "60"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def wait_until_caught_up() -> None:
    """A fresh replica replays the hub's retention first; a live viewer must not join mid catch-up."""
    last = None
    for _ in range(60):
        live = json.load(urllib.request.urlopen(f"{URL}/api/match", timeout=120))["live"]
        state = (live["run"], live["events"], live["ended"])
        if state == last and (live["ended"] or live.get("stale")):
            print(f"live feed settled on run {live['run']}")
            return
        last = state
        time.sleep(5)
    sys.exit("live feed never settled")


async def main() -> None:
    wait_until_caught_up()
    durations = json.loads((OUT / "durations.json").read_text())
    markers = json.load(urllib.request.urlopen(f"{URL}/api/insights", timeout=120))
    shift_t, equaliser_t = marker(markers, "press_shift", "63'"), marker(markers, "goal", "67'")
    recap_t = marker(markers, "recap", "90+5'")
    shutil.rmtree(FRAMES, ignore_errors=True)
    FRAMES.mkdir(parents=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(viewport=SIZE, device_scale_factor=SCALE)
        page = await ctx.new_page()
        cast = Screencast(await ctx.new_cdp_session(page))
        await page.goto(URL, wait_until="networkidle")
        await page.add_style_tag(content=CARD_CSS)
        await page.wait_for_selector("#source:not([hidden])", timeout=60_000)
        await asyncio.sleep(1.5)  # let the pitches draw
        await cast.start()
        d = Director(page, durations)

        # 0:00 title, then the two viewers waiting for a live kick-off
        await d.card("full", "<h1>MatchLens</h1><p>One match feed. A different broadcast for every viewer.</p>"
                             "<p>Microsoft Agent Framework · Microsoft Foundry · Azure Event Hubs · Azure Container Apps</p>")
        await page.select_option("#source", "live")  # the viewer waits for kick-off behind the card
        feed = publisher()  # takes a few seconds to sign in, so start it now: the 20' goal then lands on cue
        await asyncio.sleep(2.5)
        await d.card(None)
        await d.say("intro")

        # 0:12 the feed goes live through Event Hubs
        await d.card("term", "<b>$</b> python -m matchlens publish --speed 60<br>"
                             "Publishing SYN-0898 to matchlens-eh-554.servicebus.windows.net/match-events at 60.0x")
        await d.say("live")
        await d.card(None)

        # the 20' goal arrives live
        await page.wait_for_function("S.state && (S.state.score.NBR + S.state.score.CAL) >= 1", timeout=90_000)
        await asyncio.sleep(2.0)
        await d.say("goal")

        # replay: the 63' press shift
        await d.seek(shift_t - 12, "10")
        await d.say("shift", wait=False)
        await asyncio.sleep(3)
        await d.spot("#viewers > :first-child .ov:not([hidden])")
        await asyncio.sleep(durations["shift"] - 3 + GAP)
        await d.spot("#viewers > :first-child .evidence")
        await d.say("why")
        await d.spot(None)

        # the substitute's equaliser
        await d.seek(equaliser_t - 15, "10")
        await d.say("equaliser")

        # viewer B follows the substitute
        await page.select_option("#viewers > :nth-child(2) [data-k='favorite_player']", "CAL-20")
        await page.check("#viewers > :nth-child(2) [data-k='player_focus']")
        await d.spot("#viewers > :nth-child(2) [data-k='favorite_player']")
        await d.say("focus")
        await d.spot(None)

        # ask the match
        box = "#viewers > :first-child .ask input"
        await page.click(box)
        await page.type(box, "Why did Calderwick come back?", delay=45)
        await page.press(box, "Enter")
        await d.say("ask")
        await page.wait_for_function(
            "document.querySelector('#viewers > :first-child .answer')?.textContent.length > 60", timeout=60_000
        )
        await d.spot("#viewers > :first-child .answer")
        await asyncio.sleep(2.5)
        await d.spot(None)

        # full time, then the end card
        await d.seek(recap_t - 8, "10")
        await d.say("outro")
        await d.card("full", "<h1>MatchLens</h1><p>github.com/shzaidi5/matchlens</p>"
                             f"<p>{URL.removeprefix('https://')}</p><p>Synthetic data. All clubs and players are fictional.</p>")
        await asyncio.sleep(4)
        end = d.now()
        await cast.stop()
        await asyncio.sleep(0.5)

        await ctx.close()
        await browser.close()
        feed.terminate()

    plan = {"t0": d.t0_wall, "length": round(end, 2), "cues": d.cues, "frames": cast.frames}
    (OUT / "plan.json").write_text(json.dumps(plan, indent=2))
    span = cast.frames[-1][0] - cast.frames[0][0]
    print(f"recorded {end:.1f}s: {len(cast.frames)} frames ({len(cast.frames) / span:.1f}/s on average)")


if __name__ == "__main__":
    asyncio.run(main())
