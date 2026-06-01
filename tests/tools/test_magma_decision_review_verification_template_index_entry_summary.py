import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_decision_review_verification_template_index_entry import (
    INDEX_ENTRY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary import (
    SUMMARY_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary,
    render_magma_decision_review_verification_template_index_entry_summary_markdown,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (
    VERIFICATION_VERSION,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_magma_decision_review_verification_template_index_entry_summary.py"
)
COMMIT_SHA = "c" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_renders_verifier_result_without_authority() -> None:
    summary = build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
        now_utc=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-05-29T06:00:00Z"
    assert summary["release_ref"] == "pr:769"
    assert summary["commit_sha"] == COMMIT_SHA
    assert (
        summary["ci_run_ref"]
        == "gh:run:decision-reference-review-template-index-summary"
    )
    verification = summary[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    reference = summary["operator_decision_reference_review"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_verified"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
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

    markdown = render_magma_decision_review_verification_template_index_entry_summary_markdown(
        summary
    )
    assert "does not approve, merge, promote" in markdown
    assert "operator decision must be recorded separately" in markdown
    assert not any(marker in markdown for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_propagates_verifier_blockers_without_approval() -> None:
    report = _verification_report()
    report["ok"] = False
    report["rebuilt_index_entry_check"] = "mismatch"
    report["blockers"] = ["rebuilt_index_entry_mismatch"]

    summary = build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
        now_utc=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is False
    verification = summary[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is False
    assert verification["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in summary["blockers"]
    assert (
        "verification_report_rebuilt_index_entry_check_not_match"
        in summary["blockers"]
    )
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["transport_added"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_blocks_authority_flags() -> None:
    report = _verification_report()
    report["direct_bridge_write_performed"] = True

    summary = build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
        now_utc=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
    )

    assert summary["ok"] is False
    assert (
        "verification_report_direct_bridge_write_performed_not_false"
        in summary["blockers"]
    )
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["approval_granted"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_blocks_invalid_contract_fields() -> None:
    cases = (
        (
            "missing_release_ref",
            lambda report: report.pop("release_ref", None),
            "verification_report_release_ref_invalid",
        ),
        (
            "invalid_commit_sha",
            lambda report: report.__setitem__("commit_sha", "not-a-sha"),
            "verification_report_commit_sha_invalid",
        ),
        (
            "missing_verification_version",
            lambda report: report.pop("verification_version", None),
            "verification_report_verification_version_mismatch",
        ),
        (
            "missing_index_entry_version",
            lambda report: report.pop("index_entry_version", None),
            "verification_report_index_entry_version_mismatch",
        ),
        (
            "source_contract_mismatch",
            lambda report: report.__setitem__("source_contract_check", "mismatch"),
            "verification_report_source_contract_check_not_match",
        ),
        (
            "rebuilt_index_entry_mismatch",
            lambda report: report.__setitem__(
                "rebuilt_index_entry_check", "mismatch"
            ),
            "verification_report_rebuilt_index_entry_check_not_match",
        ),
        (
            "bridge_event_schema_mismatch",
            lambda report: report.__setitem__(
                "bridge_event_schema_check", "mismatch"
            ),
            "verification_report_bridge_event_schema_check_not_match",
        ),
        (
            "artifact_count_checked_mismatch",
            lambda report: report.__setitem__("artifact_count_checked", 1),
            "verification_report_artifact_count_checked_mismatch",
        ),
        (
            "template_only_false",
            lambda report: report.__setitem__("template_only", False),
            "verification_report_template_only_not_true",
        ),
        (
            "report_not_ok",
            lambda report: report.__setitem__("ok", False),
            "verification_report_not_ok",
        ),
        (
            "manual_review_required_false",
            lambda report: report.__setitem__("manual_review_required", False),
            "verification_report_manual_review_required_not_true",
        ),
    )

    for label, mutate, expected_blocker in cases:
        report = _verification_report()
        mutate(report)

        summary = build_magma_decision_review_verification_template_index_entry_summary(
            verification_report=report,
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
            now_utc=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
        )

        assert summary["ok"] is False, label
        assert expected_blocker in summary["blockers"], label
        assert expected_blocker in summary["operator_boundary"]["boundary_blockers"]
        assert summary["approval_granted"] is False
        assert summary["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_blocks_nonmatching_checks() -> None:
    cases = (
        (
            "digest_checks",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
            "mismatch",
            "verification_check_not_match:digest_checks:operator_decision_reference_review_bundle_verification_bridge_event_template",
        ),
        (
            "size_checks",
            "operator_decision_reference_review_bundle_verification_summary",
            "missing_index_record",
            "verification_check_not_match:size_checks:operator_decision_reference_review_bundle_verification_summary",
        ),
        (
            "schema_version_checks",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
            "unknown",
            "verification_check_not_match:schema_version_checks:operator_decision_reference_review_bundle_verification_bridge_event_template",
        ),
    )

    for check_name, artifact_id, status, expected_blocker in cases:
        report = _verification_report()
        report[check_name][artifact_id] = status

        summary = build_magma_decision_review_verification_template_index_entry_summary(
            verification_report=report,
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
            now_utc=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
        )

        assert summary["ok"] is False, check_name
        assert expected_blocker in summary["blockers"], check_name
        assert expected_blocker in summary["operator_boundary"]["boundary_blockers"]
        assert summary["approval_granted"] is False
        assert summary["transport_added"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_blocks_invalid_decision_reference() -> None:
    cases = (
        (
            "missing_reference",
            lambda report: report.pop("operator_decision_reference_review", None),
            "operator_decision_reference_missing",
        ),
        (
            "unsafe_reference",
            lambda report: report[
                "operator_decision_reference_review"
            ].__setitem__(
                "decision_reference",
                "bad ref",
            ),
            "operator_decision_reference_invalid",
        ),
        (
            "reference_mismatch",
            lambda report: report[
                "operator_decision_reference_review"
            ].__setitem__(
                "expected_decision_reference",
                "bridge:operator-decision:other-review",
            ),
            "operator_decision_reference_mismatch",
        ),
        (
            "reference_is_approval",
            lambda report: report[
                "operator_decision_reference_review"
            ].__setitem__(
                "decision_reference_is_approval",
                True,
            ),
            "operator_decision_reference_decision_reference_is_approval_not_false",
        ),
        (
            "reference_not_verified",
            lambda report: report[
                "operator_decision_reference_review"
            ].__setitem__(
                "decision_reference_verified",
                False,
            ),
            "operator_decision_reference_not_verified",
        ),
    )

    for label, mutate, expected_blocker in cases:
        report = _verification_report()
        mutate(report)

        summary = build_magma_decision_review_verification_template_index_entry_summary(
            verification_report=report,
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
            now_utc=datetime(2026, 5, 29, 6, 0, tzinfo=timezone.utc),
        )

        assert summary["ok"] is False, label
        assert expected_blocker in summary["blockers"], label
        assert summary["approval_granted"] is False
        assert summary["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "template_index_entry_verification_report.json"
    verification_path.write_text(
        json.dumps(_verification_report(), sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-verification-json",
            str(verification_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review-template-index-verifier",
            "--now",
            "2026-05-29T06:00:00Z",
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


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-verification-json",
            "C:/private/template_index_entry_verification_report.json",
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review-template-index-verifier",
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
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_failed:"
        "verification_report_unreadable"
    ]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    assert "template_index_entry_verification_report" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "template_index_entry_verification_report.json"
    report = _verification_report()
    report["warnings"] = [float("nan")]
    verification_path.write_text(
        json.dumps(report, sort_keys=True),
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
            "bridge:handoff:decision-reference-review-template-index-verifier",
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
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_failed:"
        "verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def _verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "release_ref": "pr:769",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-template-index-summary",
        "operator_decision_reference_review": {
            "decision_reference": DECISION_REF,
            "expected_decision_reference": DECISION_REF,
            "decision_reference_verified": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "artifact_count_checked": 2,
        "digest_checks": _checks("match"),
        "size_checks": _checks("match"),
        "schema_version_checks": _checks("match"),
        "source_contract_check": "match",
        "rebuilt_index_entry_check": "match",
        "bridge_event_schema_check": "match",
        "template_only": True,
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
            "operator_decision_reference_review_bundle_verification_summary",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
        )
    }
