# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.run_low_risk_autogrowth_chain_dry_run import (
    AXIS_ID,
    CLAIM_LABEL,
    REPORT_FILENAME,
    REPORT_VERSION,
    build_low_risk_autogrowth_chain_dry_run,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "run_low_risk_autogrowth_chain_dry_run.py"
FIXED_NOW = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_build_dry_run_report_writes_local_chain_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "dry-run"

    report = build_low_risk_autogrowth_chain_dry_run(
        out_dir=out_dir,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["generated_at_utc"] == "2026-06-07T12:00:00Z"
    assert report["axis_id"] == AXIS_ID
    assert report["claim_label"] == CLAIM_LABEL
    assert report["local_artifacts_written"] is True
    assert report["chain"]["detector_signals_recorded"] == 1
    assert report["chain"]["persisted_runtime_gap_signals"] == 1
    assert report["chain"]["intents_created"] == 1
    assert report["chain"]["intents_enqueued"] == 1
    assert report["chain"]["queued_before_tick"] == 1
    assert report["chain"]["queued_after_tick"] == 0
    assert report["chain"]["completed_queue_rows"] == 1
    assert report["chain"]["scheduler_outcome"] == "auto_promoted"
    assert report["chain"]["auto_promoted_solver_count"] == 1
    assert report["chain"]["auto_promoted_run_count"] == 1
    assert report["chain"]["growth_events"] == {
        "signal_recorded": 1,
        "intent_created": 1,
        "intent_enqueued": 1,
        "solver_auto_promoted": 1,
    }
    assert report["dispatch"]["matched"] is True
    assert report["dispatch"]["reason"] == "hit"
    assert report["dispatch"]["output"] == pytest.approx(298.15)
    assert report["control_plane"]["scope"] == "new_local_control_plane"
    assert report["control_plane"]["table_counts"]["provider_jobs"] == 0
    assert report["control_plane"]["table_counts"]["builder_jobs"] == 0
    assert report["authority_boundary"] == {
        "external_writes_applied": False,
        "production_control_plane_touched": False,
        "production_scheduler_enqueue": False,
        "provider_jobs_created": False,
        "builder_jobs_created": False,
        "gate_skip_authority": False,
        "operator_gate_bypassed": False,
        "runtime_authority_granted": False,
        "fast_track_priority": False,
    }
    assert report["no_overclaim_guardrails"] == {
        "not_a_competitor_benchmark": True,
        "not_production_autogrowth_authority": True,
        "claim_label_remains_dry_run": True,
        "no_release_boundary_change": True,
        "uses_existing_low_risk_allowlist": True,
    }
    assert (out_dir / REPORT_FILENAME).exists()
    assert (out_dir / "control_plane.sqlite").exists()


def test_render_markdown_carries_dry_run_scope(tmp_path: Path) -> None:
    report = build_low_risk_autogrowth_chain_dry_run(
        out_dir=tmp_path / "dry-run",
        now_utc=FIXED_NOW,
    )

    markdown = render_markdown(report)

    assert "Low-Risk Autogrowth Chain Dry Run" in markdown
    assert "ok: `true`" in markdown
    assert "scheduler outcome | auto_promoted" in markdown
    assert "does not touch the production control" in markdown
    assert "does not skip gates" in markdown
    assert "does not grant runtime authority" in markdown


def test_cli_json_reports_dry_run(tmp_path: Path) -> None:
    out_dir = tmp_path / "dry-run-cli"

    result = _run(
        "--json",
        "--out-dir",
        str(out_dir),
        "--now",
        "2026-06-07T12:00:00Z",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["report_version"] == REPORT_VERSION
    assert payload["claim_label"] == CLAIM_LABEL
    assert payload["chain"]["scheduler_outcome"] == "auto_promoted"
    assert payload["dispatch"]["matched"] is True
    assert payload["authority_boundary"]["external_writes_applied"] is False
    assert payload["authority_boundary"]["gate_skip_authority"] is False
    assert (out_dir / REPORT_FILENAME).exists()


def test_cli_without_out_dir_uses_temporary_control_plane() -> None:
    result = _run("--json", "--now", "2026-06-07T12:00:00Z")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["control_plane"]["db_path"] == "<temporary>"
    assert payload["local_artifacts_written"] is False
    assert payload["report_path"] is None


def test_cli_refuses_existing_out_dir(tmp_path: Path) -> None:
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    result = _run("--out-dir", str(out_dir))

    assert result.returncode == 1
    assert "out_dir must not exist" in result.stderr
