# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path

from tools.build_route_stage_handoff_verifier_summary_bridge_template_index_entry import (
    INDEX_ENTRY_VERSION,
    SUMMARY_ARTIFACT_ID,
    TEMPLATE_ARTIFACT_ID,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)
from tools.verify_route_stage_handoff_verifier_summary_bridge_template_index_entry import (
    TemplateIndexEntryVerificationError,
    VERIFICATION_VERSION,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_route_stage_handoff_verifier_summary_bridge_template_index_entry import (  # noqa: E402
    FORBIDDEN_OUTPUT_SNIPPETS,
    _artifact_bytes,
    _artifact_set,
    _json_bytes,
)


SCRIPT = (
    ROOT
    / "tools"
    / "verify_route_stage_handoff_verifier_summary_bridge_template_index_entry.py"
)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_INDEX_ENTRY_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "index-entry.json")


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_recomputes_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
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
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["direct_bridge_write_performed"] is False
    assert report["transport_added"] is False
    assert report["external_fetch_performed"] is False
    assert report["runtime_controls_added"] is False
    assert report["controls_present"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    assert report["network_access_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_rejects_path_markers_before_created_at_is_ignored() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    for marker in (
        "D:/wd/path-leak/index-entry.json",
        "/tmp/wd/path-leak/index-entry.json",
    ):
        mutated = deepcopy(index_entry)
        mutated["created_at_utc"] = marker

        try:
            verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
                index_entry=mutated,
                index_entry_verification_summary=artifacts["summary"],
                summary_bridge_event_template_report=artifacts["template"],
                index_entry_verification_summary_bytes=raw["summary"],
                summary_bridge_event_template_bytes=raw["template"],
            )
        except TemplateIndexEntryVerificationError as exc:
            assert str(exc).endswith("_forbidden_marker")
            assert marker not in str(exc)
        else:
            raise AssertionError("accepted path marker in ignored created_at_utc")


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_cli_json_is_path_free(
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
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_rejects_digest_mismatch_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["extra_context"] = "changed"
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
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item
        for item in index_entry["artifacts"]
        if item["artifact_id"] != SUMMARY_ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
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


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["approval_granted"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert "template_index_entry_approval_granted_not_false" in report["blockers"]
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["approve_release"]
    raw = _artifact_bytes(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--index-entry-json",
            FORBIDDEN_INDEX_ENTRY_PATH,
            "--summary-json",
            FORBIDDEN_INDEX_ENTRY_PATH,
            "--template-json",
            FORBIDDEN_INDEX_ENTRY_PATH,
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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_verification_failed:"
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "index-entry.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_handoff_bundle_verifier_summary_template_index_entry_verifier_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    paths = _write_bundle(tmp_path, index_entry, artifacts)
    artifacts["template"]["warnings"] = [float("nan")]
    paths["template"].write_text(
        json.dumps(artifacts["template"], sort_keys=True),
        encoding="utf-8",
    )

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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_"
        "verification_summary_bridge_event_template_index_entry_"
        "verification_summary_bridge_event_template_index_entry_verification_failed:"
        f"{TEMPLATE_ARTIFACT_ID}_json_error"
    ]
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 6, 20, 0, 11, tzinfo=timezone.utc),
    )


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
