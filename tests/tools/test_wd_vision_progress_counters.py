# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.build_wd_vision_progress_counters import (
    SCHEMA_VERSION,
    build_vision_progress_counters,
)
from tools.wd_image1_capability_manifest import build_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_wd_vision_progress_counters.py"


def _minimal_manifest() -> dict:
    return {
        "schema_version": "wd_image1_capability_manifest.v1",
        "summary": {
            "capability_count": 3,
            "status_counts": {"partial": 2, "implemented": 1},
            "all_literal_claims_safe": False,
        },
        "capabilities": [
            {
                "capability_id": "hex_mesh_entry",
                "status": "partial",
                "claim_safe": False,
                "evidence": [
                    {"path": "configs/settings.yaml", "present": True},
                    {"path": "configs/hex_cells.yaml", "present": False},
                ],
                "gaps": ["first hop is not authoritative"],
                "next_smallest_pr": "add route-order counter",
                "proof": {
                    "ok": True,
                    "literal_claim_safe": False,
                    "proves_every_query_first_enters_mesh": False,
                    "pre_hex_steps": ["language_detection", "hot_cache"],
                    "current_config": {
                        "hybrid_retrieval_mode": "candidate",
                        "hex_mesh_enabled": False,
                        "hybrid_retrieval_authoritative": False,
                    },
                },
            },
            {
                "capability_id": "magma_audit_log",
                "status": "partial",
                "claim_safe": False,
                "evidence": [
                    {
                        "path": "waggledance/instrumentation/magma_receipts.py",
                        "present": True,
                    }
                ],
                "gaps": ["default receipt sink not mandatory"],
                "next_smallest_pr": "promote receipt coverage counter",
                "proof": {
                    "ok": True,
                    "solver_call_trace_receipt_bound": True,
                    "receipt_count": 1,
                    "default_sink_required": False,
                },
            },
            {
                "capability_id": "future_waggledance_swarm",
                "status": "implemented",
                "claim_safe": True,
                "evidence": [
                    {
                        "path": "tools/run_future_scale_route_depth_benchmark.py",
                        "present": True,
                    }
                ],
                "gaps": [],
                "next_smallest_pr": "",
                "proof": {
                    "ok": True,
                    "literal_future_claim_safe": True,
                    "claim_gate_satisfied": True,
                    "axis_count": 8,
                    "runtime_evidence_summary": {
                        "required_runtime_evidence_present": True,
                        "runtime_evidence_axis_count": 8,
                    },
                },
            },
        ],
    }


def test_build_counters_from_manifest_without_claim_mutation() -> None:
    counters = build_vision_progress_counters(
        _minimal_manifest(),
        generated_at_utc="2026-06-05T18:40:00Z",
    )

    assert counters["schema_version"] == SCHEMA_VERSION
    assert counters["ok"] is True
    assert counters["blockers"] == []
    assert counters["generated_at_utc"] == "2026-06-05T18:40:00Z"
    assert counters["summary"]["capability_count"] == 3
    assert counters["summary"]["claim_safe_count"] == 1
    assert counters["summary"]["unsafe_literal_claim_count"] == 2
    assert counters["summary"]["proof_ok_count"] == 3
    assert counters["summary"]["literal_claim_safe_ratio"] == 0.333333
    assert counters["summary"]["production_safe_capability_count"] == 1
    assert counters["next_smallest_pr_count"] == 2
    assert counters["guardrails"] == {
        "read_only": True,
        "external_writes_applied": False,
        "runtime_authority_changed": False,
        "claim_safe_flip_applied": False,
        "bridge_event_written": False,
        "github_mutation_performed": False,
    }


def test_panel_counters_expose_measurable_milestones() -> None:
    counters = build_vision_progress_counters(_minimal_manifest())
    by_id = {
        item["capability_id"]: item
        for item in counters["panel_counters"]
    }

    assert by_id["hex_mesh_entry"]["panel"] == 1
    assert by_id["hex_mesh_entry"]["evidence_paths"] == [
        "configs/settings.yaml",
        "configs/hex_cells.yaml",
    ]
    assert by_id["hex_mesh_entry"]["milestones"] == {
        "authoritative_first_hop_safe": False,
        "literal_claim_safe": False,
        "pre_hex_step_count": 2,
        "hybrid_retrieval_mode": "candidate",
        "hex_mesh_enabled": False,
        "hybrid_retrieval_authoritative": False,
        # default-off first-hop coverage: absent -> measurement-only fields off
        "first_hop_coverage_present": False,
        "first_hop_coverage_available": False,
        "first_hop_coverage_ratio": None,
        "first_hop_declares_order": False,
    }
    assert by_id["magma_audit_log"]["milestones"]["receipt_count"] == 1
    assert (
        counters["milestone_counters"][
            "authoritative_first_hop_route_order_coverage"
        ]["current_value"]
        == 0.0
    )
    assert (
        counters["milestone_counters"]["per_query_receipt_claim_gate"][
            "current_value"
        ]
        is False
    )
    assert "per_query_receipt_coverage_percent" not in counters["milestone_counters"]
    assert (
        counters["milestone_counters"]["future_claim_gate_satisfied"]["satisfied"]
        is True
    )


