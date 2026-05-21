# V12 Verifiable Learning Loop — WaggleDance frame doc

Status: drafted 2026-05-21 by bridge-consensus split (claude V12 frame
doc / codex pilot JSON update). Sister artifact:
[`docs/benchmarks/2026_05_20_competitor_axis_pilot.md`](../benchmarks/2026_05_20_competitor_axis_pilot.md).

## Why this doc

The 2026-05-20 competitor pilot recorded a substrate identity choice:
**WaggleDance is a verifiable solver-growth substrate, not another
action firewall.** JamJet, Asqav, Microsoft AGT, Preloop, PolicyLayer
Intercept, and SidClaw all do action governance; if WD positioned
itself only as a "write gate" it would be commoditized.

The pilot named six contested or ceded axes (A1, A2, A5, A6, A7, A8,
A9) where rivals can match WD or are ahead, and two must-win evidence
axes (A3 counterfactual delta, A4 solver-growth lifecycle) where the
strategy depends on WD producing locally measurable proof-tool output
that no rival publicly exposes inside the same freshness window.

This is an **evidence claim, not a superiority claim**. The
competitor pilot remains `consensus_grade=false` until at least one
rival-side local check has been run; this doc inherits that honesty
boundary. WD's edge on A3 / A4 is "measurably evidenced locally and
not publicly claimed by any of the four rivals in the 2026-05-20
snapshot window" — not "proven superior across all measurable
dimensions".

This doc captures the five-element loop that makes those must-win
axes mean something. Each element is documented with its WD
implementation, its rival absence (from the 2026-05-20 snapshot), and
how it composes into the loop. The doc is **frame**, not benchmark:
the benchmark column is in the competitor pilot JSON.

## The five elements

```
                   ┌─────────────────────────────────────┐
                   │ 5. operator-owned gates             │
                   │    (charter, operator approval,     │
                   │     no auto-execute on consensus)   │
                   └────────────────┬────────────────────┘
                                    │ envelops
                                    ▼
   ┌──────────────────┐    ┌──────────────────┐    ┌────────────────────┐
   │ 1. MAGMA         │───▶│ 2. EvaluationRes │───▶│ 4. solver-growth   │
   │    receipt v1    │    │    v0            │    │    lifecycle       │
   │ chained sha256   │    │ verdict + gate + │    │ shadow→canary→live │
   │ five digests     │    │ reason codes     │    │ + revoke + quarant │
   └──────────────────┘    └──────────────────┘    └────────────────────┘
                                    ▲                        │
                                    │                        │ replay drives growth
                                    │                        ▼
                          ┌──────────────────────────────────────────────┐
                          │ 3. counterfactual replay                     │
                          │    same state + alt action → delta in gate/   │
                          │    verdict/reason_codes                       │
                          └──────────────────────────────────────────────┘
```

Each element below: definition, WD implementation, rival absence,
composition into the loop.

### 1. MAGMA receipt v1

**Definition.** A canonical-JSON sha256-digested artifact bound to a
runtime decision. Carries five binding digests:
`policy_digest`, `charter_digest`, `rco_decision_digest`,
`world_snapshot_digest`, `solver_contract_digest`. Receipts chain via
`previous_receipt` so a sequence of decisions forms a tamper-evident
audit trail.

