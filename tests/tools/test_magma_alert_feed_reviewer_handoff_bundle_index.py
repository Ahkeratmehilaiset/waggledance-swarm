import copy
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_bridge_event_template import (
    build_magma_alert_feed_reviewer_bridge_event_template,
)
from tools.build_magma_alert_feed_reviewer_handoff_bundle_index import (
    build_magma_alert_feed_reviewer_handoff_bundle_index,
)
from tools.build_magma_alert_feed_reviewer_handoff_summary import (
    build_magma_alert_feed_reviewer_handoff_summary,
)
from tools.package_magma_alert_feed_release_evidence import (
    build_magma_alert_feed_release_evidence_package,
)
from tools.validate_magma_alert_feed_release_evidence import (
    validate_magma_alert_feed_release_evidence_package,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_magma_alert_feed_reviewer_handoff_bundle_index.py"
COMMIT_SHA = "5" * 40
FIXED_NOW = datetime(2026, 5, 28, 20, 5, tzinfo=timezone.utc)
PRIVATE_MARKERS = ("C:/private", "PRIVATE_", "http://", "https://")


def _ops_payload() -> dict:
    return {
        "magma_share_import_handoff": {
            "provider_health": {
                "metrics_alert_state": {
                    "source": "prometheus_alertmanager_snapshot",
                    "status": "nominal",
                    "severity": "none",
                    "prometheus_alertmanager_feed": True,
                    "active_count": 0,
                    "active": [],
                    "feed_health": {
                        "configured": True,
                        "available": True,
                        "cache_enabled": True,
                        "cache_present": True,
                        "cache_stale": False,
                        "backoff_active": False,
                        "controls_present": False,
                        "runtime_authority_granted": False,
                        "external_writes_applied": False,
                        "status": "nominal",
                        "failure_reason": "none",
                    },
                    "slo_panels": [],
                    "drill_evidence": {
                        "required_artifacts": [
                            {"id": "metrics_scrape"},
                            {"id": "ops_snapshot"},
                            {"id": "runtime_window_logs"},
                        ],
                        "controls_present": False,
                    },
                }
            }
        }
    }


def _metrics_text() -> str:
    return "\n".join(
        [
            "waggledance_magma_handoff_alert_feed_available 1",
            "waggledance_magma_handoff_alert_feed_fetch_failures_total 0",
            "waggledance_magma_handoff_alert_feed_backoff_active 0",
            "waggledance_magma_handoff_alert_feed_cache_stale 0",
            "waggledance_magma_handoff_runtime_authority_granted 0",
            "waggledance_magma_handoff_payload_files_imported 0",
            "waggledance_magma_handoff_local_paths_recorded 0",
            "waggledance_magma_handoff_controls_present 0",
            "waggledance_magma_handoff_alert_feed_controls_present 0",
            "waggledance_magma_handoff_alert_feed_runtime_authority_granted 0",
            "waggledance_magma_handoff_alert_feed_external_writes_applied 0",
            "",
        ]
    )


def _artifact_set() -> dict[str, dict]:
    ops_bytes = json.dumps(_ops_payload(), sort_keys=True).encode("utf-8")
    metrics_bytes = _metrics_text().encode("utf-8")
    package = build_magma_alert_feed_release_evidence_package(
        ops_payload=_ops_payload(),
        metrics_text=_metrics_text(),
        release_ref="pr:758",
        commit_sha=COMMIT_SHA,
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-alert-feed-release",
        ci_run_ref="gh:run:bundle-index",
        now_utc=FIXED_NOW,
        ops_sha256=_sha256_hex(ops_bytes),
        ops_size_bytes=len(ops_bytes),
        metrics_sha256=_sha256_hex(metrics_bytes),
        metrics_size_bytes=len(metrics_bytes),
    )
    validation = validate_magma_alert_feed_release_evidence_package(
        package,
        ops_bytes=ops_bytes,
        metrics_bytes=metrics_bytes,
    )
    summary = build_magma_alert_feed_reviewer_handoff_summary(
        package=package,
        validation_report=validation,
        reviewer_agent_id="reviewer:wd-image1",
        bridge_event_ref="bridge:wd-image1-reviewer-handoff",
        now_utc=FIXED_NOW,
    )
    bridge_template = build_magma_alert_feed_reviewer_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-reviewer-handoff-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260528T200500Z",
        session_id="codex-lead-1-20260528T200500Z",
        operator_decision_ref="bridge:operator-decision:hold-20260528",
        now_utc=FIXED_NOW,
    )
    return {
        "package": package,
        "validation": validation,
        "summary": summary,
        "bridge_template": bridge_template,
    }


