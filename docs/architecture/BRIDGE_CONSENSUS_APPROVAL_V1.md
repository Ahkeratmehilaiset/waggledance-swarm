# Bridge-Consensus Approval v1

**Status:** operator-authorized 2026-05-29 (plan-approval bootstrap).
**Authorization source:** operator directive 2026-05-29 — *"rakenna kuvan
mukainen järjestelmä ei pelkkää substraattia, kaikki ilman operaattori
kyselyitä, kyselyt hoidetaan bridgen consensuksella"* (build the storyboard
system, not just substrate; all without per-action operator queries; approvals
handled by bridge consensus). Extends the prior idle-window authorization in
`IDLE_AUTONOMY_CHARTER.md` (2026-05-18: *"konsensus on maailman tehokkaimmilla
agenteilla ei minulla"*).
**Companion docs:** `IDLE_AUTONOMY_CHARTER.md`, `STAGE2_CUTOVER_RFC.md`,
`CONTINUOUS_PLAN_TO_VISION.md`, `CLAUDE.md` (Rules 6/9/10).
**Enforcement point:** `tools/idle_consensus_auto_merge.py` (the
`bridge_consensus` approver is added in Track **T0b**; this doc — Track **T0a**
— defines the contract the code must implement).

## Purpose

Move the **approval authority for an autonomous MERGE** from a *per-action
operator query/signature* to a **verified three-agent bridge consensus**. This
lets the swarm land active-development PRs (the `CONTINUOUS_PLAN_TO_VISION.md`
tracks) at machine speed while preserving an independent safety gate.

This document governs **MERGE only**. It explicitly does **not** govern the
Stage-2 atomic-flip cutover (see §"Out of scope" and `STAGE2_CUTOVER_RFC.md`).

## The bridge-consensus contract (fail-closed)

An autonomous merge is approved **only if all of the following hold**. Any
missing, duplicated, forged, or stale signal fails closed to
`operator_review_required` — the gate never default-allows.

1. **Build consensus** — two distinct verified identities, the lead
   (`codex-lead-1`) and the tools/impl peer (`codex-tools-1`), both concur on
   the change.
2. **Independent RCO pass** — a **recognized RCO identity** posts an explicit
   `RCO_PASS` (`type=decision` with a status in the approval set) on the PR's
   **canonical task_id** (= branch name) at the **exact head SHA**. The
   recognized RCO set is `{claude-rco-1, claude-rco-2}` (backup-RCO co-authority,
   added 2026-06-05). A valid `RCO_PASS` from **either** identity satisfies the
   RCO slot, giving availability when one RCO is offline. The passing RCO **must
   not be the PR author** (author ≠ reviewer): if a recognized RCO authored the
   PR, only the *other* recognized RCO can satisfy the RCO slot.
3. **RCO veto is absolute and per-identity** — any `finding`/`changes_requested`
   from **any** recognized RCO identity on that task blocks the merge
   (`tools/check_bridge_changes_requested.py`). A veto **outranks a pass**: if
   one recognized RCO passes while the other holds an unretracted veto at the
   same head, the gate is blocked. RCO is never out-voted, and the backup RCO
   can never be used to out-vote a veto.
4. **RCO absence = NO merge** — if no recognized RCO `RCO_PASS` at the exact head
   is present, the gate refuses even when build-consensus and every charter
   condition pass. Silence blocks; it does not default-allow.
5. **Three distinct identities** — the approval set is build-lead + build-tools +
   exactly one recognized RCO = three distinct verified identities. An RCO
   identity counts for the RCO slot only, never a build slot. Duplicate, missing,
   unverifiable, self-approving, or author-as-own-reviewer signal sets fail
   closed.
6. **Head-exact binding** — all three approvals bind to the exact head SHA. Any
   re-push that **changes content** invalidates all prior approvals; re-consensus
   is required (mirrors `gh pr merge --match-head-commit` and the PR #777
   head-drift fail-close). **Exception — content-identical base rebase
   (2026-06-05):** a pure rebase onto current `origin/main` with **no content
   change** carries the consensus approvals (RCO_PASS + build_consensus) forward
   to the new head, because the reviewed content is unchanged. The verifier
   proves content-identity mechanically: the diff `merge_base..head` at the new
   head is byte-identical to the diff at the prior approved head (no
   conflict-resolution edits). CI **must be re-run green against the new head**
   before merge (carry-forward covers content review only, never CI — the
   advanced base can introduce semantic skew). Any content difference forfeits
   carry-forward and forces full re-consensus. This breaks the stale-base
   deadlock (every merge re-stales the rest) without weakening content review.
7. **All existing charter conditions still hold** — the seven parallel
   conditions in `IDLE_AUTONOMY_CHARTER.md` (CI green, receipt verified, rate
   limit, mergeable clean, allowlist match, no denylist hit) are unchanged and
   additive to this contract.
8. **MAGMA receipt** — the merge emits a MAGMA receipt recording the three
   identities, the head SHA, and the `RCO_PASS` event reference. A consumer
   must be able to **re-derive** the verdict from those fields; trusting a bare
   `ok` flag is forbidden (fail-open-recurs).

## Enforcement of backup-RCO co-authority (2026-06-05 amendment)

The recognized-RCO-set semantics above require these enforcement changes
(implemented by the impl lane, reviewed independently by the *other* RCO):

* `tools/check_rco_pass_present.py` — accept `--rco-agent` as a **set** (repeat
  the flag). Return ok when a valid `RCO_PASS` at the exact head exists from
  **any** recognized identity that is **not** the PR author, and is not
  superseded by that identity's own later veto. Default set `{claude-rco-1,
  claude-rco-2}`. Fail-closed on empty set / missing / malformed.
* `tools/idle_consensus_auto_merge.py` (`verify_bridge_consensus`) — treat the
  RCO slot as satisfied by exactly one recognized RCO identity (≠ author),
  count it for the RCO slot only, and keep the three-distinct-identity check.
  A veto from **either** recognized RCO at the head blocks (veto outranks pass).
* `tools/check_bridge_changes_requested.py` — already honors *any* peer veto
  (peer = agent ≠ merging-agent), so either RCO's `changes_requested` already
  blocks; no change needed, but its behavior is now contractually required.
* Executor (`Invoke-BridgeMergeDriver.ps1`, operator-side) — pass the recognized
  RCO set; record which RCO satisfied the slot in the receipt.

Required fail-closed tests: rco-1-only pass → ok; rco-2-only pass → ok;
author-RCO self-pass (rco-2 authored + rco-2 pass, no rco-1 pass) → not ok;
one passes + other vetoes at head → blocked; neither passes → blocked; pass at
stale head → blocked; duplicate identity → not three-distinct.

`tools/idle_consensus_auto_merge.py` and `CLAUDE.md` are on the charter file
denylist, so this amendment is **operator-gated** and lands with the operator's
signature (per "Bootstrap authority" below); it cannot ride its own gate.

## Out of scope (stays operator-gated)

The **Stage-2 atomic-flip cutover** (runtime read-path migration; e.g.
`hex_mesh.enabled` flip, `hybrid_retrieval candidate→production`, live
subdivision) remains **operator-signed** under `CLAUDE.md` Rule 10 and
escalation category 5 of the charter. Consensus-gated cutover is deferred to a
**separate future amendment (Track T0c)** explicitly gated on:

* a matured synthetic adversarial corpus (Track T5) beyond today's seed set;
* a proven, tested **auto-rollback** path for the flip;
* an existing **post-cutover verification harness**.

Granting cutover authority before those dependencies are real would make the
extra cutover gate hollow. This is a deliberate sequencing choice.

## Self-modification protection (bootstrap safety)

The gate must not be able to weaken itself via the mechanism it grants. Track
T0a therefore adds to the charter file denylist:

* `tools/idle_consensus_auto_merge.py` (the merge gate itself);
* `tools/check_bridge_changes_requested.py` (the RCO-veto preflight).

Together with the pre-existing denylist entries (`CLAUDE.md`,
`IDLE_AUTONOMY_CHARTER.md`, `STAGE2_CUTOVER_RFC.md`, the operator-gate
constants), this ensures any change to the consensus machinery itself remains
operator-merged — an autonomous bridge consensus cannot self-approve a change
to its own gate.

## Bootstrap authority for T0a/T0b

`CLAUDE.md` and the charter docs are on the denylist, so the PRs that
*establish* this mechanism cannot ride it. They land via normal PR + an
independent `claude-rco-1` `RCO_PASS` + build consensus, with the **operator's
2026-05-29 plan approval** as the one-time authorization for the governance
change itself. Subsequent (non-governance) PRs use the consensus contract
above.

## Operator emergency-stop remains authoritative

Nothing here weakens the operator's ability to stop the loop. The charter's
revocation layers still apply and must succeed even while the merge tool is
running: revert the charter file; add a `CHARTER_DISABLED` constant (on the
code-pattern denylist, so it operator-merges); or a direct bridge instruction
to all active agents. The operator owns the charter; the substrate proves every
merge against it.

## Versioning

* v1 (this doc): three-agent fail-closed MERGE consensus; cutover explicitly
  out of scope. Enforcement lands in T0b; cutover loosening deferred to T0c.
