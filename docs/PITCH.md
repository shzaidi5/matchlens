# Submission drafts

The pitch and the demo script for the submission. Everything below has been run live (see the README's Azure status table). Re-check it on submission day.

## Elevator pitch

MatchLens turns a live feed of synthetic football events into explainable match intelligence that looks different for every viewer. It reads passes, shots, tackles and pressure events as they happen, detects the moments that matter, and records the evidence for why: a pressing change, a momentum swing, a game that tips from control into chaos. An analyst sees press rate, PPDA and xG on a stat card. A casual fan sees two plain sentences, in their language, about their club or the one player they follow. Every overlay is a timed, machine-readable cue a graphics engine can render on the match feed.

Built with Microsoft Agent Framework on a Microsoft Foundry model (gpt-4.1-mini): a tactical analyst agent with match tools briefs two writer agents, and a deterministic fact checker sends back or rejects any draft that quotes a number the pipeline did not compute. Events arrive live through Azure Event Hubs. The app runs on Azure Container Apps, with FastAPI and a browser viewer, and is deployed by GitHub Actions. Everything signs in with Entra identities, so the app holds no keys. The synthetic data generator ships hidden ground-truth tactics, so the detectors are scored, not just demonstrated.

## Demo video script (target 1:50, hard limit 2:00)

### Before recording

1. Use the deployed app: https://matchlens.greenmoss-a99bb9ec.eastus2.azurecontainerapps.io. Use a 1920x1080 browser window at 100% zoom, and close other tabs.
2. Leave both viewers on their defaults: **A** is the analyst in English; **B** is a fan, in Spanish, of Calderwick. Agent copy for these profiles is pre-written (`python -m matchlens warm`), so it appears instantly.
3. Open a terminal beside the browser, small and in the bottom right, at the repo root, with `.venv` active and `az login` done.
4. Wake the app first: load the page once and wait for the pitch to draw, because it scales to zero when idle.
5. Do a dry run of the live segment. A live run only ends at full time; to restart it, run `publish` again.

### Shot list

| Time | Do | Say (about 250 words in total) |
|---|---|---|
| 0:00 | Page loaded, both viewers visible. Feed picker set to **Live: Event Hubs**. | "One football match feed. Two viewers. MatchLens gives each of them a different broadcast, and it can tell you why every overlay is on screen." |
| 0:08 | In the terminal, run `python -m matchlens publish --speed 60`. Play starts in the viewer. | "This feed is arriving live through Azure Event Hubs. The app runs on Azure Container Apps, and the writing is done by agents on Microsoft Foundry. All the data is synthetic, and every club and player is fictional." |
| 0:22 | The 20' goal lands (about 20 seconds in). Hold on both panels. | "Same goal, two broadcasts. The analyst gets the expected goals, the shot speed and the five-pass build-up. The fan gets two plain sentences, in Spanish, written for a Calderwick supporter." |
| 0:38 | Switch the picker to **Replay**, then select the **63' tactical shift** marker. | "Now the moment that decides the match. Sixty-one minutes: Calderwick bring on a striker. Two minutes later, their press jumps from 33 to 70 percent." |
| 0:50 | Point the cursor at "Why this is on screen" on viewer A. | "Every overlay shows its evidence. The analyst agent called match tools, briefed two writer agents, and a fact checker rejects any number the pipeline didn't compute." |
| 1:05 | Let it run to the **67' equaliser**. | "And it pays off. The substitute scores, and MatchLens explains why: the press won the ball high." |
| 1:15 | On viewer B, set favourite player to **10 Rafael Quintero** and tick player focus. | "Every viewer can personalise. Follow one player, and a tag tracks him with his own live stats." |
| 1:27 | Type into **Ask about the match**: "Why did Calderwick come back?" Wait for the answer. | "Fans can also just ask. The answer comes from the same tools, and every number is checked." |
| 1:40 | Select the full-time marker; the recap and the momentum river are visible. | "One feed, a broadcast for every fan: explainable, personal and live on Azure. That's MatchLens." |
| 1:50 | End card: the repo URL `github.com/shzaidi5/matchlens`. | (silence) |

### Recording notes

- If the agent desk is slow on a moment, it will show template copy first. Re-record that shot rather than waiting on camera.
- The ask answer usually takes 5 to 10 seconds. Cut the wait in the edit.
- If time is tight, cut the player-focus shot (1:15) first.
- No music and no third-party logos. All club and player names are fictional.
