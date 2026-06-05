# Autonomous Promotion Pipeline V1

Status: proposed (RCO-authored policy; enforcement verifier to be implemented by
the impl lane and reviewed independently by RCO).
Author: claude-rco-1. Date: 2026-06-05.

## Problem

WD has become a self-measuring, claim-driven run loop, but the **draft → merge
promotion** step is still manual: producers leave PRs in `draft` until a human
(or RCO) marks them ready, and the merge driver skips drafts. This is the last
operator-in-the-loop gate for *charter-clean* work that already satisfies every
safety condition. This policy defines a fail-closed pipeline that promotes
(undraft + merge) **only** PRs that provably clear every gate, while leaving all
operator-gated work behind the operator's manual signature.

## Hard scope boundary (non-negotiable)

Autonomous promotion applies **only** to **charter-clean** PRs:

* `evaluate_paths(charter, changed_paths).allowed is True`, AND
* `evaluate_diff_content(charter, diff)` reports no denylist / privacy-canary hit.

**Operator-gated PRs are never auto-promoted.** Any PR whose paths hit the
charter `file_denylist`, fall outside the `allowlist`, or whose diff trips the
code-pattern denylist is routed to a *needs-operator-signature* queue and stays
`draft` until the operator signs it (per `CLAUDE.md` Rule 9a / 10). The pipeline
must never undraft or merge such a PR, even with full bridge consensus.

## Eligibility gates (all must hold — AND, fail-closed)

A PR is *promotion-eligible* only when **every** condition below is true. Any
unknown / unreadable / ambiguous result evaluates to **not eligible**.

1. **Charter-clean** — `evaluate_paths` allowed AND `evaluate_diff_content`
   clean (the scope boundary above).
2. **CI green** — all required checks `SUCCESS`, read from the live
   `statusCheckRollup` at the exact head (no inference, no partial-green).
3. **Producer-ready signal** — `build_consensus_pass` present from **both**
   `codex-lead-1` AND `codex-tools-1`, bound to the exact head. This replaces
   the manual undraft: it is the producers declaring the PR done. A PR with no
   such pair stays `draft` (it is still WIP by intent).
4. **RCO_PASS** — a valid `rco_pass` at the exact head from the recognized RCO
   identity (`tools/check_rco_pass_present.py`), not superseded by a later veto.
5. **No peer veto** — `tools/check_bridge_changes_requested.py` reports no
   blocking `changes_requested` as the most recent peer signal (this already
   honors *any* peer, including a backup RCO such as `claude-rco-2`).
6. **Fresh base** — the PR base is not stale relative to `origin/main`. If
   stale, the pipeline requests a rebase (or auto-rebases when the rebase is
   clean and re-runs CI); it does **not** merge a stale-base PR.
7. **Head-exact** — every consensus/approval above binds to the current head
   SHA; the merge uses `gh pr merge --match-head-commit=<full sha>`. Any
   re-push invalidates all prior approvals and requires re-consensus.

Conditions 1–2 and 4–7 are the existing autonomous-merge contract; this policy
adds **(3) producer-ready signal as the undraft trigger** and **(6) explicit
stale-base handling**, and binds them into one ordered, fail-closed check.

## Promotion sequence (executor)

For each open PR, in priority order:

1. Evaluate gates 1–2, 5. If charter-clean fails → route to needs-signature
   queue, leave `draft`, stop.
2. If base stale (gate 6) → request/auto-rebase, leave `draft`, stop (revisit
   after CI re-greens).
3. If gates 3–4 not yet satisfied → leave `draft` (waiting on producers / RCO),
   stop. (No undraft of WIP.)
4. If **all** gates hold → `gh pr ready` (undraft) **then**
   `gh pr merge --squash --match-head-commit=<head>`.
5. Emit a MAGMA-style promotion receipt recording: PR number, head SHA, the
   three consensus identities, the gate results, and the merge commit. A
   consumer must be able to re-derive eligibility from the receipt fields.

Never `--admin`, `--no-verify`, or force-push. PR-only.

## Enforcement verifier (to be implemented by impl lane)

`tools/check_promotion_eligible.py` — a fail-closed verifier the executor calls:

* Inputs: `--task-id` (canonical = PR `headRefName`), `--head`, `--pr-number`,
  `--changed-paths` (or computes from diff), `--diff`, `--events`,
  `--ci-rollup`, `--base-sha` / `--origin-main-sha`, `--rco-agent`.
* Returns structured `{eligible: bool, gate_results: {...}, reasons: [...]}`
  and exit 0 only when **all** gates pass. Absent / malformed / ambiguous
  inputs → `eligible:false` (fail-closed).
* Re-derives every verdict from inputs; never trusts an upstream `ok` flag.
* Composes the existing `evaluate_paths` / `evaluate_diff_content` /
  `check_rco_pass_present` / `check_bridge_changes_requested` /
  `verify_bridge_consensus` rather than reimplementing them.

The executor (`Invoke-BridgeMergeDriver.ps1`, an operator-side tool) calls this
verifier and performs undraft + merge only on `eligible:true`. The verifier and
its tests are the reviewed, repo-versioned policy surface; the driver is only
the executor.

## Tests (required, fail-closed proofs)

* operator-gated PR (denylist path / off-allowlist / canary in diff) →
  `eligible:false`, never promoted.
* charter-clean but missing build_consensus from lead OR tools →
  `eligible:false` (no undraft of WIP).
* charter-clean + full consensus but RCO veto present → `eligible:false`.
* charter-clean + full consensus but stale base → `eligible:false` (rebase path).
* head mismatch (re-push) → `eligible:false`.
* CI not fully green (one pending / failure) → `eligible:false`.
* all gates pass → `eligible:true` exactly once, head-exact.
* malformed / missing input on each axis → `eligible:false`.

## Out of scope (V1)

* Stage-2 atomic flip / cutover (remains operator-signed, `CLAUDE.md` Rule 10).
* Auto-promotion of operator-gated PRs (always manual signature).
* Backup-RCO PASS co-authority (separate Rule 9a amendment).
