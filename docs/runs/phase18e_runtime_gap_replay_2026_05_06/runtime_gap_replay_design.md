# Phase 18E — Runtime Gap Replay Design (BEFORE-CODE)

**Status:** design — written 2026-05-06 from `runtime_gap_replay_inventory.md`. Code may begin only after this doc is committed.
**Phase tag:** `phase18e`.
**Candidate prerelease tag:** `v3.10.3-runtime-gap-replay-alpha`.

The factual engineering name "runtime gap replay" reflects what this phase actually does: it persists, replays, and dispatches mined solver specs over a real durable gap-event audit trail. No marketing language was used.

## 1. Storage choice — REUSE `runtime_gap_signals`

Per the inventory, `runtime_gap_signals` exists in ControlPlaneDB schema v3 (Phase 12). Three choices were considered:

| Option | Description | Verdict |
| --- | --- | --- |
| **A. Reuse with discriminator** | Insert phase18e events into `runtime_gap_signals` with `kind = 'phase18e.runtime_gap_event.v1'`. Event payload (including deterministic `event_id`, `schema_version`, `provenance_hash`) lives in `signal_payload` JSON. | **CHOSEN** |
| B. Extend with new columns | `ALTER TABLE runtime_gap_signals ADD COLUMN event_id TEXT UNIQUE; ADD COLUMN schema_version TEXT; ADD COLUMN provenance_hash TEXT;` | Rejected. Schema migration sensitivity is high; existing Phase 12 detector inserts wouldn't fill the new columns; the unique index would interact poorly with the autoincrement `id`. Adds risk for no benefit — the JSON payload solves the same problem. |
| C. New table `runtime_gap_replay_events` | A separate table for replay events. | Rejected explicitly per operator's P1 guidance: "Do not silently create a parallel table." A separate table would orphan the existing detector lineage and split the audit trail across two stores for no architectural reason. |

**Justification for Option A.** The existing `runtime_gap_signals` table is structurally adequate. `signal_payload TEXT` is a JSON blob — exactly the right home for the canonical Phase 18B-shaped event record. `kind` becomes the schema-version discriminator (`phase18e.runtime_gap_event.v1`). `family_kind`, `weight` (= `confidence_hint`), `observed_at`, and `created_at` populate naturally. The Phase 12 detector's existing write path is untouched; it continues to write its own kind values into the same table. Reads are kind-discriminated, so no Phase 18E query ever sees a Phase 12 detector signal and vice versa.

**Backward compatibility.** Existing `record_runtime_gap_signal()`, `count_runtime_gap_signals()`, the Phase 12 `RuntimeGapDetector`, and the UI hologram aggregator continue to work unchanged. Phase 18E is additive.

## 2. Persisted gap event schema (`phase18e.runtime_gap_event.v1`)

Stored per-row in `signal_payload` as canonical JSON (sorted keys, `(",", ":")` separators, `ensure_ascii=True`).

### Required fields

| Field | Type | Semantics |
| --- | --- | --- |
| `event_id` | str (16-hex) | Deterministic SHA-256 prefix of `family_kind\|canonical(feature_dict)\|cluster_window\|evidence_ref`. Provides cross-run idempotency. |
| `schema_version` | str | Always `"phase18e.runtime_gap_event.v1"` for this release. |
| `occurred_at_utc` | str (ISO-8601 Z) | When the gap was observed. |
| `source` | str | Free-form provenance label, e.g. `"phase18e_proof_fixture"` or a real upstream module name. |
| `family_kind` | str | One of the six allowlisted families, or `"builder_handoff"`, or any other string (which becomes `OUT_OF_FAMILY_REJECTED` after replay). |
| `feature_dict` | object | Same shape Phase 18B `mine_runtime_gaps` consumes. |
| `raw_query` | str | The natural-language query that missed (no PII). |
| `miss_reason` | str | E.g. `"capability_lookup_miss"`, `"out_of_family"`, `"high_risk_blocked"`. |
| `confidence_hint` | float | 0.0–1.0. |
| `risk_label` | str | `"low_risk"`, `"medium_risk"`, or `"high_risk"`. |
| `evidence_ref` | str | Audit pointer (e.g. `"audit:phase18e:fixture:0001"`). |
| `cluster_window` | str | Used by Phase 18B clustering (lets two waves of the same gap form duplicates). Empty string for un-windowed events. |
| `provenance_hash` | str (full SHA-256 hex) | Stable hash of the canonical event JSON minus `provenance_hash` itself; used as a tamper-evidence anchor in the proof JSON. |

