# A4 Solver-Growth Lifecycle Discovery — Claude Scout

Date: 2026-05-23
Author: Claude
Task: 100h plan §48-72h (started early as parallel read-only while C2 PR
#611 in CI). Discover whether a real solver-promotion runtime path
exists; classify honestly per the plan's enum
(`real_runtime_path_exists` / `demo_only` / `synthetic_only` /
`missing`); if real, propose a one-PR receipt-binding slice; if not,
write an RFC instead.

## Method

Read-only static analysis of the v3.13.0 solver code. No code execution,
no SDK install, no test runs against the discovered paths. Operator
approval gates any subsequent implementation PR.

## Finding: `real_runtime_path_exists`

The repo already has a production-shaped solver-promotion lifecycle.
The integration point that's missing is **a production receipt-emission
sink wired to its existing optional hook** — the exact same gap pattern
as WriteRCOGate (`build_write_rco_gate_receipt`, helper-exists-no-callers).

### Evidence

**1. Lifecycle state machine** at
`waggledance/core/v3_13_0/solver_provenance.py:60-67`:

```
class ActivationState(str, Enum):
    UNACTIVATED = "unactivated"
    AWAITING_SIGNING = "awaiting_signing"
    SIGNED = "signed"
    ACTIVATED = "activated"
    QUARANTINED = "quarantined"            # reversible auto-state
    REVOKED = "revoked"                     # one-way, operator-driven
```

This is the full shadow → canary → live lifecycle the V12 5-ingredient
roadmap names. It is NOT a demo fixture.

**2. Transition methods** are real:

- `SolverProvenance.sign()` at line 200 — records a signature in the
  provenance chain; emits MAGMA audit event + bridge handoff
  `payload.kind=solver`.
- `SolverProvenance.activate()` at line 549 — promotion to ACTIVATED.
- `SolverProvenance.revoke()` at line 470 — terminal revocation.

Each emits a bridge envelope per spec edit E16 and a MAGMA audit event
via the injected `emit_magma_event` hook.

**3. Receipt-emission hook EXISTS but is OPT-IN ONLY** at line 179:

```
emit_receipt_bundle: Optional[Callable[[dict], None]] = None
"""Optional hook for MAGMA receipt-bound transition bundles.

If configured, authority transitions build and emit a payload-free
RCO/EvaluationResult/receipt bundle before durable state changes.
Hook failure propagates so the transition fails closed.
"""
```

The internal `_emit_transition_receipt_bundle` at line 660 wires this
hook into sign (line 512), activate (line 567/588/604), revoke (line 700).
The receipt body is built by
`build_solver_provenance_transition_receipt` at line 815, which
constructs a proper MAGMA receipt via `build_magma_receipt`
(line 841 import, line 893 call) with `policy_digest`,
`charter_digest`, `rco_decision_digest`, `world_snapshot_digest`,
`solver_contract_digest` — full v1 envelope shape.

**4. Production callers of `emit_receipt_bundle=` — ZERO.**

`grep -r emit_receipt_bundle=` returns exactly one match:
`tests/v3_13_0/test_solver_provenance.py:103` — a test fixture
that mocks the hook to capture emissions. **No production code wires
this in.**

This is **the same gap pattern** as `WriteRCOGate`
`build_write_rco_gate_receipt` helper which has zero production callers
(per my Slice C scout 2026-05-23T07:57Z). The infrastructure exists,
the binding does not.

## Why this matters

`docs/runs/magma_100h_sprint_2026_05_24/baseline.json` claims A4 is
`MEASURED_LOCAL_SYNTHETIC` because no solver-lifecycle event has
emitted a receipt in production. The classification is honest given
the gap, but the gap is **shallower than it looks**: the lifecycle
code is real, the receipt builder is real, and the hook contract is
real. Only the sink is missing.

Closing the gap moves A4 from `MEASURED_LOCAL_SYNTHETIC` to
`MEASURED_LOCAL_PARTIAL` — same kind of label progression Codex achieved
on A2 via #606 (AutonomyRuntime) and on A3 via #610 (counterfactual
proof v1 binding).

## Proposed one-PR receipt-binding slice (gated on operator approval + Codex consensus)

### Owner

Claude implementation; Codex peer + RCO.

### Scope (estimated ~200–300 LOC, 4–6 files)

1. **`waggledance/core/v3_13_0/solver_provenance.py`** — no change to
   the `SolverProvenance` class itself (it already has the hook).
   Optional: add docstring example showing the production caller
   pattern.

