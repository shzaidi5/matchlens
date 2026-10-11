# Submission drafts

Both sections are drafts. Fill the bracketed parts once the Azure pieces are live, and cut anything that is not true on submission day.

## Elevator pitch

MatchLens turns a live feed of synthetic football events into explainable match intelligence that looks different for every viewer. It reads passes, shots, tackles and pressure events as they happen, detects the moments that matter, and records the evidence for why: a pressing change, a momentum swing, a game that tips from control into chaos. An analyst sees press rate, PPDA and xG on a stat card. A casual fan sees two plain sentences, in their language, about their club or the one player they follow. Every overlay is a timed, machine-readable cue a graphics engine can render on the match feed.

Built with Microsoft Agent Framework on [Microsoft Foundry models]: a tactical analyst agent with match tools briefs two writer agents, and a deterministic fact checker sends back or rejects any draft that quotes a number the pipeline did not compute. Hosted on [Azure Container Apps], with FastAPI and a browser viewer. The synthetic data generator ships hidden ground-truth tactics, so the detectors are scored, not just demonstrated.

## Two-minute demo outline

| Time | On screen | Say |
|---|---|---|
| 0:00 | Viewer idle, both panels | "One match feed. Two viewers. Watch what each of them gets." |
| 0:10 | Play at 30x from kick-off, then select the 20' goal marker | Same goal: analyst gets xG, shot speed and the build-up; the fan gets "you could feel it coming". |
| 0:30 | Select the 63' tactical-shift marker | The key moment. Analyst: press rate 33% to 70%, PPDA, opponent accuracy falling. Fan, in Spanish: "they've stopped waiting". Point at "Why this is on screen": the analyst agent's brief, the tools it called, the fact checker's verdict. |
| 0:55 | Let it run to the 67' equaliser | The goal is explained by the press: reason code `press_paying_off`, plus the substitute who scored. |
| 1:10 | Switch viewer B to follow Rafael Quintero | Player-focus mode: the tag follows him, and passing detail a fan would never normally see now appears. |
| 1:25 | Jump to full time | Recap in two voices and two languages. Momentum river on the timeline shows the whole story. |
| 1:40 | Terminal: `python scripts/eval_detectors.py` | "The data has hidden ground truth, so we can say how often the detector is right: 23 of 30." |
| 1:50 | [Ask box, with the Foundry model live] | One question, answered from tools, numbers checked. |

No third-party marks or music. All club and player names are fictional.
