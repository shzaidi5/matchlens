"""Mux the recorded frames and the voiceover into build/video/matchlens-demo.mp4 (+ .srt).

The output is 3840x2160 at 30 fps with a high bitrate: YouTube re-encodes 4K
uploads at a much higher bitrate than 1080p ones, which keeps UI text crisp.
"""

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


def concat_list(plan: dict) -> Path:
    """ffmpeg concat script: each frame held until the next repaint, clipped to [t0, t0 + length]."""
    t0, t1 = plan["t0"], plan["t0"] + plan["length"]
    frames = sorted(plan["frames"])
    first = max([i for i, (ts, _) in enumerate(frames) if ts <= t0] or [0])
    lines = []
    for i in range(first, len(frames)):
        ts, name = frames[i]
        if ts >= t1:
            break
        nxt = frames[i + 1][0] if i + 1 < len(frames) else t1
        lines += [f"file 'frames/{name}'", f"duration {min(nxt, t1) - max(ts, t0):.4f}"]
    lines.append(lines[-2])  # the concat demuxer ignores the last entry's duration
    path = OUT / "frames.txt"
    path.write_text("\n".join(lines) + "\n")
    return path


def main() -> None:
    plan = json.loads((OUT / "plan.json").read_text())
    durations = json.loads((OUT / "durations.json").read_text())
    cues = plan["cues"]

    inputs = ["-f", "concat", "-safe", "0", "-i", str(concat_list(plan))]
    mix = []
    for i, cue in enumerate(cues, start=1):
        inputs += ["-i", str(OUT / f"vo_{cue['line']}.wav")]
        ms = int(cue["at"] * 1000)
        mix.append(f"[{i}:a]adelay={ms}|{ms},aresample=48000[a{i}]")
    labels = "".join(f"[a{i}]" for i in range(1, len(cues) + 1))
    graph = (
        "[0:v]fps=30,scale=3840:2160:flags=lanczos:out_color_matrix=bt709:out_range=tv,format=yuv420p[vout];"
        + ";".join(mix) + f";{labels}amix=inputs={len(cues)}:normalize=0,apad=whole_dur={plan['length']}[aout]"
    )

    out = OUT / "matchlens-demo.mp4"
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-stats", *inputs,
         "-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", str(plan["length"]),
         "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-profile:v", "high", "-level", "5.1",
         "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)],
        check=True,
    )

    srt = [f"{n}\n{stamp(c['at'])} --> {stamp(c['at'] + durations[c['line']])}\n{LINES[c['line']]}\n"
           for n, c in enumerate(cues, start=1)]
    (OUT / "matchlens-demo.srt").write_text("\n".join(srt), encoding="utf-8")
    print(f"{out} ({plan['length']:.1f}s, {out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
