# Autonomous Promotion Pipeline V1

Status: proposed (RCO-authored policy; enforcement verifier to be implemented by
the impl lane and reviewed independently by RCO).
Author: claude-rco-1. Date: 2026-06-05.

## Problem

WD has become a self-measuring, claim-driven run loop, but the **draft → merge
promotion** step is still manual: producers leave PRs in `draft` until a human
(or RCO) marks them ready, and the merge driver skips drafts. This is the last
operator-in-the-loop gate for work that already satisfies every safety condition.
This policy defines a fail-closed pipeline that promotes (undraft + merge)
**only** PRs that provably clear every gate, while leaving self-modification,
runtime activation, irreversible/outward-facing work, and secrets/payments behind
the operator's manual signature.

## Hard scope boundary (non-negotiable)

Autonomous promotion applies to PRs with **repo-versioned path authority**:

* `evaluate_paths(charter, changed_paths).allowed is True`, OR
* the executable `standing_consensus_sign` gate verifies best-possible bridge
  consensus for a normal off-allowlist / eligible high-scrutiny path, AND
* `evaluate_diff_content(charter, diff)` reports no denylist / privacy-canary hit.

**Operator-explicit carve-outs are never auto-promoted.** Any PR that touches the
merge/sign/veto verdict path, Rule-10 runtime activation, irreversible or
outward-facing actions, credentials/secrets/payments, or whose diff trips the
code-pattern denylist is routed to a *needs-operator-signature* queue and stays
`draft` until the operator signs it. Normal off-allowlist path mismatches do not
stop the loop once `standing_consensus_sign` verifies best-possible bridge
consensus at the exact head.

## Eligibility gates (all must hold — AND, fail-closed)

A PR is *promotion-eligible* only when **every** condition below is true. Any
unknown / unreadable / ambiguous result evaluates to **not eligible**.

1. **Path authority + content clean** — `evaluate_paths` allowed OR
   `standing_consensus_sign` verified, and `evaluate_diff_content` clean (the
   scope boundary above).
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
   is **not** merged as-is; instead the executor **rebases onto current main**,
   re-runs CI, and (per the head-exact carry-forward rule) carries consensus
   approvals forward iff the rebase is content-identical. See "Serial merge queue"
   below — this is what breaks the stale-base deadlock.
7. **Head-exact / content-identity** — every consensus/approval binds to the head
   SHA; the merge uses `gh pr merge --match-head-commit=<full sha>`. A
   content-changing re-push forces re-consensus; a **content-identical base
   rebase** carries approvals forward but still requires a fresh CI green at the
   new head (`BRIDGE_CONSENSUS_APPROVAL_V1.md` §Head-exact binding).

Conditions 1–2 and 4–7 are the existing autonomous-merge contract; this policy
adds **(3) producer-ready signal as the undraft trigger**, **(6) serial-rebase
stale-base handling**, and binds them into one ordered, fail-closed check.

## Serial merge queue (breaks the stale-base deadlock)

With head-exact binding and many concurrent PRs, **every merge re-stales the
rest** — a naive "refuse stale base" gate deadlocks the queue. The executor
therefore drains eligible PRs **serially**:

1. Pick the highest-priority PR that passes gates 1–5 (charter-clean, CI green,
   producer-ready, RCO_PASS, no veto).
2. If its base ≠ current `origin/main`: **rebase onto current main**. Prove the
   rebase is **content-identical** (diff `merge_base..head` byte-identical to the
   prior approved head's diff). If content changed (conflict edits) → drop to
   full re-consensus, skip this PR for now.
3. **Re-run CI** against the rebased head; require green (catches semantic skew
   from the advanced base). Consensus approvals carry forward (content unchanged).
4. Merge with `--match-head-commit=<rebased head>`.
5. Move to the next PR (now stale → rebase again). Repeat until the queue drains
   or a PR fails a gate.

Serial processing + content-identical carry-forward means the queue drains one
merge at a time without a re-review treadmill, while re-CI per step preserves the
skew guard. (A second RCO `claude-rco-2` and a cheap cross-model grok review add
review throughput for the cases that *do* need re-consensus.)

## Promotion sequence (executor, per PR)

1. Evaluate gates 1–2, 5. If charter-clean fails → route to needs-signature
   queue, leave `draft`, stop.
2. If gates 3–4 not yet satisfied → leave `draft` (waiting on producers / RCO),
   stop. (No undraft of WIP.)
3. If base stale → serial-rebase + re-CI + content-identity check (above). If the
   rebase is not content-identical → full re-consensus required, stop.
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
  set, see §backup-RCO). For the carry-forward path also:
  `--prior-approved-head` and the prior approved diff (or compute both diffs).
* Returns structured `{eligible: bool, gate_results: {...}, reasons: [...],
  base_status: fresh|content_identical_rebase|content_changed|stale,
  carry_forward: bool}` and exit 0 only when **all** gates pass. Absent /
  malformed / ambiguous inputs → `eligible:false` (fail-closed).
* **Content-identity check (carry-forward):** when `--head` ≠ the prior approved
  head, compute whether the diff `merge_base..head` is **byte-identical** to the
  prior approved head's diff. If identical → `base_status=content_identical_rebase`,
  `carry_forward=true`: prior RCO_PASS + build_consensus count for the new head,
  **but the CI gate must independently pass at the new head** (carry-forward never
  covers CI). If any difference → `carry_forward=false`, `base_status=content_changed`,
  full re-consensus required (`eligible:false` until re-consensus at the new head).
* Re-derives every verdict from inputs; never trusts an upstream `ok` flag.
* Composes the existing `evaluate_paths` / `evaluate_diff_content` /
  `check_rco_pass_present` / `check_bridge_changes_requested` /
  `verify_bridge_consensus` rather than reimplementing them.

The executor (`Invoke-BridgeMergeDriver.ps1`, an operator-side tool) calls this
verifier and performs undraft + merge only on `eligible:true`. The verifier and
its tests are the reviewed, repo-versioned policy surface; the driver is only
the executor.

## Tests (required, fail-closed proofs)

* operator-explicit PR (verdict-path carve-out / runtime activation / canary in
  diff) → `eligible:false`, never promoted.
* normal off-allowlist PR + best-possible bridge consensus + dual-RCO exact-head
  pass → `eligible:true`.
* charter-clean but missing build_consensus from lead OR tools →
  `eligible:false` (no undraft of WIP).
* charter-clean + full consensus but RCO veto present → `eligible:false`.
* charter-clean + full consensus but stale base, not yet rebased → `eligible:false`.
* **content-identical base rebase** (diff byte-identical to prior approved head)
  + CI green at new head → `carry_forward=true`, `eligible:true` (approvals carry).
* **content-changed re-push** (diff differs after rebase) → `carry_forward=false`,
  `eligible:false` until full re-consensus at the new head.
* content-identical rebase but CI **not** re-run green at new head →
  `eligible:false` (carry-forward never covers CI).
* head mismatch with no prior-approved-head provided → `eligible:false`.
* CI not fully green (one pending / failure) → `eligible:false`.
* all gates pass (fresh base) → `eligible:true` exactly once, head-exact.
* malformed / missing input on each axis → `eligible:false`.

## Out of scope (V1)

* Stage-2 atomic flip / cutover (remains operator-signed, `CLAUDE.md` Rule 10).
* Auto-promotion of operator-explicit carve-outs (always manual signature).
* Backup-RCO PASS co-authority (separate Rule 9a amendment).