### Optional fields

* `signal_id` — passes through into Phase 18B provenance (`signal_ids` list). Default: derived from `event_id`.
* `notes` — free-form audit notes.

### Forbidden fields (fail-closed if present)

* `password`, `token`, `Authorization`, `secret`, `api_key`, `private_key` (key name match, case-insensitive). Triggers a `GapEventSchemaError` and the event is not persisted.
* Anything that pattern-matches `gho_[A-Za-z0-9_]{20,}`, `github_pat_[A-Za-z0-9_]{20,}`, or `BEGIN (RSA )?PRIVATE KEY` inside the JSON payload. Same fail-closed treatment.

### Determinism

* `event_id`: `SHA256(canonical_dict_with_only(family_kind, feature_dict, cluster_window, evidence_ref))[:16]`.
* `provenance_hash`: `SHA256(canonical_dict_excluding(provenance_hash))` (full hex).

Two events with the same `(family_kind, feature_dict, cluster_window, evidence_ref)` collapse to the same `event_id`; persisting the second is an idempotent no-op.

## 3. Public API (new module `runtime_gap_replay.py`)

```python
PHASE18E_RUNTIME_GAP_EVENT_KIND = "phase18e.runtime_gap_event.v1"

class GapEventSchemaError(ValueError): ...

@dataclass(frozen=True)
class PersistedGapEvent:
    event_id: str
    schema_version: str
    occurred_at_utc: str
    source: str
    family_kind: str
    feature_dict: Mapping[str, Any]
    raw_query: str
    miss_reason: str
    confidence_hint: float
    risk_label: str
    evidence_ref: str
    cluster_window: str
    provenance_hash: str
    signal_id: str
    notes: Optional[str]

@dataclass(frozen=True)
class GapPersistResult:
    inserted_event_ids: tuple[str, ...]
    skipped_existing_event_ids: tuple[str, ...]
    rejected_event_count: int
    malformed_event_rejection_count: int
    rejected_reasons: Mapping[str, int]

@dataclass(frozen=True)
class GapReplayResult:
    loaded_event_count: int
    mining_result: GapMiningResult
    registration_summary: RegistrationSummary
    rejected_at_normalize: int
    forbidden_field_rejections: int
    counters: Mapping[str, int]

def normalize_runtime_gap_event(raw: Mapping[str, Any]) -> PersistedGapEvent: ...
def persist_runtime_gap_events(control_plane, events: Sequence[Mapping[str, Any]]) -> GapPersistResult: ...
def load_runtime_gap_events(control_plane, *, source: Optional[str] = None) -> list[PersistedGapEvent]: ...
def replay_persisted_gap_events(control_plane, *, config: Optional[GapMiningConfig] = None) -> GapReplayResult: ...
```

`replay_persisted_gap_events` does:

1. `events = load_runtime_gap_events(control_plane)` — read everything with `kind = phase18e.runtime_gap_event.v1`.
2. Convert each `PersistedGapEvent` to a Phase 18B-shaped signal mapping.
3. `mining_result = mine_runtime_gaps(signals, config=config)` — Phase 18B verbatim.
4. `registration_summary = register_mined_solver_specs(candidates=mining_result.candidates, control_plane=control_plane)` — Phase 18C verbatim.
5. Bundle counters and return.

