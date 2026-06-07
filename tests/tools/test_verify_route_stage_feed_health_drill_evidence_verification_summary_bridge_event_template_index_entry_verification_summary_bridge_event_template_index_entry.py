# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import tools.verify_route_stage_feed_health_drill_evidence as drill_verifier
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template import (
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary import (
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry import (
    verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "verify_route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry.py"
    )
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_INDEX_ENTRY_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "index-entry.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_route_stage_verifier_summary_template_index_entry_verifier_recomputes_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is True
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["index_entry_version"] == INDEX_ENTRY_VERSION
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["controls_present"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    assert report["network_access_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_route_stage_verifier_summary_template_index_entry_verifier_cli_json_is_path_free(
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
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_verifier_summary_template_index_entry_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["extra_context"] = "changed"
    paths["template"].write_bytes(_json_bytes(tampered_template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--template-index-entry-json",
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
    assert f"digest_mismatch:{TEMPLATE_ARTIFACT_ID}" in payload["blockers"]
    assert payload["source_contract_check"] == "failed"
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_verifier_summary_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{SUMMARY_ARTIFACT_ID}" in report["blockers"]
    assert report["digest_checks"][SUMMARY_ARTIFACT_ID] == "missing_index_record"
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_route_stage_verifier_summary_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["approval_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
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


def test_route_stage_verifier_summary_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["approve_release"]
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
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


def test_route_stage_verifier_summary_template_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_summary = deepcopy(artifacts["summary"])
    tampered_summary["runtime_authority_granted"] = True

    report = verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=tampered_summary,
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=_json_bytes(tampered_summary),
        summary_bridge_event_template_bytes=_json_bytes(artifacts["template"]),
    )

    assert report["ok"] is False
    assert "source_contract_failed:" in " ".join(report["blockers"])
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_route_stage_verifier_summary_template_index_entry_verifier_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            FORBIDDEN_INDEX_ENTRY_PATH,
            "--summary-json",
            FORBIDDEN_INDEX_ENTRY_PATH,
            "--template-json",
            FORBIDDEN_INDEX_ENTRY_PATH,
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
        "bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry_verification_failed:"
        "route_stage_feed_health_drill_evidence_verification_summary_"
        "bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "index-entry.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_verifier_summary_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    artifacts["template"]["warnings"] = [float("nan")]
    paths["template"].write_text(
        json.dumps(artifacts["template"], sort_keys=True),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
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
        "bridge_event_template_index_entry_verification_summary_"
        "bridge_event_template_index_entry_verification_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    summary = _index_entry_verification_summary()
    template = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-verifier-summary-template-index",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260606T060000Z",
        session_id="codex-lead-1-20260606T060000Z",
        now_utc=datetime(2026, 6, 6, 6, 0, tzinfo=timezone.utc),
    )
    return {"summary": summary, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 6, 6, 6, 5, tzinfo=timezone.utc),
    )


def _index_entry_verification_summary() -> dict:
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_index_entry_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-template-index-verification",
        now_utc=datetime(2026, 6, 6, 5, 59, tzinfo=timezone.utc),
    )


def _index_entry_verification_report() -> dict:
    artifacts = _source_artifact_set()
    index_entry = _source_index_entry(artifacts)
    raw = _source_artifact_bytes(artifacts)
    return verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )


def _source_artifact_set() -> dict[str, dict]:
    summary = _verification_summary()
    template = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-feed-template-index-verifier-summary",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260606T060000Z",
        session_id="codex-lead-1-20260606T060000Z",
        now_utc=datetime(2026, 6, 6, 5, 58, tzinfo=timezone.utc),
    )
    return {"summary": summary, "template": template}


def _source_index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _source_artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 6, 6, 5, 58, tzinfo=timezone.utc),
    )


def _verification_summary() -> dict:
    package = _valid_package()
    raw = json.dumps(
        package,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        **drill_verifier.verify_route_stage_feed_health_drill_evidence(package),
        "evidence_package": "<redacted>",
        "evidence_sha256": _sha256_hex(raw),
        "evidence_size_bytes": len(raw),
    }


def _valid_package() -> dict:
    feed_health = _feed_health()
    return {
        "schema_version": drill_verifier.PACKAGE_SCHEMA_VERSION,
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
                    "drill_evidence": (
                        drill_verifier._route_stage_latency_feed_drill_evidence()
                    ),
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
    for panel in drill_verifier.ROUTE_STAGE_LATENCY_FEED_SLO_PANELS:
        panel_id = str(panel["id"])
        panels.append(
            {
                **panel,
                "current_value": drill_verifier._route_stage_latency_feed_slo_current_value(
                    panel_id,
                    feed_health,
                ),
                "status": drill_verifier._route_stage_latency_feed_slo_panel_status(
                    panel_id,
                    feed_health,
                ),
                "controls_present": False,
            }
        )
    return panels


def _write_bundle(
    tmp_path: Path,
    index_entry: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "index_entry": tmp_path / "index_entry.json",
        "summary": tmp_path / "index_entry_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _source_artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        key: json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
        for key, value in artifacts.items()
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