def test_receipt_claim_gate_uses_same_condition_for_value_and_satisfied() -> None:
    manifest = _minimal_manifest()
    for capability in manifest["capabilities"]:
        if capability["capability_id"] == "magma_audit_log":
            capability["proof"]["default_sink_required"] = True
            capability["proof"]["solver_call_trace_receipt_bound"] = False

    counters = build_vision_progress_counters(manifest)
    receipt_gate = counters["milestone_counters"]["per_query_receipt_claim_gate"]

    assert receipt_gate["current_value"] is False
    assert receipt_gate["target_value"] is True
    assert receipt_gate["satisfied"] is False
    assert receipt_gate["coverage_measurement_available"] is False
    assert receipt_gate["measured_coverage_percent"] is None
    assert receipt_gate["measurement_basis"] == "manifest_claim_gate_flags"


def test_current_manifest_counters_do_not_upgrade_image_claims() -> None:
    counters = build_vision_progress_counters(build_manifest(ROOT))

    assert counters["source_schema_version"] == "wd_image1_capability_manifest.v1"
    assert counters["summary"]["capability_count"] == 6
    assert counters["summary"]["status_counts"] == {
        "blocked": 0,
        "implemented": 0,
        "partial": 6,
        "planned": 0,
    }
    assert counters["summary"]["claim_safe_count"] == 0
    assert counters["summary"]["all_literal_claims_safe"] is False
    assert counters["summary"]["proof_ok_count"] == 6
    assert counters["guardrails"]["claim_safe_flip_applied"] is False
    by_id = {
        item["capability_id"]: item
        for item in counters["panel_counters"]
    }
    low_risk = by_id["low_risk_autonomy_loop"]
    assert low_risk["milestones"]["real_loop_report_ok"] is True
    assert (
        low_risk["milestones"]["real_loop_claim_label"]
        == "MEASURED_LOCAL_DRY_RUN"
    )
    assert low_risk["milestones"]["measured_auto_promoted_solver_count"] == 1
    assert low_risk["milestones"]["measured_auto_promoted_run_count"] == 1
    assert low_risk["milestones"]["measured_provider_jobs_created"] == 0
    assert low_risk["milestones"]["measured_builder_jobs_created"] == 0
    assert low_risk["milestones"]["dry_run_runtime_authority_granted"] is False
    assert low_risk["milestones"]["dry_run_external_writes_applied"] is False
    gated_promotions = counters["milestone_counters"][
        "end_to_end_gated_promotions_total"
    ]
    assert gated_promotions == {
        "current_value": 1,
        "target_value": 1,
        "satisfied": True,
        "guardrail_runtime_authority_granted": False,
        "guardrail_tripped": False,
        "measurement_basis": "local_ephemeral_control_plane_real_loop",
        "claim_label": "MEASURED_LOCAL_DRY_RUN",
        "production_authority_granted": False,
    }
    future = by_id["future_waggledance_swarm"]
    assert future["milestones"]["literal_future_claim_safe"] is False
    assert future["milestones"]["future_claim_gate_satisfied"] is False
    assert future["milestones"]["required_runtime_evidence_present"] is False
    assert (
        counters["milestone_counters"]["future_claim_gate_satisfied"]["satisfied"]
        is False
    )


def test_empty_capability_manifest_blocks_and_keeps_zero_ratios() -> None:
    counters = build_vision_progress_counters(
        {
            "schema_version": "wd_image1_capability_manifest.v1",
            "summary": {"status_counts": {}},
            "capabilities": [],
        }
    )

    assert counters["ok"] is False
    assert counters["blockers"] == ["capabilities_empty"]
    assert counters["summary"]["capability_count"] == 0
    assert counters["summary"]["all_literal_claims_safe"] is False
    assert counters["summary"]["proof_ok_ratio"] == 0.0
    assert counters["summary"]["literal_claim_safe_ratio"] == 0.0
    assert counters["panel_counters"] == []


