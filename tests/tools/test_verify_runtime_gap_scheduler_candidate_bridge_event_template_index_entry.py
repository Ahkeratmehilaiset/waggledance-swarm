from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_gap_scheduler_candidate_bridge_event_template import (
    build_runtime_gap_scheduler_candidate_bridge_event_template,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    ARTIFACT_ID,
    INDEX_ENTRY_VERSION,
    TEMPLATE_ARTIFACT_ID,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry,
)
from tools.verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry.py"
)
FIXED_NOW = datetime(2026, 6, 7, 9, 30, tzinfo=timezone.utc)
DIGEST = "sha256:" + ("a" * 64)
SPEC_DIGEST = "b" * 64
CANDIDATE_DIGEST = "sha256:" + ("c" * 64)


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


def test_runtime_gap_scheduler_candidate_index_entry_verifier_recomputes_digests_without_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=index_entry,
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
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
    assert report["scheduler_enqueue_allowed"] is False
    assert report["scheduler_tick_allowed"] is False
    assert report["scheduler_tick_executed"] is False
    assert report["queue_writes_applied"] is False
    assert report["control_plane_writes_applied"] is False
    assert report["bridge_event_written"] is False
    assert report["gate_skip_allowed"] is False
    assert report["fast_track_priority"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verifier_cli_json_is_path_free(
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
            "--artifact-json",
            str(paths["artifact"]),
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
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["scheduler_tick_allowed"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_index_entry_verifier_rejects_digest_mismatch_path_free(
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
            "--candidate-artifact-json",
            str(paths["artifact"]),
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
    assert payload["approval_granted"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    for path in paths.values():
        assert path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_index_entry_verifier_rejects_missing_record() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["artifacts"] = [
        item for item in index_entry["artifacts"] if item["artifact_id"] != ARTIFACT_ID
    ]
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=index_entry,
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert f"artifact_record_missing:{ARTIFACT_ID}" in report["blockers"]
    assert report["digest_checks"][ARTIFACT_ID] == "missing_index_record"
    assert report["approval_granted"] is False
    assert report["release_decision_made"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verifier_rejects_nested_authority() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["template_index_entry"]["scheduler_tick_allowed"] = True
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=index_entry,
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert "template_index_entry_scheduler_tick_allowed_not_false" in report["blockers"]
    assert report["scheduler_tick_allowed"] is False
    assert report["runtime_authority_granted"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verifier_rejects_deterministic_entry_drift() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    index_entry["reviewer_next_actions"] = ["approve_runtime_scheduler"]
    raw = _artifact_bytes(artifacts)

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=index_entry,
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
        bridge_event_template_bytes=raw["template"],
    )

    assert report["ok"] is False
    assert report["rebuilt_index_entry_check"] == "mismatch"
    assert "rebuilt_index_entry_mismatch" in report["blockers"]
    assert report["scheduler_enqueue_allowed"] is False
    assert report["fast_track_priority"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verifier_rejects_source_contract_forgery() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_artifact = deepcopy(artifacts["artifact"])
    tampered_artifact["scheduler_enqueue_allowed"] = True

    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=index_entry,
        artifact=tampered_artifact,
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=_json_bytes(tampered_artifact),
        bridge_event_template_bytes=_json_bytes(artifacts["template"]),
    )

    assert report["ok"] is False
    assert (
        "source_contract_failed:scheduler_candidate_artifact_invalid:"
        "scheduler_enqueue_allowed_must_be_exact_false_bool"
        in report["blockers"]
    )
    assert report["source_contract_check"] == "failed"
    assert report["runtime_authority_granted"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verifier_missing_input_is_path_free(
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
            "--artifact-json",
            str(paths["artifact"]),
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
        "runtime_gap_scheduler_candidate_bridge_event_template_"
        "index_entry_verification_failed:"
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "index-entry.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_index_entry_verifier_non_finite_json_is_path_free(
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
            "--artifact-json",
            str(paths["artifact"]),
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
        "runtime_gap_scheduler_candidate_bridge_event_template_"
        "index_entry_verification_failed:"
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert paths["index_entry"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _artifact_set() -> dict[str, dict]:
    artifact = _artifact()
    template = build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-template-index-entry-verifier",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260607T093000Z",
        session_id="codex-lead-1-20260607T093000Z",
        now_utc=FIXED_NOW,
    )
    return {"artifact": artifact, "template": template}


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )


def _artifact() -> dict:
    candidate_id = "runtime_gap_scheduler_candidate:" + ("c" * 64)
    return {
        "artifact_version": "wd.runtime_gap_scheduler_candidate_artifact.v1",
        "schema_version": "runtime_gap_scheduler_candidate_artifact.v1",
        "generated_at_utc": "2026-06-06T18:45:00Z",
        "measurement_scope": "local_read_only_scheduler_candidate_preview",
        "source_report_digest": DIGEST,
        "source_report_version": "wd.runtime_gap_detector_report.v1",
        "source_report_schema_version": "runtime_gap_detector_report.v1",
        "source_report_measurement_scope": "local_read_only_gap_signal_report",
        "source_git_sha": "0123456789abcdef0123456789abcdef01234567",
        "source_branch": "codex-lead-1.runtime-gap-preview",
        "input_source_kind": "deterministic_fixture",
        "source_candidate_intent_count": 2,
        "source_scheduler_candidate_count": 1,
        "scheduler_candidate_count": 1,
        "blocked_candidate_count": 1,
        "scheduler_candidates": [
            {
                "schema_version": "runtime_gap_scheduler_candidate_preview.v1",
                "candidate_id": candidate_id,
                "candidate_digest": CANDIDATE_DIGEST,
                "candidate_kind": "runtime_gap_signal_group",
                "source_report_digest": DIGEST,
                "intent_key": "threshold_rule:energy:hot_threshold",
                "family_kind": "threshold_rule",
                "cell_coord": "energy",
                "intent_seed": "hot_threshold",
                "signal_count": 3,
                "total_weight": 3.0,
                "priority_weight": 30,
                "spec_seed_digest": SPEC_DIGEST,
                "queue_priority": "normal",
                "ready_for_scheduler_candidate": True,
                "blockers": [],
                "scheduler_enqueue_allowed": False,
                "scheduler_tick_allowed": False,
                "bridge_event_written": False,
                "runtime_authority_granted": False,
                "gate_skip_allowed": False,
                "promotion_gate_skip_allowed": False,
                "adversarial_gate_skip_allowed": False,
                "canary_gate_skip_allowed": False,
                "fast_track_priority": False,
                "raw_signal_payload_included": False,
                "raw_query_exported": False,
            }
        ],
        "artifact_path_free": True,
        "claim_gate_satisfied": False,
        "claim_safe": False,
        "literal_future_claim_safe": False,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "required_runtime_evidence_present": False,
        "runtime_detector_record_called": False,
        "digest_signals_into_intents_called": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "scheduler_tick_executed": False,
        "queue_writes_applied": False,
        "control_plane_writes_applied": False,
        "bridge_event_written": False,
        "gate_skip_allowed": False,
        "promotion_gate_skip_allowed": False,
        "adversarial_gate_skip_allowed": False,
        "canary_gate_skip_allowed": False,
        "fast_track_priority": False,
        "no_cloud_api_calls": True,
        "no_model_pull_or_download": True,
        "not_claimed": [
            "No runtime gap detector write path was called.",
            "No growth intent was inserted or enqueued.",
            "No scheduler tick or low-risk grower execution was performed.",
            "No bridge event was appended.",
            "No fast-track gate skip was granted.",
        ],
    }


def _write_bundle(
    tmp_path: Path,
    index_entry: dict,
    artifacts: dict[str, dict],
) -> dict[str, Path]:
    paths = {
        "index_entry": tmp_path / "index-entry.json",
        "artifact": tmp_path / "artifact.json",
        "template": tmp_path / "template.json",
    }
    paths["index_entry"].write_bytes(_json_bytes(index_entry))
    paths["artifact"].write_bytes(_json_bytes(artifacts["artifact"]))
    paths["template"].write_bytes(_json_bytes(artifacts["template"]))
    return paths


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
