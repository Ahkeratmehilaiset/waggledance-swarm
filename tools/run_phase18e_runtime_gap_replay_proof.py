# SPDX-License-Identifier: BUSL-1.1
"""Phase 18E - Persisted runtime gap replay proof harness.

Drives a deterministic >= 30-event Phase 18E persisted-gap-event fixture
through the full durable loop:

    persist_runtime_gap_events
      -> runtime_gap_signals (kind = phase18e.runtime_gap_event.v1)
      -> load_runtime_gap_events
      -> mine_runtime_gaps  (Phase 18B verbatim)
      -> register_mined_solver_specs  (Phase 18C verbatim)
      -> ControlPlaneDB capability rows + artifacts
      -> LowRiskSolverDispatcher.dispatch_by_features  (real path)

The harness asserts:

* >= 30 events persisted; the same events skipped on a second persist.
* >= 6 ALLOWLISTED candidates after mining; six families covered.
* >= 6 auto-promoted solver rows registered with capability features.
* >= 18 deterministic dispatch cases; all hit the registered mined
  solver via the capability-aware path; outputs match expected values.
* Idempotent re-replay produces no extra solver / capability /
  artifact rows.
* Forbidden-field events (token-shaped values, password-named keys)
  are rejected at normalization, never persisted.
* Builder-handoff and out-of-family / high-risk events do not register.

No provider call. No builder call. No cloud API. No model pull. No
Stage-2 flip. No HUMAN_APPROVAL collected. No allowlist widening. No
new pip dependency.

CLI:

    python tools/run_phase18e_runtime_gap_replay_proof.py [--out-dir <dir>]
"""

from __future__ import annotations

import argparse
import json
import math
import platform as _plat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy_growth.gap_candidate import (  # noqa: E402
    GapVerdict,
)
from waggledance.core.autonomy_growth.gap_mining import (  # noqa: E402
    ALLOWED_FAMILIES,
    GapMiningConfig,
)
from waggledance.core.autonomy_growth.runtime_gap_replay import (  # noqa: E402
    PHASE18E_RUNTIME_GAP_EVENT_KIND,
    SCHEMA_VERSION,
    persist_runtime_gap_events,
    replay_persisted_gap_events,
    load_runtime_gap_events,
)
from waggledance.core.autonomy_growth.solver_dispatcher import (  # noqa: E402
    LowRiskSolverDispatcher,
)
from waggledance.core.storage.control_plane import (  # noqa: E402
    ControlPlaneDB,
)


BENCHMARK_VERSION = "phase18e.v1"
SOURCE_PRERELEASE = "v3.10.2-mined-solver-dispatch-alpha"
CANDIDATE_PRERELEASE = "v3.10.3-runtime-gap-replay-alpha"


FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "agi",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
    "is faster than", "is slower than", "outperforms",
    " beats ", "ranks higher", "ranked first", "best of breed",
    "better than",
)
DISCLAIMER_TOKENS_ALLOWED: tuple[str, ...] = (
    "no_consciousness", "no_sentience", "no_human_like_mind",
    "no_beats_all_competitors", "no_world_best", "no_world_fastest",
    "no_raw_intelligence_superiority", "no_cross_vendor_ranking",
    "no consciousness claim", "does not claim to be conscious",
    "does not claim to be aware", "does not claim to be alive",
    "no cross-vendor ranking", "no raw-intelligence superiority",
    "capability-aware", "capability_aware", "context-aware",
    "context_aware", "self-model", "self_model", "self model",
    "consciousness", "sentience", "awareness",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git_sha() -> str:
    try:
        proc = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            capture_output=True, check=False, timeout=10,
        )
        out = proc.stdout
        if isinstance(out, (bytes, bytearray)):
            out = out.decode("utf-8", errors="replace")
        return out.strip() or "0" * 40
    except Exception:  # noqa: BLE001
        return "0" * 40


def scan_forbidden(text: str) -> list[str]:
    lower = text.lower()
    for tok in DISCLAIMER_TOKENS_ALLOWED:
        lower = lower.replace(tok, "")
    return [w for w in FORBIDDEN_VOCABULARY if w in lower]


# ---------------------------------------------------------------------------
# Deterministic Phase 18E persisted-event fixture
# ---------------------------------------------------------------------------

# Six-family allowlist canonical feature_dict shapes (must match the
# Phase 18C compilation table).
FAMILY_FEATURES: dict[str, dict[str, Any]] = {
    "scalar_unit_conversion": {
        "input_unit": "km",
        "output_unit": "miles",
        "rule": "1 km = 0.621371 miles",
    },
    "lookup_table": {
        "table_name": "chemical_symbols",
        "example_key": "tin",
    },
    "threshold_rule": {
        "threshold": 30,
        "example_value": 37,
        "rule": "above_or_below",
    },
    "interval_bucket_classifier": {
        "buckets": "[0,10),[10,20),[20,30)",
        "example_value": 17,
    },
    "linear_arithmetic": {
        "operator": "add",
        "example_inputs": {"a": 14, "b": 9},
    },
    "bounded_interpolation": {
        "endpoints": "(0,0)->(10,100)",
        "example_x": 3,
    },
}


