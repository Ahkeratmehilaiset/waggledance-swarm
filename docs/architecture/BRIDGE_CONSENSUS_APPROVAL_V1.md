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
2. **Independent RCO pass** — `claude-rco-1` posts an explicit `RCO_PASS`
   (`type=decision` with a status in the approval set) on the PR's **canonical
   task_id** (= branch name) at the **exact head SHA**.
3. **RCO veto is absolute** — any `finding`/`changes_requested` from
   `claude-rco-1` on that task blocks the merge
   (`tools/check_bridge_changes_requested.py`). RCO is never out-voted.
4. **RCO absence = NO merge** — if no explicit `claude-rco-1` `RCO_PASS` at the
   exact head is present, the gate refuses even when build-consensus and every
   charter condition pass. Silence blocks; it does not default-allow.
5. **Three distinct identities** — duplicate, missing, or unverifiable agent
   identities are refused. A 2-of-3 or self-approving signal set fails closed.
6. **Head-exact binding** — all three approvals bind to the exact head SHA. Any
   re-push invalidates all prior approvals; re-consensus is required (mirrors
   `gh pr merge --match-head-commit` and the PR #777 head-drift fail-close).
7. **All existing charter conditions still hold** — the seven parallel
   conditions in `IDLE_AUTONOMY_CHARTER.md` (CI green, receipt verified, rate
   limit, mergeable clean, allowlist match, no denylist hit) are unchanged and
   additive to this contract.
8. **MAGMA receipt** — the merge emits a MAGMA receipt recording the three
   identities, the head SHA, and the `RCO_PASS` event reference. A consumer
   must be able to **re-derive** the verdict from those fields; trusting a bare
   `ok` flag is forbidden (fail-open-recurs).

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
