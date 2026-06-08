# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary import (
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary,
)
from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template,
)
from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry,
)
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (
    build_memory_palace_shortcut_runtime_promotion_design,
)
from tools.verify_v12_memory_palace_shortcut_runtime_promotion_design import (
    verify_memory_palace_shortcut_runtime_promotion_design,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry.py"
)
FIXED_NOW = datetime(2026, 6, 8, 10, 15, tzinfo=timezone.utc)


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


def test_memory_palace_bridge_event_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-08T10:15:00Z"
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[SUMMARY_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["summary"])
    assert by_id[TEMPLATE_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["template"])
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])

    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["source_summary_sha256"] == _sha256_hex(raw["summary"])
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["event_type"] == "handoff"
    assert template_entry["event_status"] == EVENT_STATUS
    assert template_entry["runtime_promotion_design_count_checked"] == 2
    assert template_entry["manual_review_required"] is True
    assert template_entry["operator_gate_required_for_runtime_promotion"] is True
    assert template_entry["approval_granted"] is False
    assert template_entry["bridge_append_performed"] is False
    assert template_entry["scheduler_enqueue_performed"] is False
    assert template_entry["gate_skip_performed"] is False
    assert template_entry["runtime_authority_granted"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["runtime_authority_granted"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False


def test_memory_palace_bridge_event_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
            "--template-json",
            str(paths["template"]),
            "--now",
            "2026-06-08T10:15:00Z",
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
    assert payload["approval_granted"] is False
    assert payload["bridge_append_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_bridge_event_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = deepcopy(artifacts["template"])
    summary_payload = artifacts["template"]["bridge_event_template"]["payload"][
        "memory_palace_runtime_promotion_design_verification_summary"
    ]
    summary_payload["runtime_promotion_design_count_checked"] = 999
    raw = _artifact_bytes(artifacts)

    try:
        build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry(
            verification_summary=artifacts["summary"],
            bridge_event_template_report=artifacts["template"],
            verification_summary_bytes=raw["summary"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "bridge_event_template_rebuilt_mismatch"
    else:  # pragma: no cover
        raise AssertionError("expected template drift rejection")


def test_memory_palace_bridge_event_template_index_entry_rejects_authority_escalation() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = deepcopy(artifacts["summary"])
    artifacts["summary"]["runtime_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    try:
        build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry(
            verification_summary=artifacts["summary"],
            bridge_event_template_report=artifacts["template"],
            verification_summary_bytes=raw["summary"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == (
            "verification_summary_top_level_runtime_authority_granted_not_false"
        )
    else:  # pragma: no cover
        raise AssertionError("expected authority escalation rejection")


def test_memory_palace_bridge_event_template_index_entry_rejects_nested_authority_escalation() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = deepcopy(artifacts["summary"])
    artifacts["summary"]["authority_boundary"]["gate_skip_performed"] = True
    raw = _artifact_bytes(artifacts)

    try:
        build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template_index_entry(
            verification_summary=artifacts["summary"],
            bridge_event_template_report=artifacts["template"],
            verification_summary_bytes=raw["summary"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == (
            "verification_summary_gate_skip_performed_not_false"
        )
    else:  # pragma: no cover
        raise AssertionError("expected nested authority escalation rejection")


def test_memory_palace_bridge_event_template_index_entry_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            str(paths["summary"]),
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
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_index_entry_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "template.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_bridge_event_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_index_entry_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_bridge_event_template_index_entry_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
    assert f"{TEMPLATE_ARTIFACT_ID}_json_error" in result.stdout
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout


def _artifact_set() -> dict[str, dict]:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=datetime(2026, 6, 8, 7, 30, tzinfo=timezone.utc),
    )
    verification = verify_memory_palace_shortcut_runtime_promotion_design(report)
    summary = build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
        verification,
    )
    template = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="codex-lead-1/memory-palace-template-index-entry-20260608",
        to="operator,claude-rco-1,claude-rco-2,codex-tools-1",
        run_id="codex-lead-1-20260608T101500Z",
        session_id="codex-lead-1-20260608T101500Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "summary": tmp_path / "memory-palace-verification-summary.json",
        "template": tmp_path / "memory-palace-bridge-event-template.json",
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
            allow_nan=False,
        ).encode("utf-8")
        for key, artifact in artifacts.items()
    }


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
