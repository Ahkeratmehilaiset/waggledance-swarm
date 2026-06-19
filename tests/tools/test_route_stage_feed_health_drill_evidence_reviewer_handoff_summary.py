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
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary import (
    SUMMARY_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary,
    render_route_stage_feed_health_drill_evidence_reviewer_handoff_summary_markdown,
)
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
    / "build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary.py"
)
COMMIT = "0123456789abcdef0123456789abcdef01234567"
FIXED_NOW = datetime(2026, 6, 19, 6, 20, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_VERIFICATION_PATH = _joined(
    FORBIDDEN_PATH_PREFIX,
    "/",
    "final-verification.json",
)
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_route_stage_feed_health_reviewer_handoff_summary_renders_final_verifier_without_authority() -> None:
    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=_final_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-19T06:20:00Z"
    owner = summary["reviewer_ownership"]
    assert owner["reviewer_agent_id"] == "claude-rco-1"
    assert owner["handoff_ref"] == (
        "bridge:handoff:route-stage-feed-health-drill-reviewer-handoff"
    )
    assert owner["manual_review_required"] is True
    assert owner["approval_granted"] is False
    assert owner["release_decision_made"] is False
    chain = summary["validated_verifier_chain"]
    assert chain["verification_ok"] is True
    assert chain["verification_version"] == VERIFICATION_VERSION
    assert chain["index_entry_version"] == INDEX_ENTRY_VERSION
    assert chain["artifact_count_checked"] == 2
    assert set(chain["digest_checks"].values()) == {"match"}
    assert set(chain["size_checks"].values()) == {"match"}
    assert set(chain["schema_version_checks"].values()) == {"match"}
    assert chain["source_contract_check"] == "match"
    assert chain["rebuilt_index_entry_check"] == "match"
    assert chain["bridge_event_schema_check"] == "match"
    assert chain["template_only"] is True
    assert chain["blockers"] == []
    boundary = summary["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["boundary_blockers"] == []
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["controls_present"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["external_writes_applied"] is False
    assert summary["network_access_performed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(summary, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_route_stage_feed_health_reviewer_handoff_summary_markdown_is_path_free() -> None:
    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=_final_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
        now_utc=FIXED_NOW,
    )

    markdown = render_route_stage_feed_health_drill_evidence_reviewer_handoff_summary_markdown(
        summary
    )

    assert "Reviewer Handoff Summary" in markdown
    assert "Approval granted: `false`" in markdown
    assert "Direct bridge write performed: `false`" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_feed_health_reviewer_handoff_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "final-verification.json"
    verification_path.write_text(
        json.dumps(
            _final_verification_report(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
            "--now",
            "2026-06-19T06:20:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["summary_version"] == SUMMARY_VERSION
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_feed_health_reviewer_handoff_summary_rejects_authority_escalation() -> None:
    report = deepcopy(_final_verification_report())
    report["approval_granted"] = True

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_approval_granted_not_false" in summary["blockers"]
    assert "verification_report_approval_granted_not_false" in (
        summary["operator_boundary"]["boundary_blockers"]
    )
    assert summary["approval_granted"] is False


def test_route_stage_feed_health_reviewer_handoff_summary_rejects_digest_mismatch() -> None:
    report = deepcopy(_final_verification_report())
    report["digest_checks"][SUMMARY_ARTIFACT_ID] = "mismatch"

    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        f"verification_check_not_match:digest_checks:{SUMMARY_ARTIFACT_ID}"
        in summary["blockers"]
    )
    assert summary["validated_verifier_chain"]["digest_checks"][SUMMARY_ARTIFACT_ID] == (
        "mismatch"
    )
    assert summary["direct_bridge_write_performed"] is False


def test_route_stage_feed_health_reviewer_handoff_summary_missing_input_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            FORBIDDEN_VERIFICATION_PATH,
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
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
    assert payload["blockers"] == [
        "route_stage_feed_health_drill_evidence_reviewer_handoff_summary_failed:"
        "verification_report_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert "final-verification.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _final_verification_report() -> dict:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)
    return verify_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )


def _artifact_set() -> dict[str, dict]:
    summary = _index_entry_verification_summary()
    template = build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-verifier-summary-template-index",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260619T062000Z",
        session_id="codex-lead-1-20260619T062000Z",
        now_utc=datetime(2026, 6, 19, 6, 20, tzinfo=timezone.utc),
    )
    return {"summary": summary, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 6, 19, 6, 21, tzinfo=timezone.utc),
    )


def _index_entry_verification_summary() -> dict:
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_index_entry_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-template-index-verification",
        now_utc=datetime(2026, 6, 19, 6, 19, tzinfo=timezone.utc),
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
        run_id="codex-lead-1-20260619T061800Z",
        session_id="codex-lead-1-20260619T061800Z",
        now_utc=datetime(2026, 6, 19, 6, 18, tzinfo=timezone.utc),
    )
    return {"summary": summary, "template": template}


def _source_index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _source_artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_verification_summary_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 6, 19, 6, 18, tzinfo=timezone.utc),
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
    return [
        {
            **panel,
            "current_value": drill_verifier._route_stage_latency_feed_slo_current_value(
                str(panel["id"]),
                feed_health,
            ),
            "status": drill_verifier._route_stage_latency_feed_slo_panel_status(
                str(panel["id"]),
                feed_health,
            ),
            "controls_present": False,
        }
        for panel in drill_verifier.ROUTE_STAGE_LATENCY_FEED_SLO_PANELS
    ]


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        "summary": json.dumps(
            artifacts["summary"],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        "template": json.dumps(
            artifacts["template"],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
    }


def _source_artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        "summary": json.dumps(
            artifacts["summary"],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
        "template": json.dumps(
            artifacts["template"],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8"),
    }


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
