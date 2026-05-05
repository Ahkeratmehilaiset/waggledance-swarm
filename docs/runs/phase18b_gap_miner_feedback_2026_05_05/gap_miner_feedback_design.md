# Phase 18B — Gap Miner + Solver Feedback Loop (Design)

**Status:** Authoritative for Phase 18B. Code in `waggledance/core/autonomy_growth/gap_mining.py`, the proof at `tools/run_phase18b_gap_miner_feedback_proof.py`, and tests at `tests/autonomy_growth/test_phase18b_gap_miner_feedback.py` MUST conform to this document.

This document exists per master-prompt rule "Do not code until this exists."

---

## 1. Purpose

Convert observed runtime gap signals into structured, auditable verdicts that either (a) produce a six-family allowlisted low-risk solver spec, or (b) fail closed via explicit rejection / quarantined builder handoff. No automatic high-risk promotion. No live builder execution. No cloud API call.

This closes the loop:

```
runtime gap signal
  → mined gap candidate
  → verdict ∈ {ALLOWLISTED_SOLVER_SPEC, INSUFFICIENT_EVIDENCE,
              OUT_OF_FAMILY_REJECTED, HIGH_RISK_REJECTED,
              BUILDER_HANDOFF_QUARANTINED, DUPLICATE_SUPPRESSED}
  → for ALLOWLISTED_SOLVER_SPEC: deterministic spec consumed by solver bootstrap path
  → capability lookup verifies the spec was registered correctly
```

## 2. Source inventory plan (P2)

* **Primary source**: `origin/phase8.5/curiosity-organ` — read-only.
  * `tools/gap_miner.py` — campaign-artifact curiosity report generator (~700 LOC). **Different shape** from what Phase 18B needs (runtime-signal → solver-spec, not artifact-curiosity report). **Verdict: REIMPLEMENT_SMALLER_MAINLINE.**
  * `tests/test_gap_miner.py` — 42 tests against the campaign-artifact fixture. **PRESERVE_ON_BRANCH_ONLY.**
  * `tests/fixtures/gap_miner_sample/{hot_results.jsonl,query_corpus.json}` — fixtures. **PRESERVE_ON_BRANCH_ONLY.**
  * `docs/architecture/GAP_MINER_VISION.md` — design rationale. **PRESERVE_ON_BRANCH_ONLY.**
* **Reusable design vocabulary**: gap-type taxonomy (`missing_solver`, `improvement_opportunity`, `bridge_composition`, `unit_family_mismatch`, `contradiction_surface`, `low_confidence_routing`, `subdivision_pressure`, `meta_solver_opportunity`); evidence-strength thresholds; deterministic ID derivation (SHA-256 of canonical input).
* **Why not verbatim port**: the Phase 8.5 miner reads `hot_results.jsonl`/`incident_log`/`magma hybrid candidate trace`/`hex subdivision plan`/`composition report`/`cell manifests`. Phase 18B's input is structured runtime-gap signal records (a much smaller, more focused domain). The Phase 8.5 miner's output is a curiosity *report*; Phase 18B's output is a *verdict per signal*. Same name, different contract. Carrying the larger module to main would introduce coupling Phase 18B does not need.

A formal reconciliation matrix lives at `phase85_gap_miner_reconciliation_matrix.md` (P2).

## 3. Mainline architecture

```
waggledance/core/autonomy_growth/
  gap_mining.py             # public API + verdict emission
  gap_candidate.py          # GapCandidate / GapVerdict / GapMiningResult dataclasses
  gap_training_data.py      # SolverSpec construction + training-example bundling
```

Three modules so the public API stays narrow and helpers are independently testable. All under `waggledance.core.autonomy_growth` (existing package).

### 3.1 `gap_candidate.py`

Pure dataclasses. No I/O.

```python
class GapVerdict(StrEnum):
    ALLOWLISTED_SOLVER_SPEC = "ALLOWLISTED_SOLVER_SPEC"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_FAMILY_REJECTED = "OUT_OF_FAMILY_REJECTED"
    HIGH_RISK_REJECTED = "HIGH_RISK_REJECTED"
    BUILDER_HANDOFF_QUARANTINED = "BUILDER_HANDOFF_QUARANTINED"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"

@dataclass(frozen=True)
class GapCandidate:
    candidate_id: str            # SHA-256 of canonical_repr → 16 hex chars
    family_kind: str             # one of six allowlist families OR "unknown" / "high_risk"
    feature_dict: Mapping[str, Any]
    evidence_refs: tuple[str, ...]
    confidence: float            # in [0.0, 1.0]
    risk_label: str              # "low_risk" | "medium_risk" | "high_risk"
    provenance: Mapping[str, Any]  # source, signal_count, timestamps, signal_ids[]
    signal_count: int
    verdict: GapVerdict
    rejection_reason: str | None
    builder_handoff_payload: Mapping[str, Any] | None

@dataclass(frozen=True)
class GapMiningResult:
    candidates: tuple[GapCandidate, ...]
    counters: Mapping[str, int]   # by-verdict counts
    config_snapshot: Mapping[str, Any]
```