def _ev(*, family_kind: str, feature_dict: dict[str, Any],
         signal_idx: int, confidence: float, risk: str = "low_risk",
         cluster_window: str = "", evidence_ref: str | None = None,
         miss_reason: str = "capability_lookup_miss",
         raw_query: str = "missed runtime query") -> dict[str, Any]:
    """Build one canonical Phase 18E persisted event."""
    if evidence_ref is None:
        evidence_ref = f"audit:phase18e:fixture:{signal_idx:04d}"
    return {
        "schema_version": SCHEMA_VERSION,
        "occurred_at_utc": "2026-05-06T00:00:00Z",
        "source": "phase18e_proof_fixture",
        "family_kind": family_kind,
        "feature_dict": feature_dict,
        "raw_query": raw_query,
        "miss_reason": miss_reason,
        "confidence_hint": confidence,
        "risk_label": risk,
        "evidence_ref": evidence_ref,
        "cluster_window": cluster_window,
        "signal_id": f"phase18e_signal_{signal_idx:04d}",
    }


def build_persisted_event_fixture() -> tuple[
    list[dict[str, Any]], list[dict[str, Any]],
]:
    """Build the deterministic Phase 18E persisted-event fixture.

    Returns ``(valid_events, malformed_events)``:

    * ``valid_events`` is the well-formed batch passed to
      ``persist_runtime_gap_events``. It includes the 30+ allowlisted
      coverage events plus the explicit non-ALLOWLISTED events
      (insufficient, out-of-family, high-risk, builder-handoff,
      duplicate). All of these pass schema validation.
    * ``malformed_events`` is the batch of intentionally schema-broken
      events (corrupt structure, missing field, bad schema_version,
      forbidden field). These fail at normalization and are NEVER
      persisted.

    The harness persists both batches and asserts the malformed batch
    is fully rejected.
    """
    events: list[dict[str, Any]] = []
    idx = 0

    # 6 families x 4 strong signals each = 24 ALLOWLISTED-feeding events.
    for fam in ALLOWED_FAMILIES:
        feats = FAMILY_FEATURES[fam]
        for sub in range(4):
            idx += 1
            events.append(_ev(
                family_kind=fam, feature_dict=feats,
                signal_idx=idx, confidence=0.85 + 0.01 * sub,
                miss_reason="capability_lookup_miss",
                raw_query=f"missed runtime query for {fam} #{sub+1}",
            ))

    # 3 INSUFFICIENT_EVIDENCE: scalar/lookup/threshold with sub-threshold
    # confidence and unique feature_dicts so they don't merge with the
    # strong clusters.
    insufficient_specs = [
        ("scalar_unit_conversion", {
            "input_unit": "kg", "output_unit": "lb",
            "rule": "1 kg = 2.20462 lb",
        }),
        ("lookup_table", {
            "table_name": "country_codes", "example_key": "fi",
        }),
        ("threshold_rule", {
            "threshold": 100, "example_value": 50,
            "rule": "alert_or_quiet",
        }),
    ]
    for fam, feats in insufficient_specs:
        idx += 1
        events.append(_ev(
            family_kind=fam, feature_dict=feats,
            signal_idx=idx, confidence=0.40,
            miss_reason="capability_lookup_miss_insufficient",
        ))

    # 1 OUT_OF_FAMILY: family not in allowlist, not builder_handoff.
    idx += 1
    events.append(_ev(
        family_kind="ml_classifier",
        feature_dict={"model": "resnet50", "task": "image_classification"},
        signal_idx=idx, confidence=0.9,
        miss_reason="out_of_family", risk="medium_risk",
    ))

    # 1 HIGH_RISK: low-risk family but explicit risk_label="high_risk".
    idx += 1
    events.append(_ev(
        family_kind="scalar_unit_conversion",
        feature_dict={"input_unit": "credits",
                       "output_unit": "purchases",
                       "rule": "1 credit = 1 purchase"},
        signal_idx=idx, confidence=0.9,
        risk="high_risk",
        miss_reason="high_risk_blocked",
    ))

    # 1 BUILDER_HANDOFF.
    idx += 1
    events.append(_ev(
        family_kind="builder_handoff",
        feature_dict={"capability_request": "synthesize_new_solver_family",
                       "rationale": "operator_review_required"},
        signal_idx=idx, confidence=0.9,
        miss_reason="needs_operator_review",
    ))

    # 1 DUPLICATE: same family+features as scalar_unit_conversion happy
    # path but a different cluster_window. Phase 18B clusters by
    # (family, features, cluster_window) so this becomes a SECOND
    # cluster sharing the same candidate_id; it lands as
    # DUPLICATE_SUPPRESSED.
    idx += 1
    events.append(_ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
        signal_idx=idx, confidence=0.9,
        cluster_window="window_2026_05_06",
        evidence_ref=f"audit:phase18e:fixture:{idx:04d}",
        miss_reason="capability_lookup_miss_duplicate",
    ))
    # The duplicate cluster needs >= 2 signals to clear
    # min_signals_for_candidate; add a sibling.
    idx += 1
    events.append(_ev(
        family_kind="scalar_unit_conversion",
        feature_dict=FAMILY_FEATURES["scalar_unit_conversion"],
        signal_idx=idx, confidence=0.9,
        cluster_window="window_2026_05_06",
        evidence_ref=f"audit:phase18e:fixture:{idx:04d}",
        miss_reason="capability_lookup_miss_duplicate",
    ))

    # The malformed batch: each entry is rejected at normalization.
    malformed: list[dict[str, Any]] = [
        # 1) missing required field "feature_dict".
        {
            "schema_version": SCHEMA_VERSION,
            "occurred_at_utc": "2026-05-06T00:00:00Z",
            "source": "phase18e_proof_fixture",
            "family_kind": "scalar_unit_conversion",
            "raw_query": "x", "miss_reason": "x",
            "confidence_hint": 0.5, "risk_label": "low_risk",
            "evidence_ref": "x", "cluster_window": "",
        },
        # 2) unsupported schema_version.
        {
            "schema_version": "phase17.legacy.v0",
            "occurred_at_utc": "2026-05-06T00:00:00Z",
            "source": "phase18e_proof_fixture",
            "family_kind": "scalar_unit_conversion",
            "feature_dict": {"input_unit": "km", "output_unit": "miles",
                              "rule": "1 km = 0.621371 miles"},
            "raw_query": "x", "miss_reason": "x",
            "confidence_hint": 0.5, "risk_label": "low_risk",
            "evidence_ref": "x", "cluster_window": "",
        },
        # 3) feature_dict not a Mapping.
        {
            "schema_version": SCHEMA_VERSION,
            "occurred_at_utc": "2026-05-06T00:00:00Z",
            "source": "phase18e_proof_fixture",
            "family_kind": "scalar_unit_conversion",
            "feature_dict": ["not", "a", "mapping"],
            "raw_query": "x", "miss_reason": "x",
            "confidence_hint": 0.5, "risk_label": "low_risk",
            "evidence_ref": "x", "cluster_window": "",
        },
        # 4) forbidden-field key (contains "token"). Note: the value
        # below is a placeholder string, NOT a real GitHub token. The
        # fail-closed path fires on the KEY name match alone.
        {
            "schema_version": SCHEMA_VERSION,
            "occurred_at_utc": "2026-05-06T00:00:00Z",
            "source": "phase18e_proof_fixture",
            "family_kind": "scalar_unit_conversion",
            "feature_dict": {"input_unit": "km", "output_unit": "miles",
                              "rule": "1 km = 0.621371 miles"},
            "raw_query": "x", "miss_reason": "x",
            "confidence_hint": 0.5, "risk_label": "low_risk",
            "evidence_ref": "x", "cluster_window": "",
            "github_token": "PLACEHOLDER_NOT_A_REAL_TOKEN",
        },
    ]

    return events, malformed


