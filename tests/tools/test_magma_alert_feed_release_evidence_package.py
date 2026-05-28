import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.package_magma_alert_feed_release_evidence import (
    build_magma_alert_feed_release_evidence_package,
    write_magma_alert_feed_release_evidence_package,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "package_magma_alert_feed_release_evidence.py"
COMMIT_SHA = "1" * 40
FIXED_NOW = datetime(2026, 5, 28, 17, 30, tzinfo=timezone.utc)
PRIVATE_MARKERS = (
    "PRIVATE_ANNOTATION",
    "PRIVATE_LABEL",
    "C:/private",
    "http://alertmanager/private",
    "Authorization",
    "Bearer ",
)


def _ops_payload() -> dict:
    return {
        "magma_share_import_handoff": {
            "provider_health": {
                "metrics_alert_state": {
                    "source": "prometheus_alertmanager_snapshot",
                    "status": "nominal",
                    "severity": "none",
                    "prometheus_alertmanager_feed": True,
                    "active_count": 1,
                    "active": [
                        {
                            "id": "MagmaHandoffRuntimeAuthorityReported",
                            "severity": "critical",
                            "summary": (
                                "PRIVATE_ANNOTATION path=C:/private/ops.json"
                            ),
                            "labels": {"host": "PRIVATE_LABEL"},
                            "generatorURL": "http://alertmanager/private",
                            "value": 1,
                        }
                    ],
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
                        "cache_ttl_seconds": 30,
                        "failure_backoff_seconds": 30,
                        "fetch_failure_count": 0,
                        "status": "nominal",
                        "failure_reason": "none",
                    },
                    "slo_panels": [
                        {
                            "id": "magma_alert_feed_availability_5m",
                            "status": "nominal",
                            "current_value": 1,
                            "title": "PRIVATE_LABEL",
                        },
                        {
                            "id": "magma_alert_feed_fetch_failures_15m",
                            "status": "nominal",
                            "current_value": 0,
                        },
                        {
                            "id": "magma_alert_feed_backoff_15m",
                            "status": "nominal",
                            "current_value": 0,
                        },
                        {
                            "id": "magma_alert_feed_cache_stale_15m",
                            "status": "nominal",
                            "current_value": 0,
                        },
                    ],
                    "drill_evidence": {
                        "required_artifacts": [
                            {
                                "id": "metrics_scrape",
                                "source": "C:/private/metrics.prom",
                            },
                            {
                                "id": "ops_snapshot",
                                "source": "http://alertmanager/private",
                            },
                            {"id": "runtime_window_logs"},
                        ],
                        "privacy_exclusions": ["headers"],
                        "controls_present": False,
                    },
                }
            }
        }
    }


def _metrics_text(
    *,
    available: float = 1.0,
    fetch_failures: float = 0.0,
    backoff_active: float = 0.0,
    cache_stale: float = 0.0,
    runtime_authority: float = 0.0,
    payload_files: float = 0.0,
    local_paths: float = 0.0,
) -> str:
    return "\n".join(
        [
            "# HELP ignored PRIVATE_ANNOTATION C:/private/metrics.prom",
            f"waggledance_magma_handoff_alert_feed_available {available}",
            (
                "waggledance_magma_handoff_alert_feed_fetch_failures_total "
                f"{fetch_failures}"
            ),
            (
                "waggledance_magma_handoff_alert_feed_backoff_active "
                f"{backoff_active}"
            ),
            (
                "waggledance_magma_handoff_alert_feed_cache_stale "
                f"{cache_stale}"
            ),
            (
                "waggledance_magma_handoff_runtime_authority_granted "
                f"{runtime_authority}"
            ),
            (
                "waggledance_magma_handoff_payload_files_imported "
                f"{payload_files}"
            ),
            (
                "waggledance_magma_handoff_local_paths_recorded "
                f"{local_paths}"
            ),
            "waggledance_magma_handoff_controls_present 0",
            "waggledance_magma_handoff_alert_feed_controls_present 0",
            (
                "waggledance_magma_handoff_alert_feed_runtime_authority_granted "
                "0"
            ),
            (
                "waggledance_magma_handoff_alert_feed_external_writes_applied "
                "0"
            ),
            'ignored_metric{path="C:/private/label"} 1',
            "",
        ]
    )


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    ops_path = tmp_path / "ops.json"
    metrics_path = tmp_path / "metrics.prom"
    ops_path.write_text(json.dumps(_ops_payload()), encoding="utf-8")
    metrics_path.write_text(_metrics_text(), encoding="utf-8")
    return ops_path, metrics_path


