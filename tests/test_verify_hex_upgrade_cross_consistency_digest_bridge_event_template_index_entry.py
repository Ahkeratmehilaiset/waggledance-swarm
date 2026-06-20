"""Offline tests for hex cross-consistency template index-entry verification."""

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
    DIGEST_ARTIFACT_ID,
    INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID,
    build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)
from tools.verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry import (
    VERIFICATION_PROOF_ID,
    VERIFICATION_VERSION,
    verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / "verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.py"
)
FIXED_NOW = datetime(2026, 6, 20, 8, 40, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_INDEX_ENTRY_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "index-entry.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_hex_xcons_template_index_entry_verifier_recomputes_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is True
    assert report["proof_id"] == VERIFICATION_PROOF_ID
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["index_entry_version"] == INDEX_ENTRY_VERSION
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["digest_ref_check"] == "match"
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["merge_decision_made"] is False
    assert report["promotion_granted"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["runtime_authority_granted"] is False
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["bridge_event_written"] is False
    assert report["gate_skip_allowed"] is False
    assert report["fast_track_priority"] is False
    assert report["digest_payloads_included"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_hex_xcons_template_index_entry_verifier_cli_json_is_path_free(
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
            "--digest-json",
            str(paths["digest"]),
            "--bridge-event-template-json",
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


def test_hex_xcons_template_index_entry_verifier_rejects_template_digest_mismatch_path_free(
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
            "--digest-json",
            str(paths["digest"]),
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


def test_hex_xcons_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != DIGEST_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{DIGEST_ARTIFACT_ID}" in report["blockers"]
    assert report["digest_checks"][DIGEST_ARTIFACT_ID] == "missing_index_record"
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_hex_xcons_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["runtime_subdivision_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert (
        "template_index_entry_runtime_subdivision_authority_granted_not_false"
        in report["blockers"]
    )
    assert report["runtime_subdivision_authority_granted"] is False
    assert report["runtime_authority_granted"] is False


def test_hex_xcons_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["append_without_review"]
    raw = _artifact_bytes(artifacts)

    report = verify_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        index_entry=index_entry,
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["bridge_event_written"] is False
    assert report["fast_track_priority"] is False


def test_hex_xcons_template_index_entry_verifier_rejects_created_at_path_marker(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["created_at_utc"] = _joined(FORBIDDEN_PATH_PREFIX, "/", "entry.json")
    paths = _write_bundle(tmp_path, index_entry, artifacts)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
            "--digest-json",
            str(paths["digest"]),
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_forbidden_marker"
    ]
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert "entry.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verifier_missing_input_is_path_free(
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
            FORBIDDEN_INDEX_ENTRY_PATH,
            "--digest-json",
            str(paths["digest"]),
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_verification_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert "index-entry.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verifier_non_finite_json_is_path_free(
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
            "--digest-json",
            str(paths["digest"]),
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_json_error"
        in result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert paths["index_entry"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_verifier_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    paths["index_entry"].write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            str(paths["index_entry"]),
            "--digest-json",
            str(paths["digest"]),
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_"
        "index_entry_json_error"
        in result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert paths["index_entry"].name not in result.stdout


def _artifact_set() -> dict[str, dict]:
    digest = _good_digest()
    template = build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=digest,
        agent_id="codex-lead-1",
        task_id="wd-hex-xcons-template-index-entry-verifier",
        to="operator,claude-rco-1,codex-tools-1",
        run_id="codex-lead-1-20260620T084000Z",
        session_id="codex-lead-1-20260620T084000Z",
        now_utc=FIXED_NOW,
    )
    return {"digest": digest, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["template"],
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
        "digest": tmp_path / "digest.json",
        "template": tmp_path / "template.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
    paths["digest"].write_bytes(_json_bytes(artifacts["digest"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


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
