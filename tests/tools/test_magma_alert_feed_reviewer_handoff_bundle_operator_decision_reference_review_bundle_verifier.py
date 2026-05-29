import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (
    TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (
    BUNDLE_INDEX_VERSION,
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary import (
    build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_verification_summary import (
    SUMMARY_VERSION as VERIFICATION_SUMMARY_VERSION,
)
from tools.validate_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference import (
    VALIDATION_VERSION,
)
from tools.verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index import (
    VERIFICATION_VERSION,
    verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_"
        "reference_review_bundle_index.py"
    )
)
COMMIT_SHA = "8" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
FIXED_NOW = datetime(2026, 5, 29, 2, 15, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_review_bundle_verifier_recomputes_digests_without_authority() -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
        review_bundle_index=review_bundle_index,
        decision_validation_report=artifacts["validation"],
        review_summary=artifacts["summary"],
        decision_validation_bytes=raw["validation"],
        review_summary_bytes=raw["summary"],
    )

    assert report["ok"] is True
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["bundle_index_version"] == BUNDLE_INDEX_VERSION
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_check"] == "match"
    reference = report["operator_decision_reference"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_verified"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_operator_decision_reference_review_bundle_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    paths = _write_bundle(tmp_path, review_bundle_index, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--review-bundle-index-json",
            str(paths["review_bundle_index"]),
            "--decision-validation-json",
            str(paths["validation"]),
            "--review-summary-json",
            str(paths["summary"]),
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
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    paths = _write_bundle(tmp_path, review_bundle_index, artifacts)
    tampered_summary = copy.deepcopy(artifacts["summary"])
    tampered_summary["extra_context"] = "changed"
    paths["summary"].write_bytes(_json_bytes(tampered_summary))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bundle-index-json",
            str(paths["review_bundle_index"]),
            "--decision-validation-json",
            str(paths["validation"]),
            "--review-summary-json",
            str(paths["summary"]),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert (
        "digest_mismatch:operator_decision_reference_review_summary"
        in payload["blockers"]
    )
    assert (
        payload["digest_checks"]["operator_decision_reference_review_summary"]
        == "mismatch"
    )
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    review_bundle_index["artifacts"] = [
        item
        for item in review_bundle_index["artifacts"]
        if item["artifact_id"] != "operator_decision_reference_validation"
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
        review_bundle_index=review_bundle_index,
        decision_validation_report=artifacts["validation"],
        review_summary=artifacts["summary"],
        decision_validation_bytes=raw["validation"],
        review_summary_bytes=raw["summary"],
    )

    assert report["ok"] is False
    assert (
        "artifact_record_missing:operator_decision_reference_validation"
        in report["blockers"]
    )
    assert (
        report["digest_checks"]["operator_decision_reference_validation"]
        == "missing_index_record"
    )
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verifier_rejects_nested_authority_in_index() -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    review_bundle_index["operator_decision_reference"]["approval_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
        review_bundle_index=review_bundle_index,
        decision_validation_report=artifacts["validation"],
        review_summary=artifacts["summary"],
        decision_validation_bytes=raw["validation"],
        review_summary_bytes=raw["summary"],
    )

    assert report["ok"] is False
    assert (
        "operator_decision_reference_approval_granted_not_false"
        in report["blockers"]
    )
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verifier_rejects_deterministic_index_drift() -> None:
    cases = (
        (
            "reviewer_next_actions",
            lambda index: index.__setitem__(
                "reviewer_next_actions",
                ["approve_release"],
            ),
        ),
        (
            "artifact_role",
            lambda index: index["artifacts"][0].__setitem__(
                "role",
                "approve_release",
            ),
        ),
    )

    for label, mutate in cases:
        artifacts = _artifact_set()
        review_bundle_index = _review_bundle_index(artifacts)
        mutate(review_bundle_index)
        raw = _artifact_bytes(artifacts)

        report = verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
            review_bundle_index=review_bundle_index,
            decision_validation_report=artifacts["validation"],
            review_summary=artifacts["summary"],
            decision_validation_bytes=raw["validation"],
            review_summary_bytes=raw["summary"],
        )

        assert report["ok"] is False, label
        assert report["rebuilt_index_check"] == "mismatch", label
        assert "rebuilt_index_mismatch" in report["blockers"], label
        assert report["approval_granted"] is False
        assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    tampered_validation = copy.deepcopy(artifacts["validation"])
    tampered_validation["bundle_verification"]["bridge_template_version"] = "bogus"

    report = verify_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
        review_bundle_index=review_bundle_index,
        decision_validation_report=tampered_validation,
        review_summary=artifacts["summary"],
        decision_validation_bytes=_json_bytes(tampered_validation),
        review_summary_bytes=_json_bytes(artifacts["summary"]),
    )

    assert report["ok"] is False
    assert report["source_contract_check"] == "failed"
    assert (
        "source_contract_failed:"
        "operator_decision_reference_validation_bridge_template_version_mismatch"
        in report["blockers"]
    )
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verifier_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    paths = _write_bundle(tmp_path, review_bundle_index, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--review-bundle-index-json",
            str(paths["review_bundle_index"]),
            "--decision-validation-json",
            "C:/private/operator_decision_reference_validation.json",
            "--review-summary-json",
            str(paths["summary"]),
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
        "operator_decision_reference_review_bundle_verification_failed:"
        "operator_decision_reference_validation_unreadable"
    ]
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert "operator_decision_reference_validation.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    review_bundle_index = _review_bundle_index(artifacts)
    paths = _write_bundle(tmp_path, review_bundle_index, artifacts)
    artifacts["validation"]["warnings"] = [float("nan")]
    paths["validation"].write_text(
        json.dumps(artifacts["validation"], sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--review-bundle-index-json",
            str(paths["review_bundle_index"]),
            "--decision-validation-json",
            str(paths["validation"]),
            "--review-summary-json",
            str(paths["summary"]),
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
        "operator_decision_reference_review_bundle_verification_failed:"
        "operator_decision_reference_validation_json_error"
    ]
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def _artifact_set() -> dict[str, dict]:
    validation = _validation_report()
    summary = build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_summary(
        decision_validation_report=validation,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review",
        now_utc=FIXED_NOW,
    )
    return {
        "validation": validation,
        "summary": summary,
    }


def _validation_report() -> dict:
    return {
        "ok": True,
        "validation_version": VALIDATION_VERSION,
        "created_at_utc": "2026-05-29T02:00:00Z",
        "release_ref": "pr:764",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-verifier",
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


def _review_bundle_index(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
        decision_validation_report=artifacts["validation"],
        review_summary=artifacts["summary"],
        decision_validation_bytes=raw["validation"],
        review_summary_bytes=raw["summary"],
        now_utc=FIXED_NOW,
    )


def _write_bundle(
    tmp_path: Path,
    review_bundle_index: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "review_bundle_index": (
            tmp_path / "operator_decision_reference_review_bundle_index.json"
        ),
        "validation": tmp_path / "operator_decision_reference_validation.json",
        "summary": tmp_path / "operator_decision_reference_review_summary.json",
    }
    paths["review_bundle_index"].write_bytes(_json_bytes(review_bundle_index))
    for artifact_id, artifact in artifacts.items():
        paths[artifact_id].write_bytes(_json_bytes(artifact))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        artifact_id: _json_bytes(artifact)
        for artifact_id, artifact in artifacts.items()
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
