from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_hex_cell_activation_preflight_proof import (
    AXIS_ID,
    CHAIN_ID,
    CLAIM_LABEL,
    REPORT_VERSION,
    build_hex_cell_activation_preflight_proof,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_hex_cell_activation_preflight_proof.py"
FIXED_NOW = datetime(2026, 5, 31, 12, 30, tzinfo=timezone.utc)


def test_proof_builds_receipt_bound_preflight_without_authority(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "hexcell-preflight"

    report = build_hex_cell_activation_preflight_proof(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert report["chain_id"] == CHAIN_ID

    authorization = report["operator_gate_authorization"]
    preflight = report["activation_preflight"]
    assert authorization["operator_gate_cleared"] is True
    assert authorization["runtime_authority_granted"] is False
    assert preflight["receipt_bound_activation_verified"] is True
    assert preflight["operator_gate_cleared"] is True
    assert preflight["runtime_authority_granted"] is False
    assert preflight["runtime_traffic_mutation_applied"] is False
    assert preflight["candidate_state_mutation_applied"] is False
    assert preflight["required_next_gate"] == (
        "runtime_authority_activation_commit"
    )

    forge_probes = report["forge_probes"]
    assert set(forge_probes) == {
        "candidate_mismatch",
        "transition_drift",
        "evaluation_digest_drift",
        "pregranted_runtime_authority",
    }
    assert all(item["rejected"] is True for item in forge_probes.values())

    guardrails = report["no_overclaim_guardrails"]
    assert (
        guardrails[
            "operator_gate_cleared_but_runtime_authority_not_granted"
        ]
        is True
    )
    assert guardrails["no_runtime_traffic_mutation"] is True
    assert guardrails["no_candidate_state_mutation"] is True
    assert guardrails["next_gate_is_separate_runtime_commit"] is True
    assert guardrails["claim_label_remains_preflight"] is True

    proof_path = Path(report["proof_path"])
    assert proof_path.exists()
    saved = json.loads(proof_path.read_text(encoding="utf-8"))
    assert saved["ok"] is True


def test_cli_json_reports_preflight(tmp_path: Path) -> None:
    out_dir = tmp_path / "hexcell-preflight"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-05-31T12:30:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["axis_id"] == AXIS_ID
    assert payload["activation_preflight"]["runtime_authority_granted"] is False
    assert payload["activation_preflight"][
        "receipt_bound_activation_verified"
    ] is True


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "hexcell-preflight"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr


def test_cli_rejects_non_utc_now(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(tmp_path / "hexcell-preflight"),
            "--now",
            "2026-05-31T15:30:00+03:00",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "--now requires a UTC timestamp" in result.stderr