def test_magma_alert_feed_release_evidence_package_writes_sanitized_artifacts(
    tmp_path: Path,
) -> None:
    ops_path, metrics_path = _write_inputs(tmp_path)
    out_dir = tmp_path / "release-evidence"

    report = write_magma_alert_feed_release_evidence_package(
        ops_json_path=ops_path,
        metrics_scrape_path=metrics_path,
        out_dir=out_dir,
        release_ref="pr:753",
        commit_sha=COMMIT_SHA,
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-alert-feed-release",
        ci_run_ref="gh:run:26589621131",
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["artifact_names"] == [
        "magma_alert_feed_release_evidence.json",
        "magma_alert_feed_release_evidence.md",
    ]
    package_path = out_dir / "magma_alert_feed_release_evidence.json"
    markdown_path = out_dir / "magma_alert_feed_release_evidence.md"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    combined_output = "\n".join(
        [
            json.dumps(report, sort_keys=True),
            package_path.read_text(encoding="utf-8"),
            markdown_path.read_text(encoding="utf-8"),
        ]
    )

    assert package["manual_gate"]["automatic_release_decision"] is False
    assert package["manual_gate"]["manual_review_required"] is True
    assert package["manual_gate"]["current_sample_hold_reasons"] == []
    assert package["authority"]["runtime_authority_granted"] is False
    assert package["authority"]["payload_files_imported"] == 0.0
    assert package["authority"]["local_paths_recorded"] is False
    assert package["privacy"]["forbidden_tokens_found"] == []
    assert package["ops_evidence"]["active"] == [
        {
            "id": "MagmaHandoffRuntimeAuthorityReported",
            "severity": "critical",
            "metric": "waggledance_magma_handoff_runtime_authority_granted",
            "value": 1.0,
        }
    ]
    assert package["input_artifacts"]["ops_json"]["raw_payload_included"] is False
    assert package["input_artifacts"]["metrics_scrape"]["raw_scrape_included"] is False
    assert str(tmp_path) not in combined_output
    assert not any(marker in combined_output for marker in PRIVATE_MARKERS)


def test_release_evidence_package_records_holds_without_deciding_release() -> None:
    package = build_magma_alert_feed_release_evidence_package(
        ops_payload=_ops_payload(),
        metrics_text=_metrics_text(
            available=0,
            fetch_failures=2,
            backoff_active=1,
            cache_stale=1,
            runtime_authority=1,
            payload_files=1,
            local_paths=1,
        ),
        release_ref="pr:753",
        commit_sha=COMMIT_SHA,
        operator_agent_id="operator:wd-image1",
        bridge_event_ref="bridge:wd-image1-magma-alert-feed-release",
        ci_run_ref="gh:run:26589621131",
        now_utc=FIXED_NOW,
    )

    reasons = set(package["manual_gate"]["current_sample_hold_reasons"])
    assert "current_sample_hold:availability" in reasons
    assert "current_sample_hold:fetch_failures" in reasons
    assert "current_sample_hold:bounded_backoff" in reasons
    assert "current_sample_hold:cache_freshness" in reasons
    assert "current_sample_hold:runtime_authority_boundary" in reasons
    assert "current_sample_hold:payload_boundary" in reasons
    assert "current_sample_hold:local_path_boundary" in reasons
    assert package["manual_gate"]["status"] == "operator_review_required"
    assert package["manual_gate"]["automatic_release_decision"] is False
    assert package["authority"]["runtime_authority_granted"] is True
    assert package["authority"]["payload_files_imported"] == 1.0
    assert package["authority"]["local_paths_recorded"] is True


def test_release_evidence_package_rejects_unsafe_refs_and_existing_out_dir(
    tmp_path: Path,
) -> None:
    ops_path, metrics_path = _write_inputs(tmp_path)
    out_dir = tmp_path / "existing"
    out_dir.mkdir()

    with pytest.raises(ValueError, match="commit-sha"):
        build_magma_alert_feed_release_evidence_package(
            ops_payload=_ops_payload(),
            metrics_text=_metrics_text(),
            release_ref="pr:753",
            commit_sha="abc123",
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1",
            now_utc=FIXED_NOW,
        )
    with pytest.raises(ValueError, match="safe operator reference"):
        build_magma_alert_feed_release_evidence_package(
            ops_payload=_ops_payload(),
            metrics_text=_metrics_text(),
            release_ref="https://example.invalid/pr/753",
            commit_sha=COMMIT_SHA,
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1",
            now_utc=FIXED_NOW,
        )
    with pytest.raises(ValueError, match="new directory"):
        write_magma_alert_feed_release_evidence_package(
            ops_json_path=ops_path,
            metrics_scrape_path=metrics_path,
            out_dir=out_dir,
            release_ref="pr:753",
            commit_sha=COMMIT_SHA,
            operator_agent_id="operator:wd-image1",
            bridge_event_ref="bridge:wd-image1",
            now_utc=FIXED_NOW,
        )


def test_release_evidence_package_cli_json_is_path_free(tmp_path: Path) -> None:
    ops_path, metrics_path = _write_inputs(tmp_path)
    out_dir = tmp_path / "cli-evidence"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--ops-json",
            str(ops_path),
            "--metrics-scrape",
            str(metrics_path),
            "--out-dir",
            str(out_dir),
            "--release-ref",
            "pr:753",
            "--commit-sha",
            COMMIT_SHA,
            "--operator-agent",
            "operator:wd-image1",
            "--bridge-event-ref",
            "bridge:wd-image1-magma-alert-feed-release",
            "--ci-run-ref",
            "gh:run:26589621131",
            "--now",
            "2026-05-28T17:30:00Z",
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
    assert payload["manual_review_required"] is True
    assert payload["automatic_release_decision"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["external_fetch_performed"] is False
    assert str(tmp_path) not in result.stdout
    assert not any(marker in result.stdout for marker in PRIVATE_MARKERS)
