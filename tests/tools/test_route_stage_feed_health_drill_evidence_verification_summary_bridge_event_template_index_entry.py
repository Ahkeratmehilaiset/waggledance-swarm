# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import tools.verify_route_stage_feed_health_drill_evidence as verifier
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template import (
    TEMPLATE_VERSION,
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_index_entry.py"
    )
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FIXED_NOW = datetime(2026, 6, 5, 7, 15, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_TEMPLATE_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "template.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_route_stage_feed_health_verification_summary_bridge_event_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-05T07:15:00Z"
    assert entry["template_version"] == TEMPLATE_VERSION
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[
        "route_stage_feed_health_drill_evidence_verification_summary"
    ]["sha256"] == _sha256_hex(raw["summary"])
    assert by_id[
        "route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template"
    ]["sha256"] == _sha256_hex(raw["template"])
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])
    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["source_summary_sha256"] == _sha256_hex(raw["summary"])
    assert entry["operator_boundary"]["manual_review_required"] is True
    assert entry["operator_boundary"]["approval_granted"] is False
    assert entry["operator_boundary"]["release_decision_made"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["external_fetch_performed"] is False
    assert entry["runtime_controls_added"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False


def test_route_stage_feed_health_verification_summary_bridge_event_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-summary-json",
            str(paths["summary"]),
            "--bridge-event-template-json",
            str(paths["template"]),
            "--now",
            "2026-06-05T07:15:00Z",
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
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_feed_health_verification_summary_bridge_event_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"]["evidence_sha256"] = (
        "f" * 64
    )
    raw = _artifact_bytes(artifacts)

    try:
        build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
            verification_summary=artifacts["summary"],
            bridge_event_template_report=artifacts["template"],
            verification_summary_bytes=raw["summary"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "bridge_event_template_rebuilt_mismatch"
    else:  # pragma: no cover - explicit assertion keeps error message useful.
        raise AssertionError("expected template drift rejection")


def test_route_stage_feed_health_verification_summary_bridge_event_template_index_entry_rejects_summary_contract_forgery() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = deepcopy(artifacts["summary"])
    artifacts["summary"]["runtime_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    try:
        build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
            verification_summary=artifacts["summary"],
            bridge_event_template_report=artifacts["template"],
            verification_summary_bytes=raw["summary"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert (
            getattr(exc, "code", "")
            == "verification_summary_runtime_authority_granted_not_false"
        )
    else:  # pragma: no cover
        raise AssertionError("expected summary authority rejection")


def test_route_stage_feed_health_verification_summary_bridge_event_template_index_entry_missing_input_is_path_free(
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
            FORBIDDEN_TEMPLATE_PATH,
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
        "bridge_event_template_index_entry_failed:"
        "route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "template.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_feed_health_verification_summary_bridge_event_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": NaN}', encoding="utf-8")

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
        "route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_index_entry_failed:"
        "route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    summary = _verification_summary()
    template = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-feed-template-index",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260605T071500Z",
        session_id="codex-lead-1-20260605T071500Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _verification_summary() -> dict:
    package = _valid_package()
    raw = json.dumps(
        package,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        **verifier.verify_route_stage_feed_health_drill_evidence(package),
        "evidence_package": "<redacted>",
        "evidence_sha256": _sha256_hex(raw),
        "evidence_size_bytes": len(raw),
    }


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


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        key: json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
        for key, value in artifacts.items()
    }


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "summary": tmp_path / "summary.json",
        "template": tmp_path / "template.json",
    }
    for key, path in paths.items():
        path.write_text(
            json.dumps(artifacts[key], sort_keys=True, allow_nan=False),
            encoding="utf-8",
        )
    return paths


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
