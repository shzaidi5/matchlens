"""Mux the browser recording and the voiceover into build/video/matchlens-demo.mp4 (+ .srt)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import imageio_ffmpeg

from voiceover import LINES

OUT = Path(__file__).resolve().parents[2] / "build" / "video"


def stamp(s: float) -> str:
    ms = int(round(s * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def main() -> None:
    plan = json.loads((OUT / "plan.json").read_text())
    durations = json.loads((OUT / "durations.json").read_text())
    cues = plan["cues"]

    inputs = ["-ss", str(plan["offset"]), "-t", str(plan["length"]), "-i", plan["video"]]
    mix = []
    for i, cue in enumerate(cues, start=1):
        inputs += ["-i", str(OUT / f"vo_{cue['line']}.wav")]
        ms = int(cue["at"] * 1000)
        mix.append(f"[{i}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
    labels = "".join(f"[a{i}]" for i in range(1, len(cues) + 1))
    graph = ";".join(mix) + f";{labels}amix=inputs={len(cues)}:normalize=0,apad[aout]"

    out = OUT / "matchlens-demo.mp4"
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", *inputs,
         "-filter_complex", graph, "-map", "0:v", "-map", "[aout]", "-shortest",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
        check=True,
    )

    srt = [f"{n}\n{stamp(c['at'])} --> {stamp(c['at'] + durations[c['line']])}\n{LINES[c['line']]}\n"
           for n, c in enumerate(cues, start=1)]
    (OUT / "matchlens-demo.srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"{out} ({plan['length']:.1f}s, {out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
