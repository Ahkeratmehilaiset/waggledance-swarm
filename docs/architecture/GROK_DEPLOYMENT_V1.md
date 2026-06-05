# Grok Deployment V1 (cost-bounded cross-model reviewer)

Status: spec, ready for activation **2026-07-01** (Grok offline until credits
reset). Author: claude-rco-1. Date: 2026-06-05.

Grok's unique value in the swarm is **cross-model diversity**: it is the only
non-Claude (rco-1, rco-2, lead) / non-codex (tools) perspective. This spec
deploys it where that pays — adversarial review + strategy — **headless,
bounded, scheduled**, never as a continuous interactive agent (whose
context-re-read churn was what exhausted credits) and never as a producer.

## Model policy (cost control)

| Use | Model | Why |
|-----|-------|-----|
| Scheduled red-team (code) | `grok-code-fast-1` | high-volume, code-focused, cheap |
| Scheduled competitor scan | `grok-code-fast-1` | high-volume, bounded |
| **Lead-only plan-vs-vision consult** | `grok-4` | rare, high-value reasoning only |

**The strongest model (`grok-4`) is reserved for the lead-only plan consult
only.** All scheduled volume runs on `grok-code-fast-1`, so grok-4 token burn is
a few bounded calls/day, not a continuous drain. (See "Cost guardrails".)

## Component 1 — Scheduled 6h red-team + competitor scout

* **Trigger:** a Windows Scheduled Task every 6h (an **addition**; does not
  modify `start-wd-3pack.ps1` or any existing launcher, per operator rule).
* **Input (summaries, not full diffs):** the changed-file list + key hunks of
  merges since the last run + the versioned prompt + competitor watchlist + the
  `WD_VISION_MANIFEST_V1.md` reference. Summaries keep input tokens ~5-10× lower
  than full diffs.
* **Prompt (the value lever)** — versioned, three parts:
  1. **Red-team checklist:** fail-open, prompt/payload injection, secret/canary
     leak, gate-bypass, charter/path escape, non-finite, self-review — the
     classes Claude/codex agents share as blind spots.
  2. **Competitor watchlist:** Microsoft AGT (largest threat), JamJet, Asqav,
     Preloop, etc. — moves relevant to WD's "verifiable solver-growth substrate"
     positioning.
  3. **Vision-axis delta:** did this 6h window advance any panel of
     `WD_VISION_MANIFEST_V1` (grounded in `wd_image1_capability_manifest`)?
* **Output:** structured JSON findings → bridge as `grok-scout-1`, **type
  `finding` status `advisory`** (NEVER auto-block). A real defect is triaged by
  RCO/lead and, if applicable, re-filed as a per-PR finding. Advisory output
  prevents a scheduled sweep from producing false-positive merge blocks.

## Component 2 — Lead-only plan-vs-vision consult

* **Helper:** `Invoke-GrokPlanReview.ps1` (operator-side addition), invokable
  **only by lead** — caller-identity gated (refuse if invoking agent ≠
  `codex-lead-1`) and documented as lead's tool.
* **Inputs:** `WD_VISION_MANIFEST_V1.md` (fixed target) + lead's current plan +
  `tools/wd_image1_capability_manifest.py --json` (machine-readable truth:
  `proofs_ok`, status partial/implemented, `all_literal_claims_safe`).
* **Output:** per-panel structured assessment
  `{panel, status: proof|ops|shadow|future, plan_advances_it, gap,
  next_highest_leverage, literal_claim_safe}` — keeps lead's plan anchored to the
  vision **and** honest about gaps.
* **Model:** `grok-4` (reasoning; optional image input via grok-4 vision).

## Prompt discipline (RCO — non-negotiable)

Both prompts MUST instruct Grok to **grade, not cheerlead**: classify each panel
proof/ops/shadow/future and **flag unsafe literal claims** (mirror
`all_literal_claims_safe=false`). Grok is an adversarial anchor that keeps the
plan from drifting into overclaim (e.g. "infinite scalability"), not a marketing
voice. Output is advisory; humans/RCO decide.

## Cost guardrails (fail-closed)

* **Hard per-day caps** (config, e.g. `configs/grok_budget.json`): max calls and
  max tokens/day **per model**. When a cap is hit the helper/task **refuses
  (fail-closed)** and logs a `grok_budget_exhausted` event to the bridge. This
  makes spend **structurally bounded** — it can never runaway-burn like the prior
  continuous agent.
* **Summaries not full diffs** in scheduled input.
* **Tight JSON output schema** (no prose essays) caps output tokens.
* **Lead-gate** keeps grok-4 consults rare.

Rough envelope: ~4 scheduled runs/day on grok-code-fast-1 + a few lead consults
on grok-4 ≈ ~6-10 bounded calls/day — vs the prior ~220/day continuous agent.

## Security

* API key from env/secret store, **never** in the repo, prompts, or bridge
  events (the bridge writer already rejects privacy-canary substrings).
* Findings are sanitized: no raw secrets, no raw private payloads; redact like
  any other bridge content.
* Grok has **no merge authority and no write scope** — advisory only. It is not
  a recognized RCO identity for the consensus gate (`{claude-rco-1,
  claude-rco-2}` only).

## Implementation deliverables (delegated to impl lane, RCO-reviewed at activation)

1. `Invoke-GrokReview.ps1` — scheduled red-team + competitor (grok-code-fast-1),
   summary input, JSON findings → bridge advisory, cap-enforced.
2. `Invoke-GrokPlanReview.ps1` — lead-only plan-vs-vision (grok-4), cap-enforced.
3. Scheduled-Task registration (addition).
4. `configs/grok_budget.json` + cap-enforcement helper + fail-closed tests.
5. Versioned prompt files per the three-part / per-panel structure above.

## Activation checklist (2026-07-01)

* Grok credits confirmed restored.
* Caps set in `configs/grok_budget.json`.
* Dry-run each helper (cap refusal + JSON schema + sanitization verified).
* RCO independent review of the helper code (per role separation; the spec is
  RCO-authored, the code is impl-lane + RCO-reviewed).
* Enable the Scheduled Task last.
