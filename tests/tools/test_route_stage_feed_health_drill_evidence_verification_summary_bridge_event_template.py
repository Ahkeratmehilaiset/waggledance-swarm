# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path

import tools.verify_route_stage_feed_health_drill_evidence as verifier
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    TEMPLATE_VERSION,
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template.py"
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_SUMMARY_PATH = _joined(
    FORBIDDEN_PATH_PREFIX, "/", "verification-summary.json"
)
FORBIDDEN_REPORT_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "summary.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def _feed_health() -> dict:
    return {
        "source": "prometheus_alertmanager_adapter",
        "status": "warning",
        "configured": True,
        "available": True,
        "cache_enabled": True,
        "cache_present": True,
        "cache_stale": True,
        "backoff_active": True,
        "cache_ttl_seconds": 30.0,
        "failure_backoff_seconds": 30.0,
        "timeout_seconds": 3.0,
        "max_response_bytes": 1000000.0,
        "last_response_bytes": 1200.0,
        "cache_hit_count": 1.0,
        "cache_miss_count": 2.0,
        "fetch_success_count": 3.0,
        "fetch_failure_count": 2.0,
        "backoff_skip_count": 1.0,
        "last_success_at": "2026-06-01T16:00:00Z",
        "last_failure_at": "2026-06-01T16:01:00Z",
        "last_failure_reason": "FEED_READ_FAILED",
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _slo_panels(feed_health: dict) -> list[dict]:
    panels = []
    for panel in verifier.ROUTE_STAGE_LATENCY_FEED_SLO_PANELS:
        panel_id = str(panel["id"])
        panels.append(
            {
                **panel,
                "current_value": verifier._route_stage_latency_feed_slo_current_value(
                    panel_id,
                    feed_health,
                ),
                "status": verifier._route_stage_latency_feed_slo_panel_status(
                    panel_id,
                    feed_health,
                ),
                "controls_present": False,
            }
        )
    return panels


def _valid_package() -> dict:
    feed_health = _feed_health()
    return {
        "schema_version": verifier.PACKAGE_SCHEMA_VERSION,
        "commit": COMMIT,
        "collected_at_utc": "2026-06-01T16:02:00Z",
        "metrics_scrape": {
            "source": "/metrics",
            "fields": {
                "waggledance_route_stage_latency_feed_status": "warning",
                "waggledance_route_stage_latency_feed_failure_reason": (
                    "FEED_READ_FAILED"
                ),
                "waggledance_route_stage_latency_feed_backoff_active": 1,
            },
        },
        "api_ops": {
            "route_stage_latency": {
                "feed_state": {
                    "feed_health": feed_health,
                    "slo_panels": _slo_panels(feed_health),
                    "drill_evidence": verifier._route_stage_latency_feed_drill_evidence(),
                },
            },
        },
        "operator_log_window": {
            "timestamp": "2026-06-01T16:02:00Z",
            "commit": COMMIT,
            "sanitized_reason": "FEED_READ_FAILED",
        },
    }


def _verification_summary(tmp_path: Path) -> dict:
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text(json.dumps(_valid_package()), encoding="utf-8")
    return verifier.build_report(evidence_package=package_path)


def test_route_stage_feed_health_verification_summary_bridge_event_template_validates_schema(
    tmp_path: Path,
) -> None:
    report = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
        summary=_verification_summary(tmp_path),
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-feed-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260604T000000Z",
        session_id="codex-lead-1-20260604T000000Z",
        now_utc=datetime(2026, 6, 4, 0, 0, tzinfo=timezone.utc),
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(event, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["template_only"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert report["network_access_performed"] is False
    assert event["type"] == "handoff"
    assert event["status"] == EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["pid"] == 0

    payload = event["payload"]
    assert payload["schema_version"] == TEMPLATE_VERSION
    assert payload["verification_schema_version"] == verifier.VERIFICATION_SCHEMA_VERSION
    assert payload["package_schema_version"] == verifier.PACKAGE_SCHEMA_VERSION
    assert payload["template_only"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    verification = payload["route_stage_feed_health_drill_evidence_verification"]
    assert verification["verification_ok"] is True
    assert verification["verified"] is True
    assert verification["payload_included"] is False
    assert verification["blocker_count"] == 0
    assert all(verification["checks"].values())
    assert verification["required_artifact_counts"] == {
        "metrics_scrape": 3,
        "api_ops": 3,
        "operator_log_window": 3,
    }


def test_route_stage_feed_health_verification_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "verification-summary.json"
    summary_path.write_text(
        json.dumps(_verification_summary(tmp_path), sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-feed-template",
            "--to",
            "operator,claude-rco-1",
            "--run-id",
            "codex-lead-1-20260604T000000Z",
            "--session-id",
            "codex-lead-1-20260604T000000Z",
            "--now",
            "2026-06-04T00:00:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = payload["bridge_event_template"]
    validate_event(event)
    assert payload["direct_bridge_write_performed"] is False
    assert payload["approval_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert event["payload"]["evidence_sha256"]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_feed_health_verification_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            FORBIDDEN_SUMMARY_PATH,
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-feed-template",
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
        "route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_failed:verification_summary_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "verification-summary.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_feed_health_verification_summary_bridge_event_template_rejects_unsafe_bridge_fields(
    tmp_path: Path,
) -> None:
    report = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
        summary=_verification_summary(tmp_path),
        agent_id="Codex",
        task_id="wd-image1-route-stage-feed-template",
        to="operator,claude-rco-1",
    )

    assert report["ok"] is False
    assert report["blockers"] == [
        "route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_failed:agent_unsafe"
    ]
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False


def test_route_stage_feed_health_verification_summary_bridge_event_template_blocks_unsafe_summary_contract(
    tmp_path: Path,
) -> None:
    cases: tuple[tuple[str, Callable[[dict], None], str], ...] = (
        (
            "summary_not_ok",
            lambda summary: summary.__setitem__("ok", False),
            "verification_summary_not_ok",
        ),
        (
            "blocker_present",
            lambda summary: summary.__setitem__("blockers", ["not_safe"]),
            "verification_summary_blockers_present",
        ),
        (
            "blockers_string_not_sequence",
            lambda summary: summary.__setitem__("blockers", "not-a-list"),
            "verification_summary_blockers_invalid",
        ),
        (
            "blockers_non_string_entry",
            lambda summary: summary.__setitem__(
                "blockers",
                [{"unexpected": "non_string_blocker"}],
            ),
            "verification_summary_blockers_invalid",
        ),
        (
            "authority_flag",
            lambda summary: summary.__setitem__("runtime_authority_granted", True),
            "verification_summary_runtime_authority_granted_not_false",
        ),
        (
            "check_false",
            lambda summary: summary["checks"].__setitem__(
                "no_forbidden_raw_payload",
                False,
            ),
            "verification_summary_check_no_forbidden_raw_payload_not_true",
        ),
        (
            "missing_redaction",
            lambda summary: summary.__setitem__("evidence_package", "payload.json"),
            "verification_summary_evidence_package_not_redacted",
        ),
        (
            "digest_invalid",
            lambda summary: summary.__setitem__("evidence_sha256", "abc"),
            "verification_summary_evidence_sha256_invalid",
        ),
    )

    for _name, mutate, expected_reason in cases:
        summary = deepcopy(_verification_summary(tmp_path))
        mutate(summary)
        report = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
            summary=summary,
            agent_id="codex-lead-1",
            task_id="wd-image1-route-stage-feed-template",
            to="operator,claude-rco-1",
        )

        assert report["ok"] is False
        assert report["blockers"] == [
            "route_stage_feed_health_drill_evidence_verification_summary_"
            f"bridge_event_template_failed:{expected_reason}"
        ]
        assert report["direct_bridge_write_performed"] is False
        assert report["approval_granted"] is False
        assert report["artifact_payloads_included"] is False


def test_route_stage_feed_health_verification_summary_bridge_event_template_rejects_path_markers_path_free(
    tmp_path: Path,
) -> None:
    summary = _verification_summary(tmp_path)
    summary["warnings"] = [FORBIDDEN_REPORT_PATH]

    report = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-feed-template",
        to="operator,claude-rco-1",
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert report["blockers"] == [
        "route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_failed:verification_summary_not_path_free"
    ]
    assert "summary.json" not in encoded
    assert FORBIDDEN_PATH_PREFIX not in encoded


def test_route_stage_feed_health_verification_summary_bridge_event_template_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "verification-summary.json"
    summary_path.write_text('{"schema_version": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-route-stage-feed-template",
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
        "route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_failed:verification_summary_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)
