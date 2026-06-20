from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    VERIFICATION_KEY,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary,
    render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    _AUTHORITY_FALSE_FIELDS,
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT / "tests"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from test_verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (  # noqa: E402
    _artifact_bytes,
    _artifact_set,
    _index_entry,
    _json_bytes,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_index_entry_"
        "verification_summary.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 10, 30, tzinfo=timezone.utc)
FIXED_NOW_TEXT = "2026-06-20T10:30:00Z"


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


FORBIDDEN_PATH_PREFIX = "".join(("C", ":", "/", "private"))
FORBIDDEN_VERIFICATION_PATH = "".join(
    (FORBIDDEN_PATH_PREFIX, "/", "index-entry-verification.json")
)
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    _chars(80, 82, 73, 86, 65, 84, 69, 95),
    "".join((_chars(104, 116, 116, 112), ":", "/", "/")),
    "".join((_chars(104, 116, 116, 112, 115), ":", "/", "/")),
)


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_renders_without_authority() -> None:
    summary = _build_summary(_index_entry_verification_report())

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == FIXED_NOW_TEXT
    verification = summary[VERIFICATION_KEY]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True
    assert summary["operator_boundary"]["boundary_blockers"] == []
    assert summary["template_only"] is True
    assert summary["manual_review_required"] is True
    assert summary["path_free_verified"] is True
    assert all(summary[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    assert all(
        summary["operator_boundary"][field] is False
        for field in _AUTHORITY_FALSE_FIELDS
    )
    assert not any(
        marker in json.dumps(summary, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_markdown_is_path_free() -> None:
    markdown = render_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_markdown(
        _build_summary(_index_entry_verification_report())
    )

    assert "Hex Cross-Consistency Verifier-Summary" in markdown
    assert "Claim safe: `false`" in markdown
    assert "Runtime subdivision authority granted: `false`" in markdown
    assert "Artifact payloads included: `false`" in markdown
    assert "Local paths recorded: `false`" in markdown
    assert "upgrade claims" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification.json"
    report_path.write_bytes(_json_bytes(_index_entry_verification_report()))

    result = _run_cli(
        "--verification-json",
        str(report_path),
        "--reviewer-agent",
        "codex-lead-1",
        "--handoff-ref",
        "bridge:handoff:hex-xcons-verifier-summary-template-index-verification",
        "--now",
        FIXED_NOW_TEXT,
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["path_free_verified"] is True
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_rejects_verifier_drift() -> None:
    report = _index_entry_verification_report()
    report["bridge_event_schema_check"] = "failed"

    summary = _build_summary(report)

    assert summary["ok"] is False
    assert "verification_report_bridge_event_schema_check_not_match" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert all(summary[field] is False for field in _AUTHORITY_FALSE_FIELDS)


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_rejects_authority_escalation() -> None:
    report = _index_entry_verification_report()
    report["runtime_subdivision_authority_granted"] = True

    summary = _build_summary(report)

    assert summary["ok"] is False
    assert (
        "verification_report_runtime_subdivision_authority_granted_not_false"
        in summary["blockers"]
    )
    assert summary["runtime_subdivision_authority_granted"] is False
    assert summary["bridge_event_written"] is False


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_rejects_nested_payload_path_and_authority() -> None:
    report = _index_entry_verification_report()
    report["nested"] = {
        "raw_payload": "opaque",
        "source_path": "artifact.json",
        "claim_safe": True,
    }

    summary = _build_summary(report)

    serialized = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary[
        "blockers"
    ]
    assert "verification_report_forbidden_path_key:source_path" in summary[
        "blockers"
    ]
    assert (
        "verification_report_nested_authority_field_not_false:claim_safe"
        in summary["blockers"]
    )
    assert "opaque" not in serialized
    assert "artifact.json" not in serialized
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_rejects_non_list_blockers() -> None:
    report = _index_entry_verification_report()
    report["blockers"] = "not-a-list"

    summary = _build_summary(report)

    assert summary["ok"] is False
    assert "verification_report_blockers_not_list" in summary["blockers"]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_missing_input_is_path_free() -> None:
    result = _run_cli(
        "--verification-json",
        FORBIDDEN_VERIFICATION_PATH,
        "--reviewer-agent",
        "codex-lead-1",
        "--handoff-ref",
        "bridge:handoff:hex-xcons-verifier-summary-template-index-verification",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_"
        "index_entry_verification_summary_failed:verification_report_unreadable"
    ]
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    combined = result.stdout + result.stderr
    assert "index-entry-verification.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_template_index_entry_verification_summary_non_finite_json_is_path_free(
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
        "bridge:handoff:hex-xcons-verifier-summary-template-index-verification",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_"
        "index_entry_verification_summary_failed:verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert report_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _build_summary(report: dict) -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-verifier-summary-template-index-verification",
        now_utc=FIXED_NOW,
    )


def _index_entry_verification_report() -> dict:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)
    return verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=_index_entry(artifacts),
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_required_artifact_ids_are_bound() -> None:
    assert _index_entry_verification_report()["digest_checks"].keys() == {
        SUMMARY_ARTIFACT_ID,
        TEMPLATE_ARTIFACT_ID,
    }
