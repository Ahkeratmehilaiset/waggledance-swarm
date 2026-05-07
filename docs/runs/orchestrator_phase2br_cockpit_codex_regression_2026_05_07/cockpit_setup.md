# Operator Cockpit setup

Phase 2B-Revision (ARCH-011) ships an HTML Operator Cockpit at
`review_cockpit.html` that drives the manual web-UI step of the
external-review cycle. The cockpit:

* reads `state/cockpit_data.json` (regenerated after every epoch
  build by `orchestrator/Build-WaggleCockpitData.ps1`);
* renders a card per (provider, role) bundle;
* provides three actions per card: open the attachments folder
  (file:// link), copy the prompt to clipboard, open the LLM web
  UI in a new tab;
* displays a regression-ledger summary + proposal-matrix summary;
* polls the data file every 5 seconds.

The cockpit does NOT automate any web service. There is no HTTP
server. There is no browser-driving code. The operator pastes
the prompt into the LLM UI manually, attaches files manually, and
saves the response manually.

## Open the cockpit

```
powershell -File ".\orchestrator\Open-WaggleCockpit.ps1"
```

This launches `review_cockpit.html` in the default browser
(`Start-Process` on a `.html` file).

## Refresh the data

After each of these steps, re-run `Build-WaggleCockpitData.ps1`
so the cockpit picks up the new state:

* `Build-WaggleEpochEvidence.ps1` — created the epoch
* `Export-WaggleExternalReviewQueue.ps1` — bundles ready for
  reviewers
* `Import-WaggleExternalReviewResponse.ps1` — operator imported a
  reviewer response
* `Build-WaggleProposalMatrix.ps1` — matrix updated
* `Import-WaggleSynthesisResult.ps1` — synthesis decided

Suggested invocation:

```
powershell -File ".\orchestrator\Build-WaggleCockpitData.ps1" `
    -ConfigPath ".\orchestrator.config.json" `
    -EpochId <epoch_id> `
    -IterationId <last_iteration_id>
```

The cockpit refreshes within 5 seconds.

## Browser-specific behavior

* **Chrome / Edge / Firefox**: `file://` link to the attachments
  folder opens File Explorer (Windows) or the system file manager.
  Some browsers show an empty tab instead — that's a browser
  setting, not a cockpit bug.
* **Clipboard write**: `navigator.clipboard.writeText` requires a
  user gesture (click). The cockpit's "Copy prompt" button
  satisfies that. If clipboard access is blocked by browser
  policy, the cockpit shows an alert; the operator can fall back
  to opening `prompt.md` manually.
* **`window.open` to chat UI**: opens a new tab. The browser
  should be already signed in to the LLM provider; the cockpit
  does NOT perform any sign-in step.

## Allowed external origins (whitelist)

The cockpit will only open these URLs in a new tab:

* `https://gemini.google.com/app`
* `https://grok.com`
* `https://chatgpt.com`
* `https://claude.ai` (legacy / explicit-opt-in only)

No other origins, no API endpoints, no analytics. The HTML test
(`Test-CockpitData.ps1`) asserts this whitelist.

## Failure modes

* **"Could not load state/cockpit_data.json"**: run
  `Build-WaggleCockpitData.ps1`.
* **Card status shows "disabled"**: the provider's `enabled`
  flag in `external_review.providers` is `false`. Re-enable
  in the config and rebuild the queue if you want a bundle for
  that provider.
* **Cockpit shows stale state**: hard-refresh the browser
  (Ctrl+F5). The cockpit fetches with `cache: 'no-store'` but
  some browser configs still cache aggressively.
* **`Open attachments folder` does nothing**: the browser blocked
  the `file://` navigation. Open File Explorer manually at the
  path printed in the card.
* **`Copy prompt` says "Clipboard write failed"**: the browser
  blocked clipboard write. Open `prompt.md` from the bundle dir
  and copy manually.

## Files

| Role | Path |
|------|------|
| HTML | `review_cockpit.html` (committed) |
| Schema | `schemas/cockpit_data.schema.json` |
| Builder | `orchestrator/Build-WaggleCockpitData.ps1` |
| Launcher | `orchestrator/Open-WaggleCockpit.ps1` |
| Data file | `state/cockpit_data.json` (gitignored runtime) |
| Tests | `orchestrator/Test-CockpitData.ps1` |
