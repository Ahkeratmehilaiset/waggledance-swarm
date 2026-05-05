# Gap Miner + Solver Feedback Loop — 2026-Q2

**Status:** Phase 18B snapshot, derived from this session's reproducible artifacts only.
**Date:** 2026-05-05
**Branch:** `phase18b/gap-miner-feedback`
**Anchor:** `v3.10.1-gap-miner-feedback-alpha` candidate (PRERELEASE only). v3.8.0 remains GitHub Latest.

This document publishes the runtime gap-mining feedback loop that converts observed runtime gap signals into structured, auditable verdicts. It is an **engineering** record. It does not assert WaggleDance is faster, smarter, or otherwise superior to any external system. The mining contract is fail-closed: every signal lands on exactly one of six verdicts.

## Reproduce

```
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout v3.10.1-gap-miner-feedback-alpha   # or stay on main
pip install -r requirements-ci.txt              # nothing extra; mining is stdlib-only
python tools/run_phase18b_gap_miner_feedback_proof.py
python tools/validate_phase18a_benchmark_bundle.py \
    --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

Both processes exit `0`.

## What the loop does

```
runtime gap signal
  → mined gap candidate (deterministic SHA-256 candidate_id from family + features)
  → verdict ∈ {
      ALLOWLISTED_SOLVER_SPEC,
      INSUFFICIENT_EVIDENCE,
      OUT_OF_FAMILY_REJECTED,
      HIGH_RISK_REJECTED,
      BUILDER_HANDOFF_QUARANTINED,
      DUPLICATE_SUPPRESSED,
    }
  → for ALLOWLISTED: deterministic solver spec emitted in canonical
    shape; consumed by the existing Phase 9 14-stage promotion ladder.
  → for BUILDER_HANDOFF_QUARANTINED: payload retained with
    `no_auto_promotion = true`. Operator review required.
  → all other verdicts: explicit rejection with a recorded reason.
```

## Verdict pipeline (priority order)

For each cluster of signals (grouped by `family_kind` + canonical `feature_dict` + optional `cluster_window`):

1. `risk_label == "high_risk"` AND high-risk policy enabled → **HIGH_RISK_REJECTED**.
2. `family_kind` not in the six-family allowlist AND not `"builder_handoff"` → **OUT_OF_FAMILY_REJECTED**.
3. `family_kind == "builder_handoff"` → **BUILDER_HANDOFF_QUARANTINED** with `no_auto_promotion = true`.
4. `signal_count < min_signals_for_candidate` OR `confidence < min_confidence` → **INSUFFICIENT_EVIDENCE**.
5. Cluster's `candidate_id` already emitted as ALLOWLISTED in this run → **DUPLICATE_SUPPRESSED**.
6. Otherwise → **ALLOWLISTED_SOLVER_SPEC**.

`candidate_id` is the first 16 hex chars of `sha256(family_kind + "|" + canonical_json(feature_dict))`. Same input → same id.

## Six-family allowlist (unchanged)

```
scalar_unit_conversion
lookup_table
threshold_rule
interval_bucket_classifier
linear_arithmetic
bounded_interpolation
```

The allowlist is the runtime invariant from Phase 11. Phase 18B does not widen it.

## Honest scope

What you can take from this document:

* The mainline gap miner (`waggledance/core/autonomy_growth/gap_mining.py`) is stdlib-only, fail-closed, and deterministic. Same input → same verdicts → same candidate IDs across runs.
* The Phase 18B proof harness drove a 30-signal synthetic fixture covering all six verdicts: 6 ALLOWLISTED, 3 INSUFFICIENT_EVIDENCE, 2 OUT_OF_FAMILY_REJECTED, 1 HIGH_RISK_REJECTED, 1 BUILDER_HANDOFF_QUARANTINED, 1 DUPLICATE_SUPPRESSED. `release_gate_pass = true`.
* Builder handoff is a **quarantine contract**, not an autonomous builder lane. Every quarantined payload has `no_auto_promotion = true`, `no_provider_call = true`, `no_builder_call_in_proof = true`, `no_cloud_api = true`, `promotion_allowed = false`. Operator review is required before any quarantined payload turns into a real solver.

What you cannot take from this document:

* This is **not** the Phase 9 14-stage promotion ladder. Phase 18B emits structured solver specs; the ladder consumes them. Wiring `RuntimeQueryRouter.dispatch_by_features` live to consume Phase 18B specs is a separate follow-up integration sprint (`capability_lookup_status = NOT_RUN_OUT_OF_PHASE18B_SCOPE`).
* This is **not** an autonomous builder lane. Builder handoff is quarantined; nothing is auto-promoted from it.
* This is **not** a raw-intelligence claim. Phase 18B emits structured candidates. No model is consulted.

## Numbers (this session)

| metric | value |
| --- | ---: |
| signals_total | 30 |
| candidates_total | 14 |
| ALLOWLISTED_SOLVER_SPEC | 6 |
| INSUFFICIENT_EVIDENCE | 3 |
| OUT_OF_FAMILY_REJECTED | 2 |
| HIGH_RISK_REJECTED | 1 |
| BUILDER_HANDOFF_QUARANTINED | 1 |
| DUPLICATE_SUPPRESSED | 1 |
| solver_specs_total | 6 |
| provider_jobs_delta | 0 |
| builder_jobs_delta | 0 |
| `forbidden_claims_absent` | true |
| `release_gate_pass` | true |

The canonical artifact is `docs/runs/phase18b_gap_miner_feedback_2026_05_05/gap_miner_feedback_proof.{json,md}`.

## Honesty contracts

* `no_model_pull_or_download = true`
* `no_cloud_api_calls = true`
* `no_live_builder_execution = true`
* `no_raw_intelligence_superiority_claim = true`
* `no_cross_vendor_ranking_claim = true`
* `allowlist_unchanged = true`
* `provider_jobs_delta = builder_jobs_delta = 0`
* `no_stage2_flip = true`
* `no_human_approval = true`

## Position in the 2026-Q2 release line

| Tag | What it adds | Status |
|---|---|---|
| `v3.8.0` | stable release | **Latest** |
| `v3.9.0-producer-fabric-alpha` | Phase 17A producer fabric + 10k scale | Pre-release |
| `v3.9.1-local-efficiency-benchmark-alpha` | Phase 17B local efficiency benchmark harness | Pre-release |
| `v3.9.2-local-ollama-baseline-alpha` | Phase 17C local Ollama baseline (one model) | Pre-release |
| `v3.9.3-local-model-sweep-alpha` | Phase 17D 4-model panel + repeatability | Pre-release |
| `v3.10.0-benchmark-schema-alpha` | Phase 18A bundle export + schema validation | Pre-release |
| `v3.10.1-gap-miner-feedback-alpha` | Phase 18B runtime gap miner + solver feedback loop (this PR's candidate) | Pre-release (candidate) |

Phase 18B does not modify any earlier tag. v3.8.0 remains GitHub Latest.
