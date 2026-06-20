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
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary,
)
from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    HEX_XCONS_VERIFICATION_KEY,
    TEMPLATE_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
import tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as verifier
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 9, 20, tzinfo=timezone.utc)
FIXED_NOW_TEXT = "2026-06-20T09:20:00Z"


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


FORBIDDEN_PATH_PREFIX = "".join(("C", ":", "/", "private"))
FORBIDDEN_SUMMARY_PATH = "".join(
    (FORBIDDEN_PATH_PREFIX, "/", "index-entry-verification-summary.json")
)
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    _chars(80, 82, 73, 86, 65, 84, 69, 95),
    "".join((_chars(104, 116, 116, 112), ":", "/", "/")),
    "".join((_chars(104, 116, 116, 112, 115), ":", "/", "/")),
)


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_validates_schema() -> None:
    report = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=_index_entry_verification_summary(),
        agent_id="codex-lead-1",
        task_id="wd-image1-hex-xcons-verifier-summary-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260620T092000Z",
        session_id="codex-lead-1-20260620T092000Z",
        now_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["claim_safe"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["bridge_event_written"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    event = report["bridge_event_template"]
    validate_event(event)
    assert event["type"] == "handoff"
    assert event["status"] == EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["pid"] == 0
    assert event["cwd"] == "template_not_emitted"
    payload = event["payload"]
    assert payload["schema_version"] == TEMPLATE_VERSION
    assert payload["template_only"] is True
    assert payload["manual_review_required"] is True
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["automatic_release_decision"] is False
    assert payload["claim_safe"] is False
    assert payload["direct_bridge_write_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["runtime_controls_added"] is False
    assert payload["runtime_authority_granted"] is False
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    verification = payload[HEX_XCONS_VERIFICATION_KEY]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == verifier.VERIFICATION_VERSION
    assert verification["index_entry_version"] == indexer.INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 1
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["runtime_subdivision_authority_granted"] is False
    assert verification["claim_safe"] is False
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    boundary = payload["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["runtime_subdivision_authority_granted"] is False
    assert not any(
        marker in json.dumps(report, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(_index_entry_verification_summary()))

    result = _run_cli(
        "--summary-json",
        str(summary_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-image1-hex-xcons-verifier-summary-template",
        "--to",
        "operator,claude-rco-1",
        "--run-id",
        "codex-lead-1-20260620T092000Z",
        "--session-id",
        "codex-lead-1-20260620T092000Z",
        "--now",
        FIXED_NOW_TEXT,
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = payload["bridge_event_template"]
    validate_event(event)
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert event["payload"][HEX_XCONS_VERIFICATION_KEY]["verification_ok"] is True
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = _run_cli(
        "--summary-json",
        FORBIDDEN_SUMMARY_PATH,
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-image1-hex-xcons-verifier-summary-template",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_unreadable"
    ]
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    combined = result.stdout + result.stderr
    assert "index-entry-verification-summary.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_rejects_unsafe_bridge_fields(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(_index_entry_verification_summary()))

    result = _run_cli(
        "--summary-json",
        str(summary_path),
        "--agent",
        "Codex",
        "--task-id",
        "wd-image1-hex-xcons-verifier-summary-template",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_failed:"
        "agent_unsafe"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_blocks_unsafe_summary_contract(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "summary_not_ok",
            lambda summary: summary.__setitem__("ok", False),
            "index_entry_verification_summary_not_ok",
        ),
        (
            "runtime_subdivision_authority",
            lambda summary: summary.__setitem__(
                "runtime_subdivision_authority_granted",
                True,
            ),
            "index_entry_verification_summary_runtime_subdivision_authority_granted_not_false",
        ),
        (
            "source_contract_mismatch",
            lambda summary: summary[HEX_XCONS_VERIFICATION_KEY].__setitem__(
                "source_contract_check",
                "mismatch",
            ),
            "index_entry_verification_source_contract_not_match",
        ),
        (
            "verification_version_mismatch",
            lambda summary: summary[HEX_XCONS_VERIFICATION_KEY].__setitem__(
                "verification_version",
                "unknown",
            ),
            "index_entry_verification_version_mismatch",
        ),
        (
            "blocker_count_nonzero",
            lambda summary: summary[HEX_XCONS_VERIFICATION_KEY].__setitem__(
                "blocker_count",
                1,
            ),
            "index_entry_verification_blocker_count_nonzero",
        ),
        (
            "boundary_not_ok",
            lambda summary: summary["operator_boundary"].__setitem__(
                "verification_report_boundary_ok",
                False,
            ),
            "operator_boundary_verification_report_not_ok",
        ),
        (
            "verification_claim_safe",
            lambda summary: summary[HEX_XCONS_VERIFICATION_KEY].__setitem__(
                "claim_safe",
                True,
            ),
            "index_entry_verification_claim_safe_not_false",
        ),
    )

    for label, mutate, expected_reason in cases:
        summary = _index_entry_verification_summary()
        mutate(summary)
        summary_path = tmp_path / f"{label}.json"
        summary_path.write_bytes(_json_bytes(summary))

        result = _run_cli(
            "--summary-json",
            str(summary_path),
            "--agent",
            "codex-lead-1",
            "--task-id",
            "wd-image1-hex-xcons-verifier-summary-template",
            "--to",
            "operator,claude-rco-1",
            "--now",
            FIXED_NOW_TEXT,
            "--json",
        )

        assert result.returncode == 1, label
        report = json.loads(result.stdout)
        assert report["ok"] is False, label
        assert report["blockers"] == [
            "hex_upgrade_cross_consistency_digest_bridge_event_template_"
            "index_entry_verification_summary_bridge_event_template_failed:"
            f"{expected_reason}"
        ], label
        assert report["runtime_subdivision_authority_granted"] is False
        assert report["bridge_event_written"] is False
        assert report["artifact_payloads_included"] is False
        assert str(tmp_path) not in result.stdout
        assert summary_path.name not in result.stdout


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    summary = _index_entry_verification_summary()
    summary["warnings"] = ["C:/private/report.json"]
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_bytes(_json_bytes(summary))

    result = _run_cli(
        "--summary-json",
        str(summary_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-image1-hex-xcons-verifier-summary-template",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_forbidden_marker"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_index_entry_verification_summary_bridge_event_template_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary = _index_entry_verification_summary()
    summary["warnings"] = [float("nan")]
    summary_path = tmp_path / "index_entry_verification_summary.json"
    summary_path.write_text(json.dumps(summary, sort_keys=True), encoding="utf-8")

    result = _run_cli(
        "--summary-json",
        str(summary_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-image1-hex-xcons-verifier-summary-template",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_summary_bridge_event_template_failed:"
        "index_entry_verification_summary_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    assert summary_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _index_entry_verification_summary() -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="bridge:handoff:hex-xcons-template-index-verification",
        now_utc=FIXED_NOW,
    )


def _verification_report() -> dict:
    template_report = _template_report()
    return verifier.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=_index_entry(template_report),
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_json_bytes(template_report),
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
        task_id="wd-image1-hex-xcons-verifier-summary-template",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260620T092000Z",
        session_id="codex-lead-1-20260620T092000Z",
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
