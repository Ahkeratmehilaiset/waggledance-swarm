# ADR-016 — Atomic-flip uses existing Stage-2 RFC; no parallel mechanism

Status: Accepted for EIG2-M0 (Codex peer-review signed 2026-05-11)
Author: Claude (Reality Check Owner, EIG2 Part 12.7)
Peer reviewer: Codex (signed 2026-05-11)
Date: 2026-05-11
R-rule: R16 (Claude addition, agreed in bridge thread `claude-eig2-coldrehearsal-2026-05-11`)

## Context

EIG2 Part 28.4 introduces a flag-based auto-merge mechanism:
- File: `.eig2.autonomous_merge` at repo root.
- Content rule: if startup snapshot reads exactly `YES_I_UNDERSTAND_THE_RISK`, ACCEPTED verdict → fast-forward merge to main + tag without `-alpha` suffix.
- Snapshot is immutable: mid-run changes to the flag are ignored.

The WaggleDance repository already has a separate atomic-flip mechanism for production cutovers:
- File: `docs/architecture/STAGE2_CUTOVER_RFC.md` (the Stage-2 cutover RFC).
- Gate: `HUMAN_APPROVAL.yaml` signed by operator at execution time.
- CLAUDE.md rule 10: "Atomic-flip discipline... HUMAN_APPROVAL.yaml... Approval is one-shot and belongs only to the actual cutover execution session — the operator signs once at execution time."
- CLAUDE.md rule 10 explicitly forbids collecting `HUMAN_APPROVAL.yaml` during design / build / docs sessions.

Two parallel mechanisms for "operator authorizes irreversible behavior change" is a contradiction: either mechanism alone is a complete gate, but together they are confusing, and the EIG2 mechanism is weaker (snapshot is created by the agent process; the operator's signature is by file existence at startup, not active sign-off at flip time).

## Decision

EIG2 does NOT introduce a parallel atomic-flip mechanism. The `.eig2.autonomous_merge` file at repo root is REPURPOSED as a *signal of operator intent only*, never as merge authority:

1. If the startup-snapshotted `.eig2.autonomous_merge` content equals `YES_I_UNDERSTAND_THE_RISK`, the final report produces an `auto-merge: assisted` annotation, AND the PR opened by the EIG2 session gains a GitHub label `auto-merge:assisted`. (See R17 / ADR 017 for the label semantics.)
2. The label is informational. It does NOT auto-merge. A human reviewer sees the label and decides.
3. If the EIG2 work ever needs an actual atomic flip (e.g., promoting a new MAGMA storage format, switching the routing topology, replacing the LLM chain wiring), the change goes through `docs/architecture/STAGE2_CUTOVER_RFC.md`:
   - Operator authors a Stage-2 cutover RFC referencing the EIG2 change.
   - `HUMAN_APPROVAL.yaml` is collected at execution time, NOT at design time.
   - The cutover execution session is a distinct session, operator-driven, with one-shot approval.
4. EIG2 Part 28.4 wording is reinterpreted via this ADR: the "auto-merge to main" provision applies only inside the bounded scope of M0–M2 docs/config landings where CLAUDE.md rule 9's autonomous-merge guardrails already hold (clean mergeable, all required CI green, no rule violated, head SHA match). It does NOT grant authority for production-runtime cutovers.

## Alternatives considered

1. **Keep both mechanisms independent.** Rejected: two gates for the same kind of decision is a recipe for operator-confusion, and the EIG2 flag mechanism is structurally weaker (file existence vs. active sign-off).
2. **Drop `.eig2.autonomous_merge` entirely.** Rejected: EIG2 prompt explicitly names it. Reuse keeps continuity with the prompt; reinterpretation neutralizes the conflict.
3. **Have EIG2 auto-merge to main without the Stage-2 RFC.** Rejected: violates CLAUDE.md rule 10 atomic-flip discipline.

## Consequences

- **Single source of truth for production atomic flips**: STAGE2_CUTOVER_RFC.md.
- **EIG2 work can still autonomously land docs/config/skeleton PRs** under CLAUDE.md rule 9 guardrails (no special EIG2 mechanism required for this — rule 9 already permits it).
- **`.eig2.autonomous_merge` becomes a documented signaling artifact**, not a merge authorizer. Its presence at startup expresses operator intent; its effect is to label PRs, not bypass review.
- **EIG2 final report `eig2_default_enabled_actual` field** still operates correctly: it reflects the runtime config, not the merge mechanism, which is what Part 28.3 actually requires.

## Safety impact

Strongly positive. Eliminates a parallel/weaker atomic-flip mechanism. CLAUDE.md rule 10 stays load-bearing without exception.

## Performance impact

Zero.

## MAGMA invariant impact

None directly. Indirectly protects: prevents EIG2 from gaining a back-door to MAGMA schema/storage migrations without Stage-2 RFC review.

## Audit / regression class

Maps to `LICENSE_COMPLIANCE_ISSUE` or `INVARIANT_BREAK` (Part 19 RegressionClass enum, operator-discretion which) if violated: any PR that performs an atomic flip (production-runtime behavior change, storage migration, schema breakage) without a referenced STAGE2_CUTOVER_RFC.md entry is rejected at the M6 trust-filter gate.

## Reviewed by other agent

Codex reviewed and endorses. The decision keeps EIG2 autonomous implementation
separate from production atomic-flip authority and preserves the existing
STAGE2_CUTOVER_RFC path as the only production flip mechanism.

## Related tests

- (existing) Whatever current `tests/integration/test_stage2_cutover*.py` tests already cover the RFC mechanism (verify file paths during M0 PR3 review).
- (planned, M1/M8) `tests/orchestrator/test_bridge_classify_atomic_flip_without_rfc.py` — classifier emits `INVARIANT_BREAK` for any PR diff containing `.eig2.autonomous_merge` content equal to `YES_I_UNDERSTAND_THE_RISK` without an accompanying STAGE2 reference.
- (planned, PR3) `tests/contracts/test_eig2_atomic_flip_via_stage2_only.py` — contract test that fails the M0 PR3 if this ADR's rules are weakened.

## Provenance

Generalized from R16 binding-rule discussion in bridge thread `claude-eig2-coldrehearsal-2026-05-11` ts `2026-05-11T17:22:51.156126Z`. Direct evidence: CLAUDE.md rule 10 (Atomic-flip discipline). Tension surface: EIG2 Part 28.4 "auto-merge to `main` (opt-in only)". Resolution: re-scope EIG2's auto-merge to bounded M0–M2 docs/config landings under rule 9's existing guardrails; production cutovers remain Stage-2 only.

## Date

2026-05-11

## Sign-off

- Author (Claude): signed.
- Peer reviewer (Codex): signed 2026-05-11.
