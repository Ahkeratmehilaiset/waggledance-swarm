import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (
    TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary import (
    SUMMARY_VERSION,
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary,
    render_operator_decision_reference_review_summary_markdown,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_verification_summary import (
    SUMMARY_VERSION as VERIFICATION_SUMMARY_VERSION,
)
from tools.validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference import (
    VALIDATION_VERSION,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_"
        "reference_review_summary.py"
    )
)
COMMIT_SHA = "7" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_review_summary_renders_validation_without_approval() -> None:
    summary = (
        build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
            decision_validation_report=_validation_report(),
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:decision-reference-review",
            now_utc=datetime(2026, 5, 28, 23, 20, tzinfo=timezone.utc),
        )
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-05-28T23:20:00Z"
    assert summary["release_ref"] == "pr:762"
    assert summary["commit_sha"] == COMMIT_SHA
    assert summary["ci_run_ref"] == "gh:run:decision-reference-review"
    review = summary["operator_decision_reference_review"]
    assert review["decision_reference"] == DECISION_REF
    assert review["expected_decision_reference"] == DECISION_REF
    assert review["decision_reference_validated"] is True
    assert review["decision_reference_matches_expected"] is True
    assert review["decision_reference_is_approval"] is False
    assert review["decision_reference_is_release_decision"] is False
    assert review["decision_must_be_recorded_separately"] is True
    assert summary["bundle_verification"]["identity_match"] is True
    assert summary["operator_boundary"]["decision_validation_boundary_ok"] is True
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

    markdown = render_operator_decision_reference_review_summary_markdown(summary)
    assert "does not approve, merge, promote" in markdown
    assert "operator decision must be recorded separately" in markdown.lower()
    assert not any(marker in markdown for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_summary_propagates_validator_blockers_without_approval() -> None:
    report = _validation_report()
    report["ok"] = False
    report["blockers"] = ["operator_decision_reference_mismatch"]
    report["operator_decision_reference"]["decision_reference_validated"] = False

    summary = (
        build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
            decision_validation_report=report,
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:decision-reference-review",
            now_utc=datetime(2026, 5, 28, 23, 20, tzinfo=timezone.utc),
        )
    )

    assert summary["ok"] is False
    assert "operator_decision_reference_mismatch" in summary["blockers"]
    assert "decision_reference_validation_not_ok" in summary["blockers"]
    assert "operator_decision_reference_not_validated" in summary["blockers"]
    assert summary["operator_decision_reference_review"][
        "decision_reference_validated"
    ] is False
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False


def test_operator_decision_reference_review_summary_blocks_forged_reference_status() -> None:
    cases = (
        (
            "not_validated",
            lambda report: report["operator_decision_reference"].__setitem__(
                "decision_reference_validated",
                False,
            ),
            "operator_decision_reference_not_validated",
        ),
        (
            "match_flag_false",
            lambda report: report["operator_decision_reference"].__setitem__(
                "decision_reference_matches_expected",
                False,
            ),
            "operator_decision_reference_mismatch",
        ),
        (
            "approval_semantics",
            lambda report: report["operator_decision_reference"].__setitem__(
                "decision_reference_is_approval",
                True,
            ),
            "operator_decision_reference_decision_reference_is_approval_not_false",
        ),
        (
            "reference_mismatch",
            lambda report: report["operator_decision_reference"].__setitem__(
                "decision_reference",
                "bridge:operator-decision:other-review",
            ),
            "operator_decision_reference_mismatch",
        ),
    )

    for label, mutate, expected_blocker in cases:
        report = _validation_report()
        mutate(report)

        summary = (
            build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
                decision_validation_report=report,
                reviewer_agent_id="claude-rco-1",
                handoff_ref="bridge:handoff:decision-reference-review",
                now_utc=datetime(2026, 5, 28, 23, 20, tzinfo=timezone.utc),
            )
        )

        assert summary["ok"] is False, label
        assert expected_blocker in summary["blockers"], label
        assert summary["operator_decision_reference_review"][
            "decision_reference_validated"
        ] is False
        assert summary["approval_granted"] is False
        assert summary["release_decision_made"] is False


