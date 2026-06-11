# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import copy
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_memory_palace_shortcut_promotion_candidate_verification_summary import (
    SUMMARY_VERSION,
    build_memory_palace_shortcut_promotion_candidate_verification_summary,
    render_memory_palace_shortcut_promotion_candidate_verification_summary_markdown,
)
from tools.run_v12_memory_palace_shortcut_promotion_candidates import (
    build_memory_palace_shortcut_promotion_candidate_report,
)
from tools.verify_v12_memory_palace_shortcut_promotion_candidates import (
    VERIFICATION_VERSION,
    verify_memory_palace_shortcut_promotion_candidate_report,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_v12_memory_palace_shortcut_promotion_candidate_verification_summary.py"
)
FIXED_NOW = datetime(2026, 6, 11, 5, 45, tzinfo=timezone.utc)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = "C:/" + SENSITIVE_PATH_SEGMENT_FIXTURE
FORBIDDEN_INPUT_PATH = FORBIDDEN_PATH_PREFIX + "/palace.json"
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    "http://",
    "https://",
)


def test_memory_palace_shortcut_promotion_verification_summary_is_context_only() -> None:
    summary = build_memory_palace_shortcut_promotion_candidate_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="memory-palace-promotion-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-11T05:45:00Z"
    assert summary["template_only"] is True
    assert summary["read_side_report_only"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["promotion_action_allowed"] is False
    assert summary["promotion_performed"] is False
    assert summary["runtime_route_changed"] is False
    assert summary["scheduler_enqueue_performed"] is False
    assert summary["bridge_append_performed"] is False
    assert summary["gate_skip_performed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    verification = summary[
        "memory_palace_shortcut_promotion_candidate_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_report_version_check"] == "match"
    assert verification["source_claim_label_check"] == "match"
    assert verification["candidate_count_checked"] == 2
    assert verification["promotion_observable_count_checked"] == 2
    assert verification["authority_boundary_check"] == "match"
    assert verification["guardrail_check"] == "match"
    assert verification["candidate_action_boundary_check"] == "match"
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True


def test_memory_palace_shortcut_promotion_verification_summary_markdown_is_path_free() -> None:
    summary = build_memory_palace_shortcut_promotion_candidate_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="memory-palace-promotion-summary-review",
        now_utc=FIXED_NOW,
    )

    markdown = render_memory_palace_shortcut_promotion_candidate_verification_summary_markdown(
        summary
    )

    assert "Promotion action allowed: `false`" in markdown
    assert "Scheduler enqueue performed: `false`" in markdown
    assert "Bridge append performed: `false`" in markdown
    assert "change runtime routes" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_shortcut_promotion_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_bytes(_json_bytes(_verification_report()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "memory-palace-promotion-summary-review",
            "--now",
            "2026-06-11T05:45:00Z",
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
    assert payload["promotion_action_allowed"] is False
    assert payload["runtime_route_changed"] is False
    assert payload["bridge_append_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_shortcut_promotion_verification_summary_rejects_action_authority() -> None:
    verification_report = _verification_report()
    verification_report["promotion_action_allowed"] = True

    summary = build_memory_palace_shortcut_promotion_candidate_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="memory-palace-promotion-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_promotion_action_allowed_not_false" in summary[
        "blockers"
    ]
    assert summary["promotion_action_allowed"] is False
    assert summary["operator_boundary"]["promotion_action_allowed"] is False


def test_memory_palace_shortcut_promotion_verification_summary_rejects_nested_authority() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {"runtime_route_changed": True}

    summary = build_memory_palace_shortcut_promotion_candidate_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="memory-palace-promotion-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_nested_authority_field_not_false:"
        "runtime_route_changed"
    ) in summary["blockers"]
    assert summary["runtime_route_changed"] is False
    assert summary["runtime_authority_granted"] is False


def test_cli_rejects_forbidden_marker_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    report = _verification_report()
    report["nested"] = {"source_path": FORBIDDEN_INPUT_PATH}
    verification_path.write_bytes(_json_bytes(report))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "memory-palace-promotion-summary-review",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_shortcut_promotion_candidate_"
        "verification_summary_failed:verification_report_forbidden_marker"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_shortcut_promotion_verification_summary_cli_rejects_duplicate_keys(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_text(
        '{"verification_version":"x","verification_version":"y"}',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "memory-palace-promotion-summary-review",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_shortcut_promotion_candidate_"
        "verification_summary_failed:verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_summary_does_not_echo_unsafe_blocker_values() -> None:
    verification_report = _verification_report()
    unsafe_blocker = "unsafe blocker token with spaces"
    verification_report["blockers"] = [unsafe_blocker]

    summary = build_memory_palace_shortcut_promotion_candidate_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="memory-palace-promotion-summary-review",
        now_utc=FIXED_NOW,
    )
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is False
    assert "verification_report_blockers_item_unsafe" in summary["blockers"]
    assert unsafe_blocker not in encoded


def _verification_report() -> dict[str, object]:
    report = build_memory_palace_shortcut_promotion_candidate_report(
        now_utc=FIXED_NOW,
    )
    return copy.deepcopy(
        verify_memory_palace_shortcut_promotion_candidate_report(report)
    )


def _json_bytes(payload: object) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False).encode(
        "utf-8"
    )
