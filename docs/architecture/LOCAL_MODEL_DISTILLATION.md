# Local Model Distillation — Phase 9 §N

**Status:** SAFE SCAFFOLD. Optional limited enablement only under
explicit human-gated profile. By default in this branch:
- build scaffolding, contracts, safety docs, and test harnesses
- do NOT allow local model outputs to mutate foundational knowledge
  automatically.

## Why this exists

Over time, WaggleDance accumulates a substantial provider consultation
cache (Phase J §api_distillation) and a builder-lane history (Phase U2).
These collections are a high-quality teacher signal for distilling
narrow capabilities into a local model. Distillation reduces:

1. external provider dependence for repeated, low-stakes tasks
2. round-trip latency for shadow / advisory inference
3. exposure of incremental project context to third-party APIs

But the same path is the highest-leverage way to introduce silent
regressions or covertly drift the system's calibration. So this phase
is built scaffold-first.

## What is in this scaffold

| Module | Role | Side effects |
|---|---|---|
| `local_model_manager.py` | Records (model_id, version, lifecycle_status). Append-only history. | none |
| `fine_tune_pipeline.py` | Plans deterministic JobSpecs. `.execute()` raises by design. | none |
| `inference_router.py` | Decides routing destination for an inference request under the safe routing contract. | none |
| `model_evaluator.py` | Scores precomputed `(predicted, gold)` pairs into an advisory `ModelEvaluationReport`. | none |
| `drift_detector.py` | Compares accuracy against a baseline; emits a 4-level advisory severity. | none |

None of these modules:
- imports `torch`, `transformers`, `ollama`, `openai`, `anthropic`,
  `requests`, `httpx`, `urllib`
- launches a subprocess
- writes to disk
- mutates runtime / world model / self model / axioms

## The Safe Routing Contract

`InferenceRouter.decide(request)` returns one of four destinations:
- `local_model_shadow` — request runs through a shadow path; result
  is observed but not authoritative
- `local_model_advisory` — request returns an advisory hint;
  external provider remains the source of authority
- `external_provider` — default for unrecognized or default-disabled
  flows
- `refuse` — emitted whenever the request's `task_kind` is one of the
  CRITICAL kinds

CRITICAL `task_kind` values that MUST refuse local routing:
- `foundational_axiom_authoring`
- `runtime_mutation_proposal`
- `main_branch_merge_decision`
- `human_facing_calibration_claim`
- `self_model_promotion`
- `world_model_promotion`

The router never invents an inference; `call_local_model()` raises by
design.

## Promotion (out of scope here)

Whether a local model record progresses from `shadow_only` to
`advisory` or back to `retired` is a manager-level transition that
requires a non-empty rationale. There is intentionally no
`production` lifecycle status on a local model. The only way for a
local model output to ever influence main-branch / runtime authority
is to first compile a meta-proposal (Phase 9 §O) and traverse the
14-stage human-gated promotion ladder (Phase 9 §M). That route MUST
include `human_approval_id`.

## What an optional limited enablement would require

Limited enablement is gated on ALL of:

1. earlier phases F–O are green and stable on this branch
2. routing config restricts local destinations to a non-critical
   subset of `task_kind`
3. drift detector continuously watches candidate vs. baseline; any
   `elevated` or `critical` severity triggers a manual review
4. outputs remain `advisory_only=True`; never authoritative
5. an explicit human ack is recorded in the promotion ladder before
   any expansion of the local routing surface

Until those conditions are met (and recorded), the scaffold default
applies: external providers remain authoritative; local models are
shadow / advisory only.

## What is intentionally NOT here

- actual fine-tune execution (would require GPU, real corpora,
  and a separate isolated environment)
- live model inference
- automatic promotion of local model outputs to runtime authority
- generative memory compression — deferred to Phase 12+

If a future session tries to add any of these, it MUST also extend
this document with the corresponding human-gated review path.
