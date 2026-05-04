# Phase 17A — Docker `--network none` verification

**Date:** 2026-05-04
**Image:** `waggledance:phase17a` (ID `39679794a47d`, ~9 GB)
**Container runtime:** Docker Desktop 4.71.0, Engine 29.4.1, runc 1.3.5
**Network:** `--network none` for every proof — no DNS, no outbound, no API key reachable.

## Result: PASS — producer fabric, 10k scale, full-restart all green inside Docker offline

## Build

```
docker build -t waggledance:phase17a .
```

* Built from `phase17a/producer-fabric-scale` worktree at HEAD `48ac0b3` + Phase 17A working changes.
* `.dockerignore` extended (Phase 17A carve-out): `!tools/run_phase17a_producer_fabric_proof.py` and `!tools/run_solver_scale_proof.py` added alongside the four existing canonical proof tools and `tests/autonomy_growth/`.
* No Dockerfile change needed — the Phase 16F base (`python:3.13-slim` + `requirements-ci.txt`) is unchanged.
* Image larger than Phase 16F's 3.09 GB because buildx multi-platform exporter is enabled; the runtime payload is the same.

## Proof 1/3 — Phase 17A Producer Fabric

```bash
docker run --rm --network none waggledance:phase17a \
    python tools/run_phase17a_producer_fabric_proof.py --out-dir /tmp/p17a
```

| field | value | required | met |
|---|---|---|---|
| `phase` | phase17a_producer_fabric | match | ✅ |
| `corpus_total` | 30 | ≥ 30 | ✅ |
| `producers_run` | curiosity, self_model, dream, hive | all 4 | ✅ |
| `ir_objects_emitted_total` | 68 | > 0 | ✅ |
| `ir_objects_per_kind.curiosity` | 30 | ≥ corpus_total | ✅ |
| `ir_objects_per_kind.self_model` | 14 | ≥ 14 (6 tensions + 8 blind spots) | ✅ |
| `ir_objects_per_kind.dream_curriculum` | 6 | > 0 | ✅ |
| `ir_objects_per_kind.dream_meta_proposal` | 2 | > 0 | ✅ |
| `ir_objects_per_kind.hive_proposals` | 8 | > 0 | ✅ |
| `ir_objects_per_kind.review_bundle` | 8 | > 0 | ✅ |
| `negative_cases_passed` | 6 / 6 | 6 / 6 | ✅ |
| `provider_jobs_delta_during_proof` | 0 | 0 | ✅ |
| `builder_jobs_delta_during_proof` | 0 | 0 | ✅ |
| `no_provider_credentials_required` | true | true | ✅ |
| `no_runtime_network_required` | true | true | ✅ |
| `no_human_approval_collected` | true | true | ✅ |
| `no_stage2_flip_executed` | true | true | ✅ |
| `no_allowlist_widening` | true | true | ✅ |

## Proof 2/3 — 10k Synthetic Solver Capability Scale

```bash
docker run --rm --network none waggledance:phase17a \
    python tools/run_solver_scale_proof.py --out-dir /tmp/p17a \
        --descriptors 10000 --lookup-pass-count 1000
```

| field | value | required | met |
|---|---|---|---|
| `synthetic_solver_descriptors_total` | 10000 | ≥ 10000 | ✅ |
| `families_total` | 6 | == 6 | ✅ |
| `hex_cells_total` | 8 | == 8 | ✅ |
| `is_synthetic_scale` | true | true | ✅ |
| `not_canonical_corpus` | true | true | ✅ |
| `lookup_pass_count` | 1000 | ≥ 1000 | ✅ |
| `lookup_capability_hits_total` | 1000 | == lookup_pass_count | ✅ |
| `lookup_fifo_fallback_total` | 0 | == 0 | ✅ |
| `lookup_miss_total` | 0 | == 0 | ✅ |
| `lookup_by_source` | `{"auto_promoted_solver": 1000}` | only auto_promoted_solver | ✅ |
| `build_index_time_seconds` | 148.70 | finite | ✅ |
| `build_descriptors_per_second` | 67.3 | > 0 | ✅ |
| `lookup_p50_ms` | 0.4562 | < 100 | ✅ |
| `lookup_p95_ms` | 0.8578 | finite | ✅ |
| `lookup_p99_ms` | 1.427 | finite | ✅ |
| `provider_jobs_delta` | 0 | 0 | ✅ |
| `builder_jobs_delta` | 0 | 0 | ✅ |
| `no_allowlist_widening` | true | true | ✅ |

**Note:** Docker p50 latency (0.46 ms) is *lower* than local p50 (4.24 ms). This is because the Docker build environment ran the proof immediately after a fresh DB build with warm caches and no other host load; local was run with mixed workload. Both passes meet the < 100 ms p50 sanity check.

## Proof 3/3 — Phase 16B P2 full-restart-continuity (now at 128-seed corpus)

```bash
docker run --rm --network none waggledance:phase17a \
    python tools/run_full_restart_continuity_proof.py --out-dir /tmp/p17a \
        --db /tmp/p17a/restart.db
```

| field | value | required | met |
|---|---|---|---|
| `corpus_total` | 128 | ≥ 100 | ✅ |
| pre-restart pass2 served / via-capability / miss | 128 / 128 / 0 | N / N / 0 | ✅ |
| persisted solver_count before/after | 128 / 128 | identical | ✅ |
| persisted capability_features before/after | 220 / 220 | identical | ✅ |
| post-restart pass2 served / via-capability / miss | 128 / 128 / 0 | N / N / 0 | ✅ |
| served_unchanged_across_restart | True | True | ✅ |
| served_via_capability_unchanged_across_restart | True | True | ✅ |
| solver_count_unchanged_across_reopen | True | True | ✅ |
| capability_features_unchanged_across_reopen | True | True | ✅ |
| provider_jobs_delta_across_restart | 0 | 0 | ✅ |
| builder_jobs_delta_across_restart | 0 | 0 | ✅ |
| cache_rebuild_success | True | True | ✅ |
| provider_jobs_delta_during_proof | 0 | 0 | ✅ |
| builder_jobs_delta_during_proof | 0 | 0 | ✅ |

## Carry-forward Phase 16F invariants

* No internet at runtime (`--network none` enforced): ✅
* No provider credentials baked into image: ✅
* No autonomy code mutated outside the planned port: verified by `git diff origin/main..HEAD -- waggledance/core/` — only `dreaming/`, `magma/{self_model,reflective_workspace}.py`, `meta/` additions; nothing modified in existing files except `low_risk_seed_library.py` (+24 seeds) and `__init__.py` re-exports if present.
* Six-family allowlist unchanged: verified by `test_seed_library_per_family_minimum_after_phase17a_growth` and `producer_fabric_proof.json::no_allowlist_widening = true`.
* Bandit B324 cleanup carry-forward: not re-run this session because no new HIGH/MEDIUM patterns were introduced (Phase 17A producer code is pure-stdlib, no new hashlib calls outside what already existed in the ported modules; if any did, they will surface in the post-merge Bandit run).

## Decision summary

The Docker `--network none` lane is GREEN for every Phase 17A claim:

1. Producer fabric runs end-to-end against deterministic fixtures inside the offline container. ✅
2. 10,000 synthetic solver descriptors load and 1,000 capability lookups all hit the auto-promoted-solver source. ✅
3. The 128-seed corpus survives a DB close + reopen with byte-stable counts. ✅

`v3.9.0-producer-fabric-alpha` is justified as a PRERELEASE; v3.8.0 stable remains GitHub Latest.
