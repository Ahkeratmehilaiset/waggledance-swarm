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
    SUMMARY_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary,
    render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary.py"
)
FIXED_NOW = datetime(2026, 6, 25, 22, 0, tzinfo=timezone.utc)
FORBIDDEN_PATH_PREFIX = "C:/private"
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    "http://",
    "https://",
    "waggledance-agent-worktrees",
)


def test_hex_upgrade_index_entry_verification_summary_is_context_only() -> None:
    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-upgrade-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-25T22:00:00Z"
    assert summary["template_only"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False
    assert summary["fast_track_priority"] is False
    assert summary["gate_skip_allowed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False

    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert verification["runtime_subdivision_authority_granted"] is False
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True


def test_hex_upgrade_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-upgrade-summary-review",
        now_utc=FIXED_NOW,
    )

    markdown = render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "Runtime subdivision authority granted: `false`" in markdown
    assert "Runtime authority granted: `false`" in markdown
    assert "Gate skip allowed: `false`" in markdown
    assert "does not append bridge events" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_upgrade_index_entry_verification_summary_cli_json_is_path_free(
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
            "hex-upgrade-summary-review",
            "--now",
            "2026-06-25T22:00:00Z",
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


def test_hex_upgrade_index_entry_verification_summary_rejects_runtime_authority() -> None:
    verification_report = _verification_report()
    verification_report["runtime_subdivision_authority_granted"] = True

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-upgrade-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_runtime_subdivision_authority_granted_not_false"
        in summary["blockers"]
    )
    assert summary["runtime_subdivision_authority_granted"] is False
    verification = summary[
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]
    assert verification["runtime_subdivision_authority_granted"] is True


def test_hex_upgrade_index_entry_verification_summary_rejects_nested_authority() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {"runtime_authority_granted": True}

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-upgrade-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_nested_authority_field_not_false:"
        "runtime_authority_granted"
        in summary["blockers"]
    )
    assert summary["runtime_authority_granted"] is False


def test_hex_upgrade_index_entry_verification_summary_rejects_nested_payload_and_path_keys() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {
        "raw_payload": "opaque",
        "source_path": "artifact.json",
    }

    summary = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-upgrade-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary["blockers"]
    assert "verification_report_forbidden_path_key:source_path" in summary["blockers"]
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_hex_upgrade_index_entry_verification_summary_cli_rejects_duplicate_keys(
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
            "codex-lead-1",
            "--handoff-ref",
            "hex-upgrade-summary-review",
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
    assert payload["runtime_subdivision_authority_granted"] is False
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout


def test_hex_upgrade_index_entry_verification_summary_cli_rejects_forbidden_marker(
    tmp_path: Path,
) -> None:
    verification_report = _verification_report()
    verification_report["note"] = f"{FORBIDDEN_PATH_PREFIX}/artifact.json"
    verification_path = tmp_path / "verification.json"
    verification_path.write_bytes(_json_bytes(verification_report))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "hex-upgrade-summary-review",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    summary = json.loads(result.stdout)
    assert summary["ok"] is False
    assert summary["local_paths_recorded"] is False
    assert "verification_report_contains_forbidden_marker" in summary["blockers"]
    assert FORBIDDEN_PATH_PREFIX not in result.stdout


def _verification_report() -> dict:
    template = build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=_good_digest(),
        agent_id="fable-5",
        task_id="demo-task",
        now_utc=FIXED_NOW,
    )
    template_bytes = _json_bytes(template)
    entry = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template,
        bridge_event_template_bytes=template_bytes,
        now_utc=FIXED_NOW,
    )
    return verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=entry,
        bridge_event_template_report=template,
        index_entry_bytes=_json_bytes(entry),
        bridge_event_template_bytes=template_bytes,
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


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")
