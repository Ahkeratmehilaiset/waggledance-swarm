"""Offline tests for hex summary-template index-entry verifier summaries."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    TEMPLATE_ARTIFACT_ID,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary import (
    PROOF_ID,
    SUMMARY_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary,
    render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_verification_summary.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 11, 5, tzinfo=timezone.utc)


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


def test_hex_xcons_summary_template_index_entry_verification_summary_is_context_only() -> None:
    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-summary-template-index-entry-verification-summary",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["proof_id"] == PROOF_ID
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-20T11:05:00Z"
    assert summary["template_only"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["merge_decision_made"] is False
    assert summary["promotion_granted"] is False
    assert summary["claim_safe"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False
    assert summary["fast_track_priority"] is False
    assert summary["gate_skip_allowed"] is False
    assert summary["digest_payloads_included"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    assert verification["claim_safe"] is False
    assert verification["runtime_subdivision_authority_granted"] is False
    assert verification["bridge_event_written"] is False
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True


def test_hex_xcons_summary_template_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-summary-template-index-entry-verification-summary",
        now_utc=FIXED_NOW,
    )

    markdown = render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "Runtime subdivision authority granted: `false`" in markdown
    assert "Claim safe: `false`" in markdown
    assert "Fast-track priority: `false`" in markdown
    assert "upgrade claims" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_summary_template_index_entry_verification_summary_cli_json_is_path_free(
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
            "codex-lead-1",
            "--handoff-ref",
            "hex-xcons-summary-template-index-entry-verification-summary",
            "--now",
            "2026-06-20T11:05:00Z",
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
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_summary_template_index_entry_verification_summary_rejects_runtime_subdivision_authority() -> None:
    verification_report = _verification_report()
    verification_report["runtime_subdivision_authority_granted"] = True

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-summary-template-index-entry-verification-summary",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_runtime_subdivision_authority_granted_not_false"
        in summary["blockers"]
    )
    assert summary["runtime_subdivision_authority_granted"] is False
    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
    ]
    assert verification["runtime_subdivision_authority_granted"] is True


def test_hex_xcons_summary_template_index_entry_verification_summary_rejects_nested_authority() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {"claim_safe": True}

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-summary-template-index-entry-verification-summary",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_nested_authority_field_not_false:claim_safe"
        in summary["blockers"]
    )
    assert summary["claim_safe"] is False
    assert summary["runtime_authority_granted"] is False


def test_hex_xcons_summary_template_index_entry_verification_summary_rejects_nested_payload_and_path_keys() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {
        "raw_payload": "opaque",
        "source_path": "artifact.json",
    }

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-summary-template-index-entry-verification-summary",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary["blockers"]
    assert "verification_report_forbidden_path_key:source_path" in summary["blockers"]
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_hex_xcons_summary_template_index_entry_verification_summary_rejects_digest_mismatch_report() -> None:
    verification_report = _verification_report()
    verification_report["digest_checks"] = deepcopy(verification_report["digest_checks"])
    verification_report["digest_checks"][TEMPLATE_ARTIFACT_ID] = "mismatch"

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-summary-template-index-entry-verification-summary",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        f"verification_check_not_match:digest_checks:{TEMPLATE_ARTIFACT_ID}"
        in summary["blockers"]
    )
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False


def test_hex_xcons_summary_template_index_entry_verification_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            FORBIDDEN_INPUT_PATH,
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "hex-xcons-summary-template-index-entry-verification-summary",
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_failed:"
        "verification_report_unreadable"
    ]
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert "verification.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_summary_template_index_entry_verification_summary_non_finite_json_is_path_free(
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
            "codex-lead-1",
            "--handoff-ref",
            "hex-xcons-summary-template-index-entry-verification-summary",
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_failed:"
        "verification_report_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _verification_report() -> dict:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)
    return verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=_index_entry(artifacts),
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )


def _artifact_set() -> dict[str, dict]:
    summary = _index_entry_verification_summary()
    template = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-hex-xcons-summary-template-index-entry-verification-summary",
        to="operator,claude-rco-1,codex-tools-1",
        run_id="codex-lead-1-20260620T110500Z",
        session_id="codex-lead-1-20260620T110500Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _index_entry_verification_summary() -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=_source_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-index-verification-summary",
        now_utc=FIXED_NOW,
    )


def _source_verification_report() -> dict:
    artifacts = _source_artifact_set()
    raw = _source_artifact_bytes(artifacts)
    return verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=_source_index_entry(artifacts),
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["source_template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["source_template"],
    )


def _source_artifact_set() -> dict[str, dict]:
    digest = _good_digest()
    template = build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=digest,
        agent_id="codex-lead-1",
        task_id="wd-hex-xcons-summary-template-index-entry-verification-summary-source",
        to="operator,claude-rco-1,codex-tools-1",
        run_id="codex-lead-1-20260620T110500Z",
        session_id="codex-lead-1-20260620T110500Z",
        now_utc=FIXED_NOW,
    )
    return {"digest": digest, "source_template": template}


def _source_index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _source_artifact_bytes(artifacts)
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["source_template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["source_template"],
        now_utc=FIXED_NOW,
    )


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


def _good_digest() -> dict:
    return {
        "report_version": "wd.hex_upgrade_cross_consistency_digest.v1",
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


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        "summary": _json_bytes(artifacts["summary"]),
        "template": _json_bytes(artifacts["template"]),
    }


def _source_artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        "digest": _json_bytes(artifacts["digest"]),
        "source_template": _json_bytes(artifacts["source_template"]),
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
