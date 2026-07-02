# Autonomous Promotion Pipeline V1

Status: proposed (RCO-authored policy; enforcement verifier to be implemented by
the impl lane and reviewed independently by RCO).
Author: claude-rco-1. Date: 2026-06-05.

Implementation status corrected 2026-07-02: this remains a proposed pipeline.
The content-identical rebase carry-forward path described below is **not active
runtime behavior**. Today's merge gate binds approvals strictly to the exact
head, so every re-push/rebase requires fresh consensus posts at the new head
until the P3 predicate and gate wiring land as reviewed gate code.

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
6. **Fresh base** — the PR is merged against current `origin/main`. A stale base
   is **not** merged as-is. Current runtime gates do **not** implement the
   content-identical carry-forward carve-out, so any rebase/re-push requires
   approvals to be re-posted at the new head before promotion.
7. **Head-exact / content-identity** — every consensus/approval binds to the head
   SHA; the merge uses `gh pr merge --match-head-commit=<full sha>`. A
   content-changing re-push forces re-consensus; in current code, a
   **content-identical base rebase** also requires re-consensus because the
   carry-forward carve-out is specified but not implemented
   (`BRIDGE_CONSENSUS_APPROVAL_V1.md` §Head-exact binding).

Conditions 1–2 and 4–7 are the existing autonomous-merge contract; this policy
adds **(3) producer-ready signal as the undraft trigger**, **(6) serial-rebase
stale-base handling**, and binds them into one ordered, fail-closed check.

## Proposed serial merge queue (breaks the stale-base deadlock after P3 wiring)

With head-exact binding and many concurrent PRs, **every merge re-stales the
rest** — a naive "refuse stale base" gate deadlocks the queue. Once P3 is
implemented, the executor can drain eligible PRs **serially**:

1. Pick the highest-priority PR that passes gates 1–5 (charter-clean, CI green,
   producer-ready, RCO_PASS, no veto).
2. If its base ≠ current `origin/main`: **rebase onto current main** and require
   consensus approvals to be re-posted at the rebased head. A future
   content-identical carry-forward path is specified in the approval contract,
   but it is not implemented in current gate code.
3. **Re-run CI** against the rebased head; require green (catches semantic skew
   from the advanced base).
4. Merge with `--match-head-commit=<rebased head>`.
5. Move to the next PR (now stale → rebase again). Repeat until the queue drains
   or a PR fails a gate.

Serial processing still drains one merge at a time, but until carry-forward is
implemented it may require a re-consensus treadmill after rebases. Re-CI per step
preserves the skew guard. (A second RCO `claude-rco-2` and a cheap cross-model
grok review add review throughput for the cases that need re-consensus.)

## Promotion sequence (executor, per PR)

1. Evaluate gates 1–2, 5. If charter-clean fails → route to needs-signature
   queue, leave `draft`, stop.
2. If gates 3–4 not yet satisfied → leave `draft` (waiting on producers / RCO),
   stop. (No undraft of WIP.)
3. If base stale → today, refresh the base, re-run CI, and collect fresh
   consensus at the rebased head. After P3 gate wiring exists, serial-rebase +
   re-CI + content-identity check may preserve content-review approvals.
4. If **all** gates hold at the (possibly rebased) head → `gh pr ready` (undraft)
   **then** `gh pr merge --squash --match-head-commit=<head>`.
5. Emit a MAGMA-style promotion receipt recording: PR number, head SHA (and prior
   approved head if rebased + the content-identity proof), the three consensus
   identities, the gate results, and the merge commit. A consumer must be able to
   re-derive eligibility from the receipt fields.

Never `--admin`, `--no-verify`, or force-push. PR-only.

## Enforcement verifier (to be implemented by impl lane)

`tools/check_promotion_eligible.py` — a fail-closed verifier the executor calls:

* Inputs: `--task-id` (canonical = PR `headRefName`), `--head`, `--pr-number`,
  `--changed-paths` (or computes from diff), `--diff`, `--events`,
  `--ci-rollup`, `--base-sha` / `--origin-main-sha`, `--rco-agent` (repeatable
  set, see §backup-RCO). The previously specified carry-forward inputs
  (`--prior-approved-head` and prior approved diff) are dormant until the gate
  code implements that carve-out.
* Returns structured `{eligible: bool, gate_results: {...}, reasons: [...],
  base_status: fresh|content_identical_rebase|content_changed|stale,
  carry_forward: bool}` and exit 0 only when **all** gates pass. Absent /
  malformed / ambiguous inputs → `eligible:false` (fail-closed).
* **Content-identity check (carry-forward, future P3 wiring):** when `--head` ≠
  the prior approved head, compute whether the diff `merge_base..head` is
  **byte-identical** to the prior approved head's diff. If identical →
  `base_status=content_identical_rebase`, `carry_forward=true`: prior RCO_PASS +
  build_consensus count for the new head, **but the CI gate must independently
  pass at the new head** (carry-forward never covers CI). If any difference →
  `carry_forward=false`, `base_status=content_changed`, full re-consensus
  required (`eligible:false` until re-consensus at the new head). Until this is
  implemented in gate code, `carry_forward` must remain false.
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
* charter-clean + full consensus but stale base, not yet rebased → `eligible:false`.
* **content-identical base rebase** (diff byte-identical to prior approved head)
  + CI green at new head → `carry_forward=true`, `eligible:true` (approvals carry).
* **content-changed re-push** (diff differs after rebase) → `carry_forward=false`,
  `eligible:false` until full re-consensus at the new head.
* content-identical rebase but CI **not** re-run green at new head →
  `eligible:false`.
* head mismatch with no prior-approved-head provided → `eligible:false`.
* CI not fully green (one pending / failure) → `eligible:false`.
* all gates pass (fresh base) → `eligible:true` exactly once, head-exact.
* malformed / missing input on each axis → `eligible:false`.

## Out of scope (V1)

* Stage-2 atomic flip / cutover (remains operator-signed, `CLAUDE.md` Rule 10).
* Auto-promotion of operator-gated PRs (always manual signature).
* Backup-RCO PASS co-authority (separate Rule 9a amendment).
