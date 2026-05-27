# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.wd_image1_capability_manifest import build_manifest
from tools.wd_image1_capability_manifest import build_hexagonal_upgrade_proof


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "wd_image1_capability_manifest.py"


def _by_id(report: dict) -> dict[str, dict]:
    return {
        capability["capability_id"]: capability
        for capability in report["capabilities"]
    }


def test_manifest_reports_six_image_capabilities() -> None:
    report = build_manifest(ROOT)
    capabilities = _by_id(report)

    assert report["schema_version"] == "wd_image1_capability_manifest.v1"
    assert set(capabilities) == {
        "hex_mesh_entry",
        "deterministic_solver_first",
        "magma_audit_log",
        "low_risk_autonomy_loop",
        "hexagonal_upgrades",
        "future_waggledance_swarm",
    }
    assert report["summary"]["capability_count"] == 6


def test_manifest_keeps_literal_image_overclaims_unsafe() -> None:
    report = build_manifest(ROOT)
    capabilities = _by_id(report)

    assert capabilities["hex_mesh_entry"]["status"] == "partial"
    assert capabilities["hex_mesh_entry"]["claim_safe"] is False
    assert "two independent" in capabilities["hex_mesh_entry"]["safe_statement"]

    assert capabilities["magma_audit_log"]["status"] == "partial"
    assert capabilities["magma_audit_log"]["claim_safe"] is False
    assert "hard append-only" in capabilities["magma_audit_log"]["safe_statement"]

    future = capabilities["future_waggledance_swarm"]
    assert future["status"] == "planned"
    assert future["claim_safe"] is False
    assert "unlimited scalability" in future["safe_statement"]


def test_manifest_evidence_paths_are_present_for_current_repo() -> None:
    report = build_manifest(ROOT)

    for capability in report["capabilities"]:
        assert capability["evidence"], capability["capability_id"]
        assert any(item["present"] for item in capability["evidence"])


def test_hexagonal_upgrade_proof_is_pure_and_delivers_messages() -> None:
    proof = build_hexagonal_upgrade_proof()

    assert proof["ok"] is True
    assert proof["no_runtime_mutation"] is True
    assert proof["plan"]["target_state"] == "subdivision_in_shadow"
    assert proof["relations"]["thermal_children"] == [
        "thermal.cooling",
        "thermal.heating",
    ]
    assert proof["relations"]["heating_siblings"] == ["thermal.cooling"]
    assert proof["relations"]["heating_ancestors"] == ["thermal"]
    assert [item["delivered"] for item in proof["deliveries"]] == [
        True,
        True,
        True,
    ]


def test_manifest_embeds_hexagonal_upgrade_proof_without_upgrading_claim() -> None:
    report = build_manifest(ROOT)
    capability = _by_id(report)["hexagonal_upgrades"]

    assert capability["status"] == "partial"
    assert capability["claim_safe"] is False
    assert capability["proof"]["ok"] is True
    assert report["summary"]["proofs_ok"] is True


def test_cli_emits_json_and_strict_claims_fails_on_unsafe_claims() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--json"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(proc.stdout)
    assert payload["summary"]["all_literal_claims_safe"] is False

    strict = subprocess.run(
        [sys.executable, str(SCRIPT), "--strict-claims"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert strict.returncode == 2
