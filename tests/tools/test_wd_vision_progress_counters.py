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
        "first_hop_denominator_scope": None,
        "first_hop_denominator_count": 0,
        "first_hop_gap_count": 0,
        "first_hop_denominator_integrity_ok": False,
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
    contribution = counters["milestone_counters"][
        "low_risk_real_loop_manifest_contribution"
    ]
    assert contribution == {
        "contribution_available": True,
        "evidence_count": 1,
        "evidence_present": True,
        "deterministic": True,
        "guardrail_tripped": False,
        "provider_calls": 0,
        "production_authority_granted": False,
        "measurement_basis": "v1_low_risk_real_loop_manifest_contribution",
        "claim_safe": False,
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
    "first_hop_denominator_scope": "all_non_cached_first_hops",
    "first_hop_denominator_count": 30,
    "authoritative_first_hop_gap_count": 10,
    "denominator_is_all_non_cached_first_hops": True,
    "measurement_basis": "v1_first_hop_authoritative_order",
}


def test_first_hop_coverage_available_with_safe_measurement() -> None:
    gate = _first_hop_gate(_SAFE_FIRST_HOP)
    assert gate["coverage_measurement_available"] is True
    assert gate["measured_first_hop_authoritative_percent"] == 66.67
    assert gate["measurement_denominator_scope"] == "all_non_cached_first_hops"
    assert gate["measurement_denominator_count"] == 30
    assert gate["measurement_gap_count"] == 10
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
    assert gate["measurement_denominator_scope"] is None
    assert gate["measurement_denominator_count"] is None
    assert gate["measurement_gap_count"] is None
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
    # all_checks_match is the COMPLETE composite (incl source/rebuilt), so any
    # of the five check components False -> all_checks_match False.
    assert block["all_checks_match"] is False, component


@pytest.mark.parametrize("bad_blockers", [1, -1, True, "x", None, 0.0])
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


# --- hex-subdivision shadow-only invariant counter (run_shadow_only_invariant_proof wiring) ---
def _good_shadow_only_invariant():
    # Mirrors the measurement-only proof the manifest stores under
    # hex_upgrade_proof["shadow_only_invariant"].
    return {
        "report_version": "wd.shadow_only_invariant_proof.v1",
        "ok": True,
        "blockers": [],
        "measurement_basis": "v1_shadow_only_invariant",
        "deterministic_replay": {"runs": 2, "stable_identical": True},
        "invariant": {
            "invariant_holds": True,
            "shadow_to_candidate_subdivision_transitions_total": 0,
            "target_state_is_shadow": True,
            "transition_occurred": False,
            "no_runtime_mutation": True,
            "guardrails_all_clean": True,
            "artifact_ok": True,
            "claim_safe": False,
        },
    }


