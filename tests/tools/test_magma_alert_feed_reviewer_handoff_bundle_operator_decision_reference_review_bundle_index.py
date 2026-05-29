import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

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


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_"
        "reference_review_bundle_index.py"
    )
)
COMMIT_SHA = "6" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
FIXED_NOW = datetime(2026, 5, 29, 1, 10, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_review_bundle_index_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    validation_bytes = _json_bytes(artifacts["validation"])
    summary_bytes = _json_bytes(artifacts["summary"])

    index = (
        build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
            decision_validation_report=artifacts["validation"],
            review_summary=artifacts["summary"],
            decision_validation_bytes=validation_bytes,
            review_summary_bytes=summary_bytes,
            now_utc=FIXED_NOW,
        )
    )

    assert index["ok"] is True
    assert index["bundle_index_version"] == BUNDLE_INDEX_VERSION
    assert index["created_at_utc"] == "2026-05-29T01:10:00Z"
    assert index["release_ref"] == "pr:763"
    assert index["commit_sha"] == COMMIT_SHA
    assert index["ci_run_ref"] == "gh:run:decision-reference-review-index"
    assert index["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in index["artifacts"]}
    assert by_id["operator_decision_reference_validation"]["sha256"] == _sha256_hex(
        validation_bytes
    )
    assert by_id["operator_decision_reference_review_summary"][
        "sha256"
    ] == _sha256_hex(summary_bytes)
    assert all(item["payload_included"] is False for item in index["artifacts"])
    assert all(item["local_path_recorded"] is False for item in index["artifacts"])
    reference = index["operator_decision_reference"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_validated"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    assert index["consistency"]["all_artifact_digests_recorded"] is True
    assert index["consistency"]["decision_reference_match"] is True
    assert index["consistency"]["artifact_payloads_included"] is False
    assert index["operator_boundary"]["approval_granted"] is False
    assert index["operator_boundary"]["release_decision_made"] is False
    assert index["operator_boundary"]["direct_bridge_write_performed"] is False
    assert index["operator_boundary"]["transport_added"] is False
    assert index["operator_boundary"]["external_fetch_performed"] is False
    assert index["operator_boundary"]["runtime_controls_added"] is False


def test_operator_decision_reference_review_bundle_index_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--decision-validation-json",
            str(paths["validation"]),
            "--review-summary-json",
            str(paths["summary"]),
            "--now",
            "2026-05-29T01:10:00Z",
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


def test_operator_decision_reference_review_bundle_index_rejects_mismatched_identity_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"]["commit_sha"] = "5" * 40
    paths = _write_artifacts(tmp_path, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
        "operator_decision_reference_review_bundle_index_failed:"
        "artifact_identity_mismatch"
    ]
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_index_blocks_forged_artifacts() -> None:
    cases = (
        (
            "validation_blocker",
            lambda artifacts: artifacts["validation"].__setitem__(
                "blockers",
                ["operator_decision_reference_mismatch"],
            ),
            "operator_decision_reference_validation_blockers_present",
        ),
        (
            "summary_boundary_blocker",
            lambda artifacts: artifacts["summary"]["operator_boundary"].__setitem__(
                "boundary_blockers",
                ["operator_boundary_transport_added_not_false"],
            ),
            "operator_decision_reference_review_summary_boundary_blockers_present",
        ),
        (
            "summary_authority",
            lambda artifacts: artifacts["summary"].__setitem__(
                "transport_added",
                True,
            ),
            "operator_decision_reference_review_summary_transport_added_not_false",
        ),
        (
            "summary_decision_mismatch",
            lambda artifacts: artifacts["summary"][
                "operator_decision_reference_review"
            ].__setitem__(
                "decision_reference",
                "bridge:operator-decision:other-review",
            ),
            "operator_decision_reference_review_summary_decision_reference_mismatch",
        ),
        (
            "validation_verification_summary_version",
            lambda artifacts: artifacts["validation"]["bundle_verification"].__setitem__(
                "verification_summary_version",
                "bogus",
            ),
            "operator_decision_reference_validation_verification_summary_version_mismatch",
        ),
        (
            "validation_bridge_template_version",
            lambda artifacts: artifacts["validation"]["bundle_verification"].__setitem__(
                "bridge_template_version",
                "bogus",
            ),
            "operator_decision_reference_validation_bridge_template_version_mismatch",
        ),
        (
            "summary_verification_summary_version",
            lambda artifacts: artifacts["summary"]["bundle_verification"].__setitem__(
                "verification_summary_version",
                "bogus",
            ),
            "operator_decision_reference_review_summary_verification_summary_version_mismatch",
        ),
        (
            "summary_bridge_template_version",
            lambda artifacts: artifacts["summary"]["bundle_verification"].__setitem__(
                "bridge_template_version",
                "bogus",
            ),
            "operator_decision_reference_review_summary_bridge_template_version_mismatch",
        ),
    )

    for label, mutate, expected_reason in cases:
        artifacts = _artifact_set()
        mutate(artifacts)

        with pytest.raises(Exception) as exc_info:
            build_magma_alert_feed_reviewer_handoff_bundle_operator_decision_reference_review_bundle_index(
                decision_validation_report=artifacts["validation"],
                review_summary=artifacts["summary"],
                decision_validation_bytes=_json_bytes(artifacts["validation"]),
                review_summary_bytes=_json_bytes(artifacts["summary"]),
                now_utc=FIXED_NOW,
            )

        assert getattr(exc_info.value, "code", "") == expected_reason, label


def test_operator_decision_reference_review_bundle_index_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
        "operator_decision_reference_review_bundle_index_failed:"
        "operator_decision_reference_validation_unreadable"
    ]
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert "operator_decision_reference_validation.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_index_non_utf8_input_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["validation"].write_bytes(b"\xff\xfe\xff")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
        "operator_decision_reference_review_bundle_index_failed:"
        "operator_decision_reference_validation_decode_error"
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
        "created_at_utc": "2026-05-29T01:00:00Z",
        "release_ref": "pr:763",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-index",
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


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "validation": tmp_path / "operator_decision_reference_validation.json",
        "summary": tmp_path / "operator_decision_reference_review_summary.json",
    }
    for key, path in paths.items():
        path.write_bytes(_json_bytes(artifacts[key]))
    return paths


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
