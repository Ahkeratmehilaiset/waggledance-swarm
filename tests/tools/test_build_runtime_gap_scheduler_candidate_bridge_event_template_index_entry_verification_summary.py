from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_runtime_gap_scheduler_candidate_bridge_event_template import (
    build_runtime_gap_scheduler_candidate_bridge_event_template,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry,
)
from tools.build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary,
    render_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary.py"
)
FIXED_NOW = datetime(2026, 6, 7, 14, 35, tzinfo=timezone.utc)
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
FORBIDDEN_INPUT_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "verification.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_is_context_only() -> None:
    verification_report = _verification_report()

    summary = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="runtime-gap-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-07T14:35:00Z"
    assert summary["template_only"] is True
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["scheduler_enqueue_allowed"] is False
    assert summary["scheduler_tick_allowed"] is False
    assert summary["bridge_event_written"] is False
    assert summary["fast_track_priority"] is False
    assert summary["gate_skip_allowed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    verification = summary[
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["scheduler_enqueue_allowed"] is False
    assert verification["scheduler_tick_allowed"] is False
    assert verification["bridge_event_written"] is False
    assert verification["fast_track_priority"] is False
    assert verification["gate_skip_allowed"] is False
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is True


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="codex-lead-1",
        handoff_ref="runtime-gap-summary-review",
        now_utc=FIXED_NOW,
    )

    markdown = render_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "Scheduler enqueue allowed: `false`" in markdown
    assert "Fast-track priority: `false`" in markdown
    assert "Gate skip allowed: `false`" in markdown
    assert "enqueue scheduler work" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_bytes(_json_bytes(_verification_report()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "runtime-gap-summary-review",
            "--now",
            "2026-06-07T14:35:00Z",
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
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["scheduler_tick_allowed"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_rejects_scheduler_authority() -> None:
    verification_report = _verification_report()
    verification_report["scheduler_enqueue_allowed"] = True

    summary = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="runtime-gap-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_scheduler_enqueue_allowed_not_false" in summary["blockers"]
    assert summary["scheduler_enqueue_allowed"] is False
    assert summary["operator_boundary"]["scheduler_enqueue_allowed"] is False
    verification = summary[
        "runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification"
    ]
    assert verification["scheduler_enqueue_allowed"] is True


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_rejects_nested_authority() -> None:
    verification_report = _verification_report()
    verification_report["nested"] = {"scheduler_tick_allowed": True}

    summary = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=verification_report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="runtime-gap-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        "verification_report_nested_authority_field_not_false:scheduler_tick_allowed"
        in summary["blockers"]
    )
    assert summary["scheduler_tick_allowed"] is False
    assert summary["runtime_authority_granted"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_rejects_digest_mismatch_report() -> None:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    tampered_template = deepcopy(artifacts["template"])
    tampered_template["extra_context"] = "changed"
    report = verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=index_entry,
        artifact=artifacts["artifact"],
        bridge_event_template_report=tampered_template,
        artifact_bytes=_json_bytes(artifacts["artifact"]),
        bridge_event_template_bytes=_json_bytes(tampered_template),
    )

    summary = build_runtime_gap_scheduler_candidate_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="codex-lead-1",
        handoff_ref="runtime-gap-summary-review",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_not_ok" in summary["blockers"]
    assert f"digest_mismatch:runtime_gap_scheduler_candidate_bridge_event_template" in summary["blockers"]
    assert summary["scheduler_enqueue_allowed"] is False
    assert summary["bridge_event_written"] is False


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_missing_input_is_path_free() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            FORBIDDEN_INPUT_PATH,
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "runtime-gap-summary-review",
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
        "index_entry_verification_summary_failed:"
        "verification_report_unreadable"
    ]
    assert payload["scheduler_enqueue_allowed"] is False
    assert payload["bridge_event_written"] is False
    assert payload["artifact_payloads_included"] is False
    assert "verification.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_runtime_gap_scheduler_candidate_index_entry_verification_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    verification_path = tmp_path / "verification.json"
    verification_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(verification_path),
            "--reviewer-agent",
            "codex-lead-1",
            "--handoff-ref",
            "runtime-gap-summary-review",
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
        "index_entry_verification_summary_failed:"
        "verification_report_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert verification_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _verification_report() -> dict:
    artifacts = _artifact_set()
    raw = _artifact_bytes(artifacts)
    return verify_runtime_gap_scheduler_candidate_bridge_event_template_index_entry(
        index_entry=_index_entry(artifacts),
        artifact=artifacts["artifact"],
        bridge_event_template_report=artifacts["template"],
        artifact_bytes=raw["artifact"],
        bridge_event_template_bytes=raw["template"],
    )


def _artifact_set() -> dict[str, dict]:
    artifact = _artifact()
    template = build_runtime_gap_scheduler_candidate_bridge_event_template(
        artifact=artifact,
        agent_id="codex-lead-1",
        task_id="wd-runtime-gap-template-index-entry-verifier-summary",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260607T143500Z",
        session_id="codex-lead-1-20260607T143500Z",
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


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {key: _json_bytes(value) for key, value in artifacts.items()}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
