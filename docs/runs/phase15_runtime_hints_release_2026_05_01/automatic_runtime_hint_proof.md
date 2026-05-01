# Phase 15 — Automatic Runtime Hint Proof

**Generated:** 2026-05-01 by `tools/run_automatic_runtime_hint_proof.py`
**Machine-readable artifact:** [`automatic_runtime_hint_proof.json`](automatic_runtime_hint_proof.json)
**Branch:** `phase15/automatic-runtime-hints-release-readiness` (off `origin/main` @ `2733f87`)
**Composes with:** Phase 14 hot-path proof (router-level) + Phase 13 capability-aware uptake.

## What this proof shows

The first proof in the WaggleDance autonomy lineage that runs through the **production query handler** `AutonomyRuntime.handle_query(query, context)` rather than calling `SolverRouter.route` or `RuntimeQueryRouter.route` directly. The corpus is the 98-seed canonical library; each entry is rendered as a `(query: str, context: Dict[str, Any])` pair where `context["structured_request"]` carries the family-specific subkey grammar from `runtime_hint_extractor.py`. The proof never injects `low_risk_autonomy_query` — that key is derived by the deterministic Phase 15 hint extractor inside `handle_query` itself.

```
PASS 1 (before harvest)
   AutonomyRuntime.handle_query(query, context) ×98
       intent classification, capsule match, world snapshot
       hint extractor reads context["structured_request"]
       → derives low_risk_autonomy_query for every corpus item (98/98)
       SolverRouter.route(intent, query, context)
           selection.fallback_used = True (forced fallback for the proof)
           autonomy consult invoked via build_autonomy_consult(RuntimeQueryRouter)
           HotPathCache.warm_dispatch → no solver promoted yet → MISS
           BufferedSignalSink.enqueue(GapSignal(kind='runtime_miss', ...))
   Result: 0 served, 98 misses, 98 buffered runtime gap signals.

HARVEST CYCLE
   Buffered signals do not carry spec_seed (the runtime path knows the
   shape, not how to fix it). The harvest step is the natural background
   process that translates a runtime gap into a solver candidate by
   pulling spec_seed from the canonical seed library and feeding the
   AutogrowthScheduler.
   digest_signals_into_intents → 98 growth_intents created, 98 enqueued
   AutogrowthScheduler.run_until_idle → 98 promoted, 0 rejected, 0 errored

PASS 2 (after harvest, cold cache)
   AutonomyRuntime.handle_query(query, context) ×98
       same input shape, same caller path
       hint extractor derives the same hints
       SolverRouter.route → autonomy consult → HotPathCache.warm_dispatch
           cold lookup → SQLite find_auto_promoted_solvers_by_features
           cache populated for next time → execute → return
   Result: 98 served via capability lookup, 0 misses.

PASS 3 (warm only, 3 iterations)
   AutonomyRuntime.handle_query(query, context) ×294
       warm cache hits; no SQLite, no json.loads.
```

No human trigger. No HUMAN_APPROVAL. **Provider/builder-jobs delta during proof: 0 / 0** (locked by `test_proof_zero_provider_delta`). Built-in authoritative solvers untouched by contract: the consult lane only fires when `selection.fallback_used` (Phase 14 contract).

## Headline numbers

| metric | value |
|---|---:|
| corpus_total | 98 |
| selected_caller | `AutonomyRuntime.handle_query` |
| **manual_low_risk_hint_in_input_detected** | **`false`** |
| **proof_constructed_runtime_query_objects** | **`false`** |
| selected_caller_input_keys | `["profile", "structured_request"]` |
| hints_derived_total | **98** |
| pass 1 served | 0 |
| pass 1 miss/fallback | 98 |
| harvest scheduler_promoted | 98 |
| harvest scheduler_rejected / errored | 0 / 0 |
| pass 2 served | **98** |
| pass 2 served_via_capability_lookup | **98** |
| pass 2 miss | 0 |
| negative cases passed | **5 / 5** |
| **provider_jobs delta during proof** | **0** |
| **builder_jobs delta during proof** | **0** |
| HotPathCache warm_hits | 300 |
| HotPathCache cold_hits_warmed | 92 |
| HotPathCache misses | 98 (all from pass 1) |
| BufferedSignalSink max_unflushed observed | 98 |
| BufferedSignalSink documented hard-kill loss bound | 1000 |

Per-family served (after harvest): `bounded_interpolation` 13, `interval_bucket_classifier` 13, `linear_arithmetic` 13, `lookup_table` 16, `scalar_unit_conversion` 27, `threshold_rule` 16. **All six allowlisted families covered.**

