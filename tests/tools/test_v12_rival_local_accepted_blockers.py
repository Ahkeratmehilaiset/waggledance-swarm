# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from tools.run_v12_rival_local_accepted_blockers import (
    build_report,
    build_report_from_matrix,
    main,
)


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_DIR = ROOT / "docs" / "benchmarks" / "rival_local_checks"
FIXED_NOW = datetime(2026, 5, 26, 1, 40, tzinfo=timezone.utc)


def test_repository_evidence_accepts_three_explicit_blockers() -> None:
    report = build_report(evidence_dir=EVIDENCE_DIR, now_utc=FIXED_NOW)

    assert report["ok"] is True
    assert report["passed_count"] == 1
    assert report["blocked_count"] == 3
    assert report["accepted_blocker_count"] == 3
    assert report["consensus_grade"] is False
    assert report["blockers"] == []
    assert {
        row["rival"]: row["accepted_blocker_reason"]
        for row in report["accepted_blockers"]
    } == {
        "JamJet": "policy_audit_or_replay_smoke_not_run",
        "Asqav": "cloud_dependent_headline_receipt",
        "Preloop": "mcp_allow_deny_approval_smoke_not_run",
    }
    for row in report["accepted_blockers"]:
        assert row["accepted_blocker"] is True
        assert row["consensus_grade_contribution"] is False
        assert row["artifact_digest_verified"] is True
        assert row["local_artifact_path"].startswith("artifacts/")
        assert row["blocker_detail"]


def test_report_fails_closed_if_blocked_row_contributes_to_consensus() -> None:
    report = build_report(evidence_dir=EVIDENCE_DIR, now_utc=FIXED_NOW)
    matrix_like = {
        "report_version": report["matrix_report_version"],
        "generated_at_utc": report["matrix_generated_at_utc"],
        "rival_local_checks_status": report["rival_local_checks_status"],
        "required_count": 4,
        "passed_count": 1,
        "blocked_count": 3,
        "consensus_grade": False,
        "checks": [
            {
                "rival": row["rival"],
                "local_status": row["local_status"],
                "blocked_artifact_reason": row["blocked_artifact_reason"],
                "blocker": row["blocker"],
                "consensus_grade_contribution": (
                    True if row["rival"] == "JamJet" else False
                ),
                "artifact_proof": {
                    "artifact_digest_verified": row["artifact_digest_verified"],
                    "local_artifact_path": str(EVIDENCE_DIR / row["local_artifact_path"]),
                    "local_artifact_sha256": row["local_artifact_sha256"],
                },
            }
            for row in report["accepted_blockers"]
        ],
    }

    mutated = build_report_from_matrix(
        matrix_like,
        evidence_dir=EVIDENCE_DIR,
        generated_at_utc=FIXED_NOW,
    )

    assert mutated["ok"] is False
    assert "JamJet:blocked_row_contributes_to_consensus_grade" in (
        mutated["blockers"]
    )


def test_cli_writes_accepted_blockers_report(tmp_path: Path) -> None:
    output = tmp_path / "rival_local_accepted_blockers.json"

    rc = main([
        "--evidence-dir",
        str(EVIDENCE_DIR),
        "--now",
        "2026-05-26T01:40:00Z",
        "--output",
        str(output),
    ])

    assert rc == 0
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["ok"] is True
    assert loaded["accepted_blocker_count"] == 3
    assert loaded["consensus_grade"] is False
