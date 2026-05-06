# Phase 18E — P1 Runtime Gap / Storage / Dispatch Inventory

**Date (UTC):** 2026-05-06
**Sources inspected:**
* `waggledance/core/storage/control_plane_schema.py`
* `waggledance/core/storage/control_plane.py`
* `waggledance/core/autonomy_growth/gap_candidate.py`
* `waggledance/core/autonomy_growth/gap_mining.py`
* `waggledance/core/autonomy_growth/mined_solver_runtime.py`
* `waggledance/core/autonomy_growth/runtime_query_router.py`
* `tools/run_phase18b_gap_miner_feedback_proof.py`
* `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py`
* `README.md`, `CHANGELOG.md`, prior `docs/runs/phase{12,13,14,18b,18c}/...`

## 1. Existing persisted runtime-gap table — `runtime_gap_signals`

**This table exists today.** It was added in ControlPlaneDB schema v3 (Phase 12 — self-starting local-first autogrowth). It is referenced explicitly in `README.md`, `CURRENT_STATUS.md`, the Phase 12 / 13 / 14 / 16 / 17 proof artifacts, the UI hologram scale-aware aggregator, and four runtime proof harnesses that report `harvested_signals_total = cp.count_runtime_gap_signals()`.

```sql
CREATE TABLE IF NOT EXISTS runtime_gap_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    family_kind     TEXT,
    cell_coord      TEXT,
    signal_payload  TEXT,
    weight          REAL NOT NULL DEFAULT 1.0,
    observed_at     TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runtime_gap_signals_kind
    ON runtime_gap_signals(kind, observed_at);
CREATE INDEX IF NOT EXISTS idx_runtime_gap_signals_family_cell
    ON runtime_gap_signals(family_kind, cell_coord);
```

Existing public ControlPlaneDB API:

* `record_runtime_gap_signal(kind, *, family_kind=..., cell_coord=..., signal_payload=..., weight=..., observed_at=...) -> RuntimeGapSignalRecord` — unconditional INSERT, returns the autoincrement row.
* `count_runtime_gap_signals(*, kind=..., family_kind=..., cell_coord=...) -> int` — count only; **no existing list/iter helper that returns row contents.**

Live use: `RuntimeGapDetector.record(GapSignal)` (Phase 12) writes here whenever the runtime hot path observes a miss/hint. The signal_payload is opaque JSON.

## 2. Phase 18B mining contract (`mine_runtime_gaps`)

`mine_runtime_gaps(signals: Sequence[Mapping[str, Any]], *, config=...) -> GapMiningResult` consumes signal mappings whose recognized keys are:

| Key | Used in |
| --- | --- |
| `family_kind` | clustering, verdict pipeline, candidate row |
| `feature_dict` | clustering, candidate_id derivation |
| `cluster_window` | clustering (lets two waves of the same gap form two clusters with the same candidate_id; second is `DUPLICATE_SUPPRESSED`) |
| `confidence_hint` | aggregation (cluster confidence = max across signals) |
| `risk_label` | aggregation (any HIGH_RISK escalates the cluster) |
| `evidence_ref` | aggregation (union of refs) |
| `signal_id` | provenance (signal_ids list) |
| `raw_query` | provenance (raw_queries list) |
| `miss_reason` | provenance (miss_reasons union) |

Verdict pipeline (in priority order): HIGH_RISK_REJECTED → OUT_OF_FAMILY_REJECTED → BUILDER_HANDOFF_QUARANTINED → INSUFFICIENT_EVIDENCE → DUPLICATE_SUPPRESSED → ALLOWLISTED_SOLVER_SPEC.

Allowlist (`ALLOWED_FAMILIES`): `("scalar_unit_conversion", "lookup_table", "threshold_rule", "interval_bucket_classifier", "linear_arithmetic", "bounded_interpolation")`.

Determinism: `candidate_id = SHA256(family_kind + "|" + canonical_json(feature_dict))[:16]`.

## 3. Phase 18C registration contract (`register_mined_solver_specs`)

`register_mined_solver_specs(*, candidates: Sequence[GapCandidate], control_plane: ControlPlaneDB) -> RegistrationSummary`. For every `ALLOWLISTED_SOLVER_SPEC` candidate (and only those):

1. `upsert_solver_family(name=family_kind, ...)`.
2. `upsert_solver(name=f"phase18c_{family_kind}_{candidate_id}", status="auto_promoted", spec_hash=...)`.
3. `set_solver_capability_features(solver_id, family_kind, features)`.
4. `upsert_solver_artifact(solver_id, family_kind, artifact_id, spec_canonical_json, artifact_json)`.

Idempotent within a run via in-memory `seen_in_run: set[str]` of candidate_ids. Across runs, the underlying `upsert_*` methods are idempotent on their canonical key (name + version, family_kind, etc.), so re-running the same registration produces no duplicates.

`compile_mined_spec_to_runtime_artifact()` looks up `(family_kind, _canonical_features_key(feature_dict))` in `_COMPILATION_TABLE` and raises `RuntimeArtifactCompilationError` for any missing signature. The current table contains exactly the six Phase 18B fixture shapes.

## 4. Phase 18C dispatch path (`LowRiskSolverDispatcher.dispatch_by_features`)

