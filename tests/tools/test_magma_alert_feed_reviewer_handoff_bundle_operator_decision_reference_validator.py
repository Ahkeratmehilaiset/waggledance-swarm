import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (
    TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_verification_summary import (
    SUMMARY_VERSION,
)
from tools.validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference import (
    VALIDATION_VERSION,
    validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference.py"
)
COMMIT_SHA = "9" * 40
DECISION_REF = "bridge:operator-decision:hold-20260528"
FIXED_NOW = datetime(2026, 5, 28, 22, 30, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_validator_accepts_context_reference_without_approval() -> None:
    report = validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference(
        verification_summary=_verification_summary(),
        bridge_template_report=_bridge_template_report(),
        expected_decision_ref=DECISION_REF,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["validation_version"] == VALIDATION_VERSION
    assert report["created_at_utc"] == "2026-05-28T22:30:00Z"
    assert report["release_ref"] == "pr:761"
    assert report["commit_sha"] == COMMIT_SHA
    assert report["ci_run_ref"] == "gh:run:decision-ref"
    reference = report["operator_decision_reference"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_validated"] is True
    assert reference["decision_reference_matches_expected"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    assert report["bundle_verification"]["identity_match"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["automatic_release_decision"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert report["blockers"] == []


def test_operator_decision_reference_validator_blocks_approval_semantics() -> None:
    bridge_template = _bridge_template_report()
    operator_decision = bridge_template["bridge_event_template"]["payload"][
        "operator_decision"
    ]
    operator_decision["decision_reference_is_approval"] = True
    operator_decision["approval_granted"] = True

    report = validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference(
        verification_summary=_verification_summary(),
        bridge_template_report=bridge_template,
        expected_decision_ref=DECISION_REF,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "operator_decision_decision_reference_is_approval_not_false" in report[
        "blockers"
    ]
    assert "operator_decision_approval_granted_not_false" in report["blockers"]
    assert report["operator_decision_reference"][
        "decision_reference_is_approval"
    ] is False
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_validator_blocks_missing_or_mismatched_reference() -> None:
    cases = (
        (
            "missing_reference",
            lambda template: template["bridge_event_template"]["payload"][
                "operator_decision"
            ].pop("decision_reference", None),
            "operator_decision_reference_invalid",
        ),
        (
            "present_false",
            lambda template: template["bridge_event_template"]["payload"][
                "operator_decision"
            ].__setitem__("decision_reference_present", False),
            "operator_decision_reference_missing",
        ),
        (
            "mismatch",
            lambda template: template["bridge_event_template"]["payload"][
                "operator_decision"
            ].__setitem__(
                "decision_reference",
                "bridge:operator-decision:other-20260528",
            ),
            "operator_decision_reference_mismatch",
        ),
    )

    for label, mutate, expected_blocker in cases:
        bridge_template = _bridge_template_report()
        mutate(bridge_template)

        report = validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference(
            verification_summary=_verification_summary(),
            bridge_template_report=bridge_template,
            expected_decision_ref=DECISION_REF,
            now_utc=FIXED_NOW,
        )

        assert report["ok"] is False, label
        assert expected_blocker in report["blockers"], label
        assert report["operator_decision_reference"][
            "decision_reference_validated"
        ] is False
        assert report["approval_granted"] is False
        assert report["release_decision_made"] is False


def test_operator_decision_reference_validator_blocks_unverified_or_mismatched_bundle() -> None:
    verification_summary = _verification_summary()
    verification_summary["ok"] = False
    verification_summary["bundle_verification"]["verification_ok"] = False
    bridge_template = _bridge_template_report()
    bridge_template["bridge_event_template"]["payload"]["commit_sha"] = "8" * 40

    report = validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference(
        verification_summary=verification_summary,
        bridge_template_report=bridge_template,
        expected_decision_ref=DECISION_REF,
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is False
    assert "verification_summary_not_ok" in report["blockers"]
    assert "verification_summary_verification_not_ok" in report["blockers"]
    assert "artifact_identity_commit_sha_mismatch" in report["blockers"]
    assert report["bundle_verification"]["identity_match"] is False
    assert report["approval_granted"] is False
    assert report["transport_added"] is False


def test_operator_decision_reference_validator_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-summary-json",
            str(paths["verification_summary"]),
            "--bridge-template-json",
            str(paths["bridge_template"]),
            "--expected-decision-ref",
            DECISION_REF,
            "--now",
            "2026-05-28T22:30:00Z",
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
    assert payload["operator_decision_reference"][
        "decision_reference_validated"
    ] is True
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_validator_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-summary-json",
            "C:/private/bundle_verification_summary.json",
            "--bridge-template-json",
            "C:/private/bridge_event_template.json",
            "--expected-decision-ref",
            DECISION_REF,
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
        "operator_decision_reference_validation_failed:verification_summary_unreadable"
    ]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    assert "bundle_verification_summary" not in result.stdout
    assert "bridge_event_template" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_validator_rejects_unsafe_expected_ref_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_inputs(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-summary-json",
            str(paths["verification_summary"]),
            "--bridge-template-json",
            str(paths["bridge_template"]),
            "--expected-decision-ref",
            "C:/private/operator-approval.json",
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
        "operator_decision_reference_validation_failed:expected_decision_ref_unsafe"
    ]
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    assert "operator-approval" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _verification_summary() -> dict:
    return {
        "ok": True,
        "summary_version": SUMMARY_VERSION,
        "release_ref": "pr:761",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-ref",
        "bundle_verification": {
            "verification_ok": True,
            "digest_checks": _checks("match"),
            "size_checks": _checks("match"),
            "schema_version_checks": _checks("match"),
        },
        "operator_boundary": {
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


def _bridge_template_report() -> dict:
    return {
        "ok": True,
        "template_version": TEMPLATE_VERSION,
        "bridge_event_template": {
            "payload": {
                "schema_version": TEMPLATE_VERSION,
                "release_ref": "pr:761",
                "commit_sha": COMMIT_SHA,
                "ci_run_ref": "gh:run:decision-ref",
                "operator_decision": {
                    "decision_reference": DECISION_REF,
                    "decision_reference_present": True,
                    "decision_reference_is_approval": False,
                    "decision_reference_is_release_decision": False,
                    "approval_granted": False,
                    "release_decision_made": False,
                    "automatic_release_decision": False,
                    "decision_must_be_recorded_separately": True,
                },
                "template_only": True,
                "direct_bridge_write_performed": False,
                "transport_added": False,
                "external_fetch_performed": False,
                "runtime_controls_added": False,
            },
        },
        "template_only": True,
        "direct_bridge_write_performed": False,
        "automatic_release_decision": False,
        "approval_granted": False,
        "release_decision_made": False,
        "runtime_controls_added": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "blockers": [],
        "warnings": [],
    }


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    payloads = {
        "verification_summary": _verification_summary(),
        "bridge_template": _bridge_template_report(),
    }
    paths = {
        "verification_summary": tmp_path / "bundle_verification_summary.json",
        "bridge_template": tmp_path / "bridge_event_template.json",
    }
    for key, path in paths.items():
        path.write_text(json.dumps(payloads[key], sort_keys=True), encoding="utf-8")
    return paths


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
