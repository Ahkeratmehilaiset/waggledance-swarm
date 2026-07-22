# WD Vision Manifest V1 (6-panel grading reference)

**Execution companion:** `WD_PRODUCTION_CONVERGENCE_PLAN_V1.md` defines the
current phase order. This manifest remains the target rubric, not the backlog
or runtime truth source.

Status: reference for Grok plan-vs-vision grading and the scheduled vision-axis
delta (see `GROK_DEPLOYMENT_V1.md`). Author: claude-rco-1. Date: 2026-06-05.

This encodes the WaggleDance Swarm AI vision image (6 panels + maturity axis) as
a **machine-readable grading rubric**. It is the *target*; the *truth source* is
`tools/wd_image1_capability_manifest.py --json` +
`WD_IMAGE1_FUNCTIONALITY_MANIFEST.md`. A grader (Grok, RCO, or any agent) maps
the current plan/state onto each panel and reports status + whether the literal
claim is safe — **grade, do not cheerlead** (mirror `all_literal_claims_safe`).

## Canonical product target

Image #1 defines one product outcome, not six isolated demonstrations:

> WaggleDance is an event- and state-driven collective solver intelligence that
> turns detected work into a verified real-world outcome without routine user
> participation.

Within an already granted policy and connector envelope, the steady-state
target is `routine_user_actions_required = 0`. The user does not prompt each
job, choose solvers, relay data, reconcile intermediate results, or approve
every routine step. The primary interface observes outcomes and exceptions and
provides an emergency stop; it is not the workflow engine.

The normative closed loop is:

```text
observe state or event
  -> detect work
  -> assemble a specialist solver swarm
  -> solve and cross-verify
  -> record MAGMA intent and admission
  -> execute the real-world effect
  -> record the MAGMA effect transition
  -> verify and reconcile the resulting state
  -> append the MAGMA reconciliation receipt
  -> update vectorized collective memory
  -> learn, grow, subdivide, or roll back
```

Decision intelligence belongs to the collective solver swarm: authoritative
deterministic solvers first, specialist models second, and an LLM only as an
advisory or fallback layer. The Smart Router, UI, and LLM coordinate or expose
the work; none of them is the product's single central intelligence.

The six image panels are mechanisms for this zero-touch outcome. The current
eight-solver v3.13 slice is an acceptance surface, not the product boundary.
Likewise, a chat answer, a proof artifact, or a rising solver count does not by
itself satisfy the target.

This contract is non-authorizing. It does not enlarge connector permissions,
bypass an exception or safety stop, grant runtime authority, or flip
`claim_safe`. WaggleDance may autonomously act and adapt inside a pre-authorized
envelope, but it may not autonomously widen that envelope.

### Historical workload baseline

The operator attests that earlier WaggleDance generations performed real
organization-specific solver work, complete solver-driven bookkeeping, real
invoice-payment workflows, and large-database vectorization. These are the
product baseline to preserve and reintegrate, not future greenfield examples.
Private execution artifacts and customer details are intentionally excluded
from the public repository, so this attestation is historical product context,
not current-HEAD machine proof and not a `claim_safe` upgrade. The sanitized
[operator-case bundle](../operator_cases/v3_13_0_capability_seed_bundle.md)
remains a domain-shape anchor for the broader real workload surface, not
execution evidence for the private historical runs.

### Outcome acceptance axes

Plans and graders must report end-to-end outcomes in addition to panel proofs:

- eligible zero-touch workflow completion ratio;
- routine user actions per eligible completed workflow, target `0`;
- verified real-world outcome and external-effect MAGMA coverage ratios;
- collective quality gain over the best single solver;
- autonomous-growth marginal verified coverage gain and rollback correctness;
- cost, latency, memory, and energy per completed workflow.

## Status vocabulary

`proof` (proof/ops evidence exists) · `ops` (wired, operator-gated/bounded) ·
`shadow` (shadow/replay/offline only) · `future` (roadmap; benchmarks only) ·
`literal_claim_safe` (true only when the panel's literal text is production-true).

## Panels

| # | Panel (image text) | Literal claim | Current status (2026-06-05) | literal_claim_safe |
|---|---|---|---|---|
| 1 | Hex-Mesh Reititys — 8-cell honeycomb, smart router; "every query first enters the hex-mesh" (cells: Yleinen, Terminen, Energia, Turvallisuus, Kausittainen, Matematiikka, Järjestelmä, Oppiminen) | all queries first hop an authoritative 8-cell hex-mesh | proof/ops: route-order proofs, HTTP/WS traces, metrics, dashboard smoke. But `hex_mesh_enabled=false`; 8-cell retrieval is candidate-mode, not authoritative first hop | **false** |
| 2 | Layer 3 Deterministic Solver + MAGMA append-only audit; L2 = 14 specialist ML; L1 = LLM advisory only | every query: deterministic-first, full per-query MAGMA provenance, LLM only advisory | proof: deterministic stage in route proofs, opt-in solver-trace receipt, growing MAGMA handoff/export/import/reviewer/bundle/index/verifier. Not yet gapless default per-query append-only MAGMA | **false** (partial) |
| 3 | Low-Risk Autonomy Loop: Runtime Gap Detector → Autogrowth Scheduler → Lowrisk Grower; "improves itself without any human intervention" | unattended self-growth of new solvers | ops: runtime-boundary smoke, operator metrics, dashboard ops overlay, fallback alert state, sanitized + read-only autogrowth alert feed in main. Bounded/consultable/read-only/operator-gated, not free production self-growth | **false** |
| 4 | Implementing Hexagonal Upgrades: subdivision operator, ring messaging, parent-child hierarchy, incremental gap replay, Axis B | live dynamic subdivision + ring transport + hierarchy | shadow: shadow subdivision replay, offline verifiers, path-free reviewer summaries, bridge-event template/index/verifier chains. Runtime mutation + live subdivision + production ring transport absent | **false** |
| 5 | Future: dynamic self-organizing hex-mesh, unlimited scalability | self-organizing mesh, unlimited scale | future: scale-axis scorecards + local benchmarks (route depth, composite paths, contradiction rate, insight score). Local/replay, not production-scale | **false** |
| 6 | Future WaggleDance — Superior Intelligence, Scalability & Efficiency; emergent swarm | emergent superior intelligence, infinite scalability, industrial-grade efficiency | future: vision only; "infinite scalability" is not a safe claim under any current evidence | **false** |

## Maturity axis (bottom bar)

`Deterministic First → Autonomous Growth → Full Hexagonal Architecture →
Ring Messaging & Hierarchy → Emergent Intelligence → Infinite Scalability →
Industrial-Grade Efficiency`

A grader reports, per axis stage: reached / partial / not-started, and the single
highest-leverage next step. The honest equilibrium (RCO): keep accumulating
proof/ops evidence on stages 1-3, keep 4 advancing out of shadow, keep 5-7 as
honest roadmap — and **never flip a `literal_claim_safe` to true without
production evidence** (matured adversarial corpus + proven auto-rollback +
post-cutover verification, per `CLAUDE.md` Rule 10).

## Grader output schema

```
{ "panel": <1-6>, "status": "proof|ops|shadow|future",
  "plan_advances_it": <bool>, "gap": "<text>",
  "next_highest_leverage": "<text>", "literal_claim_safe": <bool> }
```

Keep this file in sync with `wd_image1_capability_manifest` when capabilities
change; it is a rubric, not a source of truth.