def test_operator_decision_reference_review_summary_blocks_bundle_and_boundary_forgery() -> None:
    cases = (
        (
            "identity_mismatch",
            lambda report: report["bundle_verification"].__setitem__(
                "identity_match",
                False,
            ),
            "bundle_verification_identity_mismatch",
        ),
        (
            "boundary_approval",
            lambda report: report["operator_boundary"].__setitem__(
                "approval_granted",
                True,
            ),
            "operator_boundary_approval_granted_not_false",
        ),
        (
            "boundary_blockers",
            lambda report: report["operator_boundary"].__setitem__(
                "boundary_blockers",
                ["operator_boundary_approval_granted_not_false"],
            ),
            "operator_boundary_blockers_present",
        ),
        (
            "top_level_transport",
            lambda report: report.__setitem__("transport_added", True),
            "decision_reference_validation_transport_added_not_false",
        ),
    )

    for label, mutate, expected_blocker in cases:
        report = _validation_report()
        mutate(report)

        summary = (
            build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
                decision_validation_report=report,
                reviewer_agent_id="claude-rco-1",
                handoff_ref="bridge:handoff:decision-reference-review",
                now_utc=datetime(2026, 5, 28, 23, 20, tzinfo=timezone.utc),
            )
        )

        assert summary["ok"] is False, label
        assert expected_blocker in summary["blockers"], label
        assert summary["operator_boundary"]["decision_validation_boundary_ok"] is (
            label not in {"boundary_approval", "boundary_blockers"}
        )
        assert summary["transport_added"] is False
        assert summary["approval_granted"] is False
        assert summary["release_decision_made"] is False


def test_operator_decision_reference_review_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "decision_validation_report.json"
    validation_path.write_text(
        json.dumps(_validation_report(), sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--decision-validation-json",
            str(validation_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review",
            "--now",
            "2026-05-28T23:20:00Z",
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
    assert payload["operator_decision_reference_review"][
        "decision_reference_validated"
    ] is True
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    assert validation_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--decision-validation-json",
            "C:/private/decision_validation_report.json",
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review",
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
        "operator_decision_reference_review_summary_failed:"
        "decision_validation_report_unreadable"
    ]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    assert "decision_validation_report.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_summary_non_utf8_input_is_path_free(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "decision_validation_report.json"
    validation_path.write_bytes(b"\xff\xfe\xff")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--decision-validation-json",
            str(validation_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review",
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
        "operator_decision_reference_review_summary_failed:"
        "decision_validation_report_decode_error"
    ]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert validation_path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_summary_rejects_unsafe_reviewer_ref(
    tmp_path: Path,
) -> None:
    validation_path = tmp_path / "decision_validation_report.json"
    validation_path.write_text(
        json.dumps(_validation_report(), sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--decision-validation-json",
            str(validation_path),
            "--reviewer-agent",
            "C:/private/reviewer",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review",
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
        "operator_decision_reference_review_summary_failed:"
        "reviewer_agent_id_unsafe"
    ]
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    assert "C:/private/reviewer" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _validation_report() -> dict:
    return {
        "ok": True,
        "validation_version": VALIDATION_VERSION,
        "created_at_utc": "2026-05-28T23:10:00Z",
        "release_ref": "pr:762",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review",
        "operator_decision_reference": {
            "decision_reference": DECISION_REF,
            "expected_decision_reference": DECISION_REF,
            "decision_reference_present": True,
            "decision_reference_validated": True,
            "decision_reference_matches_expected": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
        },
        "bundle_verification": {
            "verification_summary_ok": True,
            "verification_ok": True,
            "verification_summary_version": VERIFICATION_SUMMARY_VERSION,
            "bridge_template_version": TEMPLATE_VERSION,
            "identity_match": True,
        },
        "operator_boundary": {
            "validation_only": True,
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
        },
        "reviewer_next_actions": [
            "review_operator_decision_reference_validation",
            "record_operator_decision_separately",
        ],
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
