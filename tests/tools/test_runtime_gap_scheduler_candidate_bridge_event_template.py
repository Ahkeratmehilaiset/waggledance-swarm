from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import build_runtime_gap_scheduler_candidate_bridge_event_template as builder  # noqa: E402
from waggledance.core.bridge_event_schema import validate_event  # noqa: E402


SCRIPT = ROOT / "tools" / "build_runtime_gap_scheduler_candidate_bridge_event_template.py"
FIXED_NOW = datetime(2026, 6, 6, 19, 0, tzinfo=timezone.utc)
DIGEST = "sha256:" + ("a" * 64)
SPEC_DIGEST = "b" * 64
CANDIDATE_DIGEST = "sha256:" + ("c" * 64)


def _artifact() -> dict:
    candidate_id = "runtime_gap_scheduler_candidate:" + ("c" * 64)
    return {
        "artifact_version": builder.ARTIFACT_VERSION,
        "schema_version": builder.ARTIFACT_SCHEMA_VERSION,
        "generated_at_utc": "2026-06-06T18:45:00Z",
        "measurement_scope": builder.ARTIFACT_MEASUREMENT_SCOPE,
        "source_report_digest": DIGEST,
        "source_report_version": builder.SOURCE_REPORT_VERSION,
        "source_report_schema_version": builder.SOURCE_REPORT_SCHEMA_VERSION,
        "source_report_measurement_scope": builder.SOURCE_REPORT_MEASUREMENT_SCOPE,
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


def _run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_scheduler_candidate_bridge_event_template_validates_schema() -> None:
    report = builder.build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=_artifact(),
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-scheduler-candidate-template",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260606T190000Z",
        session_id="codex-lead-1-20260606T190000Z",
        now_utc=FIXED_NOW,
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(event, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == builder.TEMPLATE_VERSION
    assert report["template_only"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["scheduler_enqueue_allowed"] is False
    assert report["scheduler_tick_allowed"] is False
    assert report["bridge_event_written"] is False
    assert report["runtime_authority_granted"] is False
    assert report["fast_track_priority"] is False
    assert report["gate_skip_allowed"] is False
    assert event["type"] == "handoff"
    assert event["status"] == builder.EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["pid"] == 0

    payload = event["payload"]
    assert payload["schema_version"] == builder.TEMPLATE_VERSION
    assert payload["artifact_version"] == builder.ARTIFACT_VERSION
    assert payload["template_only"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    preview = payload["scheduler_candidate_preview"]
    assert preview["artifact_verified"] is True
    assert preview["artifact_path_free"] is True
    assert preview["scheduler_candidate_count"] == 1
    assert preview["blocked_candidate_count"] == 1
    assert preview["raw_artifact_payload_included"] is False
    assert preview["raw_signal_payload_included"] is False
    assert preview["raw_query_exported"] is False
    authority = payload["authority_boundary"]
    assert authority["approval_granted"] is False
    assert authority["scheduler_enqueue_allowed"] is False
    assert authority["scheduler_tick_allowed"] is False
    assert authority["bridge_event_written"] is False
    assert authority["fast_track_priority"] is False
    assert authority["gate_skip_allowed"] is False


def test_scheduler_candidate_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "runtime_gap_scheduler_candidate_artifact.json"
    artifact_path.write_text(json.dumps(_artifact(), sort_keys=True), encoding="utf-8")

    result = _run_cli(
        "--artifact-json",
        str(artifact_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-runtime-gap-scheduler-candidate-template",
        "--to",
        "operator,claude-rco-1",
        "--run-id",
        "codex-lead-1-20260606T190000Z",
        "--session-id",
        "codex-lead-1-20260606T190000Z",
        "--now",
        "2026-06-06T19:00:00Z",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = payload["bridge_event_template"]
    validate_event(event)
    assert payload["direct_bridge_write_performed"] is False
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert artifact_path.name not in result.stdout


def test_scheduler_candidate_bridge_event_template_missing_input_is_path_free() -> None:
    forbidden_path = "C:/private/runtime_gap_scheduler_candidate_artifact.json"

    result = _run_cli(
        "--artifact-json",
        forbidden_path,
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-runtime-gap-scheduler-candidate-template",
        "--json",
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert "scheduler_candidate_artifact_unreadable" in result.stdout
    assert forbidden_path not in result.stdout
    assert "C:/private" not in result.stdout


def test_scheduler_candidate_bridge_event_template_rejects_exact_false_type_confusion() -> None:
    artifact = _artifact()
    artifact["scheduler_enqueue_allowed"] = "false"

    report = builder.build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-scheduler-candidate-template",
    )

    assert report["ok"] is False
    assert any("scheduler_enqueue_allowed" in item for item in report["blockers"])

    artifact = _artifact()
    artifact["scheduler_candidates"][0]["gate_skip_allowed"] = "false"
    report = builder.build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-scheduler-candidate-template",
    )

    assert report["ok"] is False
    assert any("gate_skip_allowed" in item for item in report["blockers"])


def test_scheduler_candidate_bridge_event_template_rejects_authority_escalation() -> None:
    artifact = _artifact()
    artifact["fast_track_priority"] = True

    report = builder.build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-scheduler-candidate-template",
    )

    assert report["ok"] is False
    assert any("fast_track_priority" in item for item in report["blockers"])


def test_scheduler_candidate_bridge_event_template_rejects_unsafe_candidate_scalar() -> None:
    artifact = _artifact()
    artifact["scheduler_candidates"][0]["intent_key"] = "C:/private/gap.json"

    report = builder.build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-scheduler-candidate-template",
    )

    assert report["ok"] is False
    assert any("not_path_free" in item for item in report["blockers"])


def test_scheduler_candidate_bridge_event_template_rejects_duplicate_json_keys(
    tmp_path: Path,
) -> None:
    artifact_path = tmp_path / "artifact.json"
    artifact_path.write_text(
        '{"artifact_version":"one","artifact_version":"two"}',
        encoding="utf-8",
    )

    result = _run_cli(
        "--artifact-json",
        str(artifact_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "wd-runtime-gap-scheduler-candidate-template",
        "--json",
    )

    assert result.returncode == 1
    assert "scheduler_candidate_artifact_json_error" in result.stdout


def test_scheduler_candidate_bridge_event_template_invalid_target_fails_closed() -> None:
    report = builder.build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=_artifact(),
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-scheduler-candidate-template",
        to="operator,bad target",
    )

    assert report["ok"] is False
    assert any("to_unsafe" in item for item in report["blockers"])
