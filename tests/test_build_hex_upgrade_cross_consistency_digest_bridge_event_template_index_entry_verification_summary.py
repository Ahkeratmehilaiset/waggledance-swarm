from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (
    DIGEST_REPORT_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template,
)
import tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as indexer
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary,
    render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_markdown,
)
import tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as verifier


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 9, 0, tzinfo=timezone.utc)
FIXED_NOW_TEXT = "2026-06-20T09:00:00Z"


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


FORBIDDEN_PATH_PREFIX = "".join(("C", ":", "/", "private"))
FORBIDDEN_INPUT_PATH = "".join(
    (FORBIDDEN_PATH_PREFIX, "/", "index-entry-verification.json")
)
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    _chars(80, 82, 73, 86, 65, 84, 69, 95),
    "".join((_chars(104, 116, 116, 112), ":", "/", "/")),
    "".join((_chars(104, 116, 116, 112, 115), ":", "/", "/")),
)


def test_hex_xcons_template_index_entry_verification_summary_renders_without_authority() -> None:
    report = _verification_report()

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == FIXED_NOW_TEXT
    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == verifier.VERIFICATION_VERSION
    assert verification["index_entry_version"] == indexer.INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 1
    assert verification["digest_checks"] == {
        indexer.TEMPLATE_ARTIFACT_ID: "match",
    }
    assert verification["size_checks"] == {
        indexer.TEMPLATE_ARTIFACT_ID: "match",
    }
    assert verification["schema_version_checks"] == {
        indexer.TEMPLATE_ARTIFACT_ID: "match",
    }
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["runtime_subdivision_authority_granted"] is False
    assert verification["claim_safe"] is False
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True
    assert summary["operator_boundary"]["boundary_blockers"] == []
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["claim_safe"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert summary["path_free_verified"] is True
    assert not any(
        marker in json.dumps(summary, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_hex_xcons_template_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-template-index-verification",
        now_utc=FIXED_NOW,
    )

    markdown = render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "Hex Cross-Consistency Index-Entry Verification Summary" in markdown
    assert "Claim safe: `false`" in markdown
    assert "Runtime subdivision authority granted: `false`" in markdown
    assert "Artifact payloads included: `false`" in markdown
    assert "Local paths recorded: `false`" in markdown
    assert "upgrade claims" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification.json"
    report_path.write_bytes(_json_bytes(_verification_report()))

    result = _run_cli(
        "--verification-json",
        str(report_path),
        "--reviewer-agent",
        "codex-lead-1",
        "--handoff-ref",
        "bridge:handoff:hex-xcons-template-index-verification",
        "--now",
        FIXED_NOW_TEXT,
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verification_summary_rejects_verifier_drift() -> None:
    report = _verification_report()
    report["bridge_event_schema_check"] = "failed"

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_bridge_event_schema_check_not_match" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["claim_safe"] is False


def test_hex_xcons_template_index_entry_verification_summary_rejects_authority_escalation() -> None:
    report = _verification_report()
    report["runtime_subdivision_authority_granted"] = True

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-template-index-verification",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_runtime_subdivision_authority_granted_not_false"
        in summary["blockers"]
    )
    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]
    assert verification["runtime_subdivision_authority_granted"] is True
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False


def test_hex_xcons_template_index_entry_verification_summary_rejects_nested_payload_path_and_authority() -> None:
    report = _verification_report()
    report["nested"] = {
        "raw_payload": "opaque",
        "source_path": "artifact.json",
        "claim_safe": True,
    }

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-template-index-verification",
        now_utc=FIXED_NOW,
    )

    serialized = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary[
        "blockers"
    ]
    assert "verification_report_forbidden_path_key:source_path" in summary["blockers"]
    assert (
        "verification_report_nested_authority_field_not_false:claim_safe"
        in summary["blockers"]
    )
    assert "opaque" not in serialized
    assert "artifact.json" not in serialized
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_hex_xcons_template_index_entry_verification_summary_missing_input_is_path_free() -> None:
    result = _run_cli(
        "--verification-json",
        FORBIDDEN_INPUT_PATH,
        "--reviewer-agent",
        "codex-lead-1",
        "--handoff-ref",
        "bridge:handoff:hex-xcons-template-index-verification",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_failed:"
        "verification_report_unreadable"
    ]
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert "index-entry-verification.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verification_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification.json"
    report_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = _run_cli(
        "--verification-json",
        str(report_path),
        "--reviewer-agent",
        "codex-lead-1",
        "--handoff-ref",
        "bridge:handoff:hex-xcons-template-index-verification",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_failed:"
        "verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert report_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _verification_report() -> dict:
    template_report = _template_report()
    index_entry = _index_entry(template_report)
    raw = _json_bytes(template_report)
    return verifier.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=raw,
    )


def _digest() -> dict:
    return {
        "report_version": DIGEST_REPORT_VERSION,
        "reviewer_summary_present": True,
        "shadow_only_invariant_present": True,
        "chain_final_summary_present": True,
        "all_views_present": True,
        "reviewer_clean": True,
        "shadow_only_clean": True,
        "chain_summary_clean": True,
        "cross_consistent": True,
        "path_free_verified": True,
        "claim_safe": False,
    }


def _template_report() -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=_digest(),
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-xcons-template-index-entry-verification-summary",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260620T090000Z",
        session_id="codex-lead-1-20260620T090000Z",
        now_utc=FIXED_NOW,
    )


def _index_entry(template_report: dict) -> dict:
    return indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_json_bytes(template_report),
        now_utc=FIXED_NOW,
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