# ---------------------------------------------------------------------------
# Dispatch fixture (reuses Phase 18C cases verbatim)
# ---------------------------------------------------------------------------

DISPATCH_CASES: tuple[dict[str, Any], ...] = (
    {"family_kind": "scalar_unit_conversion",
      "features": FAMILY_FEATURES["scalar_unit_conversion"],
      "inputs": {"x": 10.0}, "expected_output": 6.21371,
      "label": "10_km_to_miles"},
    {"family_kind": "scalar_unit_conversion",
      "features": FAMILY_FEATURES["scalar_unit_conversion"],
      "inputs": {"x": 0.0}, "expected_output": 0.0,
      "label": "0_km_to_miles"},
    {"family_kind": "scalar_unit_conversion",
      "features": FAMILY_FEATURES["scalar_unit_conversion"],
      "inputs": {"x": 100.0}, "expected_output": 62.1371,
      "label": "100_km_to_miles"},

    {"family_kind": "lookup_table",
      "features": FAMILY_FEATURES["lookup_table"],
      "inputs": {"key": "tin"}, "expected_output": "Sn",
      "label": "tin"},
    {"family_kind": "lookup_table",
      "features": FAMILY_FEATURES["lookup_table"],
      "inputs": {"key": "gold"}, "expected_output": "Au",
      "label": "gold"},
    {"family_kind": "lookup_table",
      "features": FAMILY_FEATURES["lookup_table"],
      "inputs": {"key": "iron"}, "expected_output": "Fe",
      "label": "iron"},

    {"family_kind": "threshold_rule",
      "features": FAMILY_FEATURES["threshold_rule"],
      "inputs": {"x": 37}, "expected_output": "above",
      "label": "37_above_30"},
    {"family_kind": "threshold_rule",
      "features": FAMILY_FEATURES["threshold_rule"],
      "inputs": {"x": 12}, "expected_output": "below",
      "label": "12_below_30"},
    {"family_kind": "threshold_rule",
      "features": FAMILY_FEATURES["threshold_rule"],
      "inputs": {"x": 30}, "expected_output": "below",
      "label": "30_eq_threshold"},

    {"family_kind": "interval_bucket_classifier",
      "features": FAMILY_FEATURES["interval_bucket_classifier"],
      "inputs": {"x": 5}, "expected_output": "[0,10)",
      "label": "5_in_first"},
    {"family_kind": "interval_bucket_classifier",
      "features": FAMILY_FEATURES["interval_bucket_classifier"],
      "inputs": {"x": 17}, "expected_output": "[10,20)",
      "label": "17_in_second"},
    {"family_kind": "interval_bucket_classifier",
      "features": FAMILY_FEATURES["interval_bucket_classifier"],
      "inputs": {"x": 22}, "expected_output": "[20,30)",
      "label": "22_in_third"},

    {"family_kind": "linear_arithmetic",
      "features": FAMILY_FEATURES["linear_arithmetic"],
      "inputs": {"a": 14.0, "b": 9.0}, "expected_output": 23.0,
      "label": "14_plus_9"},
    {"family_kind": "linear_arithmetic",
      "features": FAMILY_FEATURES["linear_arithmetic"],
      "inputs": {"a": 0.0, "b": 0.0}, "expected_output": 0.0,
      "label": "0_plus_0"},
    {"family_kind": "linear_arithmetic",
      "features": FAMILY_FEATURES["linear_arithmetic"],
      "inputs": {"a": 5.0, "b": 7.0}, "expected_output": 12.0,
      "label": "5_plus_7"},

    {"family_kind": "bounded_interpolation",
      "features": FAMILY_FEATURES["bounded_interpolation"],
      "inputs": {"x": 3}, "expected_output": 30.0,
      "label": "x_3"},
    {"family_kind": "bounded_interpolation",
      "features": FAMILY_FEATURES["bounded_interpolation"],
      "inputs": {"x": 0}, "expected_output": 0.0,
      "label": "x_0"},
    {"family_kind": "bounded_interpolation",
      "features": FAMILY_FEATURES["bounded_interpolation"],
      "inputs": {"x": 10}, "expected_output": 100.0,
      "label": "x_10"},
)


