import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_alert_feed_reviewer_handoff_summary import (
    build_magma_alert_feed_reviewer_handoff_summary,
    render_reviewer_handoff_summary_markdown,
)
from tools.package_magma_alert_feed_release_evidence import (
    build_magma_alert_feed_release_evidence_package,
)
from tools.validate_magma_alert_feed_release_evidence import (
    validate_magma_alert_feed_release_evidence_package,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_magma_alert_feed_reviewer_handoff_summary.py"
COMMIT_SHA = "3" * 40
FIXED_NOW = datetime(2026, 5, 28, 18, 30, tzinfo=timezone.utc)
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


def _package_and_validation_report() -> tuple[dict, dict]:
    ops_bytes = json.dumps(_ops_payload(), sort_keys=True).encode("utf-8")
    metrics_bytes = _metrics_text().encode("utf-8")
    package = build_magma_alert_feed_release_evidence_package(
        ops_payload=_ops_payload(),
        metrics_text=_metrics_text(),
        release_ref="pr:755",
        commit_sha=COMMIT_SHA,
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-alert-feed-release",
        ci_run_ref="gh:run:reviewer-handoff",
        now_utc=FIXED_NOW,
        ops_sha256=_sha256_hex(ops_bytes),
        ops_size_bytes=len(ops_bytes),
        metrics_sha256=_sha256_hex(metrics_bytes),
        metrics_size_bytes=len(metrics_bytes),
    )
    report = validate_magma_alert_feed_release_evidence_package(
        package,
        ops_bytes=ops_bytes,
        metrics_bytes=metrics_bytes,
    )
    return package, report


def test_reviewer_handoff_summary_carries_validated_evidence_without_decision() -> None:
    package, report = _package_and_validation_report()

    summary = build_magma_alert_feed_reviewer_handoff_summary(
        package=package,
        validation_report=report,
        reviewer_agent_id="reviewer:wd-image1",
        bridge_event_ref="bridge:wd-image1-reviewer-handoff",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["validated_evidence"]["package_validation_ok"] is True
    assert summary["validated_evidence"]["digest_checks"] == {
        "ops_json": "match",
        "metrics_scrape": "match",
    }
    assert summary["manual_gate_snapshot"]["check_count"] == 7
    assert summary["manual_gate_snapshot"]["hold_reason_count"] == 0
    assert summary["manual_review_required"] is True
    assert summary["automatic_release_decision"] is False
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False

    markdown = render_reviewer_handoff_summary_markdown(summary)
    combined_output = json.dumps(summary, sort_keys=True) + "\n" + markdown
    assert "MAGMA Alert Feed Reviewer Handoff Summary" in markdown
    assert not any(marker in combined_output for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_summary_redacts_unsafe_validation_tokens() -> None:
    package, report = _package_and_validation_report()
    report["ok"] = False
    report["blockers"] = [
        "release_ref_unsafe",
        "C:/private/missing-package.json",
        "PRIVATE_MARKER",
    ]
    report["warnings"] = ["https://example.invalid/private"]

    summary = build_magma_alert_feed_reviewer_handoff_summary(
        package=package,
        validation_report=report,
        reviewer_agent_id="reviewer:wd-image1",
        bridge_event_ref="bridge:wd-image1-reviewer-handoff",
        now_utc=FIXED_NOW,
    )

    blockers = summary["validated_evidence"]["blockers"]
    assert summary["validated_evidence"]["package_validation_ok"] is False
    assert "release_ref_unsafe" in blockers
    assert "unsafe_marker_redacted" in blockers
    combined_output = json.dumps(summary, sort_keys=True)
    assert "missing-package" not in combined_output
    assert not any(marker in combined_output for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_summary_cli_json_is_path_free(tmp_path: Path) -> None:
    package, report = _package_and_validation_report()
    package_path = tmp_path / "magma_alert_feed_release_evidence.json"
    report_path = tmp_path / "validation.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            str(package_path),
            "--validation-json",
            str(report_path),
            "--reviewer-agent",
            "reviewer:wd-image1",
            "--bridge-event-ref",
            "bridge:wd-image1-reviewer-handoff",
            "--now",
            "2026-05-28T18:30:00Z",
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
    assert payload["automatic_release_decision"] is False
    assert payload["approval_granted"] is False
    assert str(tmp_path) not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_summary_cli_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            "C:/private/missing-package.json",
            "--validation-json",
            "C:/private/validation.json",
            "--reviewer-agent",
            "reviewer:wd-image1",
            "--bridge-event-ref",
            "bridge:wd-image1-reviewer-handoff",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == ["handoff_summary_failed:input_unreadable"]
    assert "missing-package" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def test_reviewer_handoff_summary_cli_malformed_validation_lists_are_path_free(
    tmp_path: Path,
) -> None:
    package, report = _package_and_validation_report()
    report["ok"] = False
    report["blockers"] = 123
    report["warnings"] = {"path": "C:/private/validation.json"}
    package_path = tmp_path / "magma_alert_feed_release_evidence.json"
    report_path = tmp_path / "validation.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--package-json",
            str(package_path),
            "--validation-json",
            str(report_path),
            "--reviewer-agent",
            "reviewer:wd-image1",
            "--bridge-event-ref",
            "bridge:wd-image1-reviewer-handoff",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["validated_evidence"]["package_validation_ok"] is False
    assert payload["validated_evidence"]["blockers"] == [
        "unsafe_marker_redacted"
    ]
    assert payload["validated_evidence"]["warnings"] == [
        "unsafe_marker_redacted"
    ]
    assert "Traceback" not in result.stderr
    assert str(tmp_path) not in result.stdout
    assert "validation.json" not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(data).hexdigest()
