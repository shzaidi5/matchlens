# MatchLens: context for Claude Code

Entry for the Microsoft "Inside the Game: Developer Hackathon" (with the Premier League). Submissions close **Oct 27, 2026**. Winners are announced Nov 20.

## What it is
Synthetic football events in, explainable and personalized match intelligence out, as timed overlay cues. See README.md for the architecture, and docs/PITCH.md for the pitch and demo-video outline.

## Hackathon rules that constrain the code
- Synthetic data only. All clubs and players are fictional: never add real club, player or league names, logos or music.
- The repo must be public and the project newly built. The demo video must be under 2 minutes.
- Judging is five equal parts: tech implementation (hero technologies), agentic design and multi-agent orchestration, real-world impact and production readiness, UX and presentation, and adherence to the category.
- Awards being targeted: Grand Prize, Best Use of Microsoft Foundry, Best Multi-Agent Orchestration (Microsoft Agent Framework), and Best Azure Cloud Native Integration.

## Status
- Done: synthetic generator with hidden ground truth, five-stage pipeline, detectors, template narration (English and Spanish), personalization, overlay cues with a JSON schema, a FastAPI server with SSE, and a split-screen viewer.
- Done: agent layer on Microsoft Agent Framework 1.20 (`matchlens/agents/`): a tactical analyst agent with tools, a studio writer and a fan commentator, a deterministic fact checker with a revision loop, and an ask-the-match agent.
- Done (2026-10-11): Foundry verified live. `python -m matchlens check` returns "agents are live". The resource group is `matchlens-rg` (eastus2), the resource `matchlens-ai-554`, the project `matchlens`, and the deployment `gpt-4.1-mini`. Auth is `az login` plus the "Foundry User" role; "Azure AI User" doesn't exist in this tenant.
- In progress: Container Apps. ACR Tasks (`az acr build`, `containerapp up --source`) are blocked on this subscription, so images are built by GitHub Actions (`.github/workflows/deploy.yml`) and pushed to `matchlensacr554`. The app `matchlens` (env `matchlens-env`) runs as user-assigned identity `matchlens-id`, which has AcrPull and Foundry User; `AZURE_CLIENT_ID` is set on the app. URL: https://matchlens.greenmoss-a99bb9ec.eastus2.azurecontainerapps.io (placeholder image until the first workflow run).
- Local env: Python 3.13 venv at `.venv`. The Azure CLI isn't on PATH in old terminals, so run `$env:Path += ";C:\Program Files\Microsoft SDKs\Azure\CLI2\wbin"`.
- Next: deploy to Azure Container Apps, add Event Hubs ingest, record the demo video, and push to a public GitHub repo.

## Conventions
- `pytest` must pass with no network: 79 tests, using a scripted Agent Framework client (`tests/fakes.py`).
- No number may reach the screen unless the pipeline or a tool computed it. Keep `grounding.py` and the fact checker strict.
- Detector thresholds live in `Thresholds` (moments.py). Re-run `python scripts/eval_detectors.py` after changing them, and update the README figures (currently 23 of 30 detected, 4 false alarms).
- Keep README claims true: state what has and hasn't been run live.
- Sample match: `data/sample.*` is seed 898. Regenerate with `python -m matchlens generate --seed 898 --name sample`.

## Owner preferences
Brief, directive replies with copy-paste-ready commands.