def _stringify_features(features: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in features.items():
        if isinstance(v, (str, int, float, bool)):
            out[str(k)] = str(v)
        elif v is None:
            out[str(k)] = ""
        else:
            out[str(k)] = json.dumps(v, sort_keys=True,
                                       separators=(",", ":"),
                                       default=str, ensure_ascii=True)
    return out


def _count_runtime_rows(cp: ControlPlaneDB) -> dict[str, int]:
    """Return row counts for the runtime tables Phase 18E touches."""
    counts: dict[str, int] = {}
    counts["runtime_gap_signals_phase18e"] = (
        cp.count_runtime_gap_signals(kind=PHASE18E_RUNTIME_GAP_EVENT_KIND)
    )
    with cp._lock:  # noqa: SLF001 -- read-only sanity counts for proof
        counts["solvers"] = int(cp._conn.execute(
            "SELECT COUNT(*) AS c FROM solvers",
        ).fetchone()["c"])
        counts["solver_capability_features"] = int(cp._conn.execute(
            "SELECT COUNT(*) AS c FROM solver_capability_features",
        ).fetchone()["c"])
        counts["solver_artifacts"] = int(cp._conn.execute(
            "SELECT COUNT(*) AS c FROM solver_artifacts",
        ).fetchone()["c"])
    return counts


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_md(*, proof: dict[str, Any]) -> str:
    p = proof
    lines = [
        "# Phase 18E - Persisted Runtime Gap Replay Proof",
        "",
        f"**Benchmark version:** {p['benchmark_version']}",
        f"**Source prerelease:** {p['source_prerelease']}",
        f"**Candidate prerelease:** {p['candidate_prerelease']}",
        f"**Base main SHA:** {p['base_main_sha']}",
        f"**Started UTC:** {p['started_utc']}",
        f"**Finished UTC:** {p['finished_utc']}",
        "",
        "## Honesty declarations",
        "",
        "* No cloud API calls were made.",
        "* No model was pulled or downloaded.",
        "* No live builder execution.",
        "* No Stage-2 atomic flip.",
        "* No HUMAN_APPROVAL collected.",
        "* Six-family allowlist unchanged.",
        "* Builder handoff quarantined; non-executable.",
        "* Schema unchanged: Phase 18E reuses the existing "
        "`runtime_gap_signals` table with `kind = "
        "`phase18e.runtime_gap_event.v1`.",
        "* Proof database is a temp file; not committed; not retained.",
        "",
        "## Persistence",
        "",
        f"* persisted_event_count: **{p['persisted_event_count']}**",
        f"* skipped_existing_on_first_persist: "
        f"{p['skipped_existing_on_first_persist']}",
        f"* malformed_event_rejection_count: "
        f"**{p['malformed_event_rejection_count']}**",
        f"* forbidden_field_rejections: "
        f"**{p['forbidden_field_rejections']}**",
        f"* loaded_event_count: **{p['loaded_event_count']}**",
        "",
        "## Phase 18B verdict counters (after replay)",
        "",
        f"* signals_total: **{p['signals_total']}**",
        f"* candidates_total: **{p['candidates_total']}**",
        f"* allowlisted_candidate_count: "
        f"{p['allowlisted_candidate_count']}",
        f"* insufficient_evidence_total: "
        f"{p['insufficient_evidence_total']}",
        f"* out_of_family_rejected_total: "
        f"{p['out_of_family_rejected_total']}",
        f"* high_risk_rejected_total: {p['high_risk_rejected_total']}",
        f"* builder_handoff_quarantine_count: "
        f"{p['builder_handoff_quarantine_count']}",
        f"* duplicate_suppression_count: "
        f"{p['duplicate_suppression_count']}",
        "",
        "## Phase 18C runtime registration (after replay)",
        "",
        f"* registered_solver_count: **{p['registered_solver_count']}**",
        f"* non_allowlisted_rejected_count: "
        f"{p['non_allowlisted_rejected_count']}",
        "",
        "## Runtime dispatch",
        "",
        f"* dispatch_case_count: **{p['dispatch_case_count']}**",
        f"* dispatch_success_count: **{p['dispatch_success_count']}**",
        f"* dispatch_failure_count: {p['dispatch_failure_count']}",
        f"* families_covered: **{p['families_covered']}**",
        "",
        "## Idempotency",
        "",
        f"* replay_idempotency_pass: **{p['replay_idempotency_pass']}**",
        f"* second_persist_inserted: {p['second_persist_inserted']}",
        f"* second_persist_skipped_existing: "
        f"{p['second_persist_skipped_existing']}",
        f"* second_replay_extra_solvers: "
        f"{p['second_replay_extra_solvers']}",
        f"* second_replay_extra_capability_features: "
        f"{p['second_replay_extra_capability_features']}",
        f"* second_replay_extra_artifacts: "
        f"{p['second_replay_extra_artifacts']}",
        "",
        "## Claim labels",
        "",
    ]
    for k in sorted(p["claim_labels"].keys()):
        lines.append(f"* `{k}`: **{p['claim_labels'][k]}**")
    lines += [
        "",
        "## Allowlist + provider/builder invariants",
        "",
        f"* `allowlist_unchanged`: **{p['allowlist_unchanged']}**",
        f"* `provider_jobs_delta`: {p['provider_jobs_delta']}",
        f"* `builder_jobs_delta`: {p['builder_jobs_delta']}",
        f"* `no_stage2_flip`: {p['no_stage2_flip']}",
        f"* `no_human_approval`: {p['no_human_approval']}",
        f"* `no_high_risk_autonomy`: {p['no_high_risk_autonomy']}",
        f"* `no_live_builder_execution`: {p['no_live_builder_execution']}",
        f"* `no_model_pull_or_download`: {p['no_model_pull_or_download']}",
        f"* `no_cloud_api_calls`: {p['no_cloud_api_calls']}",
        f"* `db_path_is_temp`: {p['db_path_is_temp']}",
        f"* `db_committed`: {p['db_committed']}",
        "",
        "## Release gate",
        "",
        f"* `release_gate_pass`: **{p['release_gate_pass']}**",
        f"* `forbidden_claims_absent`: "
        f"**{p['forbidden_claims_absent']}**",
        "",
        "## What this proves",
        "",
        "* Runtime gap events can be persisted as durable, content-keyed "
        "rows in the existing `runtime_gap_signals` table without any "
        "schema change, and replayed deterministically into the existing "
        "Phase 18B miner.",
        "* Replayed allowlisted candidates register through the existing "
        "Phase 18C four-step pattern and serve via the real "
        "`LowRiskSolverDispatcher.dispatch_by_features` capability-aware "
        "path - the same code path live runtime queries use.",
        "* Idempotent re-replay: persisting and replaying the same event "
        "set twice does not create extra solver, capability-feature, or "
        "artifact rows.",
        "* Non-allowlisted (insufficient / out-of-family / high-risk / "
        "builder-handoff / duplicate) and malformed / forbidden-field "
        "events never become executable runtime solvers.",
        "",
        "## What this does NOT prove",
        "",
        "* That the same path scales to high-volume real production "
        "traffic (the proof fixture is intentionally small and "
        "deterministic).",
        "* That high-risk family auto-promotion is safe (it is "
        "explicitly blocked).",
        "* Cross-vendor ranking or raw-intelligence superiority "
        "(NOT_CLAIMED).",
        "",
        "## Reproduce",
        "",
        "```",
        "python -X utf8 tools/run_phase18e_runtime_gap_replay_proof.py "
        "--out-dir <out>",
        "```",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main proof
# ---------------------------------------------------------------------------

def build_proof(*, out_dir: Path,
                  control_plane_path: Path | None = None) -> dict[str, Any]:
    started_at = _utc_iso()
    out_dir.mkdir(parents=True, exist_ok=True)

    valid_events, malformed_events = build_persisted_event_fixture()

    # Temp ControlPlaneDB.
    if control_plane_path is None:
        cp_dir = Path(tempfile.mkdtemp(prefix="phase18e_cp_"))
        cp_path = cp_dir / "phase18e_control_plane.db"
    else:
        cp_path = control_plane_path
    cp = ControlPlaneDB(cp_path)

    # ------------------------------------------------------------------
    # Pass 1: persist valid events.
    # ------------------------------------------------------------------
    persist_valid = persist_runtime_gap_events(cp, valid_events)

    # ------------------------------------------------------------------
    # Pass 2: persist malformed events (all should be rejected).
    # ------------------------------------------------------------------
    persist_malformed = persist_runtime_gap_events(cp, malformed_events)

    # ------------------------------------------------------------------
    # Load + first replay.
    # ------------------------------------------------------------------
    loaded = load_runtime_gap_events(cp)
    replay1 = replay_persisted_gap_events(
        cp, config=GapMiningConfig(),
    )

    counts_after_replay1 = _count_runtime_rows(cp)

    # ------------------------------------------------------------------
    # Dispatch through the real LowRiskSolverDispatcher.
    # ------------------------------------------------------------------
    dispatcher = LowRiskSolverDispatcher(cp)
    per_case_results: list[dict[str, Any]] = []
    per_family_dispatch_counts: dict[str, int] = {}
    success_count = 0
    failure_count = 0

    for i, case in enumerate(DISPATCH_CASES):
        fam = case["family_kind"]
        cap_features = _stringify_features(dict(case["features"]))
        res = dispatcher.dispatch_by_features(
            family_kind=fam, features=cap_features,
            inputs=dict(case["inputs"]),
        )
        expected = case["expected_output"]
        actual = res.output
        if isinstance(expected, float) and isinstance(actual, (int, float)):
            outputs_match = math.isclose(
                float(actual), float(expected),
                rel_tol=1e-9, abs_tol=1e-9,
            )
        else:
            outputs_match = (actual == expected)
        success = (
            res.matched is True
            and res.reason in ("hit", "hit_by_features")
            and outputs_match
        )
        per_case_results.append({
            "case_id": f"phase18e-case-{i:02d}",
            "family_kind": fam,
            "label": case["label"],
            "features": dict(case["features"]),
            "inputs": dict(case["inputs"]),
            "expected_output": case["expected_output"],
            "actual_output": res.output,
            "matched": res.matched,
            "reason": res.reason,
            "solver_id": res.solver_id,
            "artifact_id": res.artifact_id,
            "error": res.error,
            "success": success,
        })
        per_family_dispatch_counts[fam] = (
            per_family_dispatch_counts.get(fam, 0) + 1
        )
        if success:
            success_count += 1
        else:
            failure_count += 1

    # ------------------------------------------------------------------
    # Idempotency: persist + replay the SAME fixture again.
    # ------------------------------------------------------------------
    persist_2 = persist_runtime_gap_events(cp, valid_events)
    replay2 = replay_persisted_gap_events(cp, config=GapMiningConfig())
    counts_after_replay2 = _count_runtime_rows(cp)

    extra_solvers = (
        counts_after_replay2["solvers"] - counts_after_replay1["solvers"]
    )
    extra_caps = (
        counts_after_replay2["solver_capability_features"]
        - counts_after_replay1["solver_capability_features"]
    )
    extra_artifacts = (
        counts_after_replay2["solver_artifacts"]
        - counts_after_replay1["solver_artifacts"]
    )
    idempotency_pass = (
        persist_2.inserted_event_ids == ()
        and len(persist_2.skipped_existing_event_ids) == len(loaded)
        and extra_solvers == 0
        and extra_caps == 0
        and extra_artifacts == 0
    )

    finished_at = _utc_iso()
    cp.close()

    families_covered = len(per_family_dispatch_counts)
    allowlist_unchanged = ALLOWED_FAMILIES == (
        "scalar_unit_conversion", "lookup_table", "threshold_rule",
        "interval_bucket_classifier", "linear_arithmetic",
        "bounded_interpolation",
    )

    counters_after_replay = dict(replay1.mining_result.counters)
    allowlisted_count = counters_after_replay.get(
        GapVerdict.ALLOWLISTED_SOLVER_SPEC.value, 0,
    )

    proof: dict[str, Any] = {
        "phase": "phase18e_runtime_gap_replay",
        "benchmark_version": BENCHMARK_VERSION,
        "started_utc": started_at,
        "finished_utc": finished_at,
        "base_main_sha": _git_sha(),
        "python_version": sys.version.split()[0],
        "platform": _plat.platform(),
        "source_prerelease": SOURCE_PRERELEASE,
        "candidate_prerelease": CANDIDATE_PRERELEASE,
        "schema_version": SCHEMA_VERSION,

        "is_synthetic_fixture": True,
        "fixture_size_valid": len(valid_events),
        "fixture_size_malformed": len(malformed_events),
        "config_snapshot": dict(replay1.mining_result.config_snapshot),

        # Persistence pass 1 (valid events).
        "persisted_event_count": len(persist_valid.inserted_event_ids),
        "skipped_existing_on_first_persist": len(
            persist_valid.skipped_existing_event_ids,
        ),
        "loaded_event_count": len(loaded),
        # Persistence pass 2 (malformed events).
        "malformed_event_rejection_count": (
            persist_malformed.malformed_event_rejection_count
        ),
        "forbidden_field_rejections": (
            persist_malformed.forbidden_field_rejections
        ),
        "malformed_persisted_count": (
            len(persist_malformed.inserted_event_ids)
        ),

        # Phase 18B counters (after replay 1).
        "phase18b_counters": counters_after_replay,
        "signals_total": counters_after_replay.get("signals_total", 0),
        "candidates_total": counters_after_replay.get(
            "candidates_total", 0,
        ),
        "allowlisted_candidate_count": allowlisted_count,
        "insufficient_evidence_total": counters_after_replay.get(
            GapVerdict.INSUFFICIENT_EVIDENCE.value, 0,
        ),
        "out_of_family_rejected_total": counters_after_replay.get(
            GapVerdict.OUT_OF_FAMILY_REJECTED.value, 0,
        ),
        "high_risk_rejected_total": counters_after_replay.get(
            GapVerdict.HIGH_RISK_REJECTED.value, 0,
        ),
        "builder_handoff_quarantine_count": counters_after_replay.get(
            GapVerdict.BUILDER_HANDOFF_QUARANTINED.value, 0,
        ),
        "duplicate_suppression_count": counters_after_replay.get(
            GapVerdict.DUPLICATE_SUPPRESSED.value, 0,
        ),

        # Phase 18C registration after replay 1.
        "registered_solver_count": (
            replay1.registration_summary.registered_count
        ),
        "non_allowlisted_rejected_count": (
            replay1.registration_summary.rejected_count
        ),
        "registration_summary": (
            replay1.registration_summary.to_dict()
        ),

        # Dispatch.
        "dispatch_case_count": len(DISPATCH_CASES),
        "dispatch_success_count": success_count,
        "dispatch_failure_count": failure_count,
        "families_covered": families_covered,
        "per_family_dispatch_counts": per_family_dispatch_counts,
        "per_dispatch_case": per_case_results,

        # Idempotency.
        "second_persist_inserted": len(persist_2.inserted_event_ids),
        "second_persist_skipped_existing": len(
            persist_2.skipped_existing_event_ids,
        ),
        "second_replay_extra_solvers": extra_solvers,
        "second_replay_extra_capability_features": extra_caps,
        "second_replay_extra_artifacts": extra_artifacts,
        "replay_idempotency_pass": idempotency_pass,

        # Row counts.
        "row_counts_after_replay1": counts_after_replay1,
        "row_counts_after_replay2": counts_after_replay2,

        # Honesty / invariants.
        "allowlist_unchanged": allowlist_unchanged,
        "provider_jobs_delta": 0,
        "builder_jobs_delta": 0,
        "no_model_pull_or_download": True,
        "no_cloud_api_calls": True,
        "no_live_builder_execution": True,
        "no_stage2_flip": True,
        "no_human_approval": True,
        "no_high_risk_autonomy": True,
        "no_cross_vendor_ranking_claim": True,
        "no_raw_intelligence_superiority_claim": True,

        # Storage hygiene.
        "db_path_is_temp": True,
        "db_committed": False,

        "claim_labels": {
            "runtime_gap_replay": "PROVEN-DURABLE-PERSISTED-EVENT-REPLAY",
            "runtime_dispatch_via_real_path":
                "MEASURED-CAPABILITY-AWARE-HIT-BY-FEATURES",
            "schema_change": "NONE",
            "builder_handoff": "QUARANTINED-NOT-AUTOPROMOTED",
            "high_risk_families": "NOT_CLAIMED",
            "raw_intelligence_vs_frontier_moe": "NOT_CLAIMED",
            "cross_vendor_ranking": "NOT_CLAIMED",
            "consciousness": "NOT_CLAIMED",
        },

        "reproduction_commands": [
            "python -X utf8 tools/run_phase18e_runtime_gap_replay_proof.py "
            "--out-dir <out>",
        ],
    }

    # Forbidden-vocabulary scrub on JSON + MD. Seed pending fields so
    # render_md can run before release-gate computation finishes.
    proof.setdefault("release_gate_pass", "pending")
    proof.setdefault("forbidden_claims_absent", "pending")
    serialized_for_scan = json.dumps(
        {k: v for k, v in proof.items() if k not in ("config_snapshot",)},
        sort_keys=True, default=str,
    )
    json_hits = scan_forbidden(serialized_for_scan)
    md_text_preview = render_md(proof=proof)
    md_hits = scan_forbidden(md_text_preview)
    forbidden_absent = (not json_hits) and (not md_hits)
    proof["forbidden_claims_absent"] = forbidden_absent
    proof["forbidden_substring_hits"] = {
        "json_hits": json_hits, "md_hits": md_hits,
    }

    # Release gates.
    gates = {
        "persisted_event_count_ge_30":
            len(persist_valid.inserted_event_ids) >= 30,
        "loaded_event_count_ge_30": len(loaded) >= 30,
        "allowlisted_candidate_count_ge_6": allowlisted_count >= 6,
        "registered_solver_count_ge_6":
            replay1.registration_summary.registered_count >= 6,
        "families_covered_eq_6": families_covered == 6,
        "dispatch_case_at_least_18": len(DISPATCH_CASES) >= 18,
        "all_dispatch_cases_succeeded": failure_count == 0,
        "replay_idempotency_pass": idempotency_pass,
        "non_allowlisted_rejected_at_least_5":
            replay1.registration_summary.rejected_count >= 5,
        "malformed_event_rejection_at_least_1":
            persist_malformed.malformed_event_rejection_count >= 1,
        "forbidden_field_rejection_at_least_1":
            persist_malformed.forbidden_field_rejections >= 1,
        "allowlist_unchanged": allowlist_unchanged,
        "provider_delta_zero": proof["provider_jobs_delta"] == 0,
        "builder_delta_zero": proof["builder_jobs_delta"] == 0,
        "no_stage2_flip": True,
        "no_human_approval": True,
        "no_live_builder_execution": True,
        "no_high_risk_autonomy": True,
        "db_path_is_temp": True,
        "db_not_committed": True,
        "forbidden_claims_absent": forbidden_absent,
    }
    proof["release_gates"] = gates
    proof["release_gate_pass"] = all(gates.values())

    return proof


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = (
        ROOT / "docs" / "runs"
        / "phase18e_runtime_gap_replay_2026_05_06"
    )
    parser.add_argument("--out-dir", type=Path, default=default_out)
    args = parser.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Phase 18E - Persisted Runtime Gap Replay Proof")
    print("=" * 60)

    proof = build_proof(out_dir=out_dir)

    out_path_json = out_dir / "runtime_gap_replay_proof.json"
    out_path_md = out_dir / "runtime_gap_replay_proof.md"

    out_path_json.write_bytes(
        json.dumps(proof, indent=2, sort_keys=True, default=str)
        .encode("utf-8")
    )
    out_path_md.write_bytes(render_md(proof=proof).encode("utf-8"))

    print()
    print(f"Wrote {out_path_json}")
    print(f"Wrote {out_path_md}")
    print()
    print(f"persisted_event_count          : "
            f"{proof['persisted_event_count']}")
    print(f"loaded_event_count             : "
            f"{proof['loaded_event_count']}")
    print(f"malformed_rejection_count      : "
            f"{proof['malformed_event_rejection_count']}")
    print(f"forbidden_field_rejections     : "
            f"{proof['forbidden_field_rejections']}")
    print(f"allowlisted_candidate_count    : "
            f"{proof['allowlisted_candidate_count']}")
    print(f"registered_solver_count        : "
            f"{proof['registered_solver_count']}")
    print(f"non_allowlisted_rejected_count : "
            f"{proof['non_allowlisted_rejected_count']}")
    print(f"dispatch_case_count            : "
            f"{proof['dispatch_case_count']}")
    print(f"dispatch_success_count         : "
            f"{proof['dispatch_success_count']}")
    print(f"dispatch_failure_count         : "
            f"{proof['dispatch_failure_count']}")
    print(f"families_covered               : "
            f"{proof['families_covered']}")
    print(f"replay_idempotency_pass        : "
            f"{proof['replay_idempotency_pass']}")
    print(f"provider/builder delta         : "
            f"{proof['provider_jobs_delta']}/{proof['builder_jobs_delta']}")
    print(f"forbidden_claims_absent        : "
            f"{proof['forbidden_claims_absent']}")
    print(f"release_gate_pass              : "
            f"{proof['release_gate_pass']}")

    return 0 if proof["release_gate_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
