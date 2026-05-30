# Idle Autonomy Charter v1

**Status:** operator-authorized 2026-05-18
**Authorization source:** operator memory `feedback_codex_pre_approves_all_changes_2026-05-13` extended to idle-window operation per operator directives 2026-05-18 (see Operator Quotes below).
**Companion docs:** `docs/architecture/IDLE_PROTOCOL_V1.md`, `docs/architecture/IDLE_CONSENSUS_ARTIFACT_V1.md`, `docs/architecture/MAGMA_SUBSTRATE_AUDIT_2026_05_17.md`, `docs/architecture/POLICY_SURFACE_V0.md`.

## Purpose

This charter defines the conditions under which an idle-protocol soft or hard consensus event may be **automatically promoted to a merged pull request** without operator presence in the loop.

The companion `tools/idle_consensus_to_pr.py` (Slice 5, follow-on PR) is the only tool authorized to perform this promotion. The charter document itself defines:

1. The **allowlist** of file paths an autonomous merge may modify.
2. The **denylist** of file paths an autonomous merge must refuse (DRAFT PR remains for operator review).
3. The **code pattern denylist** of substantive code changes an autonomous merge must refuse regardless of path.
4. The **parallel conditions** that must all hold for an autonomous merge.
5. The **escalation categories** that always require operator handling.

The charter is **operator-authorized** but **not operator-implemented**. Agents read and enforce it.

## Operator Quotes (verbatim, Finnish)

These are the operator-directive statements that motivated this charter. They are reproduced verbatim for substrate transparency.

* 2026-05-18 05:46Z: "halua tästä täysin automaattisen"
* 2026-05-18 05:53Z: "konsensus on maailman tehokkaimmilla agenteilla ei minulla"
* 2026-05-18 05:53Z: "muuten työ keskeytyy ja taas odotellaan"
* 2026-05-18 05:43Z: "Bridgen yksi tarkoitus on SELF envolving ja developmet perustuen analyyseihin ja faktoihin"
* 2026-05-18 05:55Z: "tavoite on mahdollisimman tehokas agenttien välinen toiminta ja autonominen"
* 2026-05-18 06:48Z: "TEHKÄÄ IDLE JA BRIDGE VALMIIKSI"
* 2026-05-29: "rakenna kuvan mukainen järjestelmä ei pelkkää substraattia, kaikki ilman operaattori kyselyitä, kyselyt hoidetaan bridgen consensuksella" (build the storyboard system, not just substrate; all without per-action operator queries; approvals via bridge consensus — see `BRIDGE_CONSENSUS_APPROVAL_V1.md`)

## Allowlist

An autonomous merge is permitted to modify files **only** within these paths. Every changed path in the PR diff must match one of these entries.

* `tools/**` (except code-pattern denylist below)
* `tests/**`
* `schemas/v3_13_0/**` (except gate fields — see code pattern denylist)
* `docs/architecture/**` (except denylist below)
* `waggledance/core/magma/**`
* `waggledance/core/idle_protocol*`
* `waggledance/core/pdam_close_solver.py`
* `waggledance/core/idle_consensus*`
* Pattern `*_helper.py` (cross-module helper extractions)
* Pattern `shared_*.py` (shared substrate code)

## Denylist (file paths)

An autonomous merge **must refuse** to modify files matching any denylist entry. The DRAFT PR remains and the `operator-review-required` label is applied. Operator must merge manually.

* `CLAUDE.md` (operator rules)
* `memory/**` (Claude auto-memory + operator-extended)
* `.agent-bridge/bin/**` (bridge gate scripts themselves)
* `configs/bridge_event_validation_waivers.json` (waiver state)
* `docs/architecture/STAGE2_CUTOVER_RFC.md`
* `docs/architecture/HUMAN_APPROVAL*.yaml*`
* `docs/architecture/IDLE_PROTOCOL_V1.md` (protocol bounds)
* `docs/architecture/MAGMA_SUBSTRATE_AUDIT_2026_05_17.md` (intellectual-honesty audit)
* `docs/architecture/POLICY_SURFACE_V0.md` (charter-essential policy)
* `docs/architecture/IDLE_AUTONOMY_CHARTER.md` (this file — self-modification banned)
* `docs/architecture/IDLE_CONSENSUS_ARTIFACT_V1.md` (companion charter doc)
* `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md` (consensus-approval contract — self-modification banned)
* `tools/idle_consensus_auto_merge.py` (the merge gate itself — self-modification banned)
* `waggledance/core/magma/consensus_receipt.py` (bridge consensus receipt writer/verifier)
* `tools/check_bridge_changes_requested.py` (RCO-veto preflight — self-modification banned)
* `.env`
* `.env.*`
* `**/.env`
* `**/.env.*`
* `*secret*`
* `**/*secret*`
* `*token*`
* `**/*token*`
* `*credential*`
* `**/*credential*`
* `deploy/**`
* `deployment/**`
* `configs/deployment/**`
* `LICENSE`, `README.md`, `pyproject.toml` top-level (manual operator review)