def _manifest_with_shadow_only(shadow_inv, *, extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if shadow_inv is not None:
        proof["shadow_only_invariant"] = shadow_inv
    if extra:
        proof.update(extra)
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


def _shadow_counters(shadow_inv, *, extra=None):
    mc = build_vision_progress_counters(
        _manifest_with_shadow_only(shadow_inv, extra=extra)
    )["milestone_counters"]
    return (
        mc["hex_subdivision_shadow_only_invariant"],
        mc["shadow_to_candidate_subdivision_transitions_total"],
    )


def test_shadow_only_enforced_with_clean_proof():
    block, s2c = _shadow_counters(_good_shadow_only_invariant())
    assert block["invariant_proof_available"] is True
    assert block["shadow_only_enforced"] is True
    assert block["target_state_is_shadow"] is True
    assert block["no_runtime_mutation"] is True
    assert block["guardrails_all_clean"] is True
    assert block["transition_occurred"] is False
    assert block["transition_count_strict_zero"] is True
    assert block["deterministic_replay_stable"] is True
    assert block["measurement_basis"] == "v1_shadow_only_invariant"
    assert block["claim_safe"] is False
    # honest-zero transition counter is left untouched by the positive evidence
    assert s2c["current_value"] == 0
    assert s2c["satisfied"] is False


def test_shadow_only_unavailable_when_absent():
    block, _ = _shadow_counters(None)
    assert block["invariant_proof_available"] is False
    assert block["shadow_only_enforced"] is False
    assert block["measurement_basis"] == "manifest_hex_upgrade_flags"
    assert block["claim_safe"] is False


def test_shadow_only_accept_candidate_demoted_remains_shadow_only():
    # The collapse shadow rule demotes even an ACCEPT_CANDIDATE to shadow-only:
    # the proof's evidence is a shadow target state with NO transition. That clean
    # demotion reads as enforced WITHOUT bumping the transition counter.
    block, s2c = _shadow_counters(_good_shadow_only_invariant())
    assert block["shadow_only_enforced"] is True
    assert block["transition_occurred"] is False
    assert s2c["current_value"] == 0 and s2c["satisfied"] is False


def test_shadow_only_forged_promotion_fails_closed_no_fake_transition():
    # A FORGED shadow->candidate promotion (an ACCEPT_CANDIDATE that did NOT stay
    # shadow-only) must fail the enforcement claim AND must NOT bump the real
    # progress counter (no fake transition path / no transition-count upgrade).
    inv = _good_shadow_only_invariant()
    inv["invariant"]["transition_occurred"] = True
    inv["invariant"]["shadow_to_candidate_subdivision_transitions_total"] = 1
    inv["invariant"]["target_state_is_shadow"] = False
    inv["invariant"]["invariant_holds"] = True  # lying aggregate
    block, s2c = _shadow_counters(inv)
    assert block["shadow_only_enforced"] is False
    assert block["transition_occurred"] is True
    assert block["transition_count_strict_zero"] is False
    # the honest progress counter is NOT raised by forged evidence
    assert s2c["current_value"] == 0
    assert s2c["satisfied"] is False
    assert block["claim_safe"] is False


@pytest.mark.parametrize("mutate", [
    lambda inv: inv["invariant"].__setitem__("target_state_is_shadow", False),
    lambda inv: inv["invariant"].pop("target_state_is_shadow", None),  # absence != evidence
    lambda inv: inv["invariant"].__setitem__("target_state_is_shadow", "true"),  # type-confused
])
def test_shadow_only_non_shadow_or_missing_target_state_fails_closed(mutate):
    inv = _good_shadow_only_invariant()
    mutate(inv)
    block, _ = _shadow_counters(inv)
    assert block["target_state_is_shadow"] is False
    assert block["shadow_only_enforced"] is False


@pytest.mark.parametrize("bad", [False, "true", 1, None, 0])
def test_shadow_only_missing_or_false_no_runtime_mutation_fails_closed(bad):
    inv = _good_shadow_only_invariant()
    inv["invariant"]["no_runtime_mutation"] = bad
    block, _ = _shadow_counters(inv)
    assert block["no_runtime_mutation"] is False, bad
    assert block["shadow_only_enforced"] is False, bad


def test_shadow_only_missing_no_runtime_mutation_key_fails_closed():
    inv = _good_shadow_only_invariant()
    inv["invariant"].pop("no_runtime_mutation", None)
    block, _ = _shadow_counters(inv)
    assert block["no_runtime_mutation"] is False
    assert block["shadow_only_enforced"] is False


@pytest.mark.parametrize("bad_count", [0.0, True, 1, -1, "0", None, [0]])
def test_shadow_only_malformed_transition_count_fails_closed(bad_count):
    # strict-int-0: a non-strict-int (float/bool/str/None/seq) or non-zero fails
    # closed and never certifies enforcement; the progress counter is untouched.
    inv = _good_shadow_only_invariant()
    inv["invariant"]["shadow_to_candidate_subdivision_transitions_total"] = bad_count
    block, s2c = _shadow_counters(inv)
    assert block["transition_count_strict_zero"] is False, bad_count
    assert block["shadow_only_enforced"] is False, bad_count
    assert s2c["current_value"] == 0 and s2c["satisfied"] is False


@pytest.mark.parametrize("garbage", ["x", [1, 2], 123, 1.5])
def test_shadow_only_non_mapping_evidence_fails_closed(garbage):
    # malformed/type-confused top-level evidence (non-mapping) fails closed.
    block, _ = _shadow_counters(garbage)
    assert block["shadow_only_enforced"] is False, garbage
    assert block["claim_safe"] is False


def test_shadow_only_empty_mapping_not_available():
    block, _ = _shadow_counters({})
    assert block["invariant_proof_available"] is False
    assert block["shadow_only_enforced"] is False


@pytest.mark.parametrize("component", [
    "artifact_ok", "target_state_is_shadow", "no_runtime_mutation",
    "guardrails_all_clean",
])
def test_shadow_only_inconsistent_aggregate_fails_closed(component):
    # The proof's own invariant_holds aggregate is True while a COMPONENT is False:
    # the consumer RE-DERIVES from components and must fail closed (never trusts
    # the aggregate).
    inv = _good_shadow_only_invariant()
    inv["invariant"][component] = False
    inv["invariant"]["invariant_holds"] = True  # lying aggregate
    block, _ = _shadow_counters(inv)
    assert block["shadow_only_enforced"] is False, component


def test_shadow_only_proof_not_ok_fails_closed():
    inv = _good_shadow_only_invariant()
    inv["ok"] = False
    block, _ = _shadow_counters(inv)
    assert block["shadow_only_enforced"] is False


def test_shadow_only_nondeterministic_fails_closed():
    inv = _good_shadow_only_invariant()
    inv["deterministic_replay"]["stable_identical"] = False
    block, _ = _shadow_counters(inv)
    assert block["deterministic_replay_stable"] is False
    assert block["shadow_only_enforced"] is False


def test_shadow_only_no_claim_safe_or_transition_count_upgrade():
    # Even a fully clean proof never sets claim_safe True; a proof that lies
    # claim_safe=True is refused (enforcement not certified) and the counter's own
    # claim_safe stays False.
    block, _ = _shadow_counters(_good_shadow_only_invariant())
    assert block["claim_safe"] is False
    assert block["shadow_only_enforced"] is True

    inv = _good_shadow_only_invariant()
    inv["invariant"]["claim_safe"] = True  # forged self-upgrade
    block2, _ = _shadow_counters(inv)
    assert block2["claim_safe"] is False
    assert block2["shadow_only_enforced"] is False


def test_shadow_only_does_not_touch_transition_counter():
    _, s2c_with = _shadow_counters(_good_shadow_only_invariant())
    _, s2c_without = _shadow_counters(None)
    assert s2c_with == s2c_without
    assert s2c_with["current_value"] == 0
    assert s2c_with["satisfied"] is False


@pytest.mark.parametrize("bare_key", [
    "no_runtime_mutation", "runtime_authority_changed",
])
def test_shadow_only_subtree_excluded_from_recursive_scan(bare_key):
    # #1271 recursive-coupling safety: a bare authority/mutation key nested in the
    # measurement-only shadow_only_invariant subtree must NOT reach the recursive
    # _nested_flag scan that feeds the real hex milestones.
    inv = _good_shadow_only_invariant()
    inv["forged_nested"] = {
        bare_key: (False if bare_key == "no_runtime_mutation" else True)
    }
    counters = build_vision_progress_counters(_manifest_with_shadow_only(inv))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["hexagonal_upgrades"]["milestones"]
    assert milestones["no_runtime_mutation"] is True, bare_key
    assert milestones["runtime_authority_changed"] is False, bare_key


# --- hex-subdivision verifier-chain FINAL summary counter (#1276 renderer wiring) ---
def _good_chain_final_summary():
    # Mirrors the render_shadow_subdivision_verifier_chain_final_summary output the
    # manifest stores under hex_upgrade_proof["chain_final_summary"].
    return {
        "report_version": "wd.shadow_subdivision_verifier_chain_final_summary.v1",
        "chain_levels_total": 10,
        "chain_levels_present": 10,
        "chain_levels_ok": 10,
        "chain_levels_all_ok": True,
        "chain_levels_shape_ok": True,
        "artifact_verify_clean": True,
        "index_entry_verify_clean": True,
        "deepest_verify_clean": True,
        "total_blocker_count": 0,
        "total_warning_count": 0,
        "chain_clean": True,
        "path_free_verified": True,
    }


def _manifest_with_hex_chain(chain, *, hex_proof_extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if chain is not None:
        proof["chain_final_summary"] = chain
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


def _chain_counters(chain, **kw):
    mc = build_vision_progress_counters(_manifest_with_hex_chain(chain, **kw))[
        "milestone_counters"
    ]
    return mc["hex_subdivision_chain_final_summary"], mc[
        "shadow_to_candidate_subdivision_transitions_total"
    ]


def test_hex_chain_final_summary_available_with_clean_summary():
    block, _ = _chain_counters(_good_chain_final_summary())
    assert block["chain_summary_available"] is True
    assert block["chain_clean"] is True
    assert block["path_free_verified"] is True
    assert block["levels_all_ok"] is True
    assert block["levels_shape_ok"] is True
    assert block["measurement_basis"] == (
        "v1_shadow_subdivision_verifier_chain_final_summary"
    )
    assert block["claim_safe"] is False


def test_hex_chain_final_summary_unavailable_when_absent():
    block, _ = _chain_counters(None)
    assert block["chain_summary_available"] is False
    assert block["chain_clean"] is False
    assert block["measurement_basis"] == "manifest_hex_upgrade_flags"
    assert block["claim_safe"] is False


@pytest.mark.parametrize("field", [
    "path_free_verified",
    "chain_levels_all_ok", "chain_levels_shape_ok",
    "artifact_verify_clean", "index_entry_verify_clean", "deepest_verify_clean",
])
def test_hex_chain_consumer_rederives_fail_closed(field):
    # Consumer RE-DERIVES chain_clean from the COMPONENT booleans fail-closed: a
    # single degraded component -> not chain_clean (path_free -> also unavailable).
    c = _good_chain_final_summary()
    c[field] = False
    block, _ = _chain_counters(c)
    if field == "path_free_verified":
        assert block["chain_summary_available"] is False
    assert block["chain_clean"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("component", [
    "chain_levels_all_ok", "chain_levels_shape_ok",
    "artifact_verify_clean", "index_entry_verify_clean", "deepest_verify_clean",
])
def test_hex_chain_inconsistent_aggregate_fails_closed(component):
    # #1274 forge: an INCONSISTENT aggregate with a COMPONENT False but the
    # aggregate's own chain_clean True must still render chain_clean=False (consumer
    # never trusts the aggregate's own composite).
    c = _good_chain_final_summary()
    c[component] = False
    c["chain_clean"] = True  # lying aggregate composite
    block, _ = _chain_counters(c)
    assert block["chain_clean"] is False, component


@pytest.mark.parametrize("bad_blockers", [1, -1, True, "x", None, 0.0])
def test_hex_chain_nonzero_or_malformed_blockers_not_clean(bad_blockers):
    c = _good_chain_final_summary()
    c["total_blocker_count"] = bad_blockers
    block, _ = _chain_counters(c)
    assert block["chain_clean"] is False, bad_blockers


@pytest.mark.parametrize("total,present", [(10, 9), (0, 0), (10, 11)])
def test_hex_chain_incomplete_levels_not_clean(total, present):
    c = _good_chain_final_summary()
    c["chain_levels_total"] = total
    c["chain_levels_present"] = present
    block, _ = _chain_counters(c)
    assert block["chain_clean"] is False, (total, present)


@pytest.mark.parametrize("bad_total", [True, 10.0, "10", None])
def test_hex_chain_malformed_levels_total_not_clean(bad_total):
    c = _good_chain_final_summary()
    c["chain_levels_total"] = bad_total
    c["chain_levels_present"] = bad_total
    block, _ = _chain_counters(c)
    assert block["chain_clean"] is False, bad_total


def test_hex_chain_final_summary_does_not_touch_shadow_to_candidate():
    _, s2c_with = _chain_counters(_good_chain_final_summary())
    _, s2c_without = _chain_counters(None)
    assert s2c_with == s2c_without
    assert s2c_with["satisfied"] is False
    assert s2c_with["current_value"] == 0


@pytest.mark.parametrize("bare_key", [
    "no_runtime_mutation", "runtime_authority_changed",
])
def test_hex_chain_subtree_excluded_from_recursive_scan(bare_key):
    # #1271 recursive-coupling safety: a bare authority/mutation key nested in the
    # measurement-only chain_final_summary subtree must NOT reach the recursive
    # _nested_flag scan that feeds the real hex milestones.
    chain = _good_chain_final_summary()
    chain["forged_nested"] = {
        bare_key: (False if bare_key == "no_runtime_mutation" else True)
    }
    counters = build_vision_progress_counters(_manifest_with_hex_chain(chain))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["hexagonal_upgrades"]["milestones"]
    assert milestones["no_runtime_mutation"] is True, bare_key
    assert milestones["runtime_authority_changed"] is False, bare_key


def test_hex_chain_clean_surfaces_honest_10_of_10_and_counts():
    # The honest milestone surfaces present/total (10/10), the 10/10 flag, and the
    # strict-int blocker/warning counts when clean.
    block, _ = _chain_counters(_good_chain_final_summary())
    assert block["levels_present"] == 10
    assert block["levels_total"] == 10
    assert block["levels_complete_10_of_10"] is True
    assert block["blocker_count"] == 0
    assert block["warning_count"] == 0


def test_hex_chain_absent_surfaces_none_counts():
    block, _ = _chain_counters(None)
    assert block["levels_present"] is None
    assert block["levels_total"] is None
    assert block["levels_complete_10_of_10"] is False
    assert block["blocker_count"] is None
    assert block["warning_count"] is None


@pytest.mark.parametrize("bad_total", [9, 11])
def test_hex_chain_level_count_must_be_exactly_ten(bad_total):
    # The expected full chain depth is 10; a consistent-but-wrong count (present ==
    # total but != 10) still fails the honest 10/10 milestone.
    c = _good_chain_final_summary()
    c["chain_levels_total"] = bad_total
    c["chain_levels_present"] = bad_total
    block, _ = _chain_counters(c)
    assert block["levels_complete_10_of_10"] is False, bad_total
    assert block["chain_clean"] is False, bad_total
    assert block["levels_present"] is None
    assert block["levels_total"] is None


@pytest.mark.parametrize("garbage", [None, [], "x", 0, 1, True])
def test_hex_chain_non_mapping_summary_fails_closed(garbage):
    block, _ = _chain_counters(garbage)
    assert block["chain_summary_available"] is False
    assert block["chain_clean"] is False


def test_hex_chain_self_declared_claim_safe_refuses_to_certify():
    # Measurement-only NEVER upgrades a claim: a summary self-declaring claim_safe
    # True must fail closed (unavailable + not clean); emitted claim_safe stays False.
    c = _good_chain_final_summary()
    c["claim_safe"] = True
    block, _ = _chain_counters(c)
    assert block["chain_summary_available"] is False
    assert block["chain_clean"] is False
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bad_warn", ["x", 1.5, True])
def test_hex_chain_malformed_warning_surfaces_none_non_fatal(bad_warn):
    # Warnings are non-fatal: a malformed count surfaces None and never gates clean.
    c = _good_chain_final_summary()
    c["total_warning_count"] = bad_warn
    block, _ = _chain_counters(c)
    assert block["warning_count"] is None
    assert block["chain_clean"] is True


def test_hex_chain_nonzero_warning_surfaced_not_blocking():
    c = _good_chain_final_summary()
    c["total_warning_count"] = 3
    block, _ = _chain_counters(c)
    assert block["warning_count"] == 3
    assert block["chain_clean"] is True


def test_hex_chain_claim_safe_hardcoded_false_even_when_clean():
    # claim_safe is HARDCODED False on the emitted milestone regardless of cleanliness.
    block, _ = _chain_counters(_good_chain_final_summary())
    assert block["claim_safe"] is False


# --- hex cross-consistency digest counter (#1278 digest wiring) ---
def _good_cross_consistency_digest():
    # Mirrors build_cross_consistency_digest output the manifest stores under
    # hex_upgrade_proof["cross_consistency_digest"].
    return {
        "report_version": "wd.hex_upgrade_cross_consistency_digest.v1",
        "reviewer_summary_present": True,
        "shadow_only_invariant_present": True,
        "chain_final_summary_present": True,
        "all_views_present": True,
        "reviewer_clean": True,
        "shadow_only_clean": True,
        "chain_summary_clean": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _manifest_with_hex_xcons(digest, *, hex_proof_extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if digest is not None:
        proof["cross_consistency_digest"] = digest
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


def _xcons_counters(digest, **kw):
    mc = build_vision_progress_counters(_manifest_with_hex_xcons(digest, **kw))[
        "milestone_counters"
    ]
    return mc["hex_subdivision_cross_consistency_digest"], mc[
        "shadow_to_candidate_subdivision_transitions_total"
    ]


def test_hex_xcons_available_with_clean_digest():
    block, _ = _xcons_counters(_good_cross_consistency_digest())
    assert block["digest_available"] is True
    assert block["cross_consistent"] is True
    assert block["path_free_verified"] is True
    assert block["all_views_present"] is True
    assert block["measurement_basis"] == "v1_hex_upgrade_cross_consistency_digest"
    assert block["claim_safe"] is False


def test_hex_xcons_unavailable_when_absent():
    block, _ = _xcons_counters(None)
    assert block["digest_available"] is False
    assert block["cross_consistent"] is False
    assert block["measurement_basis"] == "manifest_hex_upgrade_flags"
    assert block["claim_safe"] is False


_XCONS_COMPONENT_FIELDS = [
    "path_free_verified",
    "all_views_present",
    "reviewer_clean",
    "shadow_only_clean",
    "chain_summary_clean",
]


@pytest.mark.parametrize("field", _XCONS_COMPONENT_FIELDS)
def test_hex_xcons_consumer_rederives_fail_closed(field):
    # Consumer RE-DERIVES cross_consistent from the COMPONENT booleans fail-closed.
    d = _good_cross_consistency_digest()
    d[field] = False
    block, _ = _xcons_counters(d)
    if field == "path_free_verified":
        assert block["digest_available"] is False
    assert block["cross_consistent"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("field", [
    "all_views_present", "reviewer_clean", "shadow_only_clean", "chain_summary_clean",
])
def test_hex_xcons_inconsistent_aggregate_fails_closed(field):
    # #1274: a component False but the digest's own cross_consistent True must still
    # render cross_consistent=False (consumer never trusts the aggregate composite).
    d = _good_cross_consistency_digest()
    d[field] = False
    d["cross_consistent"] = True  # lying aggregate composite
    block, _ = _xcons_counters(d)
    assert block["cross_consistent"] is False, field


def test_hex_xcons_self_claim_safe_refuses_to_certify():
    # A digest self-declaring claim_safe True must fail closed (measurement-only never
    # upgrades a claim) - mirrors the chain_final_summary self_claim_safe guard.
    d = _good_cross_consistency_digest()
    d["claim_safe"] = True
    block, _ = _xcons_counters(d)
    assert block["digest_available"] is False
    assert block["cross_consistent"] is False


def test_hex_xcons_does_not_touch_shadow_to_candidate():
    _, s2c_with = _xcons_counters(_good_cross_consistency_digest())
    _, s2c_without = _xcons_counters(None)
    assert s2c_with == s2c_without
    assert s2c_with["satisfied"] is False
    assert s2c_with["current_value"] == 0


@pytest.mark.parametrize("bare_key", [
    "no_runtime_mutation", "runtime_authority_changed",
])
def test_hex_xcons_subtree_excluded_from_recursive_scan(bare_key):
    # #1271: a bare authority/mutation key nested in the measurement-only
    # cross_consistency_digest subtree must NOT reach the recursive _nested_flag scan.
    d = _good_cross_consistency_digest()
    d["forged_nested"] = {
        bare_key: (False if bare_key == "no_runtime_mutation" else True)
    }
    counters = build_vision_progress_counters(_manifest_with_hex_xcons(d))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["hexagonal_upgrades"]["milestones"]
    assert milestones["no_runtime_mutation"] is True, bare_key
    assert milestones["runtime_authority_changed"] is False, bare_key


def test_hex_xcons_claim_safe_hardcoded_false_even_when_clean():
    block, _ = _xcons_counters(_good_cross_consistency_digest())
    assert block["claim_safe"] is False


# --- hex ring-messaging + parent-child hierarchy counter (ring/hierarchy wiring) ---
def _good_ring_hierarchy_summary():
    # Mirrors the CURATED content-safe summary the manifest stores under
    # hex_upgrade_proof["ring_hierarchy_summary"].
    return {
        "report_version": "wd.ring_messaging_hierarchy_proof.v1",
        "ok": True,
        "hierarchy_ok": True,
        "ring_boundary_ok": True,
        "no_runtime_mutation": True,
        "no_invalid_boundary_delivery": True,
        "deterministic": True,
        "blocker_count": 0,
        "path_free_verified": True,
    }


def _manifest_with_hex_ring(summary, *, hex_proof_extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if summary is not None:
        proof["ring_hierarchy_summary"] = summary
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


def _ring_counters(summary, **kw):
    mc = build_vision_progress_counters(_manifest_with_hex_ring(summary, **kw))[
        "milestone_counters"
    ]
    return mc["hex_subdivision_ring_hierarchy"], mc[
        "shadow_to_candidate_subdivision_transitions_total"
    ]


def test_hex_ring_available_with_clean_summary():
    block, _ = _ring_counters(_good_ring_hierarchy_summary())
    assert block["ring_hierarchy_available"] is True
    assert block["ring_hierarchy_clean"] is True
    assert block["hierarchy_ok"] is True
    assert block["ring_boundary_ok"] is True
    assert block["measurement_basis"] == "v1_ring_messaging_hierarchy_proof"
    assert block["claim_safe"] is False


def test_hex_ring_unavailable_when_absent():
    block, _ = _ring_counters(None)
    assert block["ring_hierarchy_available"] is False
    assert block["ring_hierarchy_clean"] is False
    assert block["measurement_basis"] == "manifest_hex_upgrade_flags"
    assert block["claim_safe"] is False


_RING_COMPONENT_FIELDS = [
    "hierarchy_ok",
    "ring_boundary_ok",
    "no_runtime_mutation",
    "no_invalid_boundary_delivery",
    "deterministic",
]


@pytest.mark.parametrize("field", _RING_COMPONENT_FIELDS)
def test_hex_ring_consumer_rederives_fail_closed(field):
    # Consumer RE-DERIVES ring_hierarchy_clean from the COMPONENT booleans fail-closed.
    s = _good_ring_hierarchy_summary()
    s[field] = False
    block, _ = _ring_counters(s)
    assert block["ring_hierarchy_clean"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("field", _RING_COMPONENT_FIELDS)
def test_hex_ring_inconsistent_aggregate_fails_closed(field):
    # #1274: a component False but the proof's own ok aggregate True must still render
    # ring_hierarchy_clean=False (consumer never trusts the aggregate's ok).
    s = _good_ring_hierarchy_summary()
    s[field] = False
    s["ok"] = True  # lying aggregate
    block, _ = _ring_counters(s)
    assert block["ring_hierarchy_clean"] is False, field


@pytest.mark.parametrize("bad_blockers", [1, -1, True, "x", None, 0.0])
def test_hex_ring_nonzero_or_malformed_blockers_not_clean(bad_blockers):
    s = _good_ring_hierarchy_summary()
    s["blocker_count"] = bad_blockers
    block, _ = _ring_counters(s)
    assert block["ring_hierarchy_clean"] is False, bad_blockers


def test_hex_ring_does_not_touch_shadow_to_candidate():
    _, s2c_with = _ring_counters(_good_ring_hierarchy_summary())
    _, s2c_without = _ring_counters(None)
    assert s2c_with == s2c_without
    assert s2c_with["satisfied"] is False
    assert s2c_with["current_value"] == 0


@pytest.mark.parametrize("bare_key", [
    "no_runtime_mutation", "runtime_authority_changed",
])
def test_hex_ring_subtree_excluded_from_recursive_scan(bare_key):
    # #1271: a bare authority/mutation key nested in the measurement-only
    # ring_hierarchy_summary subtree must NOT reach the recursive _nested_flag scan.
    s = _good_ring_hierarchy_summary()
    s["forged_nested"] = {
        bare_key: (False if bare_key == "no_runtime_mutation" else True)
    }
    counters = build_vision_progress_counters(_manifest_with_hex_ring(s))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["hexagonal_upgrades"]["milestones"]
    assert milestones["no_runtime_mutation"] is True, bare_key
    assert milestones["runtime_authority_changed"] is False, bare_key


def test_hex_ring_claim_safe_hardcoded_false_even_when_clean():
    block, _ = _ring_counters(_good_ring_hierarchy_summary())
    assert block["claim_safe"] is False


def test_hex_ring_self_claim_safe_refuses_to_certify():
    # rco-1/lead #1280 forge: a summary self-declaring claim_safe True (all components
    # otherwise clean) must fail closed - measurement-only never upgrades a claim.
    s = _good_ring_hierarchy_summary()
    s["claim_safe"] = True
    block, _ = _ring_counters(s)
    assert block["ring_hierarchy_available"] is False
    assert block["ring_hierarchy_clean"] is False


def test_hex_ring_path_free_false_refuses_to_certify():
    # rco-2 #1280 forge: path_free_verified=False (else clean) must gate availability +
    # clean (uniform with the chain/digest milestones).
    s = _good_ring_hierarchy_summary()
    s["path_free_verified"] = False
    block, _ = _ring_counters(s)
    assert block["ring_hierarchy_available"] is False
    assert block["ring_hierarchy_clean"] is False


@pytest.mark.parametrize("bad", ["notadict", 7, ["x"], ("a",)])
def test_hex_ring_non_mapping_summary_not_available(bad):
    # rco-2 #1280 forge: a malformed NON-dict (but truthy) summary must NOT read
    # available (present must be isinstance-Mapping, not merely truthy).
    block, _ = _ring_counters(bad)
    assert block["ring_hierarchy_available"] is False
    assert block["ring_hierarchy_clean"] is False


# --- low-risk repeat-window trend reviewer-summary counter (#1284 renderer wiring) ---
def _good_repeat_window_reviewer_summary():
    # Mirrors the merged #1284 render_repeat_window_trend_reviewer_summary output.
    return {
        "report_version": "wd.low_risk_repeat_window_trend_reviewer_summary.v1",
        "trend_present": True,
        "all_runs_ok": True,
        "deterministic": True,
        "promotion_count_stable": True,
        "evidence_present": True,
        "no_guardrail_tripped": True,
        "no_runtime_authority_granted": True,
        "no_external_writes": True,
        "window_size_valid": True,
        "promotion_count_positive": True,
        "trend_review_clean": True,
        "path_free_verified": True,
        "claim_safe": False,
        "window_size": 3,
        "promoted_solver_count_min": 1,
        "promoted_solver_count_max": 1,
    }


def _manifest_with_low_risk_reviewer(summary, *, low_risk_proof_extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if summary is not None:
        proof["repeat_window_trend_reviewer_summary"] = summary
    if low_risk_proof_extra:
        proof.update(low_risk_proof_extra)
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


def _reviewer_counters(summary, **kw):
    mc = build_vision_progress_counters(
        _manifest_with_low_risk_reviewer(summary, **kw)
    )["milestone_counters"]
    return mc["low_risk_repeat_window_trend_reviewer_summary"]


def test_low_risk_reviewer_summary_available_with_clean_summary():
    block = _reviewer_counters(_good_repeat_window_reviewer_summary())
    assert block["reviewer_summary_available"] is True
    assert block["review_clean"] is True
    assert block["path_free_verified"] is True
    assert block["measurement_basis"] == (
        "v1_low_risk_repeat_window_trend_reviewer_summary"
    )
    assert block["claim_safe"] is False


def test_low_risk_reviewer_summary_unavailable_when_absent():
    block = _reviewer_counters(None)
    assert block["reviewer_summary_available"] is False
    assert block["review_clean"] is False
    assert block["measurement_basis"] == "manifest_real_loop_flags"
    assert block["claim_safe"] is False


_RW_REVIEWER_COMPONENT_FIELDS = [
    "trend_present", "all_runs_ok", "deterministic", "promotion_count_stable",
    "evidence_present", "no_guardrail_tripped", "no_runtime_authority_granted",
    "no_external_writes", "window_size_valid", "promotion_count_positive",
]


@pytest.mark.parametrize("field", _RW_REVIEWER_COMPONENT_FIELDS)
def test_low_risk_reviewer_consumer_rederives_fail_closed(field):
    s = _good_repeat_window_reviewer_summary()
    s[field] = False
    block = _reviewer_counters(s)
    assert block["review_clean"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("field", _RW_REVIEWER_COMPONENT_FIELDS)
def test_low_risk_reviewer_inconsistent_aggregate_fails(field):
    # #1274: a component False but the summary's own trend_review_clean True must still
    # render review_clean=False (consumer never trusts the aggregate composite).
    s = _good_repeat_window_reviewer_summary()
    s[field] = False
    s["trend_review_clean"] = True
    block = _reviewer_counters(s)
    assert block["review_clean"] is False, field


def test_low_risk_reviewer_self_claim_safe_refuses_to_certify():
    s = _good_repeat_window_reviewer_summary()
    s["claim_safe"] = True
    block = _reviewer_counters(s)
    assert block["reviewer_summary_available"] is False
    assert block["review_clean"] is False


def test_low_risk_reviewer_path_free_false_refuses_to_certify():
    s = _good_repeat_window_reviewer_summary()
    s["path_free_verified"] = False
    block = _reviewer_counters(s)
    assert block["reviewer_summary_available"] is False
    assert block["review_clean"] is False


@pytest.mark.parametrize("field,value", [
    ("window_size", 1),
    ("window_size", 26),
    ("window_size", 3.0),
    ("window_size", True),
    ("promoted_solver_count_min", 0),
    ("promoted_solver_count_min", 1.0),
    ("promoted_solver_count_min", True),
    ("promoted_solver_count_max", 0),
    ("promoted_solver_count_max", 1.0),
    ("promoted_solver_count_max", True),
])
def test_low_risk_reviewer_count_window_values_rederived_fail_closed(field, value):
    s = _good_repeat_window_reviewer_summary()
    s[field] = value
    block = _reviewer_counters(s)
    assert block["review_clean"] is False, field
    assert block["claim_safe"] is False, field


def test_low_risk_reviewer_count_min_greater_than_max_fails_closed():
    s = _good_repeat_window_reviewer_summary()
    s["promoted_solver_count_min"] = 2
    s["promoted_solver_count_max"] = 1
    block = _reviewer_counters(s)
    assert block["review_clean"] is False
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bare_key", [
    "runtime_authority_changed", "external_writes_applied",
])
def test_low_risk_reviewer_subtree_excluded_from_recursive_scan(bare_key):
    # #1271: a bare authority key nested in the measurement-only
    # repeat_window_trend_reviewer_summary subtree must NOT reach the recursive
    # _nested_flag scan that feeds the real low-risk panel flags.
    s = _good_repeat_window_reviewer_summary()
    s["forged_nested"] = {bare_key: True}
    counters = build_vision_progress_counters(_manifest_with_low_risk_reviewer(s))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["low_risk_autonomy_loop"]["milestones"]
    assert milestones["runtime_authority_granted"] is False, bare_key
    assert milestones["external_writes_applied"] is False, bare_key


@pytest.mark.parametrize("authority_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_low_risk_reviewer_subtree_exclusion_positive_control(authority_key):
    # #1286 review: the recursive authority scan must keep its TEETH outside the
    # excluded reviewer-summary subtree. The SAME nested authority key (an exact scanned
    # field) trips the milestone when placed OUTSIDE repeat_window_trend_reviewer_summary
    # but must NOT trip when nested INSIDE it -> the exclusion is surgical, not a blanket
    # authority-scan disable.
    def _milestones(manifest):
        counters = build_vision_progress_counters(manifest)
        by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
        return by_id["low_risk_autonomy_loop"]["milestones"]

    # INSIDE the excluded subtree -> excluded, not tripped
    inside_summary = _good_repeat_window_reviewer_summary()
    inside_summary["forged_nested"] = {authority_key: True}
    inside_ms = _milestones(_manifest_with_low_risk_reviewer(inside_summary))
    assert inside_ms[authority_key] is False, f"inside:{authority_key}"

    # OUTSIDE the excluded subtree (elsewhere in the low-risk proof) -> teeth intact
    outside_ms = _milestones(
        _manifest_with_low_risk_reviewer(
            _good_repeat_window_reviewer_summary(),
            low_risk_proof_extra={"forged_outside": {authority_key: True}},
        )
    )
    assert outside_ms[authority_key] is True, f"outside:{authority_key}"


def test_low_risk_reviewer_claim_safe_hardcoded_false_even_when_clean():
    block = _reviewer_counters(_good_repeat_window_reviewer_summary())
    assert block["claim_safe"] is False


# --- low-risk deterministic real-loop manifest-contribution counter ---
def _good_real_loop_manifest_contribution():
    return {
        "report_version": "wd.low_risk_autogrowth_real_loop_proof.v1",
        "ok": True,
        "deterministic": True,
        "evidence_present": True,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "scheduler_enqueue": False,
        "production_flip": False,
        "production_authority_granted": False,
        "provider_calls": 0,
        "claim_safe": False,
        "measurement_basis": "v1_low_risk_real_loop_manifest_contribution",
    }


def _manifest_with_real_loop_manifest_contribution(
    contribution, *, low_risk_proof_extra=None
):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if contribution is not None:
        proof["real_loop_manifest_contribution"] = contribution
    if low_risk_proof_extra:
        proof.update(low_risk_proof_extra)
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


def _real_loop_manifest_contribution_counter(contribution, **kw):
    mc = build_vision_progress_counters(
        _manifest_with_real_loop_manifest_contribution(contribution, **kw)
    )["milestone_counters"]
    return mc["low_risk_real_loop_manifest_contribution"]


def test_real_loop_manifest_contribution_available_with_clean_view():
    block = _real_loop_manifest_contribution_counter(
        _good_real_loop_manifest_contribution()
    )
    assert block["contribution_available"] is True
    assert block["evidence_count"] == 1
    assert block["evidence_present"] is True
    assert block["deterministic"] is True
    assert block["guardrail_tripped"] is False
    assert block["provider_calls"] == 0
    assert block["production_authority_granted"] is False
    assert block["measurement_basis"] == "v1_low_risk_real_loop_manifest_contribution"
    assert block["claim_safe"] is False


def test_real_loop_manifest_contribution_unavailable_when_absent():
    block = _real_loop_manifest_contribution_counter(None)
    assert block["contribution_available"] is False
    assert block["evidence_count"] == 0
    assert block["measurement_basis"] == "manifest_real_loop_flags"
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bad", ["notadict", 7, ["x"], ("a",)])
def test_real_loop_manifest_contribution_non_mapping_not_available(bad):
    block = _real_loop_manifest_contribution_counter(bad)
    assert block["contribution_available"] is False
    assert block["evidence_count"] == 0
    assert block["claim_safe"] is False


@pytest.mark.parametrize("field,bad", [
    ("ok", False),
    ("deterministic", False),
    ("evidence_present", False),
    ("runtime_authority_granted", True),
    ("external_writes_applied", True),
    ("scheduler_enqueue", True),
    ("production_flip", True),
    ("production_authority_granted", True),
    ("provider_calls", 1),
    ("claim_safe", True),
])
def test_real_loop_manifest_contribution_rederived_fail_closed(field, bad):
    contribution = _good_real_loop_manifest_contribution()
    contribution[field] = bad
    block = _real_loop_manifest_contribution_counter(contribution)
    assert block["contribution_available"] is False, field
    assert block["evidence_count"] == 0, field
    assert block["claim_safe"] is False, field
    if field in {
        "runtime_authority_granted",
        "external_writes_applied",
        "scheduler_enqueue",
        "production_flip",
        "production_authority_granted",
        "provider_calls",
        "claim_safe",
    }:
        assert block["guardrail_tripped"] is True, field


def test_real_loop_manifest_contribution_bad_provider_calls_not_available():
    contribution = _good_real_loop_manifest_contribution()
    contribution["provider_calls"] = "0"
    block = _real_loop_manifest_contribution_counter(contribution)
    assert block["contribution_available"] is False
    assert block["provider_calls"] is None
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bare_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_real_loop_manifest_contribution_subtree_excluded_from_recursive_scan(
    bare_key,
):
    contribution = _good_real_loop_manifest_contribution()
    contribution["forged_nested"] = {bare_key: True}
    counters = build_vision_progress_counters(
        _manifest_with_real_loop_manifest_contribution(contribution)
    )
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["low_risk_autonomy_loop"]["milestones"]
    assert milestones["runtime_authority_granted"] is False, bare_key
    assert milestones["external_writes_applied"] is False, bare_key


@pytest.mark.parametrize("authority_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_real_loop_manifest_contribution_subtree_exclusion_positive_control(
    authority_key,
):
    def _milestones(manifest):
        counters = build_vision_progress_counters(manifest)
        by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
        return by_id["low_risk_autonomy_loop"]["milestones"]

    inside = _good_real_loop_manifest_contribution()
    inside["forged_nested"] = {authority_key: True}
    inside_ms = _milestones(_manifest_with_real_loop_manifest_contribution(inside))
    assert inside_ms[authority_key] is False, f"inside:{authority_key}"

    outside_ms = _milestones(
        _manifest_with_real_loop_manifest_contribution(
            _good_real_loop_manifest_contribution(),
            low_risk_proof_extra={"forged_outside": {authority_key: True}},
        )
    )
    assert outside_ms[authority_key] is True, f"outside:{authority_key}"


def test_real_loop_manifest_contribution_claim_safe_hardcoded_false_even_when_clean():
    block = _real_loop_manifest_contribution_counter(
        _good_real_loop_manifest_contribution()
    )
    assert block["claim_safe"] is False


# --- low-risk cross-consistency digest counter (this slice) ---
def _good_low_risk_cross_consistency_digest():
    # Mirrors build_low_risk_cross_consistency_digest output the manifest stores under
    # low_risk_autonomy_proof["cross_consistency_digest"].
    return {
        "report_version": "wd.low_risk_cross_consistency_digest.v1",
        "real_loop_present": True,
        "repeat_window_trend_present": True,
        "reviewer_summary_present": True,
        "all_views_present": True,
        "real_loop_clean": True,
        "trend_clean": True,
        "reviewer_clean": True,
        "reviewer_matches_trend": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _manifest_with_low_risk_xcons(digest, *, low_risk_proof_extra=None):
    proof = {
        "ok": True,
        "no_runtime_mutation": True,
        "runtime_authority_changed": False,
    }
    if digest is not None:
        proof["cross_consistency_digest"] = digest
    if low_risk_proof_extra:
        proof.update(low_risk_proof_extra)
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


def _lr_xcons_counters(digest, **kw):
    mc = build_vision_progress_counters(
        _manifest_with_low_risk_xcons(digest, **kw)
    )["milestone_counters"]
    return mc["low_risk_cross_consistency_digest"]


def test_low_risk_xcons_available_with_clean_digest():
    block = _lr_xcons_counters(_good_low_risk_cross_consistency_digest())
    assert block["digest_available"] is True
    assert block["cross_consistent"] is True
    assert block["path_free_verified"] is True
    assert block["all_views_present"] is True
    assert block["real_loop_clean"] is True
    assert block["trend_clean"] is True
    assert block["reviewer_clean"] is True
    assert block["reviewer_matches_trend"] is True
    assert block["measurement_basis"] == "v1_low_risk_cross_consistency_digest"
    assert block["claim_safe"] is False


def test_low_risk_xcons_unavailable_when_absent():
    block = _lr_xcons_counters(None)
    assert block["digest_available"] is False
    assert block["cross_consistent"] is False
    assert block["measurement_basis"] == "manifest_real_loop_flags"
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bad", ["notadict", 7, ["x"], ("a",)])
def test_low_risk_xcons_non_mapping_not_available(bad):
    # a malformed NON-dict (but truthy) digest must NOT read available (present must be
    # isinstance-Mapping, not merely truthy).
    block = _lr_xcons_counters(bad)
    assert block["digest_available"] is False
    assert block["cross_consistent"] is False


_LR_XCONS_COMPONENT_FIELDS = [
    "path_free_verified",
    "all_views_present",
    "real_loop_clean",
    "trend_clean",
    "reviewer_clean",
    "reviewer_matches_trend",
]


@pytest.mark.parametrize("field", _LR_XCONS_COMPONENT_FIELDS)
def test_low_risk_xcons_consumer_rederives_fail_closed(field):
    # Consumer RE-DERIVES cross_consistent from the COMPONENT booleans fail-closed.
    d = _good_low_risk_cross_consistency_digest()
    d[field] = False
    block = _lr_xcons_counters(d)
    if field == "path_free_verified":
        assert block["digest_available"] is False
    assert block["cross_consistent"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("field", [
    "all_views_present", "real_loop_clean", "trend_clean", "reviewer_clean",
    "reviewer_matches_trend",
])
def test_low_risk_xcons_inconsistent_aggregate_fails_closed(field):
    # #1274: a component False but the digest's own cross_consistent True must still
    # render cross_consistent=False (consumer never trusts the aggregate composite).
    d = _good_low_risk_cross_consistency_digest()
    d[field] = False
    d["cross_consistent"] = True  # lying aggregate composite
    block = _lr_xcons_counters(d)
    assert block["cross_consistent"] is False, field


def test_low_risk_xcons_self_claim_safe_refuses_to_certify():
    # A digest self-declaring claim_safe True must fail closed (measurement-only never
    # upgrades a claim).
    d = _good_low_risk_cross_consistency_digest()
    d["claim_safe"] = True
    block = _lr_xcons_counters(d)
    assert block["digest_available"] is False
    assert block["cross_consistent"] is False


def test_low_risk_xcons_path_free_false_refuses_to_certify():
    d = _good_low_risk_cross_consistency_digest()
    d["path_free_verified"] = False
    block = _lr_xcons_counters(d)
    assert block["digest_available"] is False
    assert block["cross_consistent"] is False


def test_low_risk_xcons_claim_safe_hardcoded_false_even_when_clean():
    block = _lr_xcons_counters(_good_low_risk_cross_consistency_digest())
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bare_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_low_risk_xcons_subtree_excluded_from_recursive_scan(bare_key):
    # #1271: a bare authority key nested in the measurement-only cross_consistency_digest
    # subtree must NOT reach the recursive _nested_flag scan feeding the real low-risk
    # panel flags.
    d = _good_low_risk_cross_consistency_digest()
    d["forged_nested"] = {bare_key: True}
    counters = build_vision_progress_counters(_manifest_with_low_risk_xcons(d))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["low_risk_autonomy_loop"]["milestones"]
    assert milestones["runtime_authority_granted"] is False, bare_key
    assert milestones["external_writes_applied"] is False, bare_key


@pytest.mark.parametrize("authority_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_low_risk_xcons_subtree_exclusion_positive_control(authority_key):
    # The recursive authority scan must keep its TEETH outside the excluded digest
    # subtree. The SAME nested authority key trips the milestone when placed OUTSIDE
    # cross_consistency_digest but must NOT trip when nested INSIDE it -> the exclusion is
    # surgical, not a blanket authority-scan disable.
    def _milestones(manifest):
        counters = build_vision_progress_counters(manifest)
        by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
        return by_id["low_risk_autonomy_loop"]["milestones"]

    # INSIDE the excluded subtree -> excluded, not tripped
    inside_digest = _good_low_risk_cross_consistency_digest()
    inside_digest["forged_nested"] = {authority_key: True}
    inside_ms = _milestones(_manifest_with_low_risk_xcons(inside_digest))
    assert inside_ms[authority_key] is False, f"inside:{authority_key}"

    # OUTSIDE the excluded subtree (elsewhere in the low-risk proof) -> teeth intact
    outside_ms = _milestones(
        _manifest_with_low_risk_xcons(
            _good_low_risk_cross_consistency_digest(),
            low_risk_proof_extra={"forged_outside": {authority_key: True}},
        )
    )
    assert outside_ms[authority_key] is True, f"outside:{authority_key}"


# ---------------- #1291 cross-consistency digest bridge-event TEMPLATE summary wiring

def _good_low_risk_xcons_template_summary():
    # Mirrors the curated content-safe summary the manifest stores under
    # low_risk_autonomy_proof["cross_consistency_digest_bridge_event_template"].
    return {
        "report_version": (
            "wd.low_risk_cross_consistency_digest_bridge_event_template_summary.v1"
        ),
        "template_available": True,
        "template_only": True,
        "no_runtime_authority_granted": True,
        "no_direct_bridge_write": True,
        "no_bridge_event_written": True,
        "no_approval_granted": True,
        "cross_consistent": True,
        "all_views_present": True,
        "real_loop_clean": True,
        "trend_clean": True,
        "reviewer_clean": True,
        "reviewer_matches_trend": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _manifest_with_low_risk_xcons_template(summary, *, low_risk_proof_extra=None):
    proof = {"ok": True, "no_runtime_mutation": True, "runtime_authority_changed": False}
    if summary is not None:
        proof["cross_consistency_digest_bridge_event_template"] = summary
    if low_risk_proof_extra:
        proof.update(low_risk_proof_extra)
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


def _lr_xcons_tpl_counters(summary, **kw):
    mc = build_vision_progress_counters(
        _manifest_with_low_risk_xcons_template(summary, **kw)
    )["milestone_counters"]
    return mc["low_risk_cross_consistency_digest_bridge_event_template"]


_XCONS_TPL_CLEAN_FIELDS = [
    "template_available", "template_only", "no_runtime_authority_granted",
    "no_direct_bridge_write", "no_bridge_event_written", "no_approval_granted",
    "cross_consistent", "all_views_present",
    # the per-view component verdicts the template carries (#1274: a forged composite
    # cross_consistent=True with a view verdict False must still fail closed - tools #1294).
    "real_loop_clean", "trend_clean", "reviewer_clean", "reviewer_matches_trend",
]


def test_low_risk_xcons_template_available_with_clean_summary():
    block = _lr_xcons_tpl_counters(_good_low_risk_xcons_template_summary())
    assert block["template_available"] is True
    assert block["template_clean"] is True
    assert block["cross_consistent"] is True
    assert block["path_free_verified"] is True
    assert block["measurement_basis"] == (
        "v1_low_risk_cross_consistency_digest_bridge_event_template"
    )
    assert block["claim_safe"] is False


def test_low_risk_xcons_template_unavailable_when_absent():
    block = _lr_xcons_tpl_counters(None)
    assert block["template_available"] is False
    assert block["template_clean"] is False
    assert block["claim_safe"] is False


@pytest.mark.parametrize("bad", [[], "x", 7, 0])
def test_low_risk_xcons_template_non_mapping_not_available(bad):
    block = _lr_xcons_tpl_counters(bad)
    assert block["template_available"] is False
    assert block["template_clean"] is False


@pytest.mark.parametrize("field", _XCONS_TPL_CLEAN_FIELDS)
def test_low_risk_xcons_template_consumer_rederives_fail_closed(field):
    s = _good_low_risk_xcons_template_summary()
    s[field] = False
    block = _lr_xcons_tpl_counters(s)
    assert block["template_clean"] is False, field
    assert block["claim_safe"] is False, field


@pytest.mark.parametrize("view", [
    "real_loop_clean", "trend_clean", "reviewer_clean", "reviewer_matches_trend",
])
def test_low_risk_xcons_template_inconsistent_composite_fails_closed(view):
    # tools #1294 forge: a per-view verdict False while the composite cross_consistent and
    # all_views_present stay True (a lying/inconsistent template) must NOT certify - the
    # consumer requires the underlying view verdicts, not just the composite (#1274).
    s = _good_low_risk_xcons_template_summary()
    s[view] = False
    s["cross_consistent"] = True
    s["all_views_present"] = True
    block = _lr_xcons_tpl_counters(s)
    assert block["template_clean"] is False, view


def test_low_risk_xcons_template_self_claim_safe_refuses_to_certify():
    s = _good_low_risk_xcons_template_summary()
    s["claim_safe"] = True
    block = _lr_xcons_tpl_counters(s)
    assert block["template_available"] is False
    assert block["template_clean"] is False


def test_low_risk_xcons_template_self_approval_refuses_to_certify():
    # a template self-declaring approval (no_approval_granted False) must not certify.
    s = _good_low_risk_xcons_template_summary()
    s["no_approval_granted"] = False
    assert _lr_xcons_tpl_counters(s)["template_clean"] is False


def test_low_risk_xcons_template_path_free_false_refuses_to_certify():
    s = _good_low_risk_xcons_template_summary()
    s["path_free_verified"] = False
    block = _lr_xcons_tpl_counters(s)
    assert block["template_available"] is False
    assert block["template_clean"] is False


def test_low_risk_xcons_template_claim_safe_hardcoded_false_even_when_clean():
    assert _lr_xcons_tpl_counters(
        _good_low_risk_xcons_template_summary()
    )["claim_safe"] is False


@pytest.mark.parametrize("bare_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_low_risk_xcons_template_subtree_excluded_from_recursive_scan(bare_key):
    # #1271: a bare authority key nested in the measurement-only template-summary subtree
    # must NOT reach the recursive _nested_flag scan feeding the real low-risk panel flags.
    s = _good_low_risk_xcons_template_summary()
    s["forged_nested"] = {bare_key: True}
    counters = build_vision_progress_counters(_manifest_with_low_risk_xcons_template(s))
    by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
    milestones = by_id["low_risk_autonomy_loop"]["milestones"]
    assert milestones["runtime_authority_granted"] is False, bare_key
    assert milestones["external_writes_applied"] is False, bare_key


@pytest.mark.parametrize("authority_key", [
    "runtime_authority_granted", "external_writes_applied",
])
def test_low_risk_xcons_template_subtree_exclusion_positive_control(authority_key):
    # exclusion is surgical: same key trips OUTSIDE the template subtree, not INSIDE it.
    def _milestones(manifest):
        counters = build_vision_progress_counters(manifest)
        by_id = {c["capability_id"]: c for c in counters["panel_counters"]}
        return by_id["low_risk_autonomy_loop"]["milestones"]

    inside = _good_low_risk_xcons_template_summary()
    inside["forged_nested"] = {authority_key: True}
    assert _milestones(_manifest_with_low_risk_xcons_template(inside))[authority_key] is False

    outside_ms = _milestones(
        _manifest_with_low_risk_xcons_template(
            _good_low_risk_xcons_template_summary(),
            low_risk_proof_extra={"forged_outside": {authority_key: True}},
        )
    )
    assert outside_ms[authority_key] is True, authority_key
