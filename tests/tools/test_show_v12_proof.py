# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/show_v12_proof.py."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "show_v12_proof.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_default_text_output_includes_expected_sections() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    out = result.stdout
    for marker in (
        "WaggleDance V12 substrate proof",
        "AUTHORITY-RECEIPT ADOPTION",
        "ADVERSARIAL CORPUS",
        "A3 COUNTERFACTUAL AXIS",
        "A4 SOLVER-GROWTH AXIS",
        "GOVERNANCE THROUGHPUT",
        "COMPETITOR-AXIS PILOT",
        "SUBSTRATE VELOCITY",
        "VERIFICATION",
    ):
        assert marker in out, f"missing section: {marker}"


def test_json_output_is_parseable_and_has_expected_keys() -> None:
    result = _run("--json")

    assert result.returncode in {0, 1}, result.stderr
    payload = json.loads(result.stdout)
    assert payload["report_version"] == "waggledance.v12_substrate_proof.v0"
    assert "adoption" in payload
    assert "adversarial_eval" in payload
    assert "a3_counterfactual_axis" in payload
    assert "a4_solver_growth_axis" in payload
    assert "governance_throughput" in payload
    assert "competitor_pilot" in payload
    assert "substrate_velocity" in payload
    assert "generated_at_utc" in payload


def test_competitor_pilot_section_resolves_axes_from_doc() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    pilot = payload["competitor_pilot"]

    assert pilot["available"] is True, pilot
    assert pilot["bridge_consensus_sealed"] is True
    assert any("A3" in axis for axis in pilot["must_win_axes"]), pilot["must_win_axes"]
    assert any("A4" in axis for axis in pilot["must_win_axes"]), pilot["must_win_axes"]
    assert pilot["rivals"], "expected at least one rival"


def test_adoption_high_gap_count_is_zero_on_current_main() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    adoption = payload["adoption"]

    assert adoption["available"] is True, adoption
    assert adoption["high_criticality_gap_count"] == 0, adoption
    assert adoption["action_required_gap_count"] == 0, adoption
    assert adoption["accepted_exception_count"] == 1, adoption
    medium_paths = adoption["medium_gap_targets"]
    assert medium_paths == [
        {
            "accepted_exception": "accepted_observability_path",
            "label": "Autonomy runtime MAGMA append path",
            "path": "waggledance/core/autonomy/runtime.py",
            "status": "magma_event_only",
        }
    ]


def test_text_output_does_not_overstate_medium_non_receipt_paths() -> None:
    result = _run()

    assert result.returncode in {0, 1}, result.stderr
    assert "medium non-receipt paths" in result.stdout
    assert "accepted_observability_path" in result.stdout
    assert "medium accepted-exception paths" not in result.stdout


def test_adversarial_corpus_section_reports_fifteen_cases() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    adv = payload["adversarial_eval"]

    assert adv["available"] is True, adv
    assert adv["case_count"] == 15
    assert adv["pass_count"] == 15
    assert adv["fail_count"] == 0
    assert adv["ok"] is True


def test_a3_counterfactual_axis_section_reports_measured_partial() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    a3 = payload["a3_counterfactual_axis"]

    assert a3["available"] is True, a3
    assert a3["counterfactual_delta_proven"] is True, a3
    assert a3["claim_label"] == "MEASURED_LOCAL_PARTIAL"
    assert a3["delta"]["kind"] == ["KEEP_WIP", "CLOSE_OK"]
    assert a3["delta"]["actual_gate"] == ["review", "allow"]
    assert a3["receipt_chain_verified"] is False


def test_a4_solver_growth_axis_section_reports_measured_synthetic() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    a4 = payload["a4_solver_growth_axis"]

    assert a4["available"] is True, a4
    assert a4["solver_growth_proven"] is True, a4
    assert a4["claim_label"] == "MEASURED_LOCAL_SYNTHETIC"
    assert a4["registration"]["registered_solver_count"] == 6
    assert a4["registration"]["rejected_registration_count"] == 8
    assert a4["dispatch"]["dispatch_success_count"] == 18
    assert a4["dispatch"]["dispatch_case_count"] == 18
    assert a4["dispatch"]["dispatch_failure_count"] == 0
    assert a4["dispatch"]["families_covered"] == 6
    assert a4["receipt_chain_verified"] is False


def test_governance_throughput_section_reports_status_counts() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)
    gov = payload["governance_throughput"]

    assert gov["available"] is True, gov
    assert gov["metric_count"] == 8
    assert isinstance(gov["event_count_in_window"], int)
    assert gov["event_count_in_window"] >= 0
    assert isinstance(gov["task_count_in_window"], int)
    assert gov["task_count_in_window"] >= 0
    assert isinstance(gov["status_counts"], dict)
    assert sum(gov["status_counts"].values()) == gov["metric_count"]


def test_substrate_velocity_returns_a_non_negative_count() -> None:
    result = _run("--json", "--since-utc-days", "1")
    payload = json.loads(result.stdout)
    velocity = payload["substrate_velocity"]

    assert velocity["available"] is True, velocity
    assert isinstance(velocity["merged_commits"], int)
    assert velocity["merged_commits"] >= 0
    assert isinstance(velocity["pr_numbers"], list)


def test_overall_ok_flag_is_true_on_current_main() -> None:
    result = _run("--json")
    payload = json.loads(result.stdout)

    assert payload["ok"] is True, payload
