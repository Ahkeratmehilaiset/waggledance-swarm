# Solver Bootstrap and Synthesis — Phase 10 P4

**Status:** new orchestration layer on top of the Phase 9 declarative pipeline. Phase 9 modules and schemas are unchanged; Phase 10 adds: cold/shadow throttler, LLM solver generator (provider-plane integration), bootstrap orchestrator that ties Phase 9 + Phase 10 P2/P3, and a small `family_specs/` subpackage.

## What Phase 9 already shipped

`waggledance/core/solver_synthesis/`:

* `solver_family_registry.py` — ten built-in family kinds with required spec keys (`scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `weighted_aggregation`, `temporal_window_rule`, `bounded_interpolation`, `structured_field_extractor`, `deterministic_composition_wrapper`).
* `declarative_solver_spec.py` — `SolverSpec` dataclass + `make_spec()` validator.
* `deterministic_solver_compiler.py` — turns a SolverSpec into a `CompiledSolver` artifact.
* `gap_to_solver_spec.py` — gap → spec mapping, including the routing decision.
* `bulk_rule_extractor.py` — extracts solver-spec candidates from observation batches.
* `solver_candidate_store.py` / `solver_quarantine.py` — candidate lifecycle.
* `validators.py` — syntactic / semantic / property / regression / shadow gates.
* `schemas/solver_spec.schema.json`, `schemas/solver_family.schema.json`, `schemas/solver_candidate.schema.json`, `schemas/solver_proposal.schema.json` — JSON contracts.

## What Phase 10 P4 adds

| Module | Role |
|---|---|
| `cold_shadow_throttler.py` (`ColdShadowThrottler`) | Two-lane token-bucket throttler with concurrency cap. Bounds the rate at which candidates enter cold and shadow evaluation; prevents flooding under 10k+ candidate growth. |
| `llm_solver_generator.py` (`generate`, `GenerationRequest`, `GenerationResult`) | U3 free-form synthesis surface. Builds a validated `ProviderRequest`, dispatches via `ProviderPlane`, attempts to parse a SolverSpec-shaped JSON from the response, returns a structured result with explicit `parse_status`. |
| `solver_bootstrap.py` (`SolverBootstrap`, `BootstrapDecision`) | The U1→U3 escalation orchestrator. Single entry point `bootstrap_from_gap(...)`. Uses the throttler to admit candidates, the family registry for declarative compile, the provider plane for free-form synthesis, the control plane to register families and solvers. |
| `family_specs/__init__.py` (`EXAMPLE_SPECS`, `example_spec`) | Concrete declarative spec templates per family kind. Used as LLM priors, test fixtures, and operator-onboarding examples. |

All four modules are protected under BUSL-1.1 with `# BUSL-Change-Date: 2030-12-31` per RULE 6 and listed in `LICENSE-CORE.md`.

## U1 → U3 escalation rule (RULE 15)

Family-first growth. The orchestrator decides per gap:

```
match_confidence >= U1_HIGH_CONFIDENCE_THRESHOLD (0.8)
    → U1 declarative (family compile via Phase 9 deterministic pipeline)

U1_LOW_CONFIDENCE_THRESHOLD (0.5) <= match_confidence < 0.8
    → U1 declarative attempt; on validation failure, fall through to U3

match_confidence < U1_LOW_CONFIDENCE_THRESHOLD (0.5)
    → U3 free-form synthesis via provider plane

throttler refuses admission
    → "deferred_throttled" — caller can retry without state mutation

control plane / provider plane absent
    → "rejected" with explicit reason (RULE 14 fail-loud)
```

Provider priority for U3 synthesis (Phase 9 chain, unchanged):

1. `claude_code_builder_lane` — preferred for code-aware synthesis.
2. `anthropic_api`
3. `gpt_api`
4. `local_model_service`
5. `dry_run_stub` — last-resort fallback when no warm provider is available; always present so the orchestrator stays exercisable without credentials.

## Throttler semantics

`ColdShadowThrottler` is a deterministic two-lane token bucket:

| Lane | Default capacity | Default refill | Purpose |
|---|---|---|---|
| `cold` | 10 tokens | 1.0 tokens/s | First-observation gate. Candidates entering cold evaluation against live samples. |
| `shadow` | 50 tokens | 5.0 tokens/s | Eligible-for-promotion gate. Candidates that have passed cold and continue to be evaluated. |

Plus a global `max_in_flight` (default 32). The orchestrator calls `admit(lane)` before each candidate enters and `release(lane)` when evaluation completes. Refusals carry an explicit reason: `cold_lane_tokens_exhausted`, `shadow_lane_tokens_exhausted`, or `max_in_flight=N reached`.

The clock is injectable for deterministic tests. Production runs use `time.monotonic`.

## Scalability target

A single `SolverBootstrap` instance can run unattended through tens of thousands of gaps. Per-gap cost:

* Throttle admit: O(1), microseconds.
* Declarative path (high confidence): O(1) family lookup + spec validation + control-plane upsert. Sub-millisecond per candidate at 50k-row population (verified by `tests/storage/test_control_plane.py::test_count_solvers_at_50k_within_budget`).
* Free-form path (low confidence): bounded by the provider call budget (RULE 8 — Anthropic/GPT/Claude Code calls per section), which the throttler holds at the shadow-lane refill rate.

There is no per-gap manual review. Promotion to a "promoted" status flows through the existing Phase 9 promotion ladder.

## Control-plane integration

* `register_default_families()` mirrors the Phase 9 ten built-in family kinds into the control plane's `solver_families` table. Idempotent — re-running updates the row without creating duplicates.
* Each successful U1 compile calls `control_plane.upsert_solver(...)` with the spec hash, family link, and `status='draft'`. This is the *current state*; the Phase 9 candidate store / quarantine are unchanged and continue to record the *history* via MAGMA.
* U3 calls flow through the provider plane, which records `provider_jobs` rows automatically (see P3 `ProviderPlane.dispatch`).

## What Phase 10 P4 does NOT do

* Does not replace Phase 9 modules. They are imported and composed.
* Does not run actual shadow evaluation against live traffic — that runs through the existing Phase 9 `validators.py` pipeline; the throttler only gates *admission*.
* Does not perform LLM calls in tests. The provider plane's `dry_run_stub` is the default; real Anthropic / OpenAI HTTP adapters are follow-up work.
* Does not promote any solver. Promotion happens via the Phase 9 promotion ladder + human gate.

## Cross-references

* `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — substrate (P2).
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — execution layer (P3).
* `waggledance/core/solver_synthesis/__init__.py` — Phase 9 family kinds + thresholds.
* `schemas/solver_spec.schema.json` — Phase 9 spec contract.
* `LICENSE-CORE.md` — protected-modules map.