## 4. Persistence flow (`persist_runtime_gap_events`)

1. For each raw event mapping:
   a. Try `normalize_runtime_gap_event(raw)`. On failure (forbidden field, missing field, unsupported schema_version), increment `malformed_event_rejection_count` and skip.
   b. Compute deterministic `event_id` (if not already present) and `provenance_hash`.
2. Load the existing set of `event_id`s currently stored under `kind = phase18e.runtime_gap_event.v1`.
3. For each normalized event:
   * If `event_id` already in the loaded set → record in `skipped_existing_event_ids`. No INSERT.
   * Else → `record_runtime_gap_signal(kind=PHASE18E_RUNTIME_GAP_EVENT_KIND, family_kind=event.family_kind, signal_payload=canonical_json(event), weight=event.confidence_hint, observed_at=event.occurred_at_utc)`. Append to `inserted_event_ids`.

Result is a `GapPersistResult` with truthful counters.

## 5. Read flow (`load_runtime_gap_events`)

* Adds a minimal `list_runtime_gap_signals(*, kind: Optional[str] = None, family_kind: Optional[str] = None, limit: Optional[int] = None) -> list[RuntimeGapSignalRecord]` helper to `ControlPlaneDB`.
* Phase 18E loader passes `kind=PHASE18E_RUNTIME_GAP_EVENT_KIND`, deserializes each `signal_payload` into a `PersistedGapEvent`, and returns the list. Malformed payloads (e.g. non-JSON in the row) raise `GapEventSchemaError` and are counted, not silently dropped.

## 6. Idempotency proof strategy

Per inventory §8, idempotency lives in two layers and the proof harness exercises both. Specifically the harness:

1. Persists the deterministic 30+ event fixture (replay 1).
2. Loads them; replays into mine → register → dispatch.
3. Records baseline counts: `solvers`, `solver_capability_features`, `solver_artifacts`, `runtime_gap_signals` rows.
4. Persists the **same fixture** a second time. Asserts `inserted_event_ids` is empty and `skipped_existing_event_ids` size equals the original event count.
5. Replays a second time. Asserts:
   * registered_count is unchanged or 0 net (depending on whether the second replay invokes `register_mined_solver_specs` again — it does, but `upsert_*` is idempotent so post-replay row counts are unchanged).
   * dispatch hits still 18/18.
   * `solver_capability_features` row count unchanged.
   * `solver_artifacts` row count unchanged.
6. Reports `replay_idempotency_pass = true` only if all the above hold byte-exactly.

## 7. Phase 18E proof fixture coverage

The proof harness `tools/run_phase18e_runtime_gap_replay_proof.py` ships a deterministic ≥30-event fixture covering:

| Category | Events | Purpose |
| --- | --- | --- |
| 6 × ALLOWLISTED happy path (one per family) × ≥3 signals | ≥18 | drive 6/6 family registrations, hit `min_signals_for_candidate=2` and `min_confidence=0.55` |
| INSUFFICIENT_EVIDENCE (low confidence, single signal) | ≥2 | exercise threshold rejection |
| OUT_OF_FAMILY_REJECTED (family not in allowlist) | ≥1 | exercise allowlist gate |
| HIGH_RISK_REJECTED (risk_label="high_risk") | ≥1 | exercise risk gate |
| BUILDER_HANDOFF_QUARANTINED (`family_kind="builder_handoff"`) | ≥1 | exercise quarantine path |
| DUPLICATE_SUPPRESSED (same family+features, different cluster_window) | ≥1 | exercise duplicate gate |
| Malformed events (corrupt JSON, missing required field, unsupported schema_version) | ≥3 | exercise normalize-time rejection |
| Forbidden-field event (contains `"token": "gho_..."` shape) | ≥1 | exercise secret-hygiene rejection |

The fixture is constructed deterministically (no `random`, no `uuid`, no time-of-day inputs), so the same fixture produces the same `event_id`s and `provenance_hash`es across runs.

