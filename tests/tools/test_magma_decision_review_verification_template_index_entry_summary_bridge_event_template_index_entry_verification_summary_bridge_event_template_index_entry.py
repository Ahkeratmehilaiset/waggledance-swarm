import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.build_magma_decision_review_verification_template_index_entry import (
    INDEX_ENTRY_VERSION as ROOT_INDEX_ENTRY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary import (
    build_magma_decision_review_verification_template_index_entry_summary,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template import (
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    TEMPLATE_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (
    VERIFICATION_VERSION as ROOT_VERIFICATION_VERSION,
)
from tools.verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
    verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_magma_decision_review_verification_template_index_entry_summary_"
        "bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry.py"
    )
)
COMMIT_SHA = "b" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
FIXED_NOW = datetime(2026, 5, 29, 9, 5, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")
EVENT_STATUS = "decision_reference_review_verifier_summary_bridge_event_template_ready"


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-05-29T09:05:00Z"
    assert entry["release_ref"] == "pr:776"
    assert entry["commit_sha"] == COMMIT_SHA
    assert entry["ci_run_ref"] == "gh:run:decision-reference-verifier-summary-template-index"
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[SUMMARY_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["summary"])
    assert by_id[TEMPLATE_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["template"])
    assert by_id[SUMMARY_ARTIFACT_ID]["json_schema_version"] == SUMMARY_VERSION
    assert by_id[TEMPLATE_ARTIFACT_ID]["json_schema_version"] == TEMPLATE_VERSION
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])
    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["event_status"] == EVENT_STATUS
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["source_summary_sha256"] == _sha256_hex(raw["summary"])
    reference = entry["operator_decision_reference_review"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_verified"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    assert entry["operator_boundary"]["approval_granted"] is False
    assert entry["operator_boundary"]["release_decision_made"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["external_fetch_performed"] is False
    assert entry["runtime_controls_added"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(entry, sort_keys=True) for marker in PRIVATE_MARKERS
    )


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
            "--template-json",
            str(paths["template"]),
            "--now",
            "2026-05-29T09:05:00Z",
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
    assert payload["template_index_entry"]["rebuilt_template_check"] == "match"
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = copy.deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"]["commit_sha"] = "c" * 40
    raw = _artifact_bytes(artifacts)

    with pytest.raises(Exception) as exc_info:
        build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert (
        getattr(exc_info.value, "code", "")
        == "summary_bridge_event_template_rebuilt_mismatch"
    )


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_rejects_summary_contract_forgery() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"][
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
    ]["blocker_count"] = "1"
    raw = _artifact_bytes(artifacts)

    with pytest.raises(Exception) as exc_info:
        build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert (
        getattr(exc_info.value, "code", "")
        == "summary_bridge_event_template_source_contract_failed:"
        "verifier_summary_blocker_count_nonzero"
    )


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_rejects_raw_bytes_mismatch() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    with pytest.raises(Exception) as exc_info:
        build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=b'{"forged":true}',
            now_utc=FIXED_NOW,
        )

    assert getattr(exc_info.value, "code", "") == f"{TEMPLATE_ARTIFACT_ID}_bytes_mismatch"


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            "C:/private/verifier_summary.json",
            "--template-json",
            "C:/private/verifier_summary_template.json",
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
        "operator_decision_reference_review_verifier_summary_"
        "bridge_event_template_index_entry_failed:"
        f"{SUMMARY_ARTIFACT_ID}_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "verifier_summary.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_verifier_summary_bridge_event_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["template"]["warnings"] = [float("nan")]
    paths = _write_artifacts(tmp_path, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
            "--template-json",
            str(paths["template"]),
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
        "operator_decision_reference_review_verifier_summary_"
        "bridge_event_template_index_entry_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def _artifact_set() -> dict[str, dict]:
    summary = _verifier_summary()
    template = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-verifier-summary-template-index",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260529T090000Z",
        session_id="codex-lead-1-20260529T090000Z",
        now_utc=datetime(2026, 5, 29, 9, 0, tzinfo=timezone.utc),
    )
    return {
        "summary": summary,
        "template": template,
    }


def _verifier_summary() -> dict:
    return build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-verifier-summary",
        now_utc=datetime(2026, 5, 29, 8, 55, tzinfo=timezone.utc),
    )


def _verification_report() -> dict:
    artifacts = _source_artifact_set()
    index_entry = _source_index_entry(artifacts)
    raw = _source_artifact_bytes(artifacts)
    return verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )


def _source_artifact_set() -> dict[str, dict]:
    summary = _source_summary()
    template = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-decision-review-template-index-summary-template-index-verifier",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260529T083500Z",
        session_id="codex-lead-1-20260529T083500Z",
        now_utc=datetime(2026, 5, 29, 8, 35, tzinfo=timezone.utc),
    )
    return {
        "summary": summary,
        "template": template,
    }


def _source_summary() -> dict:
    return build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=_source_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary",
        now_utc=datetime(2026, 5, 29, 8, 30, tzinfo=timezone.utc),
    )


def _source_verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": ROOT_VERIFICATION_VERSION,
        "index_entry_version": ROOT_INDEX_ENTRY_VERSION,
        "release_ref": "pr:776",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-verifier-summary-template-index",
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
        "digest_checks": _root_checks("match"),
        "size_checks": _root_checks("match"),
        "schema_version_checks": _root_checks("match"),
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


def _source_index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _source_artifact_bytes(artifacts)
    return build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 5, 29, 8, 40, tzinfo=timezone.utc),
    )


def _root_checks(status: str) -> dict[str, str]:
    return {
        artifact_id: status
        for artifact_id in (
            "operator_decision_reference_review_bundle_verification_summary",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
        )
    }


def _source_artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        artifact_id: json.dumps(artifact, sort_keys=True).encode("utf-8")
        for artifact_id, artifact in artifacts.items()
    }


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        artifact_id: json.dumps(artifact, sort_keys=True).encode("utf-8")
        for artifact_id, artifact in artifacts.items()
    }


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "summary": tmp_path / "verifier-summary.json",
        "template": tmp_path / "verifier-summary-template.json",
    }
    paths["summary"].write_text(
        json.dumps(artifacts["summary"], sort_keys=True),
        encoding="utf-8",
    )
    paths["template"].write_text(
        json.dumps(artifacts["template"], sort_keys=True),
        encoding="utf-8",
    )
    return paths


def _sha256_hex(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()