def test_malformed_manifest_reports_blockers_without_favorable_claims() -> None:
    counters = build_vision_progress_counters(
        {
            "schema_version": "unexpected",
            "capabilities": [
                {
                    "capability_id": "hex_mesh_entry",
                    "status": "partial",
                    "claim_safe": "yes",
                    "evidence": "not-list",
                    "proof": {"ok": "true"},
                },
                "not-a-capability",
            ],
        }
    )

    assert counters["ok"] is False
    assert "unexpected_manifest_schema_version" in counters["blockers"]
    assert "summary_not_mapping" in counters["blockers"]
    assert "hex_mesh_entry_claim_safe_not_bool" in counters["blockers"]
    assert "hex_mesh_entry_proof_ok_not_bool" in counters["blockers"]
    assert "hex_mesh_entry_evidence_not_list" in counters["blockers"]
    assert "capability_1_not_mapping" in counters["blockers"]
    assert counters["summary"]["claim_safe_count"] == 0
    assert counters["summary"]["proof_ok_count"] == 0
    assert counters["summary"]["literal_claim_safe_ratio"] == 0.0


def test_invalid_summary_status_counts_fail_closed_and_rederive_counts() -> None:
    counters = build_vision_progress_counters(
        {
            "schema_version": "wd_image1_capability_manifest.v1",
            "summary": {"status_counts": {"implemented": "one"}},
            "capabilities": [
                {
                    "capability_id": "hex_mesh_entry",
                    "status": "partial",
                    "claim_safe": False,
                    "evidence": [],
                    "proof": {"ok": True},
                }
            ],
        }
    )

    assert counters["ok"] is False
    assert (
        "summary_status_counts_implemented_not_non_negative_int"
        in counters["blockers"]
    )
    assert counters["summary"]["status_counts"] == {"partial": 1}
    assert counters["summary"]["all_literal_claims_safe"] is False


def test_non_mapping_manifest_returns_structured_blocker() -> None:
    counters = build_vision_progress_counters([])

    assert counters["ok"] is False
    assert counters["blockers"] == ["manifest_not_mapping"]
    assert counters["summary"]["capability_count"] == 0
    assert counters["summary"]["all_literal_claims_safe"] is False
    assert counters["panel_counters"] == []

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--manifest", "-"],
        input="[]",
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["ok"] is False
    assert payload["blockers"] == ["manifest_not_mapping"]


