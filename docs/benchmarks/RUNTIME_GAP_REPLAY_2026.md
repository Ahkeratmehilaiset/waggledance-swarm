# Runtime Gap Replay — 2026 (Phase 18E)

**Status:** PRERELEASE candidate `v3.10.3-runtime-gap-replay-alpha`. Not GitHub Latest.
**Released as:** prerelease only. `v3.8.0` remains GitHub Latest. `v3.10.2-mined-solver-dispatch-alpha` remains the previous prerelease.
**Phase 18D (immediately prior):** docs-only PR; no tag created.

## Why this phase exists

Phase 18B closed the runtime-gap-mining feedback half of the autonomous learning loop, and Phase 18C wired the mined ALLOWLISTED solver specs through the real `ControlPlaneDB` + `LowRiskSolverDispatcher.dispatch_by_features` path. Both phases proved the loop on an **in-memory synthetic fixture** built inside a proof harness.

Phase 18E ships the next maturity step: the same loop, but starting from a **persisted, durable, content-keyed gap-event audit trail**. The new entry point is a deterministic event store inside the existing `runtime_gap_signals` table; the rest of the pipeline (Phase 18B miner, Phase 18C registration, real dispatcher) is reused verbatim.

## What this proves

```
persisted runtime gap events        (runtime_gap_signals,
                                     kind = phase18e.runtime_gap_event.v1)
    -> load_runtime_gap_events
    -> mine_runtime_gaps             (Phase 18B verbatim)
    -> register_mined_solver_specs   (Phase 18C verbatim)
    -> ControlPlaneDB capability rows + artifacts
    -> LowRiskSolverDispatcher.dispatch_by_features
```

* Runtime gap events are persisted as durable rows with deterministic `event_id`s, content-derived `provenance_hash`es, and a versioned schema discriminator (`phase18e.runtime_gap_event.v1`). No new table; no `ALTER TABLE`; existing schema v3 unchanged.
* Replay is idempotent at two layers: persistence (same `event_id` skipped) and runtime registration (`upsert_*` no-op on second call).
* All six low-risk allowlist families register; 18 / 18 deterministic dispatch cases hit through the real capability-aware dispatcher with `reason = "hit_by_features"`.
* Non-allowlisted events (insufficient evidence, out-of-family, high-risk, builder-handoff, duplicate) and malformed events (corrupt JSON, missing field, bad schema_version, forbidden field) never become executable runtime solvers.
* Proof reproduces under Docker `--network none` with no Ollama, no cloud, no builder execution.

## Measured proof (host run + Docker `--network none`)

| Counter | Value |
| --- | --- |
| persisted_event_count | 32 |
| loaded_event_count | 32 |
| malformed_event_rejection_count | 3 |
| forbidden_field_rejections | 1 |
| signals_total | 32 |
| candidates_total | 13 |
| allowlisted_candidate_count | 6 |
| insufficient_evidence_total | 3 |
| out_of_family_rejected_total | 1 |
| high_risk_rejected_total | 1 |
| builder_handoff_quarantine_count | 1 |
| duplicate_suppression_count | 1 |
| registered_solver_count | 6 |
| non_allowlisted_rejected_count | 7 |
| dispatch_case_count | 18 |
| dispatch_success_count | 18 |
| dispatch_failure_count | 0 |
| families_covered | 6 |
| replay_idempotency_pass | True |
| second_persist_inserted | 0 |
| second_persist_skipped_existing | 32 |
| second_replay_extra_solvers | 0 |
| second_replay_extra_capability_features | 0 |
| second_replay_extra_artifacts | 0 |
| provider_jobs_delta / builder_jobs_delta | 0 / 0 |
| **release_gate_pass** | **True** |

## Persisted gap event schema (v1)

`signal_payload` of each `runtime_gap_signals` row with `kind = phase18e.runtime_gap_event.v1` is canonical JSON with these required fields:

`event_id`, `schema_version`, `occurred_at_utc`, `source`, `family_kind`, `feature_dict`, `raw_query`, `miss_reason`, `confidence_hint`, `risk_label`, `evidence_ref`, `cluster_window`, `provenance_hash`. Plus optional `signal_id`, `notes`. Forbidden keys (case-insensitive substring match): `token`, `password`, `authorization`, `secret`, `api_key`, `private_key`. Forbidden value patterns: `gho_*`, `github_pat_*`, `https://x-access-token:...@`, `Authorization: Bearer ...`, `BEGIN (RSA )?PRIVATE KEY`. Any forbidden field or value triggers `GapEventSchemaError` and the event is **not** persisted.

`event_id = SHA256(family_kind | canonical(feature_dict) | cluster_window | evidence_ref)[:16]`. `provenance_hash = SHA256(canonical_dict_excluding_provenance_hash_itself)`.

## Storage choice — REUSE not parallel

Phase 18E does **not** create a new gap-event table. The existing `runtime_gap_signals` table (Phase 12 schema v3, written by `RuntimeGapDetector.record(GapSignal)`) is structurally adequate: `signal_payload TEXT` carries the canonical JSON, `family_kind` is already present, `kind` becomes the schema-version discriminator, `weight` carries `confidence_hint`, `observed_at` carries the event time. This decision is documented with alternatives weighed in `docs/runs/phase18e_runtime_gap_replay_2026_05_06/runtime_gap_replay_design.md` § 1.

Compatibility: the existing `record_runtime_gap_signal()` API, `count_runtime_gap_signals()` API, the Phase 12 `RuntimeGapDetector` write path, and the UI hologram aggregator continue to work unchanged. Phase 18E is purely additive.

## What Phase 18E does NOT claim

* No raw-intelligence superiority claim. **NOT_CLAIMED.**
* No cross-vendor ranking claim. **NOT_CLAIMED.**
* No high-risk family auto-promotion. **BLOCKED by design.**
* No allowlist widening.
* No live builder execution; builder handoff remains a quarantined contract.
* No model pull / download. No cloud API call. No Stage-2 atomic flip. No HUMAN_APPROVAL collected.
* No production-certified factory deployment. No "beats all competitors" or "world fastest" language.
* No consciousness, sentience, awareness, or AGI claim.

## Reproduce

```
python -X utf8 tools/run_phase18e_runtime_gap_replay_proof.py \
    --out-dir docs/runs/phase18e_runtime_gap_replay_2026_05_06
```

Inside Docker `--network none`:

```
docker build -t waggledance:phase18e -f Dockerfile .
docker run --rm --network none waggledance:phase18e \
    python tools/run_phase18e_runtime_gap_replay_proof.py --out-dir /tmp/p18e
```

Tests:

```
python -X utf8 -m pytest tests/autonomy_growth/test_phase18e_runtime_gap_replay.py -q
→ 48 passed
```

Targeted carry-forward (Phase 18E + 18C + 18B + 18A + phase10 + storage + ui_hologram + solver_router):

```
python -X utf8 -m pytest \
    tests/autonomy_growth/test_phase18e_runtime_gap_replay.py \
    tests/autonomy_growth/test_phase18c_mined_solver_runtime_dispatch.py \
    tests/autonomy_growth/test_phase18b_gap_miner_feedback.py \
    tests/benchmarks/test_phase18a_benchmark_externalization.py \
    tests/phase10/ tests/storage/ tests/ui_hologram/ \
    tests/autonomy/test_solver_router.py -q
→ 251 passed
```
