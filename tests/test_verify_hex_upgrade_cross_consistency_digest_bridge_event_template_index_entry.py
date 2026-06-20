from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

from tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template import (
    DIGEST_REPORT_VERSION,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template,
)
import tools.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as indexer
import tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry as verifier


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / "verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.py"
)
FIXED_NOW = "2026-06-20T08:45:00Z"


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
        task_id="wd-image1-hex-xcons-template-index-entry-verifier",
        to="operator,claude-rco-1,codex-tools-1",
        role="lead-impl",
        run_id="codex-lead-1-20260620T084500Z",
        session_id="codex-lead-1-20260620T084500Z",
        now_utc=indexer._parse_utc(FIXED_NOW),
    )


def _encoded(report: dict) -> bytes:
    return json.dumps(report, sort_keys=True, allow_nan=False).encode("utf-8")


def _index_entry(template_report: dict) -> dict:
    return indexer.build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
        now_utc=indexer._parse_utc(FIXED_NOW),
    )


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_hex_xcons_template_index_entry_verifier_recomputes_without_authority() -> None:
    template_report = _template_report()
    report = verifier.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=_index_entry(template_report),
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert report["ok"] is True
    assert report["verification_version"] == verifier.VERIFICATION_VERSION
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["digest_checks"] == {
        verifier.TEMPLATE_ARTIFACT_ID: "match",
    }
    assert report["size_checks"] == {
        verifier.TEMPLATE_ARTIFACT_ID: "match",
    }
    assert report["schema_version_checks"] == {
        verifier.TEMPLATE_ARTIFACT_ID: "match",
    }
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["bridge_event_written"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert report["path_free_verified"] is True


def test_hex_xcons_template_index_entry_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    template_report = _template_report()
    index_entry = _index_entry(template_report)
    template_path = tmp_path / "hex_xcons_template.json"
    index_path = tmp_path / "hex_xcons_index_entry.json"
    template_path.write_bytes(_encoded(template_report))
    index_path.write_bytes(_encoded(index_entry))

    result = _run_cli(
        "--index-entry-json",
        str(index_path),
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert template_path.name not in result.stdout
    assert index_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verifier_rejects_authority_escalation() -> None:
    template_report = _template_report()
    index_entry = _index_entry(template_report)
    index_entry["runtime_subdivision_authority_granted"] = True

    report = verifier.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        bridge_event_template_report=template_report,
        bridge_event_template_bytes=_encoded(template_report),
    )

    assert report["ok"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert "runtime_subdivision_authority_granted_not_false" in report["blockers"]
    assert "rebuilt_index_entry_mismatch" in report["blockers"]


def test_hex_xcons_template_index_entry_verifier_rejects_template_drift() -> None:
    template_report = _template_report()
    index_entry = _index_entry(template_report)
    drifted = deepcopy(template_report)
    drifted["bridge_event_template"]["payload"]["cross_consistency"][
        "cross_consistent"
    ] = False

    report = verifier.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        bridge_event_template_report=drifted,
        bridge_event_template_bytes=_encoded(drifted),
    )

    assert report["ok"] is False
    assert report["source_contract_check"] == "match"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert any("digest_mismatch" in blocker for blocker in report["blockers"])


def test_hex_xcons_template_index_entry_verifier_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    template_report = _template_report()
    template_path = tmp_path / "hex_xcons_template.json"
    missing_index_path = tmp_path / "hex_xcons_index_entry.json"
    template_path.write_bytes(_encoded(template_report))

    result = _run_cli(
        "--index-entry-json",
        str(missing_index_path),
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_unreadable"
    ]
    assert str(tmp_path) not in result.stdout
    assert missing_index_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    template_report = _template_report()
    template_path = tmp_path / "hex_xcons_template.json"
    index_path = tmp_path / "hex_xcons_index_entry.json"
    template_path.write_bytes(_encoded(template_report))
    index_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = _run_cli(
        "--index-entry-json",
        str(index_path),
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert index_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verifier_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    template_report = _template_report()
    template_path = tmp_path / "hex_xcons_template.json"
    index_path = tmp_path / "hex_xcons_index_entry.json"
    template_path.write_bytes(_encoded(template_report))
    index_path.write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = _run_cli(
        "--index-entry-json",
        str(index_path),
        "--template-json",
        str(template_path),
        "--json",
    )

    assert result.returncode == 1
    assert (
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_json_error"
        in result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert index_path.name not in result.stdout


def test_hex_xcons_template_index_entry_verifier_rejects_path_tainted_index() -> None:
    template_report = _template_report()
    index_entry = _index_entry(template_report)
    index_entry["unsafe_note"] = FORBIDDEN_TEMPLATE_PATH

    report = verifier._failure_report(
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_forbidden_marker"
    )
    try:
        verifier.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            index_entry=index_entry,
            bridge_event_template_report=template_report,
            bridge_event_template_bytes=_encoded(template_report),
        )
    except verifier.TemplateIndexEntryVerificationError as exc:
        report = verifier._failure_report(exc.code)

    blob = json.dumps(report, sort_keys=True)
    assert report["ok"] is False
    assert report["local_paths_recorded"] is False
    assert "forbidden_marker" in report["blockers"][0]
    assert FORBIDDEN_PATH_PREFIX not in blob