## Denylist (code patterns)

Even within allowlisted file paths, an autonomous merge **must refuse** if the diff content matches any of these patterns:

* Changes to `auto_execute=False` or `operator_gate_required=True` constants in any schema or solver
* Changes to `DEFAULT_MAX_INSTANCES_PER_DAY` constant in `tools/idle_protocol_activate.py`
* Changes to `_safe_label`, `_sequence_errors`, or instance-chain logic in `tools/idle_protocol_activate.py`
* Changes to `verify_manifest` ok-check or canonical digest logic in `tools/verify_magma_receipt.py` or `waggledance/core/magma/`
* Changes to denylist or allowlist content in `tools/idle_consensus_to_pr.py` (self-modification ban)
* Changes to the three-identity / RCO-pass / head-binding verification logic in `tools/idle_consensus_auto_merge.py` (consensus-gate self-modification ban)
* Changes that disable the adversarial-corpus promotion gate (T5b): flipping `require_adversarial_gate` to a `False` default, weakening `ADVERSARIAL_CORPUS_MIN_CASES`, or altering `verify_adversarial_corpus_gate` re-derivation/binding in `waggledance/core/magma/adversarial_gate.py` or `auto_promotion_engine.py` (gate self-modification ban; opt-out is test-only)
* Changes to `PRIVATE_MARKER` constant or `_DO_NOT_LEAK` detection logic anywhere
* Changes that REMOVE existing `write_receipt_bundle` or `verify_manifest` calls

## Parallel conditions for autonomous merge

All seven conditions must hold. Failure of any one downgrades the PR to operator-review-required.

1. **Consensus**: soft or hard convergence reached in an idle-protocol instance (`waggledance/core/idle_protocol.detect_idle_convergence` returns `soft_convergence` or `hard_convergence`).
2. **CI green**: all required GitHub status checks pass.
3. **Receipt verified**: a MAGMA receipt bundle is written for the consensus and `verify_manifest` returns `ok=True` (via `waggledance/core/magma/receipt_bundle.write_receipt_bundle`).
4. **Rate limit**: within the UTC-day cap of `DEFAULT_MAX_AUTONOMOUS_MERGES_PER_DAY = 5` (matches `tools/idle_protocol_activate.DEFAULT_MAX_INSTANCES_PER_DAY`).
5. **Mergeable clean**: GitHub reports `mergeable: clean` (no conflicts).
6. **Allowlist match**: every changed file path in the diff matches an allowlist entry.
7. **No denylist hit**: no changed file path matches the file denylist and no diff content matches the code-pattern denylist.

## Bridge-consensus approval path (2026-05-29)

The **Consensus** condition (#1 above) may be satisfied either by idle-protocol
soft/hard convergence **or** by a verified **three-agent bridge consensus**, per
the operator's 2026-05-29 directive (approvals via bridge consensus, not
per-action operator queries). The full contract is in
`docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`; the gate
(`tools/idle_consensus_auto_merge.py`) enforces it fail-closed (enforcement
lands in Track T0b). In summary, a bridge consensus requires **all** of:

* **three distinct verified identities** — lead (`codex-lead-1`) + tools peer
  (`codex-tools-1`) build consensus, plus an independent `claude-rco-1`
  `RCO_PASS`; duplicate/missing/unverifiable identities or a 2-of-3 set fail
  closed;