Per-cell served: 8 distinct hex cells.

## Latency

| measurement | p50 (ms) | p95 (ms) | p99 (ms) |
|---|---:|---:|---:|
| pass 1 `handle_query` (full path, miss, buffered enqueue) | 10.7 | — | 465 |
| pass 2 cold `handle_query` (warm cache cold-fill) | 10.7 | — | 17 |
| pass 3 warm `handle_query` (full path, warm hit) | 9.4 | — | 18 |
| **hint extractor only** | **0.014** | — | **0.029** |

Notes:
* `handle_query` p50 includes intent classification, capsule match, world model snapshot, MAGMA audit, and the consult lane invocation. Phase 14 measured the warm consult path itself in isolation at ~0.06 ms p50; the rest of `handle_query`'s ~10 ms is the production wrapper around it.
* Hint extractor latency is sub-microsecond at the median — pure Python, deterministic, no I/O.
* Pass 1 p99 spike (465 ms) is one buffered-sink force-flush event during the loop where many enqueues coincided; this is expected behaviour given the 1000-signal / 500 ms bound.

## Negative corpus — 5/5 pass

| input pattern | expected outcome | observed |
|---|---|---|
| ambiguous structured_request (multiple subkeys) | `rejected_ambiguous` | ✅ |
| high-risk family-shaped (`temporal_window_check`) | `rejected_family_not_low_risk` | ✅ |
| missing required field (`unit_conversion` without `to`) | `rejected_missing_fields` | ✅ |
| free-text-only (no `structured_request`) | `skipped` | ✅ |
| malformed `x` (non-numeric) | `rejected_malformed` | ✅ |

In every negative case, the consult lane is **not** invoked, **no** runtime gap signal is emitted, and `handle_query` falls through to its normal "no capabilities available" branch with the rejection kind surfaced for observability.

## Truthfulness boundary

* **Not Stage-2 cutover.** `core/faiss_store.py:26 _DEFAULT_FAISS_DIR=data/faiss/` unchanged.
* **The selected caller is the production query handler.** `AutonomyRuntime.handle_query` is invoked by `CompatibilityLayer.handle_query` (autonomy primary path) which is invoked by `AutonomyService.handle_query` (the service-layer entrypoint). Phase 15 wires the hint extractor + autonomy consult inside `AutonomyRuntime.handle_query`; the change is backwards-compatible (existing callers without `structured_request` see Phase 14 behaviour).
* **Hint extractor is deterministic.** No LLM, no embedding lookup, no fuzzy NLP. The extractor reads only `context["structured_request"]` and derives the autonomy hint from explicit structured subfields.
* **Six-family allowlist preserved.** RULE 13 honoured.
* **No actuator-side autonomy.** Auto-promoted solvers remain *consultable*; built-in solvers retain precedence.
* **No real Anthropic / OpenAI HTTP adapters.** Only `dry_run_stub` and `claude_code_builder_lane` are exercisable. The inner-loop common path makes zero provider calls.
* **No consciousness claim.** Automatic hint derivation is an engineering mechanism — observable, reversible, audit-traceable.

## Reproducing the proof

```
python tools/run_automatic_runtime_hint_proof.py
```

The script deletes its scratch DB at the start of each run so pass 1 truthfully misses. Default outputs:

* `docs/runs/phase15_runtime_hints_release_2026_05_01/automatic_runtime_hint_proof.json` — committed canonical machine-readable evidence.
* `docs/runs/phase15_runtime_hints_release_2026_05_01/automatic_runtime_hint_proof.db` — scratch SQLite, regenerated each run; gitignored via the repo's `*.db` rule.

The script accepts `--out-dir` and `--db` to redirect outputs.

## Cross-references

* `docs/architecture/RUNTIME_ENTRYPOINT_TRUTH_MAP.md` — Phase 15 P1 entrypoint inventory.
* `docs/architecture/LOW_RISK_AUTOGROWTH_POLICY.md` — Phase 11 policy envelope.
* `docs/architecture/PROVIDER_PLANE_AND_BUILDER_LANES.md` — inner / outer loop boundary.
* `waggledance/core/autonomy_growth/runtime_hint_extractor.py` — Phase 15 deterministic extractor.
* `waggledance/core/autonomy/runtime.py` — `AutonomyRuntime.handle_query` wiring.
* Phase 14 proof: `docs/runs/phase14_live_runtime_hotpath_2026_05_01/live_runtime_hotpath_proof.md`.
* `tests/autonomy_growth/test_automatic_runtime_hint_proof_smoke.py` — locks the proof invariants.