2. **NEW `tools/run_solver_provenance_receipt_emission_proof.py`** —
   production-shaped proof that mirrors `tools/run_runtime_receipt_emission_proof.py`
   (#606) and `tools/run_a3_counterfactual_axis_proof.py` (#610):
   - Instantiate `SolverProvenance` with the `emit_receipt_bundle` hook
     wired to a `write_receipt_bundle`-based sink (similar to
     Codex's `_emit_a3_v1_receipt_bundle` in #610).
   - Run a deterministic 3–4 transition sequence
     (`sign → sign → activate → revoke`).
   - Verify the receipt bundle via `verify_manifest` round-trip.
   - Assert `verify_manifest` rejects a tampered payload AND a
     tampered evaluation_result.
   - Emit JSON report with `axis_id=A4`, `claim_label=MEASURED_LOCAL_PARTIAL`
     (NOT `PROVEN`), `evaluation_result_version=magma.evaluation_result.v1`,
     `receipt_chain_id=magma:v12_a4_solver_growth_axis:v1`.

3. **NEW `tests/unit/test_solver_provenance_receipt_emission.py`** —
   exercise:
   - Hook `None` (default) preserves all existing behavior — no receipts emitted.
   - Hook wired emits one bundle per transition; chain hash links via
     `previous_receipt`.
   - `EvaluationResult` v0 default + v1 opt-in via #603 dispatcher.
   - Unknown evaluation_result version → fail-closed (per Codex's bfe2a41c).
   - Tampered payload + tampered evaluation_result rejected by verifier.

4. **`tools/magma_receipt_adoption_report.py`** — extend `CRITICAL_PATHS`
   to include `solver_provenance.SolverProvenance.{sign,activate,revoke}`
   so the adoption report tracks it. Classify as `receipt_capable_opt_in`
   (matching Codex's precision rule from
   [[rco-discipline-wait-ci-green-and-audit-precision]] §2; NOT
   `receipt_bound` since the hook is opt-in).

5. **Counter-read invariants preserved** —
   `tools/magma_slice_counter_read.py` continues to report
   `release_boundary` all-false, `forbidden_claims` preserved,
   `consensus_grade=false`.

### Acceptance tests

- 3–4 transition sequence emits 3–4 MAGMA receipts.
- Chain integrity verifies via `verify_manifest`.
- Tampered detection per above.
- `axis_id=A4`, `claim_label="MEASURED_LOCAL_PARTIAL"` (UNQUALIFIED_LABELS
  guard not invalidated).
- `competitor_axis_reference="A4"` on every v1 evaluation_result.
- `confidence_basis={method: "point_estimate", sample_count: 1,
  methodology_reference: "tools/run_solver_provenance_receipt_emission_proof.py"}`
  — honest for a deterministic transition fixture (NOT a statistical
  benchmark; do not invent bootstrap).
- v0 backwards-compat preserved (the optional hook does not break any
  existing caller).

### No-go criteria

- If the `SolverProvenance` class is mid-refactor with an open redesign
  ticket in Sprint 3 backlog, abort.
- If `_emit_transition_receipt_bundle` already has any production
  caller I missed in the grep, abort and re-scope.
- If wiring the proof CLI would mutate any baseline.json claim label
  other than tracking the new path under `receipt_adoption` —
  abort, surface as RCO instead.
- If the soak window is open and the proof emission would mutate any
  release-gate-observed artifact, defer until v3.12.0 stable is tagged.
- If `build_solver_provenance_transition_receipt` (line 815) is found
  to not actually wire all the v1 envelope fields the receipt schema
  requires — implement that fix as a separate prerequisite PR before
  the proof.

## Comparison with parallel WriteRCOGate slice

Both have the same gap shape (`emit_receipt_bundle` hook exists, no
production callers). Differences:

| Slice | A4 (this scout) | WriteRCOGate (my 07:57Z scout) |
|---|---|---|
| Axis | A4 solver-growth lifecycle | A1 action gate |
| Strategic value | Must-win | Contested (rivals already strong) |
| Existing helper | `build_solver_provenance_transition_receipt` | `build_write_rco_gate_receipt` |
| Hook param | `emit_receipt_bundle: Optional[Callable]` | `write_rco_receipt_sink: Optional[Callable]` |
| Lifecycle states | 6 (UNACTIVATED → REVOKED) | 3 (allow/deny/defer) |
| Risk | LOW — code is real, just needs production-shaped sink wiring | LOW — same reason |
| LOC estimate | ~200–300 (4–6 files) | ~250 (6 files) |

Both slices can run in parallel because they touch disjoint files
(`solver_provenance.py` family vs `write_rco_gate.py` family). Per the
100h plan §24-48h WriteRCOGate is Codex's, §48-72h A4 is mine —
parallelizable.

## Why this scout went deeper than the plan required

The plan's 48-72h section asks for "scout/RFC or proof-path discovery,
not a synthetic receipt PR." This scout is the proof-path discovery:
the real path exists, the helper exists, the hook exists. The scout
output is the implementation spec the PR will use, not an RFC for a
hypothetical path. RFC scope is unnecessary because the path is
already in code.

## What this scout is NOT

- Not a PR. Not an implementation. Not a claim label change.
- Not authorization to wire the hook. Operator + Codex consensus gates
  the implementation PR.
- Not a `MEASURED_LOCAL_PARTIAL` label promotion for A4. The label
  promotion comes ONLY after the proof emission is committed AND its
  receipts are verified locally on disk.
- Not a `consensus_grade=true` claim. The consensus_grade aggregate
  stays `false`.

## Anti-claim guardrails honored

- No `consensus_grade=true` claim.
- No A4 label upgrade.
- No release-boundary touch.
- No `forbidden_claims` mutation.
- Honest classification: `real_runtime_path_exists` is the strongest
  category in the plan's enum, but only because the lifecycle code,
  receipt builder, and hook contract are all real — not because A4
  has *already* moved.

## End of scout — awaiting Codex consensus + operator approval to schedule the implementation PR
