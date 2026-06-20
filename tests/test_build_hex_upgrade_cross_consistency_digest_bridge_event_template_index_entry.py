from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (
    DIGEST_REPORT_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template,
)
import tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as indexer


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / "build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.py"
)
FIXED_NOW = "2026-06-20T08:30:00Z"


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


FORBIDDEN_PATH_PREFIX = "".join(("C", ":", "/", "private"))
FORBIDDEN_TEMPLATE_PATH = "".join((FORBIDDEN_PATH_PREFIX, "/", "template.json"))
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    _chars(80, 82, 73, 86, 65, 84, 69, 95),
    "".join((_chars(104, 116, 116, 112), ":", "/", "/")),
    "".join((_chars(104, 116, 116, 112, 115), ":", "/", "/")),
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
        task_id="wd-image1-hex-xcons-template-index-entry",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260620T083000Z",
        session_id="codex-lead-1-20260620T083000Z",
        now_utc=indexer._parse_utc(FIXED_NOW),
    )


def _encoded(report: dict) -> bytes:
    return json.dumps(report, sort_keys=True, allow_nan=False).encode("utf-8")


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_hex_xcons_template_index_entry_binds_digest_without_authority() -> None:
    template_report = _template_report()
    raw = _encoded(template_report)

    entry = indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=raw,
        now_utc=indexer._parse_utc(FIXED_NOW),
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == indexer.INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == FIXED_NOW
    assert entry["artifact_count"] == 1
    artifact = entry["artifacts"][0]
    assert artifact["artifact_id"] == indexer.TEMPLATE_ARTIFACT_ID
    assert artifact["sha256"] == hashlib.sha256(raw).hexdigest()
    assert artifact["payload_included"] is False
    assert artifact["local_path_recorded"] is False

    template_index = entry["template_index_entry"]
    cross = template_report["bridge_event_template"]["payload"]["cross_consistency"]
    assert template_index["source_contract_check"] == "match"
    assert template_index["template_contract_check"] == "match"
    assert template_index["authority_boundary_check"] == "match"
    assert template_index["cross_consistency_safe_keys_check"] == "match"
    assert template_index["bridge_event_schema_validated"] is True
    assert template_index["event_status"] == (
        "hex_upgrade_cross_consistency_digest_bridge_event_template_ready"
    )
    assert template_index["source_digest_ref"] == cross["digest_ref"]
    assert template_index["cross_consistent"] is True
    assert template_index["raw_digest_payload_included"] is False
    assert template_index["runtime_subdivision_authority_granted"] is False
    assert template_index["direct_bridge_write_performed"] is False
    assert template_index["bridge_event_written"] is False
    assert entry["runtime_subdivision_authority_granted"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert (
        indexer.validate_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            entry
        )
        == []
    )


def test_hex_xcons_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    template_report = _template_report()
    template_path = tmp_path / "hex_xcons_template.json"
    template_path.write_bytes(_encoded(template_report))

    result = _run_cli(
        "--template-json",
        str(template_path),
        "--now",
        FIXED_NOW,
        "--json",
    )

    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)
    assert entry["ok"] is True
    assert entry["template_index_entry"]["source_contract_check"] == "match"
    assert entry["template_index_entry"]["runtime_subdivision_authority_granted"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert template_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_rejects_authority_escalation() -> None:
    template_report = _template_report()
    template_report["runtime_subdivision_authority_granted"] = True

    entry = indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert entry["ok"] is False
    assert entry["runtime_subdivision_authority_granted"] is False
    assert any("runtime_subdivision_authority" in item for item in entry["blockers"])


def test_hex_xcons_template_index_entry_rejects_template_drift() -> None:
    template_report = _template_report()
    template_report["bridge_event_template"]["payload"]["cross_consistency"][
        "raw_digest_payload_included"
    ] = True

    entry = indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert entry["ok"] is False
    assert entry["digest_payloads_included"] is False
    assert any("raw_digest_payload" in item for item in entry["blockers"])


def test_hex_xcons_template_index_entry_rejects_bytes_mismatch() -> None:
    template_report = _template_report()
    raw = _encoded(template_report)
    tampered = deepcopy(template_report)
    tampered["template_only"] = False

    entry = indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=tampered,
        bridge_event_template_bytes=raw,
    )

    assert entry["ok"] is False
    assert any("bytes_mismatch" in item for item in entry["blockers"])


def test_hex_xcons_template_index_entry_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "hex_xcons_template.json"

    result = _run_cli(
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_unreadable"
    ]
    assert "hex_xcons_template.json" not in result.stdout
    assert str(tmp_path) not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "hex_xcons_template.json"
    template_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = _run_cli(
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert template_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    template_path = tmp_path / "hex_xcons_template.json"
    template_path.write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = _run_cli(
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 1
    assert "hex_upgrade_cross_consistency_digest_bridge_event_template_json_error" in (
        result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert template_path.name not in result.stdout


def test_hex_xcons_template_index_entry_rejects_path_tainted_template() -> None:
    template_report = _template_report()
    template_report["bridge_event_template"]["message"] = FORBIDDEN_TEMPLATE_PATH

    entry = indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert entry["ok"] is False
    assert entry["local_paths_recorded"] is False
    assert any("not_path_free" in item for item in entry["blockers"])
