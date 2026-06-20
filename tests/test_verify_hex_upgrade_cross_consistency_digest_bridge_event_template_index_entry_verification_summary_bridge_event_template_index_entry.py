"""Offline tests for hex verifier-summary bridge-template index-entry verification."""

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
    INDEX_ENTRY_VERSION,
    PROOF_ID as SOURCE_PROOF_ID,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_ARTIFACT_ID,
    VERIFICATION_PROOF_ID,
    VERIFICATION_VERSION,
    _AUTHORITY_FALSE_FIELDS,
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / (
        "verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_"
        "entry_verification_summary_bridge_event_template_index_entry.py"
    )
)
FIXED_NOW = datetime(2026, 6, 20, 10, 40, tzinfo=timezone.utc)
FORBIDDEN_OUTPUT_SNIPPETS = (
    "C:/private",
    "PRIVATE_",
    "http://",
    "https://",
)


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_recomputes_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is True
    assert report["proof_id"] == VERIFICATION_PROOF_ID
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["verified_proof_id"] == SOURCE_PROOF_ID
    assert report["index_entry_version"] == INDEX_ENTRY_VERSION
    assert report["artifact_count_checked"] == 2
    assert report["digest_checks"] == {
        SUMMARY_ARTIFACT_ID: "match",
        TEMPLATE_ARTIFACT_ID: "match",
    }
    assert report["size_checks"] == {
        SUMMARY_ARTIFACT_ID: "match",
        TEMPLATE_ARTIFACT_ID: "match",
    }
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["blockers"] == []
    for field in _AUTHORITY_FALSE_FIELDS:
        assert report[field] is False


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_cli_json_is_path_free(
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
            "--index-entry-verification-summary-json",
            str(paths["summary"]),
            "--summary-bridge-event-template-json",
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
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_rejects_template_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["warnings"] = ["changed"]
    paths["template"].write_bytes(_json_bytes(tampered_template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--template-index-entry-json",
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
    assert f"digest_mismatch:{TEMPLATE_ARTIFACT_ID}" in payload["blockers"]
    assert payload["source_contract_check"] == "failed"
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
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


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["runtime_subdivision_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert (
        "template_index_entry_runtime_subdivision_authority_granted_not_false"
        in report["blockers"]
    )
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["runtime_authority_granted"] is False


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["append_without_review"]
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["bridge_event_written"] is False
    assert report["fast_track_priority"] is False


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = deepcopy(artifacts["summary"])
    artifacts["summary"][
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification"
    ]["blocker_count"] = 1
    index_entry = _index_entry(_artifact_set())
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert any(
        blocker.startswith("source_contract_failed:")
        for blocker in report["blockers"]
    )
    assert report["source_contract_check"] == "failed"
    assert report["bridge_event_written"] is False


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_missing_input_is_path_free(
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
            "C:/private/index-entry.json",
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_verification_failed:"
        f"{INDEX_ENTRY_ARTIFACT_ID}_unreadable"
    ]
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert "index-entry.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    paths["index_entry"].write_text('{"ok": NaN}', encoding="utf-8")

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
    assert (
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_json_error"
        in result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert paths["index_entry"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_verifier_summary_bridge_template_index_entry_verifier_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["created_at_utc"] = "C:/private/index-entry.json"
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

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_verification_failed:"
        f"{INDEX_ENTRY_ARTIFACT_ID}_forbidden_marker"
    ]
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert "index-entry.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    summary = _index_entry_verification_summary()
    template = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-hex-xcons-summary-template-index-entry-verifier",
        to="operator,claude-rco-1,codex-tools-1",
        run_id="codex-lead-1-20260620T104000Z",
        session_id="codex-lead-1-20260620T104000Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _index_entry_verification_summary() -> dict:
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="hex-xcons-index-verification-summary",
        now_utc=FIXED_NOW,
    )


def _verification_report() -> dict:
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
        task_id="wd-hex-xcons-summary-template-index-entry-verifier-source",
        to="operator,claude-rco-1,codex-tools-1",
        run_id="codex-lead-1-20260620T104000Z",
        session_id="codex-lead-1-20260620T104000Z",
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


def _write_bundle(
    tmp_path: Path,
    index_entry: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "index_entry": tmp_path / "index-entry.json",
        "summary": tmp_path / "summary.json",
        "template": tmp_path / "template.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


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