## 8. Release-gate criteria (per master-prompt P4 + operator note 2)

Per operator note 2, the master prompt's `exactly 6 registered allowlisted` is relaxed to `>= 6 AND families_covered == 6`. The release gate passes only if:

| Gate | Threshold |
| --- | --- |
| `persisted_event_count` | ≥ 30 |
| `loaded_event_count` | ≥ 30 |
| `allowlisted_candidate_count` | ≥ 6 |
| `registered_solver_count` | ≥ 6 |
| `families_covered` | == 6 |
| `dispatch_case_count` | ≥ 18 |
| `dispatch_success_count` | == `dispatch_case_count` |
| `dispatch_failure_count` | == 0 |
| `replay_idempotency_pass` | true |
| `non_allowlisted_rejected_count` | ≥ 5 (3 INSUFFICIENT + 1 OUT_OF_FAMILY + 1 HIGH_RISK + 1 BUILDER_HANDOFF + 1 DUPLICATE) |
| `malformed_event_rejection_count` | ≥ 1 (typically 3 — corrupt + missing + bad schema) |
| `forbidden_field_rejections` | ≥ 1 |
| `provider_jobs_delta` | == 0 |
| `builder_jobs_delta` | == 0 |
| `allowlist_unchanged` | true |
| `forbidden_claims_absent` | true |
| `no_model_pull_or_download` | true |
| `no_cloud_api_calls` | true |
| `no_live_builder_execution` | true |
| `no_stage2_flip` | true |
| `no_human_approval` | true |
| `no_high_risk_autonomy` | true |
| `db_path_is_temp` | true |
| `db_committed` | false |

## 9. Tests strategy

`tests/autonomy_growth/test_phase18e_runtime_gap_replay.py` covers ≥30 cases at minimum:

* Normalization happy path (one per family).
* Missing required field rejection.
* Unsupported schema_version rejection.
* Corrupt JSON rejection (raises `GapEventSchemaError`).
* Forbidden-field rejection (`token`, `password`, etc.).
* Deterministic `event_id` stable across runs.
* Deterministic `provenance_hash` stable.
* Persist happy path inserts and returns the expected `inserted_event_ids`.
* Persist with the same event twice is idempotent (row count unchanged on second persist).
* Load returns the expected canonical shape.
* Load distinguishes `phase18e.runtime_gap_event.v1` from other `kind` values.
* Replay calls Phase 18B `mine_runtime_gaps` (not a fork).
* Replay calls Phase 18C `register_mined_solver_specs` (not a fork).
* 6/6 allowlisted families produce candidates and register.
* Out-of-family event does not register.
* High-risk event does not register.
* Builder-handoff event does not register and remains quarantined.
* Duplicate event collapses to a single registration.
* Real `LowRiskSolverDispatcher.dispatch_by_features` returns `reason="hit_by_features"` for each registered solver.
* 18/18 dispatch cases pass deterministically.
* Idempotent re-replay: row counts in `solvers`, `solver_capability_features`, `solver_artifacts` unchanged.
* `provider_jobs_delta == 0`, `builder_jobs_delta == 0`.
* Phase 18A bundle validator still passes (carry-forward).
* Phase 18B proof harness can still be imported and runs (smoke).
* Phase 18C proof harness can still be imported and runs (smoke).
* Forbidden vocabulary scrub on proof JSON.
* Forbidden vocabulary scrub on proof Markdown.
* Proof JSON schema valid.
* `release_gate_pass = true`.
* No DB / SQLite / WAL / SHM file under repo path after harness exit.
* No GitHub token / cloud key / private-key marker in any committed file.

Plus the targeted carry-forward suite: `tests/autonomy_growth/test_phase18c_*`, `tests/autonomy_growth/test_phase18b_*`, `tests/benchmarks/test_phase18a_*`, `tests/phase10/`, `tests/storage/`, `tests/ui_hologram/`, `tests/autonomy/test_solver_router.py`.

