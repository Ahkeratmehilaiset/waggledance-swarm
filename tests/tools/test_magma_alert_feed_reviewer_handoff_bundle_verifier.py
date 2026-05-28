import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (
    TEMPLATE_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_index import (
    BUNDLE_INDEX_VERSION,
)
from tools.build_magma_alert_feed_reviewer_handoff_summary import (
    SUMMARY_VERSION,
)
from tools.package_magma_alert_feed_release_evidence import PACKAGE_VERSION
from tools.verify_magma_alert_feed_reviewer_handoff_bundle_index import (
    verify_magma_alert_feed_reviewer_handoff_bundle_index,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_magma_alert_feed_reviewer_handoff_bundle_index.py"
COMMIT_SHA = "7" * 40
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def test_reviewer_handoff_bundle_verifier_recomputes_digests_without_authority() -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        package=artifacts["release_evidence_package"],
        validation_report=artifacts["validator_report"],
        reviewer_summary=artifacts["reviewer_handoff_summary"],
        bridge_template_report=artifacts["bridge_event_template"],
        package_bytes=raw["release_evidence_package"],
        validation_bytes=raw["validator_report"],
        summary_bytes=raw["reviewer_handoff_summary"],
        bridge_template_bytes=raw["bridge_event_template"],
    )

    assert report["ok"] is True
    assert report["artifact_count_checked"] == 4
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_reviewer_handoff_bundle_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    paths = _write_bundle(tmp_path, bundle_index, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bundle-index-json",
            str(paths["bundle_index"]),
            "--package-json",
            str(paths["release_evidence_package"]),
            "--validation-json",
            str(paths["validator_report"]),
            "--summary-json",
            str(paths["reviewer_handoff_summary"]),
            "--bridge-template-json",
            str(paths["bridge_event_template"]),
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


def test_reviewer_handoff_bundle_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    paths = _write_bundle(tmp_path, bundle_index, artifacts)
    tampered_summary = copy.deepcopy(artifacts["reviewer_handoff_summary"])
    tampered_summary["extra_context"] = "changed"
    paths["reviewer_handoff_summary"].write_bytes(_json_bytes(tampered_summary))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bundle-index-json",
            str(paths["bundle_index"]),
            "--package-json",
            str(paths["release_evidence_package"]),
            "--validation-json",
            str(paths["validator_report"]),
            "--summary-json",
            str(paths["reviewer_handoff_summary"]),
            "--bridge-template-json",
            str(paths["bridge_event_template"]),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert "digest_mismatch:reviewer_handoff_summary" in payload["blockers"]
    assert payload["digest_checks"]["reviewer_handoff_summary"] == "mismatch"
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_bundle_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    bundle_index["artifacts"] = [
        item
        for item in bundle_index["artifacts"]
        if item["artifact_id"] != "validator_report"
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        package=artifacts["release_evidence_package"],
        validation_report=artifacts["validator_report"],
        reviewer_summary=artifacts["reviewer_handoff_summary"],
        bridge_template_report=artifacts["bridge_event_template"],
        package_bytes=raw["release_evidence_package"],
        validation_bytes=raw["validator_report"],
        summary_bytes=raw["reviewer_handoff_summary"],
        bridge_template_bytes=raw["bridge_event_template"],
    )

    assert report["ok"] is False
    assert "artifact_record_missing:validator_report" in report["blockers"]
    assert report["digest_checks"]["validator_report"] == "missing_index_record"
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_reviewer_handoff_bundle_verifier_rejects_missing_identity() -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    for field in ("release_ref", "commit_sha", "ci_run_ref"):
        bundle_index.pop(field, None)
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        package=artifacts["release_evidence_package"],
        validation_report=artifacts["validator_report"],
        reviewer_summary=artifacts["reviewer_handoff_summary"],
        bridge_template_report=artifacts["bridge_event_template"],
        package_bytes=raw["release_evidence_package"],
        validation_bytes=raw["validator_report"],
        summary_bytes=raw["reviewer_handoff_summary"],
        bridge_template_bytes=raw["bridge_event_template"],
    )

    assert report["ok"] is False
    assert "artifact_identity_release_ref_missing" in report["blockers"]
    assert "artifact_identity_commit_sha_missing" in report["blockers"]
    assert "artifact_identity_ci_run_ref_missing" in report["blockers"]
    assert report["release_ref"] == "invalid_ref"
    assert report["commit_sha"] == "invalid_ref"
    assert report["ci_run_ref"] == "invalid_ref"


def test_reviewer_handoff_bundle_verifier_rejects_identity_mismatch() -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    bundle_index["release_ref"] = "pr:other"
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        package=artifacts["release_evidence_package"],
        validation_report=artifacts["validator_report"],
        reviewer_summary=artifacts["reviewer_handoff_summary"],
        bridge_template_report=artifacts["bridge_event_template"],
        package_bytes=raw["release_evidence_package"],
        validation_bytes=raw["validator_report"],
        summary_bytes=raw["reviewer_handoff_summary"],
        bridge_template_bytes=raw["bridge_event_template"],
    )

    assert report["ok"] is False
    assert "artifact_identity_release_ref_mismatch" in report["blockers"]
    assert report["release_ref"] == "invalid_ref"


