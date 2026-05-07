# Operator Cockpit

Single-file passive HTML UI for the manual web-UI step of the
Phase 2B-Revision review workflow. Lives here (instead of repo
root) per Phase 2B-R2 ARCH-005 — see
`docs/runs/orchestrator_phase2br1_self_epoch_2026_05_07/p9_self_review_evidence.md`
finding ARCH-005 + the matching proposal `PM-CL-004`.

## Files in this directory

| File | Purpose |
|------|---------|
| `review_cockpit.html` | Single-file cockpit (HTML + embedded JS + embedded CSS). Polls `state/cockpit_data.json` every ~5 seconds. |
| `README.md` | This file. |

## Contract: `state/cockpit_data.json`

Producer: `orchestrator/Build-WaggleCockpitData.ps1`.
Consumer: `review_cockpit.html` (polled at runtime; no HTTP server,
no automation).
Schema: `schemas/cockpit_data.schema.json`.

The cockpit polls `state/cockpit_data.json` from the same repo root.
The polling interval is **~5 seconds** (configurable via the
`POLL_INTERVAL_MS` constant inside `review_cockpit.html`). Polling
is purely client-side; there is no backend server.

## Origin allowlist

The cockpit's "Open <provider>" buttons whitelist external review
origins. Only the following are reachable from the embedded UI:

- `gemini.google.com`
- `grok.com`
- `chatgpt.com`
- `claude.ai`

Adding more origins is a **deliberate decision**: edit the
`ALLOWED_ORIGINS` constant inside `review_cockpit.html`, run
`Test-CockpitData.ps1`, and update `Test-CockpitData.ps1` to
assert the new entry. Do not extend the allowlist via `Build-WaggleCockpitData`.

## Copy-to-clipboard

The cockpit uses `navigator.clipboard.writeText` for the
"copy paste-block" actions. There is **no** browser automation; the
operator manually pastes into the chosen provider, runs the review
in the provider's UI, and brings the response back via
`Import-WaggleSynthesisResult` / `Import-WaggleExternalReview` /
`Import-WaggleCodexFindings`.

## Launching

```powershell
orchestrator\Open-WaggleCockpit.ps1
```

`Open-WaggleCockpit.ps1` resolves the cockpit at
`orchestrator/cockpit/review_cockpit.html` (this file). For
backward-compat with branches that have not pulled the
Phase 2B-R2 cutover yet, it falls back to a repo-root
`review_cockpit.html` if the new path is absent.

## Future cockpit assets

If the cockpit grows past a single HTML file (e.g. a CSS module,
a JSON fixture, a JS module, screenshots for `docs/`), put each
new asset under this folder. Repo root stays for top-level
project files; cockpit assets accrete here.
