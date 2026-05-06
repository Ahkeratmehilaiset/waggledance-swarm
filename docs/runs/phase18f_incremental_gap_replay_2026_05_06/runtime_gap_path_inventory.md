# Phase 18F — P1 Runtime Gap / Replay / Detector Path Inventory

**Date (UTC):** 2026-05-06
**Sources inspected:**

* `waggledance/core/autonomy_growth/runtime_gap_replay.py` (Phase 18E persist + load + replay)
* `waggledance/core/autonomy_growth/gap_intake.py` (Phase 12 `RuntimeGapDetector` + `GapSignal`)
* `waggledance/core/autonomy_growth/gap_mining.py` (Phase 18B miner)
* `waggledance/core/autonomy_growth/mined_solver_runtime.py` (Phase 18C registration)
* `waggledance/core/autonomy_growth/solver_dispatcher.py` (Phase 17A `LowRiskSolverDispatcher`)
* `waggledance/core/storage/control_plane.py` + `control_plane_schema.py`
* `tools/run_phase18e_runtime_gap_replay_proof.py`

## 1. `runtime_gap_signals` table — confirmed reuse

```sql
CREATE TABLE IF NOT EXISTS runtime_gap_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,   -- monotonic cursor key
    kind            TEXT NOT NULL,                       -- discriminator
    family_kind     TEXT,
    cell_coord      TEXT,
    signal_payload  TEXT,                                -- canonical JSON
    weight          REAL NOT NULL DEFAULT 1.0,
    observed_at     TEXT NOT NULL,
    created_at      TEXT NOT NULL                        -- INSERT timestamp
);
CREATE INDEX idx_runtime_gap_signals_kind ON runtime_gap_signals(kind, observed_at);
CREATE INDEX idx_runtime_gap_signals_family_cell ON runtime_gap_signals(family_kind, cell_coord);
```

**Cursor anchor:** `id INTEGER PRIMARY KEY AUTOINCREMENT` provides a deterministic, monotonic, gap-free row ordering. Phase 18F uses `id` as the cursor key. No equal-timestamp tie-breaker needed; SQLite guarantees `id` strictly increasing per insertion within the table.

**Existing public API:** `record_runtime_gap_signal()` (insert), `count_runtime_gap_signals()` (count), `list_runtime_gap_signals()` (Phase 18E read helper). None of these use `id > cursor` filtering — Phase 18F adds a small `list_runtime_gap_signals_after_id()` helper.

**Verdict:** Phase 18F **REUSES `runtime_gap_signals` as the event table**. No new event table; no parallel storage; no schema change.

## 2. `schema_meta` table — replay-state home

Schema-v0 base table:

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

This is exactly the right shape for replay state. Phase 18F stores:

| Key | Value JSON shape | Purpose |
| --- | --- | --- |
| `phase18f.replay_cursor.v1` | `{"last_processed_id": <int>, "advanced_at_utc": "<iso>"}` | persistent cursor; updated only on successful replay |
| `phase18f.replay_lock.v1` | `{"acquired_at_utc": "<iso>", "owner": "<pid:host>"}` | optional in-DB lock to serialize concurrent replay attempts |

**Verdict:** **NO new state table.** Phase 18F reuses `schema_meta` with two phase18f-prefixed keys. A small `set_meta()` / `get_meta()` / `delete_meta()` helper is added to `ControlPlaneDB`.

## 3. RuntimeGapDetector — Phase 12 source

`waggledance/core/autonomy_growth/gap_intake.py` defines:

```python
@dataclass(frozen=True)
class GapSignal:
    kind: str                                  # 'miss' | 'fallback' | 'shadow_mismatch' | 'family_hole' | ...
    family_kind: Optional[str]
    cell_coord: Optional[str]
    intent_seed: Optional[str] = None
    weight: float = 1.0
    payload: Optional[Mapping[str, Any]] = None
    spec_seed: Optional[Mapping[str, Any]] = None

class RuntimeGapDetector:
    def record(self, signal: GapSignal) -> RuntimeGapSignalRecord:
        # writes to runtime_gap_signals via cp.record_runtime_gap_signal
        # also emits "signal_recorded" growth_event
```

The detector's `kind` is **free-form** (e.g. `"miss"`); `signal_payload` is a JSON-encoded `payload` dict. The detector does NOT itself produce Phase 18E `kind = phase18e.runtime_gap_event.v1` rows.

### Bridge strategy

Phase 18F adds **`bridge_detector_signal_to_phase18e_event()`**: takes a `GapSignal` (or its persisted detector-row equivalent) plus required keyword arguments (`raw_query`, `miss_reason`, `confidence_hint`, `risk_label`, `evidence_ref`) and returns a Phase 18E event mapping. The bridge requires the payload to contain a structured `feature_dict` (a Mapping); detector signals whose payload is missing `feature_dict` or whose payload is not a Mapping are rejected at bridge time with a fail-closed error.

A second helper, **`persist_detector_gap_signals_as_replay_events()`**, accepts a list of `(GapSignal, contextual_kwargs)` tuples and round-trips each through bridge + Phase 18E `persist_runtime_gap_events`. This is the path the proof harness (Stage H) and tests use to demonstrate the detector → replay pipeline end-to-end without inventing a fake parallel detector.

The `RuntimeGapDetector.record()` call path itself is **unchanged**; Phase 18F is additive.

## 4. Phase 18E load / Phase 18B mine / Phase 18C register paths

All three are reused **verbatim** by Phase 18F:

