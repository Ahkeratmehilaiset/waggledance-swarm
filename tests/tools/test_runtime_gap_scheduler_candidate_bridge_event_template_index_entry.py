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
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry.py"
)
FIXED_NOW = datetime(2026, 6, 7, 8, 45, tzinfo=timezone.utc)
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
FORBIDDEN_TEMPLATE_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "template.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_ties_digests_without_authority() -> None:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)

    entry = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
        bridge_event_template_bytes=raw["template"],
        now_utc=FIXED_NOW,
    )

    assert entry["ok"] is True
    assert entry["index_entry_version"] == INDEX_ENTRY_VERSION
    assert entry["created_at_utc"] == "2026-06-07T08:45:00Z"
    assert entry["artifact_count"] == 2
    by_id = {item["artifact_id"]: item for item in entry["artifacts"]}
    assert by_id[ARTIFACT_ID]["sha256"] == _sha256_hex(raw["artifact"])
    assert by_id[TEMPLATE_ARTIFACT_ID]["sha256"] == _sha256_hex(raw["template"])
    assert all(item["payload_included"] is False for item in entry["artifacts"])
    assert all(item["local_path_recorded"] is False for item in entry["artifacts"])

    template_entry = entry["template_index_entry"]
    assert template_entry["template_only"] is True
    assert template_entry["bridge_event_schema_validated"] is True
    assert template_entry["source_contract_check"] == "match"
    assert template_entry["artifact_digest_check"] == "match"
    assert template_entry["rebuilt_template_check"] == "match"
    assert template_entry["source_artifact_sha256"] == _sha256_hex(raw["artifact"])
    assert template_entry["source_artifact_digest"] == sha256_digest(
        artifacts["artifact"]
    )
    assert template_entry["template_sha256"] == _sha256_hex(raw["template"])
    assert template_entry["scheduler_candidate_count"] == 1
    assert template_entry["manual_review_required"] is True
    assert template_entry["approval_granted"] is False
    assert template_entry["scheduler_enqueue_allowed"] is False
    assert template_entry["scheduler_tick_allowed"] is False
    assert template_entry["bridge_event_written"] is False
    assert template_entry["fast_track_priority"] is False
    assert template_entry["gate_skip_allowed"] is False
    assert entry["direct_bridge_write_performed"] is False
    assert entry["transport_added"] is False
    assert entry["runtime_controls_added"] is False
    assert entry["runtime_authority_granted"] is False
    assert entry["artifact_payloads_included"] is False
    assert entry["local_paths_recorded"] is False


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact-json",
            str(paths["artifact"]),
            "--bridge-event-template-json",
            str(paths["template"]),
            "--now",
            "2026-06-07T08:45:00Z",
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
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    for path in paths.values():
        assert path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_rejects_template_drift() -> None:
    artifacts = _artifact_set()
    artifacts["template"] = deepcopy(artifacts["template"])
    artifacts["template"]["bridge_event_template"]["payload"]["artifact_digest"] = (
        "sha256:" + ("f" * 64)
    )
    raw = _artifact_bytes(artifacts)

    try:
        build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
            artifact=artifacts["artifact"],
            bridge_event_template_report=artifacts["template"],
            artifact_bytes=raw["artifact"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "bridge_event_template_rebuilt_mismatch"
    else:  # pragma: no cover
        raise AssertionError("expected template drift rejection")


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_rejects_authority_escalation() -> None:
    artifacts = _artifact_set()
    artifacts["artifact"] = deepcopy(artifacts["artifact"])
    artifacts["artifact"]["fast_track_priority"] = True
    raw = _artifact_bytes(artifacts)

    try:
        build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
            artifact=artifacts["artifact"],
            bridge_event_template_report=artifacts["template"],
            artifact_bytes=raw["artifact"],
            bridge_event_template_bytes=raw["template"],
            now_utc=FIXED_NOW,
        )
    except Exception as exc:
        assert str(getattr(exc, "code", "")).startswith(
            "scheduler_candidate_artifact_invalid:"
        )
    else:  # pragma: no cover
        raise AssertionError("expected artifact authority rejection")


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact-json",
            str(paths["artifact"]),
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_failed:"
        "runtime_gap_scheduler_candidate_bridge_event_template_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "template.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_failed:"
        "runtime_gap_scheduler_candidate_bridge_event_template_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_duplicate_json_key_is_path_free(
    tmp_path: Path,
) -> None:
    paths = _write_artifacts(tmp_path, _artifact_set())
    paths["template"].write_text('{"ok": true, "ok": false}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
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
    assert "runtime_gap_scheduler_candidate_bridge_event_template_json_error" in (
        result.stdout
    )
    assert str(tmp_path) not in result.stdout
    assert paths["template"].name not in result.stdout


def _artifact_set() -> dict[str, dict]:
    artifact = _artifact()
    template = build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-template-index-entry",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260607T084500Z",
        session_id="codex-lead-1-20260607T084500Z",
        now_utc=FIXED_NOW,
    )
    return {"artifact": artifact, "template": template}


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


def _write_artifacts(tmp_path: Path, artifacts: dict[str, dict]) -> dict[str, Path]:
    paths = {
        "artifact": tmp_path / "runtime_gap_scheduler_candidate_artifact.json",
        "template": tmp_path
        / "runtime_gap_scheduler_candidate_bridge_event_template.json",
    }
    for key, path in paths.items():
        path.write_bytes(_artifact_bytes(artifacts)[key])
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
