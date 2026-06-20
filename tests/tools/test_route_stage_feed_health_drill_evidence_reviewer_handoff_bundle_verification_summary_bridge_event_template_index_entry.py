# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    TEMPLATE_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    TemplateIndexEntryError,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template import (  # noqa: E402
    FORBIDDEN_OUTPUT_SNIPPETS,
    ROUTE_STAGE_BUNDLE_VERIFICATION_KEY,
    SUMMARY_VERSION,
    _bundle_verification_summary,
)


SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry.py"
    )
)
FIXED_NOW = datetime(2026, 6, 19, 7, 5, tzinfo=timezone.utc)
FORBIDDEN_SUMMARY_PATH = "C:/private/handoff-bundle-summary-template.json"


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
        bundle_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        bundle_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-19T07:05:00Z"
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
    assert entry["operator_boundary"]["approval_granted"] is False
    assert entry["operator_boundary"]["release_decision_made"] is False
    assert entry["operator_boundary"]["runtime_authority_granted"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["external_fetch_performed"] is False
    assert entry["runtime_controls_added"] is False
    assert entry["controls_present"] is False
    assert entry["runtime_authority_granted"] is False
    assert entry["external_writes_applied"] is False
    assert entry["network_access_performed"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(entry, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_cli_json_is_path_free(
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
            "2026-06-19T07:05:00Z",
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
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"][
        ROUTE_STAGE_BUNDLE_VERIFICATION_KEY
    ]["source_contract_check"] = "mismatch"
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
            bundle_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            bundle_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert exc_info.value.code == "summary_bridge_event_template_rebuilt_mismatch"


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_rejects_summary_contract_forgery() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = deepcopy(artifacts["summary"])
    artifacts["summary"]["approval_granted"] = True
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
            bundle_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            bundle_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )

    assert (
        exc_info.value.code
        == "summary_bridge_event_template_source_contract_failed:"
        "bundle_verification_summary_approval_granted_not_false"
    )


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_rejects_raw_bytes_mismatch() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    with pytest.raises(TemplateIndexEntryError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry(
            bundle_verification_summary=artifacts["summary"],
            summary_bridge_event_template_report=artifacts["template"],
            bundle_verification_summary_bytes=raw["summary"],
            summary_bridge_event_template_bytes=b'{"forged":true}',
            now_utc=FIXED_NOW,
        )

    assert exc_info.value.code == f"{TEMPLATE_ARTIFACT_ID}_bytes_mismatch"


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--summary-json",
            FORBIDDEN_SUMMARY_PATH,
            "--template-json",
            FORBIDDEN_SUMMARY_PATH,
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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_"
        "bundle_verification_summary_bridge_event_template_index_"
        f"entry_failed:{SUMMARY_ARTIFACT_ID}_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "handoff-bundle-summary-template.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["template"]["warnings"] = [float("nan")]
    paths = {
        "summary": tmp_path / "handoff_bundle_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    paths["summary"].write_bytes(_json_bytes(artifacts["summary"]))
    paths["template"].write_text(
        json.dumps(artifacts["template"], sort_keys=True),
        encoding="utf-8",
    )

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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_"
        "bundle_verification_summary_bridge_event_template_index_"
        f"entry_failed:{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_rejects_path_markers_without_leak(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["summary"]["warnings"] = ["C:/private/summary.json"]
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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_"
        "bundle_verification_summary_bridge_event_template_index_"
        f"entry_failed:{SUMMARY_ARTIFACT_ID}_not_path_free"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verification_summary_template_index_entry_rejects_warning_filename_without_leak(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    artifacts["template"]["warnings"] = ["evidence.json"]
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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_"
        "bundle_verification_summary_bridge_event_template_index_"
        "entry_failed:summary_bridge_event_template_warnings_item_unsafe"
    ]
    combined = result.stdout + result.stderr
    assert "evidence.json" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    summary = _bundle_verification_summary()
    template = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-route-stage-bundle-summary-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260619T070000Z",
        session_id="codex-lead-1-20260619T070000Z",
        now_utc=datetime(2026, 6, 19, 7, 0, tzinfo=timezone.utc),
    )
    return {"summary": summary, "template": template}


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "summary": tmp_path / "handoff_bundle_verification_summary.json",
        "template": tmp_path / "summary_bridge_event_template_report.json",
    }
    for key, path in paths.items():
        path.write_bytes(_json_bytes(artifacts[key]))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