### 3.2 `gap_mining.py`

Public API:

```python
ALLOWED_FAMILIES = (
    "scalar_unit_conversion", "lookup_table", "threshold_rule",
    "interval_bucket_classifier", "linear_arithmetic", "bounded_interpolation",
)

@dataclass(frozen=True)
class GapMiningConfig:
    min_signals_for_candidate: int = 2          # below this → INSUFFICIENT_EVIDENCE
    min_confidence: float = 0.55                # below this → INSUFFICIENT_EVIDENCE
    high_risk_block: bool = True                # if True, risk_label="high_risk" → HIGH_RISK_REJECTED
    suppress_duplicates: bool = True
    enable_builder_handoff_quarantine: bool = True

def mine_runtime_gaps(
    signals: Sequence[Mapping[str, Any]],
    *,
    config: GapMiningConfig | None = None,
) -> GapMiningResult: ...

def candidate_to_solver_spec(candidate: GapCandidate) -> Mapping[str, Any] | None: ...

def build_quarantined_builder_handoff(candidate: GapCandidate) -> Mapping[str, Any]: ...
```

### 3.3 Verdict logic (priority order)

For each grouped signal cluster (signals grouped by `family_kind` + canonical-feature-key):

1. If `risk_label == "high_risk"` AND `config.high_risk_block`: `HIGH_RISK_REJECTED`.
2. Else if `family_kind` not in `ALLOWED_FAMILIES` AND `family_kind != "builder_handoff"`: `OUT_OF_FAMILY_REJECTED`.
3. Else if `family_kind == "builder_handoff"` AND `config.enable_builder_handoff_quarantine`: `BUILDER_HANDOFF_QUARANTINED` with non-empty `builder_handoff_payload`, `no_auto_promotion=true`.
4. Else if `signal_count < config.min_signals_for_candidate` OR `confidence < config.min_confidence`: `INSUFFICIENT_EVIDENCE`.
5. Else if a previous candidate with the same `candidate_id` was already emitted: `DUPLICATE_SUPPRESSED`.
6. Else: `ALLOWLISTED_SOLVER_SPEC`.

`candidate_id` derivation: `sha256(family_kind + "|" + canonical_json(feature_dict))[:16]`. Deterministic across runs.

## 4. Input contract — runtime gap signal

```json
{
  "signal_id": "<deterministic id>",
  "family_kind": "scalar_unit_conversion",
  "feature_dict": {"input_unit": "km", "output_unit": "miles", "rule": "1 km = 0.621371 miles"},
  "raw_query": "Convert 10 km to miles",
  "miss_reason": "no_solver_for_unit_pair",
  "confidence_hint": 0.82,
  "risk_label": "low_risk",
  "evidence_ref": "phase17b/track_A/missed_query_42",
  "occurred_at_utc": "2026-05-05T..."
}
```

Source of signals at runtime: Phase 12 `RuntimeGapDetector` rows in `runtime_gap_signals` (or compatible records). For Phase 18B's proof we synthesize 30+ deterministic signals across all 6 allowlist families plus edge cases (insufficient evidence, out-of-family, high-risk, duplicates, builder-handoff). This is **fixture-driven** and does not require a live runtime; the harness records `is_synthetic=true` in its output.

## 5. Output contract — solver spec (when ALLOWLISTED_SOLVER_SPEC)

```json
{
  "spec_id": "<candidate_id>",
  "candidate_id": "<candidate_id>",
  "family_kind": "scalar_unit_conversion",
  "feature_dict": {...},
  "training_examples": [
    {"inputs": {...}, "expected_output": ...},
    ...
  ],
  "evidence_refs": ["phase17b/track_A/missed_query_42", ...],
  "confidence": 0.82,
  "risk_label": "low_risk",
  "promotion_allowed": true,
  "expected_artifact_type": "deterministic_low_risk_solver",
  "provenance": {
    "source": "phase18b_gap_mining",
    "signal_count": 3,
    "signal_ids": ["sig_001", "sig_007", "sig_023"],
    "config_snapshot": {...}
  }
}
```

The spec is a JSON-serializable dict. The proof harness MAY pass it to existing solver-bootstrap paths if compatible; if not, the harness records that downstream registration as `NOT_RUN_API_MISMATCH` honestly rather than fake-passing.

## 6. Output contract — builder handoff (when BUILDER_HANDOFF_QUARANTINED)

```json
{
  "handoff_id": "<candidate_id>",
  "reason": "out_of_six_family_allowlist_but_not_high_risk",
  "quarantined_payload": {
    "raw_signals": [...],
    "candidate_features": {...}
  },
  "no_auto_promotion": true,
  "no_provider_call": true,
  "no_builder_call_in_proof": true,
  "no_cloud_api": true,
  "promotion_allowed": false,
  "next_step_for_operator": "review the payload manually; if a low-risk family port is feasible, author the spec by hand and submit through the existing Phase 9 14-stage promotion ladder"
}
```

Builder handoff is **never** auto-promoted by Phase 18B. The harness records `provider_jobs_delta = builder_jobs_delta = 0`.