**WD implementation.** `waggledance/core/magma/receipt.py`,
`schemas/v3_13_0/magma_receipt.v1.json`. Receipt-bound authority
paths confirmed by `tools/magma_receipt_adoption_report.py` (HIGH
gap count = 0 as of 2026-05-21). Authority chain shipped:
WriteRCOGate (PR #504), auto-promotion engine (#505), solver
provenance transitions (#506), adversarial eval report (#507).

**Rival absence (2026-05-20 verbatim snapshot
[`2026_05_20_rival_public_doc_snapshot.md`](../benchmarks/2026_05_20_rival_public_doc_snapshot.md)).**
JamJet has signed receipts (`receipt_hash` + `arguments_hash`); AGT
has Merkle-chained audit logs + Decision BOMs; Asqav has ML-DSA-65 +
RFC 3161 hash chain. WD's chain-of-five-digests-per-receipt is
distinct in shape (every receipt carries policy, charter, rco,
world-snapshot, solver-contract digests together — no rival exposes
that exact composition publicly) but is **commoditized in spirit**:
this element alone is not the uniqueness claim. Receipts feed the
verifier and chain into element 2; that is where the differentiation
starts.

**Composition.** Receipts are the audit-time substrate that every
other element binds into. EvaluationResult v0 carries
`canonical_payload_digest` matching the receipt; solver promotion
records receipt-bound transitions; counterfactual replay produces a
RECEIPT for the counterfactual run that can be diffed against the
factual receipt.

### 2. EvaluationResult v0

**Definition.** A typed verdict over a candidate decision: includes
`expected_gate` vs `actual_gate`, `verdict` (`pass`/`refuse`/`review`/
`fail`/`abstain`/`insufficient_evidence`), structured
`reason_codes`, `verifier_path`, `solver_selection`,
`policy_version`, `charter_version`, `confidence_score`,
`uncertainty_sources`. Bound to a `target_digest` so the evaluation
is provably about a specific payload.

**WD implementation.** `waggledance/core/magma/evaluation_result.py`,
`schemas/v3_13_0/magma_evaluation_result.v0.json`. Adversarial corpus
(`tests/fixtures/magma_adversarial_corpus/v0.json`, 15 cases as of
PR #508) is evaluated through this contract and produces 15/15
demo-policy pass for the report-level eval (PR #507's receipt-bound
adversarial-eval).

**Rival absence (2026-05-20 verbatim snapshot, expires 2026-06-03).**
**No rival in the four-rival snapshot window publicly exposed
EvaluationResult v0 as a primitive at retrieval time.** JamJet,
Preloop, and PolicyLayer Intercept (per snapshot) have policy
decisions but no typed verdict envelope binding decision + verifier
path + reason codes + uncertainty + target digest in one canonical
artifact. AGT exposes Decision BOMs reconstructed from logs
(post-hoc) rather than a v0 typed contract carried with each
decision. This is one of WD's locally-evidenced unique-loop
elements **within the snapshot window**; the claim is brittle to a
single rival publishing a typed-evaluation contract during the
freshness window.

**Composition.** Every counterfactual replay produces an
EvaluationResult that names what it tested and what verdict it
reached. Solver-growth lifecycle records EvaluationResults per
candidate to drive shadow→canary→live promotion. Operator-owned
gates read EvaluationResults to decide which decisions to gate.

### 3. Counterfactual replay (must-win A3)

**Definition.** Replay the same world state with an alternative
action and measure the delta in gate / verdict / reason codes. Not
"replay debugging" (re-run the same path), but
**counterfactual delta** — the verifier sees the alt-action and
reports whether the gate moves (e.g., `review` → `allow`).

**WD implementation.** Demo and runtime-level proof:
`tools/run_pdam_counterfactual_demo.py` (counterfactual demo with
receipt bundle, PR #501-spine), `tools/run_v12_a3_counterfactual_axis_proof.py`
(PR #515-516) which reports `delta_proven=True` against the
`KEEP_WIP → CLOSE_OK` action delta (gate moves `review → allow`).
Status as of 2026-05-21: **MEASURED_LOCAL_PARTIAL** per V12 demo
tool output. The pdam_counterfactual_demo emits a verified
receipt bundle that can be re-verified end-to-end.

**Rival absence (2026-05-20 verbatim snapshot, expires 2026-06-03).**
**No rival in the four-rival snapshot window publicly claimed
counterfactual delta at retrieval time.** AGT (per snapshot) mentions
"replay debugging" under Agent SRE — same-state-same-action replay
for post-hoc inspection, not delta. JamJet (per snapshot) replays the
event log "and resumes at the failed node" — crash recovery, not
counterfactual. Asqav and Preloop (per snapshot) do not mention
replay at all in the window. This is the A3 must-win evidence axis;
the claim is brittle to a single rival publishing a counterfactual-
delta primitive during the freshness window.

**Composition.** Counterfactual replay consumes the world snapshot
implied by an existing MAGMA receipt (element 1), produces a new
EvaluationResult v0 (element 2), and feeds the delta into
solver-growth lifecycle (element 4) as evidence: when a candidate
solver's counterfactual evidence drifts from a baseline, that drives
promotion / quarantine decisions.

### 4. Solver-growth lifecycle (must-win A4)

**Definition.** Solvers are first-class candidates with a typed
authority lifecycle: signed → activated → (revoked | quarantined).
Shadow execution → canary execution → live execution. Each
transition is a MAGMA-receipt-bound decision recorded by the
provenance engine.

**WD implementation.** `waggledance/core/v3_13_0/solver_provenance.py`
(PR #506 receipt-binds activation_authorised, activation_refused,
activation_revoked, quarantined transitions); auto-promotion engine
(PR #505); proof tool
`tools/run_v12_a4_solver_growth_axis_proof.py` (PR #517) which
reports **MEASURED_LOCAL_SYNTHETIC**: 6 registered solvers / 18
dispatch successes / 6 families covered.

**Rival absence (2026-05-20 verbatim snapshot, expires 2026-06-03).**
**No rival in the four-rival snapshot window publicly claimed a
solver-growth lifecycle at retrieval time.** AGT (per snapshot) has
"Agent Lifecycle" (provisioning, rotation, orphan detection,
decommissioning) — about the AGENT, not about candidate solvers
inside the agent. JamJet, Asqav, Preloop (per snapshot) do not
expose a solver-promotion concept at all in the window. This is the
A4 must-win evidence axis; the claim is brittle to a single rival
publishing a solver-promotion lifecycle during the freshness window.

**Composition.** Solver-growth lifecycle reads
EvaluationResults (element 2) from counterfactual replays
(element 3), records lifecycle transitions as MAGMA receipts
(element 1), and operates only when the operator gate (element 5)
permits the candidate to promote.

### 5. Operator-owned gates

**Definition.** Every consensus, every promotion, every
charter-affecting decision carries
`operator_gate_required = true` and `auto_execute = false`. The
substrate may emit findings, draft PRs, run shadow / canary
solvers, and produce verified receipts autonomously, but it never
applies operator-affecting changes without explicit operator
approval. The charter is a real, code-enforced artifact, not a
policy file the agent can edit at runtime.

**WD implementation.**
[`docs/architecture/IDLE_AUTONOMY_CHARTER.md`](IDLE_AUTONOMY_CHARTER.md);
charter-version digests carried in every MAGMA receipt; explicit
`operator_gate_required` field on every `idle_consensus_reached`
payload; autonomous-merge guardrails (CLAUDE.md Rule 9) + bridge
peer-block preflight (PR #530, hardened #531-#536) that prevent
race-past-peer-block.

**Rival semi-presence.** Asqav and Preloop expose
`require_approval` and `require_justification` policy verbs;
JamJet has approval flows. None of them expose
**operator-owned charter-versioned gates that are code-enforced and
audit-trailed via per-decision digests**. The rival pattern is
"operator approves once per action"; WD's pattern is "operator owns
the charter, the substrate proves every action against the charter
version digest carried in the receipt." This is qualitatively
different in audit-time but operationally similar enough that the
A1 (pre-execution gate) axis is `contested`, not `must-win`.

**Composition.** Operator-owned gates envelop the other four
elements. The charter version digest is one of the five binding
digests in every MAGMA receipt (element 1). EvaluationResult
(element 2) carries `charter_version` explicitly. Counterfactual
replay (element 3) is operator-gated for any externally-effective
counterfactual. Solver promotion (element 4) requires operator
gate to cross shadow → canary → live.

## The loop in one sentence

A solver candidate's promotion is gated by counterfactual
evidence whose verdict is recorded as an EvaluationResult bound
to a tamper-evident MAGMA receipt that names the charter version
the operator owns. **No rival in the 2026-05-20 four-rival
snapshot window publicly composed all five at retrieval time** —
a claim explicitly scoped to that freshness window and brittle to
any rival publishing a counterfactual + solver-growth + typed-
evaluation combination during the window.

## What this doc is NOT

* **Not a benchmark**. The benchmark column lives in
  [`docs/benchmarks/2026_05_20_competitor_axis_pilot.json`](../benchmarks/2026_05_20_competitor_axis_pilot.json)
  (updated by codex per the 2026-05-21 bridge consensus split).
* **Not a measurement claim**. Each element above cites WD
  proof-tool output (e.g., `MEASURED_LOCAL_PARTIAL` for A3,
  `MEASURED_LOCAL_SYNTHETIC` for A4); rival absence is qualified
  by "in the 2026-05-20 snapshot window" — if a rival publishes an
  equivalent claim after that window, this doc needs to be
  refreshed.
* **Not a marketing claim**. The five elements are technical
  primitives that compose; there is no claim that operators will
  prefer the loop over a simpler write gate without seeing the
  proof-tool output.

## Freshness

Rival-absence statements expire when the
`2026_05_20_rival_public_doc_snapshot.md` source expires (per its
own freshness_policy: 2026-06-03). When that snapshot is refreshed,
review the rival-absence column for each element here. The
must-win column is brittle — a single rival publishing a
counterfactual-delta primitive or a solver-growth lifecycle would
shift A3 or A4 from `must-win` to `contested`.

## Cross-references

* Competitor pilot scope: `docs/benchmarks/2026_05_20_competitor_axis_pilot.md`
* Competitor pilot JSON (machine-readable, updated by codex per
  split): `docs/benchmarks/2026_05_20_competitor_axis_pilot.json`
* Rival verbatim snapshot: `docs/benchmarks/2026_05_20_rival_public_doc_snapshot.md`
* Charter: `docs/architecture/IDLE_AUTONOMY_CHARTER.md`
* MAGMA receipt schema: `schemas/v3_13_0/magma_receipt.v1.json`
* Adoption report tool: `tools/magma_receipt_adoption_report.py`
* A3 proof tool: `tools/run_v12_a3_counterfactual_axis_proof.py`
* A4 proof tool: `tools/run_v12_a4_solver_growth_axis_proof.py`
* V12 demo aggregator: `tools/show_v12_proof.py`
