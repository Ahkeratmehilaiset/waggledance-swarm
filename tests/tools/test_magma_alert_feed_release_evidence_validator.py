import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.package_magma_alert_feed_release_evidence import (
    build_magma_alert_feed_release_evidence_package,
)
from tools.validate_magma_alert_feed_release_evidence import (
    validate_magma_alert_feed_release_evidence_package,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "validate_magma_alert_feed_release_evidence.py"
COMMIT_SHA = "2" * 40
FIXED_NOW = datetime(2026, 5, 28, 18, 0, tzinfo=timezone.utc)
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


def _package() -> tuple[dict, bytes, bytes]:
    ops_bytes = json.dumps(_ops_payload(), sort_keys=True).encode("utf-8")
    metrics_bytes = _metrics_text().encode("utf-8")
    package = build_magma_alert_feed_release_evidence_package(
        ops_payload=_ops_payload(),
        metrics_text=_metrics_text(),
        release_ref="pr:754",
        commit_sha=COMMIT_SHA,
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-alert-feed-validator",
        ci_run_ref="gh:run:validator",
        now_utc=FIXED_NOW,
        ops_sha256=_sha256_hex(ops_bytes),
        ops_size_bytes=len(ops_bytes),
        metrics_sha256=_sha256_hex(metrics_bytes),
        metrics_size_bytes=len(metrics_bytes),
    )
    return package, ops_bytes, metrics_bytes


def test_release_evidence_validator_accepts_package_and_digest_inputs() -> None:
    package, ops_bytes, metrics_bytes = _package()

    report = validate_magma_alert_feed_release_evidence_package(
        package,
        ops_bytes=ops_bytes,
        metrics_bytes=metrics_bytes,
    )

    assert report["ok"] is True
    assert report["digest_checks"] == {
        "ops_json": "match",
        "metrics_scrape": "match",
    }
    assert report["manual_review_required"] is True
    assert report["automatic_release_decision"] is False
    assert report["release_decision_made"] is False
    assert report["runtime_controls_added"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["blockers"] == []


def test_release_evidence_validator_rejects_tampered_control_and_privacy_flags() -> None:
    package, ops_bytes, metrics_bytes = _package()
    tampered = copy.deepcopy(package)
    tampered["release_ref"] = "https://example.invalid/pr/754"
    tampered["manual_gate"]["automatic_release_decision"] = True
    tampered["privacy"]["urls_recorded"] = True
    tampered["input_artifacts"]["metrics_scrape"]["raw_scrape_included"] = True
    tampered["authority"]["runtime_controls_added"] = True

    report = validate_magma_alert_feed_release_evidence_package(
        tampered,
        ops_bytes=ops_bytes,
        metrics_bytes=metrics_bytes,
    )

    assert report["ok"] is False
    assert "release_ref_unsafe" in report["blockers"]
    assert "manual_gate_automatic_release_decision_not_false" in report["blockers"]
    assert "privacy_urls_recorded_not_false" in report["blockers"]
    assert "metrics_scrape_raw_scrape_included_not_false" in report["blockers"]
    assert "authority_runtime_controls_added_not_false" in report["blockers"]
    assert "package_forbidden_marker:https://" in report["blockers"]
    assert report["automatic_release_decision"] is False
    assert report["release_decision_made"] is False


def test_release_evidence_validator_reports_digest_mismatch() -> None:
    package, _, metrics_bytes = _package()

    report = validate_magma_alert_feed_release_evidence_package(
        package,
        ops_bytes=b'{"changed": true}',
        metrics_bytes=metrics_bytes,
    )

    assert report["ok"] is False
    assert report["digest_checks"]["ops_json"] == "mismatch"
    assert "ops_json_digest_mismatch" in report["blockers"]
    assert report["digest_checks"]["metrics_scrape"] == "match"


def test_release_evidence_validator_cli_json_is_path_free(tmp_path: Path) -> None:
    package, ops_bytes, metrics_bytes = _package()
    package_path = tmp_path / "magma_alert_feed_release_evidence.json"
    ops_path = tmp_path / "ops.json"
    metrics_path = tmp_path / "metrics.prom"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    ops_path.write_bytes(ops_bytes)
    metrics_path.write_bytes(metrics_bytes)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            str(package_path),
            "--ops-json",
            str(ops_path),
            "--metrics-scrape",
            str(metrics_path),
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
    assert payload["digest_checks"]["ops_json"] == "match"
    assert payload["digest_checks"]["metrics_scrape"] == "match"
    assert payload["automatic_release_decision"] is False
    assert str(tmp_path) not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()
