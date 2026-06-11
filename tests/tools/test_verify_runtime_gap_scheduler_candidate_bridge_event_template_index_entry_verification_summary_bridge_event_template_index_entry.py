# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    ARTIFACT_ID,
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID as SOURCE_TEMPLATE_ARTIFACT_ID,
)
import tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary as runtime_summary
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary import (
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template import (
    RUNTIME_GAP_VERIFICATION_KEY,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)
from tools.verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    _AUTHORITY_FALSE_FIELDS,
    verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry.py"
    )
)
FIXED_NOW = datetime(2026, 6, 11, 12, 15, tzinfo=timezone.utc)
FORBIDDEN_PATH = "C:/private/index-entry.json"
FORBIDDEN_OUTPUT_SNIPPETS = (
    "C:/private",
    "PRIVATE_",
    "http://",
    "https://",
)


def test_runtime_gap_verifier_summary_template_index_entry_verifier_recomputes_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
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
    assert report["manual_review_required"] is True
    assert all(report[field] is False for field in _AUTHORITY_FALSE_FIELDS)


def test_runtime_gap_verifier_summary_template_index_entry_verifier_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)

    result = _run_cli(paths)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["rebuilt_index_entry_check"] == "match"
    assert payload["bridge_event_schema_check"] == "match"
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    _assert_path_free_output(result, tmp_path, paths.values())


def test_runtime_gap_verifier_summary_template_index_entry_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["extra_context"] = "changed"
    paths["template"].write_bytes(_json_bytes(tampered_template))

    result = _run_cli(paths)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert f"digest_mismatch:{TEMPLATE_ARTIFACT_ID}" in payload["blockers"]
    assert payload["source_contract_check"] == "failed"
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    _assert_path_free_output(result, tmp_path, paths.values())


def test_runtime_gap_verifier_summary_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{SUMMARY_ARTIFACT_ID}" in report["blockers"]
    assert report["digest_checks"][SUMMARY_ARTIFACT_ID] == "missing_index_record"
    assert all(report[field] is False for field in _AUTHORITY_FALSE_FIELDS)


def test_runtime_gap_verifier_summary_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["scheduler_enqueue_allowed"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert "template_index_entry_scheduler_enqueue_allowed_not_false" in report[
        "blockers"
    ]
    assert all(report[field] is False for field in _AUTHORITY_FALSE_FIELDS)


def test_runtime_gap_verifier_summary_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["approve_release"]
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert all(report[field] is False for field in _AUTHORITY_FALSE_FIELDS)


def test_runtime_gap_verifier_summary_template_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_summary = deepcopy(artifacts["summary"])
    tampered_summary[RUNTIME_GAP_VERIFICATION_KEY]["runtime_authority_granted"] = True

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=tampered_summary,
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=_json_bytes(tampered_summary),
        summary_bridge_event_template_bytes=_json_bytes(artifacts["template"]),
    )

    assert report["ok"] is False
    assert "source_contract_failed:" in " ".join(report["blockers"])
    assert all(report[field] is False for field in _AUTHORITY_FALSE_FIELDS)


def test_runtime_gap_verifier_summary_template_index_entry_verifier_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            FORBIDDEN_PATH,
            "--summary-json",
            FORBIDDEN_PATH,
            "--template-json",
            FORBIDDEN_PATH,
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
        "verification_summary_bridge_event_template_index_entry_"
        "verification_failed:"
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_unreadable"
    ]
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    _assert_path_free_output(result, None, [])


def test_runtime_gap_verifier_summary_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    paths["template"].write_text('{"ok": NaN}', encoding="utf-8")

    result = _run_cli(paths)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    _assert_path_free_output(result, tmp_path, paths.values())


def test_runtime_gap_verifier_summary_template_index_entry_verifier_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_summary = deepcopy(artifacts["summary"])
    tampered_summary["warnings"] = ["C:/private/report.json"]
    paths = _write_bundle(
        tmp_path,
        index_entry,
        {"summary": tampered_summary, "template": artifacts["template"]},
    )

    result = _run_cli(paths)

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_failed:"
        f"{SUMMARY_ARTIFACT_ID}_forbidden_marker"
    ]
    assert all(payload[field] is False for field in _AUTHORITY_FALSE_FIELDS)
    _assert_path_free_output(result, tmp_path, paths.values())


def _artifact_set() -> dict[str, dict]:
    summary = _index_entry_verification_summary()
    template = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-summary-template-index-verifier",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260611T121500Z",
        session_id="codex-lead-1-20260611T121500Z",
        now_utc=FIXED_NOW,
    )
    return {"summary": summary, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


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
        "verification_version": SOURCE_VERIFICATION_VERSION,
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


def _write_bundle(
    tmp_path: Path,
    index_entry: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "index_entry": tmp_path / "index_entry.json",
        "summary": tmp_path / "index_entry_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


def _run_cli(paths: dict[str, Path]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
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


def _assert_path_free_output(
    result: subprocess.CompletedProcess[str],
    tmp_path: Path | None,
    paths: object,
) -> None:
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    if tmp_path is not None:
        assert str(tmp_path) not in combined
    for path in paths:
        assert path.name not in combined
    assert "index-entry.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        "summary": _json_bytes(artifacts["summary"]),
        "template": _json_bytes(artifacts["template"]),
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
