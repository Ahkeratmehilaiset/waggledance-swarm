import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_decision_review_verification_template_index_entry import (
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary import (
    build_magma_decision_review_verification_template_index_entry_summary,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template import (
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)
from tools.verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "verify_magma_decision_review_verification_template_index_entry_summary_"
        "bridge_event_template_index_entry.py"
    )
)
COMMIT_SHA = "f" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
FIXED_NOW = datetime(2026, 5, 29, 8, 40, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_recomputes_digests_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is True
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["index_entry_version"] == INDEX_ENTRY_VERSION
    assert report["release_ref"] == "pr:775"
    assert report["commit_sha"] == COMMIT_SHA
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    reference = report["operator_decision_reference_review"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_verified"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    assert report["template_only"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
            "--index-entry-verification-summary-json",
            str(paths["summary"]),
            "--summary-bridge-event-template-json",
            str(paths["template"]),
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
    assert payload["rebuilt_index_entry_check"] == "match"
    assert payload["bridge_event_schema_check"] == "match"
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = copy.deepcopy(artifacts["template"])
    tampered_template["extra_context"] = "changed"
    paths["template"].write_bytes(_json_bytes(tampered_template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-bridge-template-index-entry-json",
            str(paths["index_entry"]),
            "--verification-summary-json",
            str(paths["summary"]),
            "--bridge-event-template-json",
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
    assert (
        "digest_mismatch:"
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template"
        in payload["blockers"]
    )
    assert payload["source_contract_check"] == "failed"
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != _REQUIRED_SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{_REQUIRED_SUMMARY_ARTIFACT_ID}" in report[
        "blockers"
    ]
    assert report["digest_checks"][_REQUIRED_SUMMARY_ARTIFACT_ID] == (
        "missing_index_record"
    )
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_rejects_nested_authority_in_index() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["approval_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert "template_index_entry_approval_granted_not_false" in report["blockers"]
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["approve_release"]
    raw = _artifact_bytes(artifacts)

    report = verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_summary = copy.deepcopy(artifacts["summary"])
    tampered_summary[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification"
    ]["blocker_count"] = "1"

    report = verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=tampered_summary,
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=_json_bytes(tampered_summary),
        summary_bridge_event_template_bytes=_json_bytes(artifacts["template"]),
    )

    assert report["ok"] is False
    assert report["source_contract_check"] == "failed"
    assert (
        "source_contract_failed:summary_bridge_event_template_source_contract_failed:"
        "index_entry_verification_blocker_count_nonzero"
        in report["blockers"]
    )
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            "C:/private/index_entry.json",
            "--verification-summary-json",
            str(paths["summary"]),
            "--bridge-event-template-json",
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
        "operator_decision_reference_review_bundle_verification_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_index_entry_"
        "verification_failed:operator_decision_reference_review_bundle_"
        "verification_bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    combined = result.stdout + result.stderr
    assert "index_entry.json" not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


def test_operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_summary_bridge_event_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    index_entry["warnings"] = [float("nan")]
    paths["index_entry"].write_text(
        json.dumps(index_entry, sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
            "--verification-summary-json",
            str(paths["summary"]),
            "--bridge-event-template-json",
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
        "operator_decision_reference_review_bundle_verification_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_index_entry_"
        "verification_failed:operator_decision_reference_review_bundle_"
        "verification_bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in PRIVATE_MARKERS)


_REQUIRED_SUMMARY_ARTIFACT_ID = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary"
)
_REQUIRED_TEMPLATE_ARTIFACT_ID = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template"
)


def _artifact_set() -> dict[str, dict]:
    summary = _summary()
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


def _summary() -> dict:
    return build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary",
        now_utc=datetime(2026, 5, 29, 8, 30, tzinfo=timezone.utc),
    )


def _verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": SOURCE_VERIFICATION_VERSION,
        "index_entry_version": SOURCE_INDEX_ENTRY_VERSION,
        "release_ref": "pr:775",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-summary-template-index-verifier",
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
        "digest_checks": _source_checks("match"),
        "size_checks": _source_checks("match"),
        "schema_version_checks": _source_checks("match"),
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


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


def _source_checks(status: str) -> dict[str, str]:
    return {
        artifact_id: status
        for artifact_id in (
            "operator_decision_reference_review_bundle_verification_summary",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
        )
    }


def _write_bundle(
    tmp_path: Path,
    index_entry: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "index_entry": tmp_path / "summary_bridge_template_index_entry.json",
        "summary": tmp_path / "index_entry_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
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