* `load_runtime_gap_events()` (Phase 18E) — adds an optional `min_event_id_exclusive` filter via the new helper, but the deserialization + normalization code is unchanged.
* `mine_runtime_gaps()` (Phase 18B) — called with the loaded post-cursor signals.
* `register_mined_solver_specs()` (Phase 18C) — called with the new ALLOWLISTED candidates.
* `LowRiskSolverDispatcher.dispatch_by_features()` — the real runtime path.

No fork, no duplicate implementation.

## 5. Phase 18C compilation table — feature-shape coverage

Current compilation table (in `mined_solver_runtime.py`) maps `(family_kind, _canonical_features_key(feature_dict))` → executor artifact and contains exactly six entries (one per family) keyed by the Phase 18B fixture's `feature_dict`. To prove **post-cursor new solvers per family**, Phase 18F's post-cursor fixture must use feature_dicts that match either:

(a) the existing six fixture shapes (re-using known compile rules), or
(b) new shapes supported by an **extended** compilation table.

Phase 18F adds **six additional compile-rule entries** — one new feature_dict per family — to support post-cursor learning of *distinct* solvers (e.g. `m → ft` instead of `km → miles`). The extension is strict: each new rule is hardcoded with its own feature_dict + executor artifact. **No generic / arbitrary code generation; no allowlist widening; no new family_kind.** The compilation table remains a static, documented, fail-closed lookup.

## 6. Concurrency model

`ControlPlaneDB._lock` is an in-process threading lock around SQLite operations. SQLite itself is single-writer per connection. Phase 18F's lock strategy:

* **Logical lock** in `schema_meta` (`phase18f.replay_lock.v1`): set/checked at the start of replay; cleared at the end. If present and stale (older than a configurable TTL, default 30 s), reclaimable. Matches the `LOCKED_NOT_RUN` master-prompt option.
* **Physical lock**: SQLite's per-connection serialization implicitly prevents concurrent writes against the same DB file across connections. Combined with the logical lock, two replay attempts cannot double-register.

The proof harness (Stage I) demonstrates by simulating a held lock and verifying the second attempt returns `LOCKED_NOT_RUN`.

## 7. Strict event loading — what already passes

Phase 18E's `normalize_runtime_gap_event` already enforces:

* `schema_version` exact match;
* required-field presence;
* `feature_dict` must be a Mapping (rejects array, string, null);
* `confidence_hint` must be float in [0, 1];
* forbidden key substrings (`token`, `password`, `authorization`, `secret`, `api_key`, `private_key`);
* forbidden value patterns (gho_, github_pat_, x-access-token URL, Bearer, PRIVATE KEY).

Phase 18F additionally enforces in **`load_runtime_gap_events_after_id()`**:

* JSON parse failure on `signal_payload` → counted in `malformed_event_rejection_count`, row skipped, NOT raised.
* Type-confused JSON top-level (e.g. `signal_payload = '"a string"'` or `'[1,2,3]'`) → rejected as malformed.
* Empty `signal_payload` → rejected as malformed.

The strict mode used by Phase 18F's incremental loader **silently skips** malformed rows in production but **counts** them so the proof can prove the rejection happened — Phase 18E's loader by contrast raised on malformed rows. Phase 18F's incremental loader has a `strict_mode` flag; default for the proof is "count + skip" so a single corrupted historical row cannot break replay.

## 8. Six-family post-cursor coverage strategy

Each post-cursor event uses one of six new (family_kind, feature_dict) combinations that the Phase 18F-extended compilation table supports. The extended table adds (alongside the existing 6 entries):

| Family | New feature_dict | Executor artifact |
| --- | --- | --- |
| `scalar_unit_conversion` | `{"input_unit": "m", "output_unit": "ft", "rule": "1 m = 3.28084 ft"}` | factor=3.28084, offset=0.0 |
| `lookup_table` | `{"table_name": "country_codes", "example_key": "fi"}` | small static FI/SE/NO/DK table |
| `threshold_rule` | `{"threshold": 100, "example_value": 150, "rule": "alert_or_quiet"}` | operator=">", labels alert/quiet |
| `interval_bucket_classifier` | `{"buckets": "[0,33),[33,66),[66,100]", "example_value": 50}` | three buckets low/mid/high |
| `linear_arithmetic` | `{"operator": "subtract", "example_inputs": {"a": 20, "b": 5}}` | coefficients [1.0, -1.0] |
| `bounded_interpolation` | `{"endpoints": "(0,0)->(100,1)", "example_x": 50}` | knots (0,0)→(100,1) |

These are six distinct mined specs (different `feature_dict` ⇒ different `candidate_id`) that will register as six new auto-promoted solvers and dispatch via `LowRiskSolverDispatcher.dispatch_by_features` independently of the original six. After Stage E the database holds **12** auto-promoted solvers (6 original + 6 new), one of each per family pair.

## 9. Verdicts (P1 → P2 design inputs)

* **Event table:** REUSE `runtime_gap_signals`.
* **Replay state:** REUSE `schema_meta` with two phase18f-prefixed keys.
* **Cursor:** integer `id`, monotonic, gap-free.
* **Lock:** logical lock in `schema_meta` + SQLite serialized writes.
* **Detector bridge:** small adapter on top of `GapSignal` + `RuntimeGapDetector`; existing detector unchanged.
* **Compile-table:** six new strict per-family rules added; no generic code generation, no allowlist widening, no new family_kind.
* **Strict load:** Phase 18F adds counted-skip behavior for malformed rows and rejects non-object `signal_payload` JSON.
* **No new pip dependency.** Stdlib + WaggleDance only.

Phase 18F may proceed to P2 design.
