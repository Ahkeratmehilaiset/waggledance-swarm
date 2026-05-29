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
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template import (
    TEMPLATE_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (
    VERIFICATION_VERSION,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template.py"
)
COMMIT_SHA = "d" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")
EVENT_STATUS = (
    "decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_ready"
)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_validates_bridge_schema() -> None:
    report = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template(
        summary=_summary(),
        agent_id="codex-lead-1",
        task_id="wd-image1-template-index-summary-ready",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260529T072000Z",
        session_id="codex-lead-1-20260529T072000Z",
        now_utc=datetime(2026, 5, 29, 7, 20, tzinfo=timezone.utc),
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(event, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert event["type"] == "handoff"
    assert event["status"] == EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["pid"] == 0
    payload = event["payload"]
    assert payload["schema_version"] == TEMPLATE_VERSION
    assert payload["summary_version"] == SUMMARY_VERSION
    assert payload["release_ref"] == "pr:770"
    assert payload["commit_sha"] == COMMIT_SHA
    assert payload["template_only"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    verification = payload[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    assert verification["blocker_count"] == 0
    assert set(verification["digest_checks"].values()) == {"match"}
    reference = payload["operator_decision_reference_review"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_verified"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    assert reference["decision_must_be_recorded_separately"] is True


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_text(json.dumps(_summary(), sort_keys=True), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-verification-summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-template-index-summary-ready",
            "--to",
            "operator,claude-rco-1",
            "--run-id",
            "codex-lead-1-20260529T072000Z",
            "--session-id",
            "codex-lead-1-20260529T072000Z",
            "--now",
            "2026-05-29T07:20:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = payload["bridge_event_template"]
    validate_event(event)
    assert payload["direct_bridge_write_performed"] is False
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert event["payload"]["operator_decision_reference_review"][
        "decision_reference"
    ] == DECISION_REF
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            "C:/private/index_entry_verification_summary.json",
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-template-index-summary-ready",
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
        "verification_summary_bridge_event_template_failed:index_entry_verification_summary_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["approval_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert "index_entry_verification_summary.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_rejects_unsafe_bridge_fields(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_text(json.dumps(_summary(), sort_keys=True), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "Codex",
            "--task-id",
            "wd-image1-template-index-summary-ready",
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
        "verification_summary_bridge_event_template_failed:agent_unsafe"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_blocks_unsafe_summary_contract(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "summary_not_ok",
            lambda summary: summary.__setitem__("ok", False),
            "index_entry_verification_summary_not_ok",
        ),
        (
            "approval_granted",
            lambda summary: summary.__setitem__("approval_granted", True),
            "index_entry_verification_summary_approval_granted_not_false",
        ),
        (
            "source_contract_mismatch",
            lambda summary: summary[
                "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
            ].__setitem__("source_contract_check", "mismatch"),
            "index_entry_verification_source_contract_not_match",
        ),
        (
            "verification_version_mismatch",
            lambda summary: summary[
                "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
            ].__setitem__("verification_version", "unknown"),
            "index_entry_verification_version_mismatch",
        ),
        (
            "index_entry_version_mismatch",
            lambda summary: summary[
                "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
            ].__setitem__("index_entry_version", "unknown"),
            "index_entry_verification_index_entry_version_mismatch",
        ),
        (
            "blocker_count_string",
            lambda summary: summary[
                "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
            ].__setitem__("blocker_count", "1"),
            "index_entry_verification_blocker_count_nonzero",
        ),
        (
            "reference_is_approval",
            lambda summary: summary["operator_decision_reference_review"].__setitem__(
                "decision_reference_is_approval",
                True,
            ),
            "operator_decision_reference_decision_reference_is_approval_not_false",
        ),
        (
            "boundary_blocker_present",
            lambda summary: summary["operator_boundary"].__setitem__(
                "boundary_blockers",
                ["rebuilt_index_entry_mismatch"],
            ),
            "operator_boundary_blockers_present",
        ),
    )

    for label, mutate, expected_reason in cases:
        summary = _summary()
        mutate(summary)
        summary_path = tmp_path / f"{label}.json"
        summary_path.write_text(
            json.dumps(summary, sort_keys=True),
            encoding="utf-8",
        )

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--summary-json",
                str(summary_path),
                "--agent",
                "codex-lead-1",
                "--task-id",
                "wd-image1-template-index-summary-ready",
                "--to",
                "operator,claude-rco-1",
                "--now",
                "2026-05-29T07:20:00Z",
                "--json",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 1, label
        report = json.loads(result.stdout)
        assert report["ok"] is False, label
        assert report["blockers"] == [
            "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_"
            f"verification_summary_bridge_event_template_failed:{expected_reason}"
        ], label
        assert report["approval_granted"] is False
        assert report["release_decision_made"] is False
        assert report["direct_bridge_write_performed"] is False
        assert str(tmp_path) not in result.stdout
        assert summary_path.name not in result.stdout


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary = _summary()
    summary["warnings"] = [float("nan")]
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-template-index-summary-ready",
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
        "verification_summary_bridge_event_template_failed:index_entry_verification_summary_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def _summary() -> dict:
    return build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-verifier",
        now_utc=datetime(2026, 5, 29, 7, 15, tzinfo=timezone.utc),
    )


def _verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": INDEX_ENTRY_VERSION,
        "release_ref": "pr:770",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-template-index-summary-template",
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
