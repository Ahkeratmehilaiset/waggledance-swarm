# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_sample_projection import (
    build_memory_palace_sample_projection,
)
from tools.build_memory_palace_visualization_export import (
    build_memory_palace_visualization_export,
)
from tools.build_memory_palace_visualization_export_verification_summary import (
    build_memory_palace_visualization_export_verification_summary,
)
from tools.build_memory_palace_visualization_export_verification_summary_bridge_event_template import (
    build_memory_palace_visualization_export_verification_summary_bridge_event_template,
)
from tools.build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry,
)
from tools.build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary,
    render_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_memory_palace_visualization_export import (
    verify_memory_palace_visualization_export,
)
from tools.verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary.py"
)
FIXED_NOW = datetime(2026, 6, 8, 22, 45, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_INPUT_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "verification.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_memory_palace_template_index_entry_verification_summary_is_context_only() -> None:
    verification_report = _verification_report()

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-08T22:45:00Z"
    assert summary["template_only"] is True
    assert summary["read_side_report_only"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["runtime_route_changed"] is False
    assert summary["storage_write_performed"] is False
    assert summary["bridge_append_performed"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["solver_call_performed"] is False
    assert summary["scheduler_enqueue_performed"] is False
    assert summary["promotion_performed"] is False
    assert summary["gate_skip_performed"] is False
    assert summary["network_access_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["controls_present"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["external_writes_applied"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    verification = summary[
        "memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert verification["digest_checks"] == {
        SUMMARY_ARTIFACT_ID: "match",
        TEMPLATE_ARTIFACT_ID: "match",
    }
    assert verification["size_checks"] == {
        SUMMARY_ARTIFACT_ID: "match",
        TEMPLATE_ARTIFACT_ID: "match",
    }
    assert verification["schema_version_checks"] == {
        SUMMARY_ARTIFACT_ID: "match",
        TEMPLATE_ARTIFACT_ID: "match",
    }
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    assert verification["read_side_report_only"] is True
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True


def test_memory_palace_template_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    markdown = render_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "Scheduler enqueue performed: `false`" in markdown
    assert "Bridge append performed: `false`" in markdown
    assert "Gate skip performed: `false`" in markdown
    assert "Runtime authority granted: `false`" in markdown
    assert "enqueue scheduler work" in markdown
    assert "skip gates" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_bytes(_json_bytes(_verification_report()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-tools-1",
            "--handoff-ref",
            "memory-palace-template-index-review",
            "--now",
            "2026-06-08T22:45:00Z",
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
    assert payload["bridge_append_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["gate_skip_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_template_index_entry_verification_summary_rejects_bridge_authority() -> None:
    verification_report = _verification_report()
    verification_report["bridge_append_performed"] = True

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_bridge_append_performed_not_false" in summary["blockers"]
    assert summary["bridge_append_performed"] is False
    assert summary["operator_boundary"]["bridge_append_performed"] is False


def test_memory_palace_template_index_entry_verification_summary_rejects_nested_authority() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {"gate_skip_performed": True}

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_nested_authority_field_not_false:gate_skip_performed"
        in summary["blockers"]
    )
    assert summary["gate_skip_performed"] is False
    assert summary["runtime_authority_granted"] is False


def test_memory_palace_template_index_entry_verification_summary_rejects_nested_summary_authority_fields() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {
        "automatic_release_decision": True,
        "network_access_performed": True,
    }

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_nested_authority_field_not_false:"
        "automatic_release_decision"
        in summary["blockers"]
    )
    assert (
        "verification_report_nested_authority_field_not_false:"
        "network_access_performed"
        in summary["blockers"]
    )
    assert summary["automatic_release_decision"] is False
    assert summary["network_access_performed"] is False


def test_memory_palace_template_index_entry_verification_summary_rejects_nested_authority_container() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {
        "scheduler_authority": {"scheduler_enqueue_performed": False}
    }

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_forbidden_authority_container:scheduler_authority"
        in summary["blockers"]
    )
    assert summary["scheduler_enqueue_performed"] is False
    assert summary["runtime_authority_granted"] is False


def test_memory_palace_template_index_entry_verification_summary_rejects_nested_payload_and_path_keys() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {
        "raw_payload": "opaque",
        "source_path": "artifact.json",
    }

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary["blockers"]
    assert "verification_report_forbidden_path_key:source_path" in summary["blockers"]
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_memory_palace_template_index_entry_verification_summary_rejects_digest_mismatch_report() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["bridge_event_template"]["payload"][
        "source_summary_sha256"
    ] = "f" * 64
    report = verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=tampered_template,
        verification_summary_bytes=_json_bytes(artifacts["summary"]),
        bridge_event_template_bytes=_json_bytes(tampered_template),
    )

    summary = build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-tools-1",
        handoff_ref="memory-palace-template-index-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_not_ok" in summary["blockers"]
    assert f"digest_mismatch:{TEMPLATE_ARTIFACT_ID}" in summary["blockers"]
    assert (
        f"verification_check_not_match:digest_checks:{TEMPLATE_ARTIFACT_ID}"
        in summary["blockers"]
    )
    assert summary["bridge_append_performed"] is False
    assert summary["scheduler_enqueue_performed"] is False


def test_memory_palace_template_index_entry_verification_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            FORBIDDEN_INPUT_PATH,
            "--reviewer-agent",
            "codex-tools-1",
            "--handoff-ref",
            "memory-palace-template-index-review",
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
        "memory_palace_visualization_export_verification_summary_"
        "bridge_event_template_index_entry_verification_summary_failed:"
        "verification_report_unreadable"
    ]
    assert payload["bridge_append_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "verification.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_template_index_entry_verification_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-tools-1",
            "--handoff-ref",
            "memory-palace-template-index-review",
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
        "memory_palace_visualization_export_verification_summary_"
        "bridge_event_template_index_entry_verification_summary_failed:"
        "verification_report_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_template_index_entry_verification_summary_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-tools-1",
            "--handoff-ref",
            "memory-palace-template-index-review",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert (
        "memory_palace_visualization_export_verification_summary_"
        "bridge_event_template_index_entry_verification_summary_failed:"
        "verification_report_json_error"
        in result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout


def _verification_report() -> dict:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)
    return verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=_index_entry(artifacts),
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )


def _artifact_set() -> dict[str, dict]:
    export = build_memory_palace_visualization_export(
        build_memory_palace_sample_projection(),
    )
    verification = verify_memory_palace_visualization_export(export)
    summary = build_memory_palace_visualization_export_verification_summary(
        verification,
    )
    template = build_memory_palace_visualization_export_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-tools-1",
        task_id=(
            "codex-tools-1/"
            "memory-palace-visualization-summary-template-index-summary-20260608"
        ),
        to="operator,codex-lead-1,claude-rco-1,claude-rco-2",
        run_id="codex-tools-1-20260608T224500Z",
        session_id="codex-tools-1-20260608T224500Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