Lives at `waggledance/core/autonomy_growth/runtime_query_router.py`. Performs the SQL-backed capability superset lookup against `solver_capability_features` and returns a `DispatchResult` with `reason="hit_by_features"` for capability-hit auto-promoted solvers. This is the **same** runtime path used by `RuntimeQueryRouter` and the Phase 17A 10k-scale proof.

## 5. Inventory verdict per operator note 1

> "If the inventory finds an existing runtime_gap_signals or equivalent table, the design doc MUST justify whether Phase 18E reuses it, extends it with replay-required columns (schema_version, provenance_hash), or creates a separate replay-projection view. Do not silently create a parallel table."

A separate replay-projection table is **NOT** justified. The existing `runtime_gap_signals` table is a structurally adequate fit. Phase 18E will reuse it with a discriminator. The design doc (P2) will state this choice explicitly and walk through the alternatives that were considered and rejected.

## 6. Files Phase 18E will modify or add

**Add (mainline):**

* `waggledance/core/autonomy_growth/runtime_gap_replay.py` — Phase 18E replay module (persist + load + replay).

**Add (proof + tests):**

* `tools/run_phase18e_runtime_gap_replay_proof.py` — proof harness end-to-end (persist → load → replay → register → dispatch + idempotency double-replay).
* `tests/autonomy_growth/test_phase18e_runtime_gap_replay.py` — ≥30 unit/integration tests.

**Add (docs):**

* `docs/runs/phase18e_runtime_gap_replay_2026_05_06/*` (this session folder).
* `docs/benchmarks/RUNTIME_GAP_REPLAY_2026.md` (public report).

**Modify (minimal):**

* `waggledance/core/storage/control_plane.py` — add a single read helper `list_runtime_gap_signals(*, kind=None, ...)` returning the actual rows. **No schema change. No `ALTER TABLE`. No new column.**
* `CHANGELOG.md`, `CURRENT_STATUS.md`, `README.md`, `docs/release/RELEASE_READINESS.md`, `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` — release-facing entries.
* `.dockerignore` — carve out the new proof harness so it ships in the image.

## 7. Files Phase 18E will NOT modify

* `waggledance/core/storage/control_plane_schema.py` — the schema is unchanged.
* `waggledance/core/autonomy_growth/gap_candidate.py` — the dataclasses are reused verbatim.
* `waggledance/core/autonomy_growth/gap_mining.py` — `mine_runtime_gaps` is reused verbatim.
* `waggledance/core/autonomy_growth/mined_solver_runtime.py` — `register_mined_solver_specs`, `_COMPILATION_TABLE`, etc. are reused verbatim.
* `waggledance/core/autonomy_growth/runtime_query_router.py` — `LowRiskSolverDispatcher.dispatch_by_features` is reused verbatim.
* `gap_intake.py`, `RuntimeGapDetector` write path — unchanged.
* All Phase 18A bundle / Phase 18B proof / Phase 18C proof artifacts — carry-forward only.

## 8. Idempotency answer

Two independent layers:

1. **Persistence layer.** Phase 18E computes a deterministic `event_id` (SHA-256 prefix of `family_kind` + canonical(`feature_dict`) + `cluster_window` + `evidence_ref`). Before INSERT, `persist_runtime_gap_events` queries the kind-discriminated subset of the table and skips events whose `event_id` is already present. So persisting the same fixture twice produces no extra rows.
2. **Mining + registration layer.** Already idempotent in Phase 18B (`DUPLICATE_SUPPRESSED` for same candidate_id within a run) and Phase 18C (`seen_in_run` set; `upsert_*` is idempotent across runs by canonical key). So replaying the same persisted event set twice produces:
   * the same six allowlisted candidates (same `candidate_id`s);
   * the same six `phase18c_<family>_<candidate_id>` solver rows (`upsert_solver` no-op on second call);
   * the same six `solver_capability_features` rows;
   * the same six `solver_artifacts` rows.

The proof harness exercises this by replaying the loaded event set a second time and asserting:

* `registered_count` after replay 2 == 0 new (or `seen_in_run` blocks even before that),
* dispatch hits unchanged,
* total row counts in `solvers`, `solver_capability_features`, `solver_artifacts` unchanged between replay 1 and replay 2.

## 9. Malformed / corrupt event handling

Three rejection layers, in order:

1. **Normalize-time rejection** (`normalize_runtime_gap_event`):
   * unsupported `schema_version` → `GapEventSchemaError`.
   * missing required fields → `GapEventSchemaError`.
   * non-JSON or non-dict raw input → `GapEventSchemaError`.
   * non-allowlisted family + not `builder_handoff` (these are still persisted as audit records but flagged so they cannot register).
2. **Persistence-time fail-closed**: malformed payloads never reach the table; they are rejected with the pattern name (no values printed).
3. **Replay-time fail-closed**:
   * deserialization errors → counted in `malformed_event_rejection_count`.
   * verdicts other than `ALLOWLISTED_SOLVER_SPEC` → never registered.
   * compilation errors (novel feature_dict signatures) → counted in `compilation_failed_count`; not registered.
   * builder-handoff → `BUILDER_HANDOFF_QUARANTINED`; quarantined payload preserved; never executable.

The proof harness fixture deliberately includes (a) one corrupt-JSON event, (b) one missing-required-field event, (c) one unsupported-schema-version event, (d) one out-of-family event, (e) one high-risk event, (f) one builder-handoff event, (g) two insufficient-evidence events, (h) one duplicate event — to exercise every rejection branch.

Phase 18E may proceed to P2 design doc.