def test_cli_emits_json_from_manifest_file_and_strict_claims_fails(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_minimal_manifest()), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json", "--manifest", str(manifest_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    payload = json.loads(proc.stdout)

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["summary"]["all_literal_claims_safe"] is False

    strict = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--strict-claims",
            "--manifest",
            str(manifest_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert strict.returncode == 2


def test_cli_strict_claims_fails_when_manifest_has_blockers() -> None:
    manifest = {
        "schema_version": "unexpected",
        "summary": {"status_counts": {"implemented": 1}},
        "capabilities": [
            {
                "capability_id": "future_waggledance_swarm",
                "status": "implemented",
                "claim_safe": True,
                "evidence": [],
                "proof": {"ok": True},
            }
        ],
    }

    strict = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict-claims", "--manifest", "-"],
        input=json.dumps(manifest),
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(strict.stdout)

    assert strict.returncode == 2
    assert payload["ok"] is False
    assert "unexpected_manifest_schema_version" in payload["blockers"]
    assert payload["summary"]["claim_safe_count"] == 0
    assert payload["summary"]["all_literal_claims_safe"] is False
    assert payload["summary"]["literal_claim_safe_ratio"] == 0.0
    assert payload["summary"]["production_safe_capability_count"] == 0
    assert payload["summary"]["unsafe_literal_claim_ids"] == [
        "future_waggledance_swarm"
    ]


def test_cli_run_does_not_mutate_tracked_files(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_minimal_manifest()), encoding="utf-8")
    before = subprocess.run(
        ["git", "status", "--short", "-uno"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    after = subprocess.run(
        ["git", "status", "--short", "-uno"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout

    assert json.loads(proc.stdout)["guardrails"]["read_only"] is True
    assert after == before


def test_gated_promotions_fail_closed_on_guardrail_leaks() -> None:
    # Regression for the tools changes_requested: a guardrail leak (runtime
    # authority / external writes / provider jobs / builder jobs) must drive
    # end_to_end_gated_promotions_total.current_value to 0, satisfied=False,
    # guardrail_tripped=True - never a misleading count of 1.
    import copy

    base = build_manifest(ROOT)

    def _inject(real_loop: dict, name: str) -> None:
        if name == "runtime_authority":
            real_loop.setdefault("authority_boundary", {})["runtime_authority_granted"] = True
        elif name == "external_writes":
            real_loop.setdefault("authority_boundary", {})["external_writes_applied"] = True
        elif name == "provider_jobs":
            real_loop.setdefault("control_plane", {}).setdefault("table_counts", {})["provider_jobs"] = 1
        elif name == "builder_jobs":
            real_loop.setdefault("control_plane", {}).setdefault("table_counts", {})["builder_jobs"] = 1

    for name in ("runtime_authority", "external_writes", "provider_jobs", "builder_jobs"):
        manifest = copy.deepcopy(base)
        for cap in manifest["capabilities"]:
            if cap["capability_id"] == "low_risk_autonomy_loop":
                _inject(cap["proof"].setdefault("real_loop_dry_run", {}), name)
        counters = build_vision_progress_counters(manifest)
        gp = counters["milestone_counters"]["end_to_end_gated_promotions_total"]
        assert gp["guardrail_tripped"] is True, name
        assert gp["satisfied"] is False, name
        assert gp["current_value"] == 0, name

    # production_authority_granted is derived, not hardcoded: true when a
    # runtime-authority leak is observed.
    leaked = copy.deepcopy(base)
    for cap in leaked["capabilities"]:
        if cap["capability_id"] == "low_risk_autonomy_loop":
            cap["proof"].setdefault("real_loop_dry_run", {}).setdefault(
                "authority_boundary", {}
            )["runtime_authority_granted"] = True
    gp_leaked = build_vision_progress_counters(leaked)["milestone_counters"][
        "end_to_end_gated_promotions_total"
    ]
    assert gp_leaked["production_authority_granted"] is True


def test_gated_promotions_fail_closed_on_every_authority_axis() -> None:
    # rco-1 re-audit fix: guardrail must derive from the FULL authority_boundary
    # (any axis True), not a hand-enumerated subset. Forge ALL 9 emitted axes -
    # including the 5 previously ungated (production_control_plane_touched,
    # production_scheduler_enqueue, gate_skip_authority, operator_gate_bypassed,
    # fast_track_priority) - and require each to fail closed.
    import copy

    base = build_manifest(ROOT)
    axes = (
        "external_writes_applied",
        "production_control_plane_touched",
        "production_scheduler_enqueue",
        "provider_jobs_created",
        "builder_jobs_created",
        "gate_skip_authority",
        "operator_gate_bypassed",
        "runtime_authority_granted",
        "fast_track_priority",
    )
    for axis in axes:
        manifest = copy.deepcopy(base)
        for cap in manifest["capabilities"]:
            if cap["capability_id"] == "low_risk_autonomy_loop":
                cap["proof"].setdefault("real_loop_dry_run", {}).setdefault(
                    "authority_boundary", {}
                )[axis] = True
        gp = build_vision_progress_counters(manifest)["milestone_counters"][
            "end_to_end_gated_promotions_total"
        ]
        assert gp["guardrail_tripped"] is True, axis
        assert gp["satisfied"] is False, axis
        assert gp["current_value"] == 0, axis


# --- per-query receipt coverage counter (option A: default-off/on-demand) ---
def _manifest_with_coverage(coverage: object) -> dict:
    """Minimal manifest whose magma proof carries a per_query_receipt_coverage
    aggregate (simulates the manifest having run the opt-in proof). Pass None to
    omit the key (the flag-off shape)."""
    import copy

    manifest = copy.deepcopy(_minimal_manifest())
    for capability in manifest["capabilities"]:
        if capability["capability_id"] == "magma_audit_log":
            if coverage is not None:
                capability["proof"]["per_query_receipt_coverage"] = coverage
    return manifest


def _coverage_gate(coverage: object) -> dict:
    counters = build_vision_progress_counters(_manifest_with_coverage(coverage))
    return counters["milestone_counters"]["per_query_receipt_claim_gate"]


_SAFE_COVERAGE = {
    "ok": True,
    "receipt_coverage_ratio": 1.0,
    "all_queries_receipt_bound": True,
    "raw_payload_leak_check": True,
    "default_sink_required": False,
    "default_runtime_receipt_emission_changed": False,
}


def test_per_query_coverage_available_with_safe_measurement() -> None:
    gate = _coverage_gate(_SAFE_COVERAGE)
    assert gate["coverage_measurement_available"] is True
    assert gate["measured_coverage_percent"] == 100.0
    assert gate["measurement_basis"] == "v12_per_query_receipt_coverage_proof"
    # measurement is decoupled from the real claim gate
    assert gate["satisfied"] is False
    assert gate["current_value"] is False


def test_per_query_coverage_measurement_never_upgrades_satisfied() -> None:
    # Forge: a 100% local measurement must NOT flip satisfied/current_value.
    # satisfied with coverage present == satisfied with coverage absent.
    with_cov = _coverage_gate(_SAFE_COVERAGE)
    without_cov = _coverage_gate(None)
    assert with_cov["satisfied"] == without_cov["satisfied"] is False
    assert with_cov["current_value"] == without_cov["current_value"] is False


def test_per_query_coverage_unavailable_when_raw_leak_check_false() -> None:
    # Forge 6: present but raw_payload_leak_check=False -> unavailable (derived).
    gate = _coverage_gate({**_SAFE_COVERAGE, "raw_payload_leak_check": False})
    assert gate["coverage_measurement_available"] is False
    assert gate["measured_coverage_percent"] is None
    assert gate["measurement_basis"] == "manifest_claim_gate_flags"


def test_per_query_coverage_unavailable_when_ok_false() -> None:
    gate = _coverage_gate({**_SAFE_COVERAGE, "ok": False})
    assert gate["coverage_measurement_available"] is False
    assert gate["measured_coverage_percent"] is None


def test_per_query_coverage_unavailable_when_ratio_out_of_range() -> None:
    for bad in (1.5, -0.1):
        gate = _coverage_gate({**_SAFE_COVERAGE, "receipt_coverage_ratio": bad})
        assert gate["coverage_measurement_available"] is False, bad
        assert gate["measured_coverage_percent"] is None, bad


def test_per_query_coverage_unavailable_when_ratio_is_bool_or_missing() -> None:
    for bad in (True, None, "1.0"):
        gate = _coverage_gate({**_SAFE_COVERAGE, "receipt_coverage_ratio": bad})
        assert gate["coverage_measurement_available"] is False, bad
        assert gate["measured_coverage_percent"] is None, bad


def test_per_query_coverage_unavailable_when_absent() -> None:
    gate = _coverage_gate(None)
    assert gate["coverage_measurement_available"] is False
    assert gate["measured_coverage_percent"] is None
    assert gate["measurement_basis"] == "manifest_claim_gate_flags"


def test_per_query_coverage_derived_not_hardcoded() -> None:
    # Two inputs differing only in leak_check yield different availability:
    # proves the flag is DERIVED, not a hardcoded constant.
    leakfree = _coverage_gate(_SAFE_COVERAGE)["coverage_measurement_available"]
    leaky = _coverage_gate(
        {**_SAFE_COVERAGE, "raw_payload_leak_check": False}
    )["coverage_measurement_available"]
    assert leakfree is True and leaky is False


def test_per_query_coverage_partial_ratio_reported() -> None:
    gate = _coverage_gate({**_SAFE_COVERAGE, "receipt_coverage_ratio": 0.6667})
    assert gate["coverage_measurement_available"] is True
    assert gate["measured_coverage_percent"] == 66.67


def test_claim_safe_count_unaffected_by_coverage() -> None:
    # A 100% local measurement must not bump the capability claim_safe_count.
    base = build_vision_progress_counters(_manifest_with_coverage(None))
    with_cov = build_vision_progress_counters(
        _manifest_with_coverage(_SAFE_COVERAGE)
    )
    assert (
        with_cov["summary"]["claim_safe_count"]
        == base["summary"]["claim_safe_count"]
    )


# --- authoritative first-hop coverage counter (default-off/on-demand) ---
def _manifest_with_first_hop(coverage: object) -> dict:
    import copy

    manifest = copy.deepcopy(_minimal_manifest())
    for capability in manifest["capabilities"]:
        if capability["capability_id"] == "hex_mesh_entry" and coverage is not None:
            capability["proof"]["first_hop_coverage"] = coverage
    return manifest


def _first_hop_gate(coverage: object) -> dict:
    counters = build_vision_progress_counters(_manifest_with_first_hop(coverage))
    return counters["milestone_counters"][
        "authoritative_first_hop_route_order_coverage"
    ]


_SAFE_FIRST_HOP = {
    "coverage_measurement_available": True,
    "authoritative_first_hop_coverage": 0.6667,
    "capsule_declares_authoritative_order": True,
    "measurement_basis": "v1_first_hop_authoritative_order",
}


def test_first_hop_coverage_available_with_safe_measurement() -> None:
    gate = _first_hop_gate(_SAFE_FIRST_HOP)
    assert gate["coverage_measurement_available"] is True
    assert gate["measured_first_hop_authoritative_percent"] == 66.67
    assert gate["measurement_basis"] == "v1_first_hop_authoritative_order"


def test_first_hop_measurement_never_upgrades_satisfied() -> None:
    # A measured coverage must NOT flip satisfied/current_value off the real claim.
    with_cov = _first_hop_gate(_SAFE_FIRST_HOP)
    without_cov = _first_hop_gate(None)
    assert with_cov["satisfied"] == without_cov["satisfied"] is False
    assert with_cov["current_value"] == without_cov["current_value"] == 0.0


def test_first_hop_unavailable_when_not_declared() -> None:
    # capsule with no declared order -> measurement unavailable (NOT 100%).
    gate = _first_hop_gate({**_SAFE_FIRST_HOP,
                            "capsule_declares_authoritative_order": False,
                            "coverage_measurement_available": False,
                            "authoritative_first_hop_coverage": None})
    assert gate["coverage_measurement_available"] is False
    assert gate["measured_first_hop_authoritative_percent"] is None
    assert gate["measurement_basis"] == "manifest_hex_mesh_flags"


def test_first_hop_unavailable_when_flag_false() -> None:
    gate = _first_hop_gate({**_SAFE_FIRST_HOP,
                            "coverage_measurement_available": False})
    assert gate["coverage_measurement_available"] is False
    assert gate["measured_first_hop_authoritative_percent"] is None


def test_first_hop_unavailable_when_ratio_invalid() -> None:
    for bad in (1.5, -0.1, True, None, "0.6"):
        gate = _first_hop_gate({**_SAFE_FIRST_HOP,
                                "authoritative_first_hop_coverage": bad})
        assert gate["coverage_measurement_available"] is False, bad
        assert gate["measured_first_hop_authoritative_percent"] is None, bad


def test_first_hop_unavailable_when_absent() -> None:
    gate = _first_hop_gate(None)
    assert gate["coverage_measurement_available"] is False
    assert gate["measured_first_hop_authoritative_percent"] is None
    assert gate["measurement_basis"] == "manifest_hex_mesh_flags"


def test_first_hop_derived_not_hardcoded() -> None:
    avail = _first_hop_gate(_SAFE_FIRST_HOP)["coverage_measurement_available"]
    unavail = _first_hop_gate(
        {**_SAFE_FIRST_HOP, "coverage_measurement_available": False}
    )["coverage_measurement_available"]
    assert avail is True and unavail is False


# --- low-risk repeat-window trend counter (default-off/on-demand) ---
_TREND_AUTHORITY_AXES = (
    "external_writes_applied", "production_control_plane_touched",
    "production_scheduler_enqueue", "provider_jobs_created", "builder_jobs_created",
    "gate_skip_authority", "operator_gate_bypassed", "runtime_authority_granted",
    "fast_track_priority",
)


def _good_trend():
    return {
        "ok": True,
        "deterministic": True,
        "evidence_present": True,
        # PREFIXED so the root authority scan never picks these up.
        "trend_runtime_authority_granted": False,
        "trend_external_writes_applied": False,
        "window_size": 3,
        "all_runs_ok": True,
        "any_guardrail_tripped": False,
        "promoted_solver_count_min": 1,
        "promoted_solver_count_max": 1,
        "promoted_solver_count_stable": True,
        "measurement_basis": "v1_low_risk_real_loop_repeat_window",
    }


def _manifest_with_low_risk_trend(trend):
    proof = {
        "ok": True,
        "real_loop_dry_run": {
            "ok": True,
            "claim_label": "MEASURED_LOCAL_DRY_RUN",
            "chain": {"auto_promoted_solver_count": 1, "auto_promoted_run_count": 1},
            "authority_boundary": {axis: False for axis in _TREND_AUTHORITY_AXES},
            "control_plane": {"table_counts": {"provider_jobs": 0, "builder_jobs": 0}},
        },
    }
    if trend is not None:
        proof["repeat_window_trend"] = trend
    return {
        "schema_version": "wd_image1_capability_manifest.v1",
        "summary": {"capability_count": 1, "status_counts": {"partial": 1},
                    "all_literal_claims_safe": False},
        "capabilities": [{
            "capability_id": "low_risk_autonomy_loop", "status": "partial",
            "claim_safe": False, "evidence": [], "gaps": [], "next_smallest_pr": "x",
            "proof": proof,
        }],
    }


def _trend_counters(trend):
    mc = build_vision_progress_counters(_manifest_with_low_risk_trend(trend))[
        "milestone_counters"
    ]
    return mc["low_risk_real_loop_repeat_window_trend"], mc[
        "end_to_end_gated_promotions_total"
    ]


def test_repeat_window_trend_available_with_safe_measurement() -> None:
    trend_gate, _ = _trend_counters(_good_trend())
    assert trend_gate["trend_measurement_available"] is True
    assert trend_gate["measured_window_size"] == 3
    assert trend_gate["measured_stable_promotion_count"] == 1
    assert trend_gate["promotion_count_stable"] is True
    assert trend_gate["measurement_basis"] == "v1_low_risk_real_loop_repeat_window"
    assert trend_gate["claim_safe"] is False


def test_repeat_window_trend_never_upgrades_end_to_end_gate() -> None:
    # The end_to_end gate verdict must be identical with the trend present (stable
    # 100%) vs absent - the measurement is fully decoupled from the claim.
    _, e2e_with = _trend_counters(_good_trend())
    _, e2e_without = _trend_counters(None)
    assert e2e_with["satisfied"] == e2e_without["satisfied"] is True
    assert e2e_with["current_value"] == e2e_without["current_value"]


def test_repeat_window_trend_unavailable_when_absent() -> None:
    trend_gate, _ = _trend_counters(None)
    assert trend_gate["trend_measurement_available"] is False
    assert trend_gate["measured_window_size"] is None
    assert trend_gate["measured_stable_promotion_count"] is None
    assert trend_gate["measurement_basis"] == "manifest_real_loop_flags"


@pytest.mark.parametrize("field,bad", [
    ("ok", False),
    ("deterministic", False),
    ("evidence_present", False),
    ("all_runs_ok", False),
    ("any_guardrail_tripped", True),
    ("promoted_solver_count_stable", False),
    ("window_size", 1),
    ("window_size", 1_000_000),  # above MAX_WINDOW upper bound
    # independently re-derived authority flags (do not trust evidence_present)
    ("trend_runtime_authority_granted", True),
    ("trend_external_writes_applied", True),
])
def test_repeat_window_trend_unavailable_when_degraded(field, bad) -> None:
    trend = _good_trend()
    trend[field] = bad
    trend_gate, _ = _trend_counters(trend)
    assert trend_gate["trend_measurement_available"] is False, field
    assert trend_gate["measured_window_size"] is None, field
    assert trend_gate["claim_safe"] is False, field


@pytest.mark.parametrize("authority_field", [
    "trend_runtime_authority_granted",
    "trend_external_writes_applied",
])
def test_trend_authority_flag_does_not_leak_into_end_to_end_gate(authority_field):
    # The CRITICAL #1271 tools forge: a trend authority flag True must NOT flip the
    # real end_to_end gate (the trend keys are prefixed so the root _nested_flag
    # authority scan never sees them); and the trend itself is unavailable.
    trend = _good_trend()
    trend[authority_field] = True
    trend_gate, e2e_with = _trend_counters(trend)
    _, e2e_clean = _trend_counters(_good_trend())
    assert e2e_with["satisfied"] is True, authority_field
    assert e2e_with["satisfied"] == e2e_clean["satisfied"], authority_field
    assert e2e_with["current_value"] == e2e_clean["current_value"], authority_field
    assert e2e_with["guardrail_tripped"] is False, authority_field
    assert trend_gate["trend_measurement_available"] is False, authority_field


@pytest.mark.parametrize("bare_key", [
    "runtime_authority_granted",
    "external_writes_applied",
    "operator_visible_metrics",
])
def test_bare_nested_authority_key_in_trend_subtree_does_not_leak(bare_key):
    # The DEFINITIVE #1271 fix: even a BARE authority key nested anywhere in the
    # measurement-only repeat_window_trend subtree (e.g. a forged per-window
    # authority_boundary) must NOT reach the recursive root authority scan and
    # flip the real end_to_end gate - the trend subtree is excluded from that scan.
    trend = _good_trend()
    trend["forged_nested"] = {"authority_boundary": {bare_key: True}}
    _, e2e_with = _trend_counters(trend)
    _, e2e_clean = _trend_counters(_good_trend())
    assert e2e_with["satisfied"] is True, bare_key
    assert e2e_with["satisfied"] == e2e_clean["satisfied"], bare_key
    assert e2e_with["current_value"] == e2e_clean["current_value"], bare_key
    assert e2e_with["guardrail_tripped"] is False, bare_key


def test_repeat_window_trend_derived_not_hardcoded() -> None:
    avail = _trend_counters(_good_trend())[0]["trend_measurement_available"]
    unavail = _trend_counters({**_good_trend(), "ok": False})[0][
        "trend_measurement_available"
    ]
    assert avail is True and unavail is False


# --- hex-subdivision reviewer summary counter (#1273 renderer wiring) ---
def _good_reviewer_summary():
    return {
        "report_version": "wd.hex_subdivision_reviewer_summary.v1",
        "verdict_ok": True,
        "blocker_count": 0,
        "warning_count": 0,
        "source_contract_match": True,
        "rebuilt_index_entry_match": True,
        "digest_all_match": True,
        "size_all_match": True,
        "schema_version_all_match": True,
        "all_checks_match": True,
        "review_clean": True,
        "path_free_verified": True,
    }


def _manifest_with_hex_reviewer(reviewer, *, hex_proof_extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if reviewer is not None:
        proof["reviewer_summary"] = reviewer
    if hex_proof_extra:
        proof.update(hex_proof_extra)
    return {
        "schema_version": "wd_image1_capability_manifest.v1",
        "summary": {"capability_count": 1, "status_counts": {"partial": 1},
                    "all_literal_claims_safe": False},
        "capabilities": [{
            "capability_id": "hexagonal_upgrades", "status": "partial",
            "claim_safe": False, "evidence": [], "gaps": [], "next_smallest_pr": "x",
            "proof": proof,
        }],
    }


def _hex_counters(reviewer, **kw):
    mc = build_vision_progress_counters(_manifest_with_hex_reviewer(reviewer, **kw))[
        "milestone_counters"
    ]
    return mc["hex_subdivision_reviewer_summary"], mc[
        "shadow_to_candidate_subdivision_transitions_total"
    ]


def test_hex_reviewer_summary_available_with_clean_summary():
    block, _ = _hex_counters(_good_reviewer_summary())
    assert block["reviewer_summary_available"] is True
    assert block["review_clean"] is True
    assert block["path_free_verified"] is True
    assert block["measurement_basis"] == "v1_hex_subdivision_reviewer_summary"
    assert block["claim_safe"] is False


def test_hex_reviewer_summary_unavailable_when_absent():
    block, _ = _hex_counters(None)
    assert block["reviewer_summary_available"] is False
    assert block["review_clean"] is False
    assert block["measurement_basis"] == "manifest_hex_upgrade_flags"
    assert block["claim_safe"] is False


@pytest.mark.parametrize("field", [
    "verdict_ok", "path_free_verified",
    # COMPONENT booleans the consumer must re-derive review_clean from:
    "source_contract_match", "rebuilt_index_entry_match",
    "digest_all_match", "size_all_match", "schema_version_all_match",
])
def test_hex_reviewer_summary_consumer_rederives_fail_closed(field):
    # Consumer re-derives review_clean from the COMPONENT booleans fail-closed: a
    # single degraded component -> not review_clean (and path_free -> unavailable).
    r = _good_reviewer_summary()
    r[field] = False
    block, _ = _hex_counters(r)
    if field == "path_free_verified":
        assert block["reviewer_summary_available"] is False
    assert block["review_clean"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("component", [
    "source_contract_match", "rebuilt_index_entry_match",
    "digest_all_match", "size_all_match", "schema_version_all_match",
])
def test_hex_reviewer_inconsistent_aggregate_fails_closed(component):
    # The #1274 tools forge: an INCONSISTENT aggregate where a COMPONENT is False
    # but the aggregate's own composite review_clean/all_checks_match is True must
    # still render review_clean=False (consumer does not trust the composite).
    r = _good_reviewer_summary()
    r[component] = False
    r["review_clean"] = True       # lying aggregate composite
    r["all_checks_match"] = True   # lying aggregate composite
    block, _ = _hex_counters(r)
    assert block["review_clean"] is False, component
    if component in ("digest_all_match", "size_all_match", "schema_version_all_match"):
        assert block["all_checks_match"] is False, component


@pytest.mark.parametrize("bad_blockers", [1, -1, True, "x", None])
def test_hex_reviewer_nonzero_or_malformed_blockers_not_clean(bad_blockers):
    r = _good_reviewer_summary()
    r["blocker_count"] = bad_blockers
    block, _ = _hex_counters(r)
    assert block["review_clean"] is False, bad_blockers


def test_hex_reviewer_summary_does_not_touch_shadow_to_candidate():
    _, s2c_with = _hex_counters(_good_reviewer_summary())
    _, s2c_without = _hex_counters(None)
    assert s2c_with == s2c_without
    assert s2c_with["satisfied"] is False
    assert s2c_with["current_value"] == 0


@pytest.mark.parametrize("bare_key", [
    "no_runtime_mutation", "runtime_authority_changed",
])
def test_hex_reviewer_subtree_excluded_from_recursive_scan(bare_key):
    # #1271 recursive-coupling safety: a bare authority/mutation key nested in the
    # measurement-only reviewer_summary subtree must NOT reach the recursive
    # _nested_flag scan that feeds the real hex milestones.
    reviewer = _good_reviewer_summary()
    reviewer["forged_nested"] = {bare_key: (False if bare_key == "no_runtime_mutation" else True)}
    counters = build_vision_progress_counters(_manifest_with_hex_reviewer(reviewer))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["hexagonal_upgrades"]["milestones"]
    # the real milestones reflect the hex proof root, NOT the forged subtree
    assert milestones["no_runtime_mutation"] is True, bare_key
    assert milestones["runtime_authority_changed"] is False, bare_key
