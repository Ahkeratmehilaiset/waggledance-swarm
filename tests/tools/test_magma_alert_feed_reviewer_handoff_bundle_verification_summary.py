import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_handoff_bundle_index import (
    BUNDLE_INDEX_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_verification_summary import (
    SUMMARY_VERSION,
    build_magma_alert_feed_reviewer_handoff_bundle_verification_summary,
    render_bundle_verification_summary_markdown,
)
from tools.verify_magma_alert_feed_reviewer_handoff_bundle_index import (
    VERIFICATION_VERSION,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_magma_alert_feed_reviewer_handoff_bundle_verification_summary.py"
)
COMMIT_SHA = "8" * 40
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_reviewer_handoff_bundle_verification_summary_renders_verifier_result_without_authority() -> None:
    summary = build_magma_alert_feed_reviewer_handoff_bundle_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:bundle-verification",
        now_utc=datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-05-28T08:00:00Z"
    assert summary["release_ref"] == "pr:760"
    assert summary["commit_sha"] == COMMIT_SHA
    assert summary["ci_run_ref"] == "gh:run:bundle-verification"
    assert summary["bundle_verification"]["verification_ok"] is True
    assert set(summary["bundle_verification"]["digest_checks"].values()) == {
        "match"
    }
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert summary["blockers"] == []

    markdown = render_bundle_verification_summary_markdown(summary)
    assert "does not approve, merge, promote" in markdown
    assert not any(marker in markdown for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_bundle_verification_summary_propagates_verifier_blockers_without_approval() -> None:
    report = _verification_report()
    report["ok"] = False
    report["digest_checks"]["reviewer_handoff_summary"] = "mismatch"
    report["blockers"] = ["digest_mismatch:reviewer_handoff_summary"]

    summary = build_magma_alert_feed_reviewer_handoff_bundle_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:bundle-verification",
        now_utc=datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is False
    assert summary["bundle_verification"]["verification_ok"] is False
    assert "digest_mismatch:reviewer_handoff_summary" in summary["blockers"]
    assert summary["bundle_verification"]["digest_checks"][
        "reviewer_handoff_summary"
    ] == "mismatch"
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["transport_added"] is False


def test_reviewer_handoff_bundle_verification_summary_blocks_authority_flags() -> None:
    report = _verification_report()
    report["transport_added"] = True

    summary = build_magma_alert_feed_reviewer_handoff_bundle_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:bundle-verification",
        now_utc=datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is False
    assert "verification_report_transport_added_not_false" in summary["blockers"]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["transport_added"] is False
    assert summary["approval_granted"] is False


def test_reviewer_handoff_bundle_verification_summary_blocks_invalid_contract_fields() -> None:
    cases = (
        (
            "missing_release_ref",
            lambda report: report.pop("release_ref", None),
            "verification_report_release_ref_invalid",
        ),
        (
            "invalid_release_ref",
            lambda report: report.__setitem__("release_ref", "bad ref"),
            "verification_report_release_ref_invalid",
        ),
        (
            "missing_commit_sha",
            lambda report: report.pop("commit_sha", None),
            "verification_report_commit_sha_invalid",
        ),
        (
            "invalid_commit_sha",
            lambda report: report.__setitem__("commit_sha", "not-a-sha"),
            "verification_report_commit_sha_invalid",
        ),
        (
            "missing_ci_run_ref",
            lambda report: report.pop("ci_run_ref", None),
            "verification_report_ci_run_ref_invalid",
        ),
        (
            "missing_verification_version",
            lambda report: report.pop("verification_version", None),
            "verification_report_verification_version_mismatch",
        ),
        (
            "missing_bundle_index_version",
            lambda report: report.pop("bundle_index_version", None),
            "verification_report_bundle_index_version_mismatch",
        ),
    )

    for label, mutate, expected_blocker in cases:
        report = _verification_report()
        mutate(report)

        summary = build_magma_alert_feed_reviewer_handoff_bundle_verification_summary(
            verification_report=report,
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:bundle-verification",
            now_utc=datetime(2026, 5, 28, 8, 0, tzinfo=timezone.utc),
        )

        assert summary["ok"] is False, label
        assert expected_blocker in summary["blockers"], label
        assert expected_blocker in summary["operator_boundary"]["boundary_blockers"]
        assert summary["approval_granted"] is False
        assert summary["release_decision_made"] is False


def test_reviewer_handoff_bundle_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "bundle_verification_report.json"
    verification_path.write_text(
        json.dumps(_verification_report(), sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:bundle-verification",
            "--now",
            "2026-05-28T08:00:00Z",
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
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_bundle_verification_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            "C:/private/bundle_verification_report.json",
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:bundle-verification",
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
        "handoff_bundle_verification_summary_failed:verification_report_unreadable"
    ]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    assert "bundle_verification_report" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": VERIFICATION_VERSION,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "release_ref": "pr:760",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:bundle-verification",
        "artifact_count_checked": 4,
        "digest_checks": _checks("match"),
        "size_checks": _checks("match"),
        "schema_version_checks": _checks("match"),
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": [],
    }


def _checks(status: str) -> dict[str, str]:
    return {
        artifact_id: status
        for artifact_id in (
            "release_evidence_package",
            "validator_report",
            "reviewer_handoff_summary",
            "bridge_event_template",
        )
    }
