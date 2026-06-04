import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (
    BUNDLE_INDEX_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template import (
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary import (
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary,
)
from tools.build_magma_decision_review_verification_template_index_entry import (
    INDEX_ENTRY_VERSION,
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry,
)
from tools.build_magma_decision_review_verification_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_magma_decision_review_verification_template_index_entry_verification_summary,
)
from tools.verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (
    VERIFICATION_VERSION as REVIEW_BUNDLE_VERIFICATION_VERSION,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (
    VERIFICATION_VERSION,
    verify_magma_decision_review_verification_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_magma_decision_review_verification_template_index_entry_verification_summary.py"
)
COMMIT_SHA = "c" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
FIXED_NOW = datetime(2026, 5, 29, 5, 45, tzinfo=timezone.utc)
FORBIDDEN_OUTPUT_TOKENS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_renders_verifier_result_without_authority() -> None:
    report = _index_entry_verification_report()

    summary = build_magma_decision_review_verification_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:template-index-entry-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["release_ref"] == "pr:767"
    assert summary["commit_sha"] == COMMIT_SHA
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
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report = _index_entry_verification_report()
    report_path = tmp_path / "index_entry_verification.json"
    report_path.write_bytes(_json_bytes(report))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:template-index-entry-verification",
            "--now",
            "2026-05-29T05:45:00Z",
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
    assert payload[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
    ]["bridge_event_schema_check"] == "match"
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_TOKENS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_rejects_verifier_drift() -> None:
    report = _index_entry_verification_report()
    report["bridge_event_schema_check"] = "failed"

    summary = build_magma_decision_review_verification_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:template-index-entry-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_bridge_event_schema_check_not_match" in (
        summary["blockers"]
    )
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_rejects_authority_escalation() -> None:
    report = _index_entry_verification_report()
    report["approval_granted"] = True

    summary = build_magma_decision_review_verification_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:template-index-entry-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_approval_granted_not_false" in summary["blockers"]
    assert summary["approval_granted"] is False
    assert summary["direct_bridge_write_performed"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            "C:/private/index_entry_verification.json",
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:template-index-entry-verification",
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
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_"
        "verification_summary_failed:verification_report_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    combined = result.stdout + result.stderr
    assert "index_entry_verification.json" not in combined
    assert str(tmp_path) not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_TOKENS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    report = _index_entry_verification_report()
    report["warnings"] = [float("nan")]
    report_path = tmp_path / "index_entry_verification.json"
    report_path.write_text(json.dumps(report, sort_keys=True), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:template-index-entry-verification",
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
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_"
        "verification_summary_failed:verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert report_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_TOKENS)


def _index_entry_verification_report() -> dict:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)
    return verify_magma_decision_review_verification_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )


def _artifact_set() -> dict[str, dict]:
    summary = _summary()
    template = build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-template-index-entry-verification-summary",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260529T054000Z",
        session_id="codex-lead-1-20260529T054000Z",
        now_utc=datetime(2026, 5, 29, 5, 40, tzinfo=timezone.utc),
    )
    return {
        "summary": summary,
        "template": template,
    }


def _summary() -> dict:
    return build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-bundle-verification",
        now_utc=datetime(2026, 5, 29, 5, 35, tzinfo=timezone.utc),
    )


def _verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": REVIEW_BUNDLE_VERIFICATION_VERSION,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "release_ref": "pr:767",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-template-index-verifier",
        "operator_decision_reference": {
            "decision_reference": DECISION_REF,
            "expected_decision_reference": DECISION_REF,
            "decision_reference_verified": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
        },
        "artifact_count_checked": 2,
        "digest_checks": _checks("match"),
        "size_checks": _checks("match"),
        "schema_version_checks": _checks("match"),
        "source_contract_check": "match",
        "rebuilt_index_check": "match",
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


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 5, 29, 5, 42, tzinfo=timezone.utc),
    )


def _checks(status: str) -> dict[str, str]:
    return {
        artifact_id: status
        for artifact_id in (
            "operator_decision_reference_validation",
            "operator_decision_reference_review_summary",
        )
    }


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        artifact_id: _json_bytes(artifact)
        for artifact_id, artifact in artifacts.items()
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")
