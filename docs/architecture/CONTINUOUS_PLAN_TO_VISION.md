# Continuous Plan to Vision — storyboard WaggleDance, consensus-gated

**Status:** operator-authorized 2026-05-29 (plan approval).
**Authorization source:** operator directive 2026-05-29 — build the full system
shown in the `WaggleDanceSwarmAi.png` storyboard (not just substrate/shadow),
without per-action operator queries; approvals via bridge consensus.
**Companion docs:** `BRIDGE_CONSENSUS_APPROVAL_V1.md`, `DREAM_MODE_AGENDA.md`,
`IDLE_AUTONOMY_CHARTER.md`, `V12_VERIFIABLE_LEARNING_LOOP.md`,
`STAGE2_CUTOVER_RFC.md`.

## Purpose

This is the tracked, versioned backlog the three bridge agents poll each idle
round (via `tools/agent_next_task.py`) to drive WaggleDance from its current
state to the storyboard end-state. It complements `DREAM_MODE_AGENDA.md` (which
keeps idle rounds on strategy) with an explicit gap-closing track structure and
per-panel definition-of-done.

## Where we are vs. the storyboard (honest gap, 2026-05-29)

The codebase is **ahead of the image's "v3.8.0" caption — it is v3.12.0 /
v3.13.0**. Panels 1–3 are essentially done; the frontier is panel 4 flowing
into the panel 5–6 "future". The dominant remaining gap is **substrate/shadow →
runtime activation**, plus closing the V12 learning loop.

| Storyboard element | Roadmap chip | Status | Evidence |
|---|---|---|---|
| Hex-mesh smart router, 8-cell | Deterministic First | ✅ done (exceeded) | `hex_cell_topology.py` (candidate), `hex_topology_registry.py` (7-cell, disabled) |
| L3 deterministic + specialist ML + MAGMA, LLM advisory | Deterministic First | ✅ done (22 axiom models; gold/silver/bronze) | `capabilities/selector.py`, `configs/axioms/`, `magma/receipt.py` v1.1 |
| gap detector → autogrowth → lowrisk grower | Autonomous Growth | ✅ done (6-family envelope, consultable-only) | `autonomy_growth/*` |
| subdivision, ring messaging, parent-child, gap replay, Axis B | Full Hexagonal | ⏳ ~70% — structures real, runtime activation gated | `hex_topology/*` (`no_runtime_mutation=True`), `run_release_axis_b_gate.py` |
| ring transport + hierarchy in runtime | Ring Messaging & Hierarchy | ⏳ ~60% — delivery validated, no network transport | `hex_topology/ring_messaging.py` |
| self-organizing mesh, emergent | Emergent Intelligence | ❌ early — counterfactual partial, competitive promo missing | A3 `MEASURED_LOCAL_PARTIAL`, `hex_cell_competition.py` |
| unlimited scalability | Infinite Scalability | ❌ design-only | multi-instance flywheel = V12 #2 |
| industrial-grade efficiency | Industrial-grade Efficiency | ❌ no measured program | no efficiency benchmark/budget gate |

V12 five-ingredient spine: #1 MAGMA receipt v1 ✅ · #2 multi-instance flywheel
❌ · #3 counterfactual eval ⏳ (A3) · #4 hex-cell competitive promotion ⏳ ·
#5 synthetic adversarial corpus ⏳ (seed).

## Roles
- **`codex-lead-1` (Claude 4.8, temporary lead)** — lead/merge-decider,
  consensus chair, architecture & frame/schema docs, cadence driver.
- **`codex-tools-1` (gpt-5.3-codex-spark xhigh)** — heavy tools/runtime impl:
  proof tools, runtime wiring, fast iteration.
- **`claude-rco-1`** — independent RCO/security gate, adversarial verification,
  veto in consensus, `RCO_PASS` on every merge.

## Tracks

- **T0 — Governance enablement** (consensus-gated approval). Split per RCO:
  - **T0a** — docs (`BRIDGE_CONSENSUS_APPROVAL_V1.md`, this file) + charter
    denylist hardening + `CLAUDE.md` Rule 9 (consensus-gated MERGE).
    **Rule 10 unchanged.**
  - **T0b** — `tools/idle_consensus_auto_merge.py` `bridge_consensus` approver
    (spoof-resistant, fail-closed; forge-probed by RCO).
  - **T0c (deferred)** — Rule 10 cutover amendment, gated on T5 maturity +
    proven auto-rollback + post-cutover verification harness.
- **T1 — Counterfactual A3 → full runtime pipeline** (V12 #3 → *Emergent*).
- **T2 — Hex-cell competitive promotion** (V12 #4 → *Full Hexagonal* +
  *Emergent*); raise auto-promoted solvers to authoritative-within-cell.
- **T3 — Hex-mesh runtime activation** (panels 4→5); shadow→canary→live with
  consensus-gated cutover (requires T0c) + reversibility.
- **T4 — Multi-instance replay flywheel** (V12 #2 → *Infinite Scalability*).
- **T5 — Synthetic adversarial corpus** (V12 #5 → safety gate for all of
  T1–T4 and a hard dependency of T0c).
- **T6 — Industrial-grade efficiency** (panel 6): benchmark harness + budgets.
- **T7 — Competitor-evidence & security caretaking** (ongoing; `DREAM_MODE_
  AGENDA.md` §A/§B).

## Sequencing & cadence
- Order: **T0a → T0b**, **T5** seeded early (safety gate), then **T1 → T2**,
  with **T4/T6/T7** interleaved. **T3 live cutover waits on T0c + T5 +
  reversibility.**
- `WaggleDanceIdleLoopOnce` tick (30 min) drives rounds; one smallest-safe step
  (≤ ~400 LoC PR) per round.
- Autonomous-merge/day cap starts at the current 5; any cap change is itself a
  consensus PR.

## Definition of done (measurable, per panel/chip)
- *Deterministic First*: existing proofs (already done).
- *Autonomous Growth*: authoritative-within-cell promotion (T2).
- *Full Hexagonal*: `hex_mesh.enabled=true` in production + live subdivision +
  Axis-B floor held (needs T0c).
- *Ring Messaging & Hierarchy*: ring transport carrying real cell messages.
- *Emergent Intelligence*: counterfactual-driven competitive promotion that
  autonomously changes routing, evidenced by chained MAGMA receipts.
- *Infinite Scalability*: flywheel sharing sanitized MAGMA across ≥2 instances.
- *Industrial-grade Efficiency*: efficiency benchmark green against budgets.

## Verification
Each track carries a proof tool (pattern `tools/run_v12_*_axis_proof.py`,
aggregated by `tools/show_v12_proof.py`); progress = status transitions
`PARTIAL → MEASURED_LOCAL → RUNTIME → PRODUCTION`. Every merge: full
`pytest tests/` + `RCO_PASS` + bridge consensus + MAGMA receipt. Every cutover
(T3/T4): reversibility proof + post-cutover verification + auto-rollback test.

## Versioning
* v0 (this doc): initial track structure T0–T7, RCO-approved T0 split.
  Future versions retire completed tracks and refresh the gap table as panels
  reach their definition-of-done.