def test_reviewer_handoff_bundle_index_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    package_bytes = _json_bytes(artifacts["package"])
    validation_bytes = _json_bytes(artifacts["validation"])
    summary_bytes = _json_bytes(artifacts["summary"])
    bridge_bytes = _json_bytes(artifacts["bridge_template"])

    index = build_magma_alert_feed_reviewer_handoff_bundle_index(
        package=artifacts["package"],
        validation_report=artifacts["validation"],
        reviewer_summary=artifacts["summary"],
        bridge_template_report=artifacts["bridge_template"],
        package_bytes=package_bytes,
        validation_bytes=validation_bytes,
        summary_bytes=summary_bytes,
        bridge_template_bytes=bridge_bytes,
        now_utc=FIXED_NOW,
    )

    assert index["ok"] is True
    assert index["artifact_count"] == 4
    assert index["release_ref"] == "pr:758"
    assert index["commit_sha"] == COMMIT_SHA
    assert index["ci_run_ref"] == "gh:run:bundle-index"
    by_id = {item["artifact_id"]: item for item in index["artifacts"]}
    assert by_id["release_evidence_package"]["sha256"] == _sha256_hex(
        package_bytes
    )
    assert by_id["validator_report"]["sha256"] == _sha256_hex(validation_bytes)
    assert by_id["reviewer_handoff_summary"]["sha256"] == _sha256_hex(
        summary_bytes
    )
    assert by_id["bridge_event_template"]["sha256"] == _sha256_hex(bridge_bytes)
    assert all(item["payload_included"] is False for item in index["artifacts"])
    assert all(item["local_path_recorded"] is False for item in index["artifacts"])
    assert index["consistency"]["all_artifact_digests_recorded"] is True
    assert index["consistency"]["artifact_payloads_included"] is False
    assert index["operator_boundary"]["approval_granted"] is False
    assert index["operator_boundary"]["release_decision_made"] is False
    assert index["operator_boundary"]["direct_bridge_write_performed"] is False
    assert index["operator_boundary"]["transport_added"] is False
    assert index["operator_boundary"]["external_fetch_performed"] is False
    assert index["operator_boundary"]["runtime_controls_added"] is False


def test_reviewer_handoff_bundle_index_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    paths = _write_artifacts(tmp_path, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            str(paths["package"]),
            "--validation-json",
            str(paths["validation"]),
            "--summary-json",
            str(paths["summary"]),
            "--bridge-template-json",
            str(paths["bridge_template"]),
            "--now",
            "2026-05-28T20:05:00Z",
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
    assert payload["operator_boundary"]["approval_granted"] is False
    assert payload["operator_boundary"]["release_decision_made"] is False
    assert payload["operator_boundary"]["artifact_payloads_included"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_bundle_index_rejects_mismatched_identity_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"]["commit_sha"] = "6" * 40
    paths = _write_artifacts(tmp_path, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            str(paths["package"]),
            "--validation-json",
            str(paths["validation"]),
            "--summary-json",
            str(paths["summary"]),
            "--bridge-template-json",
            str(paths["bridge_template"]),
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
        "handoff_bundle_index_failed:artifact_identity_mismatch"
    ]
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_bundle_index_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    paths = _write_artifacts(tmp_path, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            "C:/private/magma_alert_feed_release_evidence.json",
            "--validation-json",
            str(paths["validation"]),
            "--summary-json",
            str(paths["summary"]),
            "--bridge-template-json",
            str(paths["bridge_template"]),
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
        "handoff_bundle_index_failed:release_evidence_package_unreadable"
    ]
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert "magma_alert_feed_release_evidence" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "package": tmp_path / "magma_alert_feed_release_evidence.json",
        "validation": tmp_path / "magma_alert_feed_validation.json",
        "summary": tmp_path / "reviewer_handoff_summary.json",
        "bridge_template": tmp_path / "bridge_event_template.json",
    }
    for key, path in paths.items():
        path.write_bytes(_json_bytes(artifacts[key]))
    return paths


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()