## 10. Docker `--network none` strategy

Build `waggledance:phase18e` from existing `Dockerfile`. `.dockerignore` adds a single carve-out for `tools/run_phase18e_runtime_gap_replay_proof.py`. Inside the image, run four `--network none` invocations:

1. `tools/run_phase18e_runtime_gap_replay_proof.py --out-dir /tmp/phase18e_docker`
2. `tools/run_phase18c_mined_solver_runtime_dispatch_proof.py --out-dir /tmp/phase18e_docker_phase18c`
3. `tools/run_phase18b_gap_miner_feedback_proof.py --out-dir /tmp/phase18e_docker_phase18b`
4. `tools/validate_phase18a_benchmark_bundle.py --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle`

All four must exit 0.

## 11. Secret-hygiene strategy

Identical to Phase 18D. The new module includes a forbidden-field scan that fails closed before any persistence, so an event whose payload contains a token-shaped string is rejected at `normalize_runtime_gap_event` rather than committed. The proof harness emits a forbidden-vocabulary scrub on its JSON and Markdown output.

The local Git config was cleaned at P0 (9 stale token-bearing branch upstreams rewritten to `origin`). All push operations during P10 use plain `git push -u origin <branch>` with the `gh` keyring credential helper.

## 12. Rollback strategy

* If host carry-forward fails or the Phase 18E proof itself fails, hold the branch locally and switch to Decision B; do not push or PR.
* If CI fails on the open PR, do not merge; push fixes (still no tag) or close.
* If the post-merge tag-time reproduction fails (extremely unlikely for an offline-deterministic proof), do not tag; instead open a follow-up issue and a revert PR. Do not move v3.8.0 / v3.10.2.
* The schema additions are exactly zero, so a rollback is "revert the PR" with no DB migration to undo.

## 13. Allowed / forbidden claims

**Allowed claims** (each backed by an artifact in this session folder):

* Phase 18E proves persisted runtime gap events can be replayed deterministically through the existing Phase 18B miner and Phase 18C runtime registration / dispatch path.
* All 8 prior tag SHAs unchanged. v3.8.0 remains GitHub Latest. v3.10.2-mined-solver-dispatch-alpha remains the previous prerelease.
* Six-family allowlist unchanged. No high-risk autonomy added.
* Builder handoff remains quarantined.
* No model pull/download. No cloud API. No live builder execution. No Stage-2 flip. No HUMAN_APPROVAL.
* No new pip dependency. No DB / SQLite / WAL / SHM committed.
* Proof DB is temp; ControlPlaneDB schema unchanged.

**Forbidden claims** (must NOT appear anywhere):

* Cross-vendor ranking.
* Raw-intelligence superiority.
* "Beats all competitors" / "world fastest" / "world best".
* Consciousness, sentience, awareness, AGI.
* Production-certified factory deployment.
* Allowlist expansion.

## 14. Stop / abort triggers

Phase 18E switches to Decision B and stops fail-closed if any of the master-prompt's documented STOP triggers fires. The relevant ones for this design are reproduced verbatim:

* token/secret printed/committed/embedded;
* any prior tag moves;
* v3.8.0 not Latest;
* persisted-replay path cannot be implemented safely;
* proof falls back to in-memory-only;
* idempotency fails;
* non-allowlisted event registers;
* corrupt event not rejected;
* Phase 18A / 18B / 18C carry-forward fails;
* Docker `--network none` fails;
* CI fails;
* `provider_jobs_delta != 0` or `builder_jobs_delta != 0`;
* allowlist changes;
* DB / SQLite / WAL / SHM file would be committed;
* new pip dependency would be required;
* live builder, cloud, model pull, Stage-2, HUMAN_APPROVAL would be required.

If stopped, no tag is created; `release_decision.md` records Decision B with exact blockers and exact next commands.