## 7. Release thresholds

For `release_gate_pass = true` the proof must record:

* `signals_total >= 30`
* `allowlisted_candidates_total >= 6`
* `solver_specs_total >= 6`
* At least one allowlisted candidate per six-family family OR explicit documented reason if a family cannot be represented
* `insufficient_evidence_total >= 3`
* `out_of_family_rejected_total >= 2`
* `high_risk_rejected_total >= 1`
* `builder_handoff_quarantined_total >= 1`
* `duplicates_suppressed_total >= 1`
* `provider_jobs_delta == 0`
* `builder_jobs_delta == 0`
* `allowlist_unchanged == true`
* `no_stage2_flip == true`
* `no_human_approval == true`

If `capability_lookup_status` returns `NOT_RUN_API_MISMATCH` (because the existing `RuntimeQueryRouter` shape can't accept Phase 18B's spec without changes that exceed scope), the proof must NOT fake the gate. Instead it records:

* `capability_lookup_status = "NOT_RUN_API_MISMATCH"`
* `exact_api_blocker = "..."`
* `release_gate_pass = false`

In that case **Decision B applies** (no tag), per master prompt P9.

## 8. Test plan (P5, ≥17 tests)

`tests/autonomy_growth/test_phase18b_gap_miner_feedback.py`:

1. `test_mines_allowlisted_candidates_from_runtime_signals` — happy path, six-family signals → six allowlisted specs.
2. `test_candidate_ids_are_deterministic` — same input twice → identical IDs.
3. `test_every_candidate_has_provenance` — `provenance` dict on every emitted candidate.
4. `test_rejects_unknown_family` — `family_kind="unknown_thing"` → `OUT_OF_FAMILY_REJECTED`.
5. `test_rejects_high_risk_candidate` — `risk_label="high_risk"` → `HIGH_RISK_REJECTED`.
6. `test_rejects_insufficient_evidence` — single-signal cluster → `INSUFFICIENT_EVIDENCE`.
7. `test_suppresses_duplicates` — two identical clusters → second is `DUPLICATE_SUPPRESSED`.
8. `test_builder_handoff_is_quarantined_and_no_auto_promotion` — `family_kind="builder_handoff"` → quarantined verdict with `no_auto_promotion=true`.
9. `test_candidate_to_solver_spec_requires_allowlist` — non-allowlisted candidate → `candidate_to_solver_spec` returns `None`.
10. `test_candidate_to_solver_spec_shape` — full required key set on emitted spec.
11. `test_proof_json_shape` — proof harness JSON has all required top-level keys.
12. `test_proof_release_gate_passes` — invoking the proof on the synthetic fixture returns `release_gate_pass=true`.
13. `test_provider_builder_delta_zero` — proof JSON records 0/0 deltas.
14. `test_allowlist_unchanged` — `ALLOWED_FAMILIES` tuple is exactly the six canonical families.
15. `test_no_stage2_no_human_approval_flags` — proof JSON records `no_stage2_flip=true`, `no_human_approval=true`.
16. `test_phase18a_bundle_still_validates` — invokes Phase 18A validator on the committed bundle.
17. `test_no_forbidden_docs_claims_in_phase18b_outputs` — scrub the proof JSON+MD + new docs files for the 16-substring forbidden vocabulary list.

## 9. Forbidden vocabulary scrub

Same denylist as Phase 17C/17D/18A:

```
conscious, sentient, aware, alive, agi, revolutionary, magical,
human-like mind, self-aware, explosive intelligence, emergent,
beats all competitors, world's best, world's fastest,
is faster than, is slower than, outperforms, " beats ",
ranks higher, ranked first, best of breed, better than
```

Compound technical terms (`capability-aware`, `context-aware`, `self-model`) and explicit non-claim phrasings stay whitelisted via the validator's strip-pass.

## 10. Docker `--network none` (P7)

`.dockerignore` carve-outs:

```
!tools/run_phase18b_gap_miner_feedback_proof.py
!waggledance/core/autonomy_growth/gap_mining.py
!waggledance/core/autonomy_growth/gap_candidate.py
!waggledance/core/autonomy_growth/gap_training_data.py
!tests/autonomy_growth/test_phase18b_gap_miner_feedback.py
```

Build:

```
docker build -t waggledance:phase18b -f Dockerfile .
```

Run:

```
docker run --rm --network none waggledance:phase18b \
    python tools/run_phase18b_gap_miner_feedback_proof.py \
        --out-dir /tmp/phase18b_gap_miner_feedback

docker run --rm --network none waggledance:phase18b \
    python tools/validate_phase18a_benchmark_bundle.py \
        --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle
```

Both must exit 0. The container has no Ollama, no network, no cloud reachability — the proof is fixture-driven and pure-stdlib.

## 11. Release decision (P9)

* **Decision A — release `v3.10.1-gap-miner-feedback-alpha` PRERELEASE** if and only if all P5/P7/P9 gates green.
* **Decision B — no tag** if any gate fails.

## 12. Sign-off

This design is the canonical contract for Phase 18B. Any deviation in the implementation must be reflected back into this document in the same PR.
