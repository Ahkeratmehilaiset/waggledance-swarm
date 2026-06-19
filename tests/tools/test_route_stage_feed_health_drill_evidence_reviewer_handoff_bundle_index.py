# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (
    BUNDLE_INDEX_VERSION,
    FINAL_VERIFICATION_ARTIFACT_ID,
    HandoffBundleIndexError,
    REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index,
)
from tools.build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary import (
    SUMMARY_VERSION,
    build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary,
)
from tools.verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index import (
    VERIFICATION_VERSION,
    verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index,
)


ROOT = Path(__file__).resolve().parents[2]
HELPER_DIR = ROOT / "tests" / "tools"
if str(HELPER_DIR) not in sys.path:
    sys.path.insert(0, str(HELPER_DIR))

from test_route_stage_feed_health_drill_evidence_reviewer_handoff_summary import (  # noqa: E402
    _final_verification_report,
)


BUILD_SCRIPT = (
    ROOT
    / "tools"
    / "build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index.py"
)
VERIFY_SCRIPT = (
    ROOT
    / "tools"
    / "verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index.py"
)
FIXED_NOW = datetime(2026, 6, 19, 6, 30, tzinfo=timezone.utc)
SUMMARY_NOW = datetime(2026, 6, 19, 6, 20, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_VERIFICATION_PATH = _joined(
    FORBIDDEN_PATH_PREFIX,
    "/",
    "final-verification.json",
)
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_route_stage_reviewer_handoff_bundle_index_ties_artifacts_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    index = build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
        verification_report=artifacts["verification"],
        reviewer_handoff_summary=artifacts["summary"],
        verification_report_bytes=raw["verification"],
        reviewer_handoff_summary_bytes=raw["summary"],
        now_utc=FIXED_NOW,
    )

    assert index["ok"] is True
    assert index["bundle_index_version"] == BUNDLE_INDEX_VERSION
    assert index["created_at_utc"] == "2026-06-19T06:30:00Z"
    assert index["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in index["artifacts"]}
    assert by_id[FINAL_VERIFICATION_ARTIFACT_ID]["sha256"] == _sha256_hex(
        raw["verification"]
    )
    assert by_id[REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID]["sha256"] == _sha256_hex(
        raw["summary"]
    )
    assert by_id[REVIEWER_HANDOFF_SUMMARY_ARTIFACT_ID][
        "json_schema_version"
    ] == SUMMARY_VERSION
    assert all(item["payload_included"] is False for item in index["artifacts"])
    assert all(item["local_path_recorded"] is False for item in index["artifacts"])
    bundle = index["handoff_bundle"]
    assert bundle["source_contract_check"] == "match"
    assert bundle["rebuilt_summary_check"] == "match"
    assert bundle["summary_sha256"] == _sha256_hex(raw["summary"])
    assert bundle["source_verification_sha256"] == _sha256_hex(raw["verification"])
    assert bundle["manual_review_required"] is True
    assert bundle["approval_granted"] is False
    assert bundle["release_decision_made"] is False
    assert bundle["runtime_authority_granted"] is False
    assert index["operator_boundary"]["approval_granted"] is False
    assert index["operator_boundary"]["runtime_authority_granted"] is False
    assert index["direct_bridge_write_performed"] is False
    assert index["transport_added"] is False
    assert index["external_fetch_performed"] is False
    assert index["runtime_controls_added"] is False
    assert index["controls_present"] is False
    assert index["runtime_authority_granted"] is False
    assert index["external_writes_applied"] is False
    assert index["network_access_performed"] is False
    assert index["artifact_payloads_included"] is False
    assert index["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(index, sort_keys=True)
        for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_route_stage_reviewer_handoff_bundle_index_verifier_recomputes_digests() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)
    index = _bundle_index(artifacts)

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
        bundle_index=index,
        verification_report=artifacts["verification"],
        reviewer_handoff_summary=artifacts["summary"],
        verification_report_bytes=raw["verification"],
        reviewer_handoff_summary_bytes=raw["summary"],
    )

    assert report["ok"] is True
    assert report["verification_version"] == VERIFICATION_VERSION
    assert report["bundle_index_version"] == BUNDLE_INDEX_VERSION
    assert report["artifact_count_checked"] == 2
    assert set(report["digest_checks"].values()) == {"match"}
    assert set(report["size_checks"].values()) == {"match"}
    assert set(report["schema_version_checks"].values()) == {"match"}
    assert report["source_contract_check"] == "match"
    assert report["rebuilt_bundle_index_check"] == "match"
    assert report["reviewer_handoff_summary_check"] == "match"
    assert report["manual_review_required"] is True
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False
    assert report["runtime_authority_granted"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_route_stage_reviewer_handoff_bundle_index_cli_roundtrip_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    build_result = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--verification-json",
            str(paths["verification"]),
            "--summary-json",
            str(paths["summary"]),
            "--now",
            "2026-06-19T06:30:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert build_result.returncode == 0, build_result.stderr
    index = json.loads(build_result.stdout)
    assert index["ok"] is True
    combined = build_result.stdout + build_result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)

    index_path = tmp_path / "bundle-index.json"
    index_path.write_text(
        json.dumps(index, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    verify_result = subprocess.run(
        [
            sys.executable,
            str(VERIFY_SCRIPT),
            "--bundle-index-json",
            str(index_path),
            "--verification-json",
            str(paths["verification"]),
            "--summary-json",
            str(paths["summary"]),
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert verify_result.returncode == 0, verify_result.stderr
    report = json.loads(verify_result.stdout)
    assert report["ok"] is True
    combined = verify_result.stdout + verify_result.stderr
    assert str(tmp_path) not in combined
    assert index_path.name not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_route_stage_reviewer_handoff_bundle_index_rejects_summary_authority_escalation() -> None:
    artifacts = _artifact_set()
    artifacts["summary"] = copy.deepcopy(artifacts["summary"])
    artifacts["summary"]["runtime_authority_granted"] = True
    raw = _artifact_bytes(artifacts)

    with pytest.raises(HandoffBundleIndexError) as exc_info:
        build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
            verification_report=artifacts["verification"],
            reviewer_handoff_summary=artifacts["summary"],
            verification_report_bytes=raw["verification"],
            reviewer_handoff_summary_bytes=raw["summary"],
            now_utc=FIXED_NOW,
        )

    assert "runtime_authority_granted_not_false" in exc_info.value.code


def test_route_stage_reviewer_handoff_bundle_index_verifier_detects_digest_mismatch() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)
    index = _bundle_index(artifacts)
    index = copy.deepcopy(index)
    index["artifacts"][0]["sha256"] = "0" * 64

    report = verify_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
        bundle_index=index,
        verification_report=artifacts["verification"],
        reviewer_handoff_summary=artifacts["summary"],
        verification_report_bytes=raw["verification"],
        reviewer_handoff_summary_bytes=raw["summary"],
    )

    assert report["ok"] is False
    assert f"digest_mismatch:{FINAL_VERIFICATION_ARTIFACT_ID}" in report["blockers"]
    assert report["direct_bridge_write_performed"] is False
    assert report["runtime_authority_granted"] is False


def test_route_stage_reviewer_handoff_bundle_index_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_SCRIPT),
            "--verification-json",
            FORBIDDEN_VERIFICATION_PATH,
            "--summary-json",
            FORBIDDEN_VERIFICATION_PATH,
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
        "route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index_"
        f"failed:{FINAL_VERIFICATION_ARTIFACT_ID}_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert "final-verification.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    verification = _final_verification_report()
    summary = build_route_stage_feed_health_drill_evidence_reviewer_handoff_summary(
        verification_report=verification,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:route-stage-feed-health-drill-reviewer-handoff",
        now_utc=SUMMARY_NOW,
    )
    return {"verification": verification, "summary": summary}


def _bundle_index(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_route_stage_feed_health_drill_evidence_reviewer_handoff_bundle_index(
        verification_report=artifacts["verification"],
        reviewer_handoff_summary=artifacts["summary"],
        verification_report_bytes=raw["verification"],
        reviewer_handoff_summary_bytes=raw["summary"],
        now_utc=FIXED_NOW,
    )


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        name: json.dumps(
            artifact,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        for name, artifact in artifacts.items()
    }


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "verification": tmp_path / "verification.json",
        "summary": tmp_path / "summary.json",
    }
    for name, path in paths.items():
        path.write_bytes(_artifact_bytes(artifacts)[name])
    return paths


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
