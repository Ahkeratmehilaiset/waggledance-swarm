"""Offline tests for hex cross-consistency bridge-template index entries."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
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
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    ROOT
    / "tools"
    / "build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry.py"
)
FIXED_NOW = datetime(2026, 6, 20, 8, 20, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_TEMPLATE_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "template.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_hex_xcons_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
        digest=artifacts["digest"],
        bridge_event_template_report=artifacts["template"],
        digest_bytes=raw["digest"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-20T08:20:00Z"
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[DIGEST_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["digest"])
    assert by_id[TEMPLATE_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["template"])
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])

    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["digest_ref_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["source_digest_sha256"] == _sha256_hex(raw["digest"])
    assert template_entry["source_digest_ref"] == sha256_digest(artifacts["digest"])
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["cross_consistent"] is True
    assert template_entry["runtime_subdivision_authority_granted"] is False
    assert template_entry["bridge_event_written"] is False
    assert template_entry["artifact_payloads_included"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["runtime_controls_added"] is False
    assert entry["runtime_authority_granted"] is False
    assert entry["runtime_subdivision_authority_granted"] is False
    assert entry["digest_payloads_included"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False


def test_hex_xcons_template_index_entry_cli_json_is_path_free(tmp_path: Path) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--digest-json",
            str(paths["digest"]),
            "--bridge-event-template-json",
            str(paths["template"]),
            "--now",
            "2026-06-20T08:20:00Z",
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
    assert payload["template_index_entry"]["rebuilt_template_check"] == "match"
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = deepcopy(artifacts["template"])
    cross = artifacts["template"]["bridge_event_template"]["payload"][
        "cross_consistency"
    ]
    cross["digest_ref"] = "sha256:" + ("f" * 64)
    raw = _artifact_bytes(artifacts)

    try:
        build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            digest=artifacts["digest"],
            bridge_event_template_report=artifacts["template"],
            digest_bytes=raw["digest"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "bridge_event_template_rebuilt_mismatch"
    else:  # pragma: no cover
        raise AssertionError("expected template drift rejection")


def test_hex_xcons_template_index_entry_rejects_authority_escalation() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"][
        "authority_boundary"
    ]["runtime_subdivision_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    try:
        build_hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry(
            digest=artifacts["digest"],
            bridge_event_template_report=artifacts["template"],
            digest_bytes=raw["digest"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "bridge_event_template_rebuilt_mismatch"
    else:  # pragma: no cover
        raise AssertionError("expected authority escalation rejection")


def test_hex_xcons_template_index_entry_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--digest-json",
            str(paths["digest"]),
            "--template-json",
            FORBIDDEN_TEMPLATE_PATH,
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
        "hex_upgrade_cross_consistency_digest_bridge_event_template_index_entry_failed:"
        "hex_upgrade_cross_consistency_digest_bridge_event_template_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["runtime_subdivision_authority_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert "template.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
    assert "hex_upgrade_cross_consistency_digest_bridge_event_template_json_error" in (
        result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_hex_xcons_template_index_entry_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
    assert "hex_upgrade_cross_consistency_digest_bridge_event_template_json_error" in (
        result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout


def _artifact_set() -> dict[str, dict]:
    digest = _good_digest()
    template = build_hex_upgrade_cross_consistency_digest_bridge_event_template(
        digest=digest,
        agent_id="codex-lead-1",
        task_id="wd-hex-xcons-template-index-entry",
        to="operator,claude-rco-1,codex-tools-1",
        run_id="codex-lead-1-20260620T082000Z",
        session_id="codex-lead-1-20260620T082000Z",
        now_utc=FIXED_NOW,
    )
    return {"digest": digest, "template": template}


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


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "digest": tmp_path / "hex_cross_consistency_digest.json",
        "template": tmp_path / "hex_cross_consistency_template.json",
    }
    raw = _artifact_bytes(artifacts)
    for key, path in paths.items():
        path.write_bytes(raw[key])
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        key: json.dumps(
            artifact,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        for key, artifact in artifacts.items()
    }


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
