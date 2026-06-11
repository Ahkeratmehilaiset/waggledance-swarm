from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    ARTIFACT_ID,
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID as SOURCE_TEMPLATE_ARTIFACT_ID,
)
import tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary as runtime_summary
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    RUNTIME_GAP_VERIFICATION_KEY,
    TEMPLATE_VERSION,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    TemplateIndexEntryError,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry.py"
    )
)
FIXED_NOW = datetime(2026, 6, 11, 8, 20, tzinfo=timezone.utc)
FORBIDDEN_OUTPUT_SNIPPETS = (
    "C:/private",
    "PRIVATE_",
    "http://",
    "https://",
)


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-11T08:20:00Z"
    assert entry["summary_version"] == SUMMARY_VERSION
    assert entry["template_version"] == TEMPLATE_VERSION
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[SUMMARY_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["summary"])
    assert by_id[TEMPLATE_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["template"])
    assert by_id[SUMMARY_ARTIFACT_ID]["json_schema_version"] == SUMMARY_VERSION
    assert by_id[TEMPLATE_ARTIFACT_ID]["json_schema_version"] == TEMPLATE_VERSION
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])
    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_summary_sha256"] == _sha256_hex(raw["summary"])
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["event_status"] == EVENT_STATUS
    assert template_entry["approval_granted"] is False
    assert template_entry["release_decision_made"] is False
    assert template_entry["runtime_authority_granted"] is False
    assert template_entry["scheduler_enqueue_allowed"] is False
    assert template_entry["fast_track_priority"] is False
    assert template_entry["gate_skip_allowed"] is False
    assert entry["operator_boundary"]["approval_granted"] is False
    assert entry["operator_boundary"]["scheduler_tick_allowed"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["scheduler_enqueue_allowed"] is False
    assert entry["scheduler_tick_allowed"] is False
    assert entry["bridge_event_written"] is False
    assert entry["runtime_authority_granted"] is False
    assert entry["fast_track_priority"] is False
    assert entry["gate_skip_allowed"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(entry, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_cli_json_is_path_free(
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
            "2026-06-11T08:20:00Z",
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
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["fast_track_priority"] is False
    assert payload["gate_skip_allowed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = copy.deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"][
        RUNTIME_GAP_VERIFICATION_KEY
    ]["source_contract_check"] = "mismatch"
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert exc_info.value.code == "summary_bridge_event_template_rebuilt_mismatch"


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_rejects_summary_contract_forgery() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"][RUNTIME_GAP_VERIFICATION_KEY]["blocker_count"] = 1
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert (
        exc_info.value.code
        == "summary_bridge_event_template_source_contract_failed:"
        "index_entry_verification_blocker_count_nonzero"
    )


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_rejects_raw_bytes_mismatch() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
            index_entry_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            index_entry_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=b'{"forged":true}',
            now_utc=FIXED_NOW,
        )

    assert exc_info.value.code == f"{TEMPLATE_ARTIFACT_ID}_bytes_mismatch"


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            "C:/private/index-entry-verification-summary.json",
            "--template-json",
            "C:/private/summary-bridge-event-template-report.json",
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_failed:"
        f"{SUMMARY_ARTIFACT_ID}_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "index-entry-verification-summary.json" not in combined
    assert "summary-bridge-event-template-report.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = {
        "summary": tmp_path / "index_entry_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    artifacts = _artifact_set()
    paths["summary"].write_text(
        json.dumps(artifacts["summary"], sort_keys=True),
        encoding="utf-8",
    )
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_verifier_summary_bridge_event_template_index_entry_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"]["warnings"] = ["C:/private/report.json"]
    paths = _write_artifacts(tmp_path, artifacts)

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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_failed:"
        f"{SUMMARY_ARTIFACT_ID}_not_path_free"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    summary = _index_entry_verification_summary()
    template = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-summary-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260611T082000Z",
        session_id="codex-lead-1-20260611T082000Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _index_entry_verification_summary() -> dict:
    return build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:runtime-gap-template-index-verifier",
        now_utc=FIXED_NOW,
    )


def _verification_report() -> dict:
    report = {
        "ok": True,
        "verification_version": VERIFICATION_VERSION,
        "index_entry_version": SOURCE_INDEX_ENTRY_VERSION,
        "artifact_count_checked": 2,
        "digest_checks": _checks("match"),
        "size_checks": _checks("match"),
        "schema_version_checks": _checks("match"),
        "source_contract_check": "match",
        "rebuilt_index_entry_check": "match",
        "bridge_event_schema_check": "match",
        "template_only": True,
        "manual_review_required": True,
        "blockers": [],
        "warnings": [],
    }
    report.update(
        {field: False for field in runtime_summary._VERIFICATION_FALSE_FIELDS}
    )
    return report


def _checks(status: str) -> dict[str, str]:
    return {ARTIFACT_ID: status, SOURCE_TEMPLATE_ARTIFACT_ID: status}


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "summary": tmp_path / "index_entry_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        "summary": _json_bytes(artifacts["summary"]),
        "template": _json_bytes(artifacts["template"]),
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
