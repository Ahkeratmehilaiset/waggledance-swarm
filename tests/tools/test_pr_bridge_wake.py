# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.pr_bridge_wake import (
    REPORT_VERSION,
    build_pr_bridge_wake_headsafe_evidence,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "pr_bridge_wake.py"
NOW = datetime(2026, 7, 4, 17, 0, tzinfo=timezone.utc)
HEAD_REF = "codex-lead-1/phase2f-headsafe-evidence-manifest-integration-20260704"


def test_builds_schema_valid_wake_template_without_side_effect_authority() -> None:
    report = build_pr_bridge_wake_headsafe_evidence(
        pr_number=1420,
        head_ref_name=HEAD_REF,
        target_agent="codex-tools-1",
        requester_agent="codex-lead-1",
        now_utc=NOW,
    )

    assert report["ok"] is True
    assert report["report_version"] == REPORT_VERSION
    assert report["task_id"] == HEAD_REF
    assert report["task_id_matches_head_ref"] is True
    assert report["head_ref_safe"] is True
    assert report["wake_request_template_valid"] is True
    assert report["template_only"] is True
    assert report["wake_request_emitted"] is False
    assert report["bridge_event_written"] is False
    assert report["github_mutation_performed"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_authority_granted"] is False
    assert report["claim_safe"] is False
    assert report["path_free_verified"] is True

    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "wake_request"
    assert event["task_id"] == HEAD_REF
    assert event["to"] == "codex-tools-1"
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["payload"]["authority_boundary"]["bridge_event_written"] is False
    assert event["payload"]["authority_boundary"]["wake_request_emitted"] is False

    encoded = json.dumps(report, sort_keys=True)
    assert "C:\\Python" not in encoded
    assert "PRIVATE_" not in encoded
    assert "secret" not in encoded.lower()


@pytest.mark.parametrize(
    "head_ref,blocker",
    [
        ("../escape", "head_ref_path_shape_invalid"),
        ("codex\\escape", "head_ref_forbidden_token"),
        ("https://example.invalid/head", "head_ref_path_shape_invalid"),
        ("PRIVATE_MARKER_branch", "head_ref_forbidden_token"),
        ("/absolute", "head_ref_path_shape_invalid"),
        ("codex//double", "head_ref_path_shape_invalid"),
    ],
)
def test_unsafe_head_refs_fail_closed(head_ref: str, blocker: str) -> None:
    report = build_pr_bridge_wake_headsafe_evidence(
        pr_number=1420,
        head_ref_name=head_ref,
        target_agent="codex-tools-1",
        requester_agent="codex-lead-1",
        now_utc=NOW,
    )

    assert report["ok"] is False
    assert blocker in report["blockers"]
    assert "bridge_event_template" not in report
    assert report["wake_request_template_valid"] is False
    assert report["wake_request_emitted"] is False
    assert report["bridge_event_written"] is False
    assert report["github_mutation_performed"] is False
    assert report["claim_safe"] is False


def test_invalid_pr_or_agent_fails_closed() -> None:
    report = build_pr_bridge_wake_headsafe_evidence(
        pr_number=0,
        head_ref_name=HEAD_REF,
        target_agent="Codex.Tools",
        requester_agent="codex-lead-1",
        now_utc=NOW,
    )

    assert report["ok"] is False
    assert "pr_number_not_positive_int" in report["blockers"]
    assert "target_agent_invalid" in report["blockers"]
    assert report["runtime_authority_granted"] is False


def test_cli_emits_json_and_strict_failure_code() -> None:
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-number",
            "1420",
            "--head-ref-name",
            HEAD_REF,
            "--target-agent",
            "codex-tools-1",
            "--json",
            "--strict",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(proc.stdout)["ok"] is True

    failed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-number",
            "1420",
            "--head-ref-name",
            "../escape",
            "--target-agent",
            "codex-tools-1",
            "--strict",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert failed.returncode == 2
    assert json.loads(failed.stdout)["ok"] is False
