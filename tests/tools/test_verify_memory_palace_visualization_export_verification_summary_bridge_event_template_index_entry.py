# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_sample_projection import (
    build_memory_palace_sample_projection,
)
from tools.build_memory_palace_visualization_export import (
    build_memory_palace_visualization_export,
)
from tools.build_memory_palace_visualization_export_verification_summary import (
    build_memory_palace_visualization_export_verification_summary,
)
from tools.build_memory_palace_visualization_export_verification_summary_bridge_event_template import (
    build_memory_palace_visualization_export_verification_summary_bridge_event_template,
)
from tools.build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_memory_palace_visualization_export import (
    verify_memory_palace_visualization_export,
)
from tools.verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_ARTIFACT_ID,
    VERIFICATION_VERSION,
    verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry.py"
)
FIXED_NOW = datetime(2026, 6, 8, 22, 20, tzinfo=timezone.utc)


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


def test_visualization_summary_template_index_entry_verifier_recomputes_digests_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is True
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["index_entry_version"] == INDEX_ENTRY_VERSION
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_index_entry_check"] == "match"
    assert report["bridge_event_schema_check"] == "match"
    assert report["template_only"] is True
    assert report["read_side_report_only"] is True
    assert report["manual_review_required"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["bridge_append_performed"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["scheduler_enqueue_performed"] is False
    assert report["solver_call_performed"] is False
    assert report["promotion_performed"] is False
    assert report["gate_skip_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["controls_present"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    assert report["network_access_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_visualization_summary_template_index_entry_verifier_cli_json_is_path_free(
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

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["rebuilt_index_entry_check"] == "match"
    assert payload["bridge_event_schema_check"] == "match"
    assert payload["bridge_append_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["gate_skip_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_visualization_summary_template_index_entry_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["bridge_event_template"]["payload"][
        "source_summary_sha256"
    ] = "f" * 64
    paths["template"].write_bytes(_json_bytes(tampered_template))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--template-index-entry-json",
            str(paths["index_entry"]),
            "--verification-summary-json",
            str(paths["summary"]),
            "--bridge-event-template-json",
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
    assert payload["approval_granted"] is False
    assert payload["bridge_append_performed"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_visualization_summary_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{SUMMARY_ARTIFACT_ID}" in report["blockers"]
    assert report["digest_checks"][SUMMARY_ARTIFACT_ID] == "missing_index_record"
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_visualization_summary_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["scheduler_enqueue_performed"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert (
        "template_index_entry_scheduler_enqueue_performed_not_false"
        in report["blockers"]
    )
    assert report["scheduler_enqueue_performed"] is False
    assert report["runtime_authority_granted"] is False


def test_visualization_summary_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["append_without_review"]
    raw = _artifact_bytes(artifacts)

    report = verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["bridge_append_performed"] is False
    assert report["gate_skip_performed"] is False


def test_visualization_summary_template_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_summary = deepcopy(artifacts["summary"])
    tampered_summary["runtime_authority_granted"] = True

    report = verify_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        verification_summary=tampered_summary,
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=_json_bytes(tampered_summary),
        bridge_event_template_bytes=_json_bytes(artifacts["template"]),
    )

    assert report["ok"] is False
    assert (
        "source_contract_failed:"
        "verification_summary_top_level_runtime_authority_granted_not_false"
        in report["blockers"]
    )
    assert report["source_contract_check"] == "failed"
    assert report["runtime_authority_granted"] is False


def test_visualization_summary_template_index_entry_verifier_missing_input_is_path_free(
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
        "memory_palace_visualization_export_verification_summary_"
        "bridge_event_template_index_entry_verification_failed:"
        f"{INDEX_ENTRY_ARTIFACT_ID}_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "index-entry.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_visualization_summary_template_index_entry_verifier_non_finite_json_is_path_free(
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
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_visualization_export_verification_summary_"
        "bridge_event_template_index_entry_verification_failed:"
        f"{INDEX_ENTRY_ARTIFACT_ID}_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert paths["index_entry"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_visualization_summary_template_index_entry_verifier_duplicate_json_key_is_path_free(
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
    assert f"{INDEX_ENTRY_ARTIFACT_ID}_json_error" in result.stdout
    assert str(tmp_path) not in result.stdout
    assert paths["index_entry"].name not in result.stdout


def _artifact_set() -> dict[str, dict]:
    export = build_memory_palace_visualization_export(
        build_memory_palace_sample_projection(),
    )
    verification = verify_memory_palace_visualization_export(export)
    summary = build_memory_palace_visualization_export_verification_summary(
        verification,
    )
    template = build_memory_palace_visualization_export_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-tools-1",
        task_id=(
            "codex-tools-1/"
            "memory-palace-visualization-summary-template-index-verifier-20260608"
        ),
        to="operator,codex-lead-1,claude-rco-1,claude-rco-2",
        run_id="codex-tools-1-20260608T222000Z",
        session_id="codex-tools-1-20260608T222000Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_memory_palace_visualization_export_verification_summary_bridge_event_template_index_entry(
        verification_summary=artifacts["summary"],
        bridge_event_template_report=artifacts["template"],
        verification_summary_bytes=raw["summary"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


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
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