* **RCO veto + RCO absence = no merge** — any `claude-rco-1`
  `finding`/`changes_requested` blocks; absence of an explicit `RCO_PASS` at the
  exact head also blocks (silence never default-allows);
* **head-exact binding** — all three approvals bind to the exact head SHA; any
  re-push invalidates them and requires re-consensus;
* the seven parallel conditions above, plus a **MAGMA receipt** recording the
  three identities + head SHA + `RCO_PASS` reference, re-derivable by a consumer.

This path governs **MERGE only**. It does **not** authorize the Stage-2 cutover,
which stays operator-signed (Rule 10 / escalation category 5) until a separate
future amendment.

## Escalation categories (always operator-required)

The following always escalate to operator review regardless of consensus, even when the changed paths are in the allowlist. These mirror the five boundary categories in operator memory `feedback_codex_pre_approves_all_changes_2026-05-13`.

1. **Destructive filesystem or git** — `rm -rf`, force push, branch deletion, history rewrite, lock-file removal, RAM-disk operations.
2. **Credentials** — API keys, tokens, `.env`, key rotation, vault changes.
3. **External payment** — anything touching billing, Stripe, payment processors, paid third-party APIs.
4. **Unresolved write-scope conflict** — two agents claim overlapping write scope without bridge-resolved consensus.
5. **Legally / security sensitive** — license changes, atomic-flip cutover (per `CLAUDE.md` rule #10), security advisories, public-disclosure surfaces.

## Daily rate-limit details

The rate-limit is **5 autonomous merges per UTC day**. The counter resets at UTC midnight.

The autonomous-merge tool must increment the counter only on successful merge (not on DRAFT creation, not on refused merge). The counter is maintained in `docs/handoffs/idle_auto_merges_{YYYY-MM-DD}.md` (Slice 6 future) and cross-referenced in the receipt bundle of each autonomous merge.

A sixth attempt in the same UTC day is refused with `rate_limited` exit code; the DRAFT PR remains for operator review.

## Operator handoff

For every autonomous merge:

* The PR title is prefixed `[IDLE-CONSENSUS] <proposal_id>`.
* The PR body contains the full deliberation transcript (rounds 1..N) and a link to the local MAGMA receipt bundle.
* The PR is created as DRAFT initially; the autonomous-merge tool promotes it to ready and merges only after all parallel conditions hold.
* Each merge appends an entry to `docs/handoffs/idle_auto_merges_{YYYY-MM-DD}.md` with the PR number, consensus proposal ID, receipt-bundle path, CI status, and a one-line `git revert` command.

## Self-modification ban

This charter document is on the file denylist. Any PR that modifies `docs/architecture/IDLE_AUTONOMY_CHARTER.md` itself must be operator-merged. Agents cannot weaken their own gates.

The same ban applies to denylist edits inside `tools/idle_consensus_to_pr.py`, to the consensus-verification logic inside `tools/idle_consensus_auto_merge.py` and `tools/check_bridge_changes_requested.py`, to `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`, and to operator-gate constants throughout the substrate. A bridge consensus can never self-approve a change to the gate that evaluates it — such PRs always operator-merge.

## Charter scope

This charter authorizes **automatic merges for idle-protocol consensus events and for verified three-agent bridge-consensus events** (see the bridge-consensus approval path above). It does not authorize:

* Automatic merges of operator-directed PRs (operator continues to merge those manually unless explicitly authorized).
* Automatic merges of dependabot PRs (separate operator policy may authorize batch handling).
* Automatic merges driven by sources other than idle-protocol consensus.
* Cron-driven idle-protocol activation (operator OS-level scheduling remains out-of-repo).
* Modification of this charter or any companion charter doc.

The charter is a **substrate-defensive permit** that explicitly enumerates the operator-authorized autonomy surface. Anything not explicitly allowed remains operator-gated.

## Revocation

The operator may revoke this charter at any time by:

* Reverting this file (the autonomous-merge tool reads it at runtime).
* Adding a `CHARTER_DISABLED` constant in `tools/idle_consensus_to_pr.py` (this constant addition is on the code-pattern denylist so it must operator-merge — that is the intended emergency-stop semantics).
* Direct bridge-message instruction to all active agents.

Substrate-defensive invariant: revocation must succeed even when the autonomous-merge tool is running.