def test_reviewer_handoff_bundle_verifier_rejects_artifact_count_mismatch() -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    bundle_index["artifact_count"] = 999
    raw = _artifact_bytes(artifacts)

    report = verify_magma_alert_feed_reviewer_handoff_bundle_index(
        bundle_index=bundle_index,
        package=artifacts["release_evidence_package"],
        validation_report=artifacts["validator_report"],
        reviewer_summary=artifacts["reviewer_handoff_summary"],
        bridge_template_report=artifacts["bridge_event_template"],
        package_bytes=raw["release_evidence_package"],
        validation_bytes=raw["validator_report"],
        summary_bytes=raw["reviewer_handoff_summary"],
        bridge_template_bytes=raw["bridge_event_template"],
    )

    assert report["ok"] is False
    assert "artifact_count_mismatch" in report["blockers"]
    assert report["artifact_count_checked"] == 4


def test_reviewer_handoff_bundle_verifier_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    bundle_index = _bundle_index(artifacts)
    paths = _write_bundle(tmp_path, bundle_index, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--bundle-index-json",
            str(paths["bundle_index"]),
            "--package-json",
            "C:/private/magma_alert_feed_release_evidence.json",
            "--validation-json",
            str(paths["validator_report"]),
            "--summary-json",
            str(paths["reviewer_handoff_summary"]),
            "--bridge-template-json",
            str(paths["bridge_event_template"]),
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
        "handoff_bundle_verification_failed:release_evidence_package_unreadable"
    ]
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert "magma_alert_feed_release_evidence" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _artifact_set() -> dict[str, dict]:
    return {
        "release_evidence_package": {
            "package_version": PACKAGE_VERSION,
            "ok": True,
            "release_ref": "pr:759",
            "commit_sha": COMMIT_SHA,
            "ci_run_ref": "gh:run:bundle-verifier",
        },
        "validator_report": {
            "package_version": PACKAGE_VERSION,
            "ok": True,
            "release_ref": "pr:759",
            "commit_sha": COMMIT_SHA,
            "ci_run_ref": "gh:run:bundle-verifier",
        },
        "reviewer_handoff_summary": {
            "summary_version": SUMMARY_VERSION,
            "ok": True,
            "release_ref": "pr:759",
            "commit_sha": COMMIT_SHA,
            "ci_run_ref": "gh:run:bundle-verifier",
        },
        "bridge_event_template": {
            "template_version": TEMPLATE_VERSION,
            "ok": True,
            "release_ref": "pr:759",
            "commit_sha": COMMIT_SHA,
            "ci_run_ref": "gh:run:bundle-verifier",
        },
    }


def _bundle_index(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    records = []
    for artifact_id, artifact in artifacts.items():
        records.append({
            "artifact_id": artifact_id,
            "role": artifact_id,
            "sha256": _sha256_hex(raw[artifact_id]),
            "size_bytes": len(raw[artifact_id]),
            "json_schema_version": _schema_version(artifact),
            "payload_included": False,
            "local_path_recorded": False,
        })
    return {
        "ok": True,
        "bundle_index_version": BUNDLE_INDEX_VERSION,
        "release_ref": "pr:759",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:bundle-verifier",
        "artifact_count": 4,
        "artifacts": records,
        "consistency": {
            "artifact_payloads_included": False,
            "local_paths_recorded": False,
        },
        "operator_boundary": {
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
    }


def _write_bundle(
    tmp_path: Path,
    bundle_index: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "bundle_index": tmp_path / "reviewer_handoff_bundle_index.json",
        "release_evidence_package": tmp_path / "magma_alert_feed_package.json",
        "validator_report": tmp_path / "magma_alert_feed_validation.json",
        "reviewer_handoff_summary": tmp_path / "reviewer_handoff_summary.json",
        "bridge_event_template": tmp_path / "bridge_event_template.json",
    }
    paths["bundle_index"].write_bytes(_json_bytes(bundle_index))
    for artifact_id, artifact in artifacts.items():
        paths[artifact_id].write_bytes(_json_bytes(artifact))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        artifact_id: _json_bytes(artifact)
        for artifact_id, artifact in artifacts.items()
    }


def _schema_version(artifact: dict) -> str:
    for field in ("package_version", "summary_version", "template_version"):
        value = artifact.get(field)
        if isinstance(value, str):
            return value
    return "validator_report.v1"


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
