# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

import tools.idle_protocol_activate as activator
from tools.idle_check import _is_substantive_agent_message
from tools.idle_protocol_activate import ActivationError, activate_idle_protocol
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.bridge_event_schema import validate_event
from waggledance.core.magma.canonical import sha256_digest


NOW = datetime(2026, 5, 17, 12, 0, tzinfo=timezone.utc)


def _event(
    *,
    ts_utc: str,
    agent: str = "codex",
    type: str = "message",
    task_id: str = "idle-activation-smoke",
    status: str = "note",
    to: str = "claude",
    message: str = "Substantive bridge content that should count as agent activity.",
    payload: dict | None = None,
) -> dict[str, object]:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type,
        "task_id": task_id,
        "status": status,
        "severity": "",
        "to": to,
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": payload or {},
    }


def _proposal(proposal_id: str = "idle-prop-20260517-001") -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": "idle_proposal",
        "proposal_id": proposal_id,
        "round_number": 1,
        "proposes_substrate_change": True,
        "problem_statement": "Strategic bridge deliberation stalls when no PR vehicle exists.",
        "proposal": (
            "Emit a manual idle proposal only after the detector reports an idle bridge "
            "and keep all execution behind the operator gate."
        ),
        "tradeoff_axis": "Operator-gated activation versus slower unattended strategic deliberation.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": (
                "A quiet ninety minute bridge window accepts round one while a recent "
                "message rejects it as active."
            ),
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": (
                "Manual emission has no auto-execute path and consensus remains operator gated."
            ),
        },
    }


def _counter() -> dict:
    event = _proposal("idle-prop-20260517-002")
    event.update(
        {
            "event_type": "idle_counter_proposal",
            "round_number": 2,
            "responds_to": "idle-prop-20260517-001",
            "alternative_proposal": (
                "Continue the protocol after the first idle proposal without requiring "
                "the bridge to remain idle during the active deliberation."
            ),
            "reasoning_points": [
                "If round two required idle, the first idle proposal would block the required reply.",
                "If prior idle payloads are absent, a round two event must fail before emission.",
                "When a bridge event already carries idle-protocol.v1, continuation remains traceable.",
            ],
        }
    )
    del event["proposal"]
    return event


def _consensus(proposal_id: str) -> dict:
    event = _proposal(proposal_id)
    event.update(
        {
            "event_type": "idle_consensus_reached",
            "round_number": 5,
            "proposes_substrate_change": False,
            "consensus_target_proposal_id": "idle-prop-20260517-002",
            "operator_gate_required": True,
            "auto_execute": False,
        }
    )
    del event["proposal"]
    return event


def _adversarial(proposal_id: str = "idle-prop-20260517-003") -> dict:
    event = _proposal(proposal_id)
    event.update(
        {
            "event_type": "idle_adversarial_review",
            "round_number": 3,
            "responds_to": "idle-prop-20260517-002",
            "counterexamples": [
                "If an active claim exists without recent messages, the detector must block false idle.",
                "When malformed bridge lines are present, activation should fail rather than infer silence.",
            ],
        }
    )
    del event["proposal"]
    return event


def _charter_violation(proposal_id: str = "idle-prop-20260517-004") -> dict:
    event = _proposal(proposal_id)
    event.update(
        {
            "event_type": "idle_charter_violation",
            "round_number": 4,
            "proposes_substrate_change": False,
            "violating_proposal_id": "idle-prop-20260517-002",
            "violation_reason": "The proposal would convert idle consensus into automatic execution.",
            "terminate_protocol": True,
            "operator_escalation_required": True,
            "charter_alignment": {
                "compatible": False,
                "reasoning": "Automatic execution would bypass the operator-owned gate.",
            },
        }
    )
    del event["proposal"]
    return event


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _base_events() -> list[dict[str, object]]:
    return [
        _event(
            ts_utc="2026-05-17T10:20:00Z",
            type="done",
            status="merged_postmerge_green",
            message="Merged work was verified more than one idle window ago.",
        ),
        _event(
            ts_utc="2026-05-17T10:30:00Z",
            agent="claude",
            status="scout_answered",
            message="Substantive scout response older than the idle window.",
        ),
    ]


def _idle_instance_events(
    count: int,
    *,
    date: str = "2026-05-17",
) -> list[dict[str, object]]:
    return [
        _event(
            ts_utc=f"{date}T09:{index:02d}:00Z",
            status="idle_proposal",
            message="Prior idle protocol proposal emitted outside the current quiet window.",
            payload=_proposal(f"idle-prop-20260517-rate-{index:03d}"),
        )
        for index in range(count)
    ]


def _activate(
    tmp_path: Path,
    payload: dict,
    *,
    events: list[dict[str, object]] | None = None,
    emit: bool = False,
    receipt_out_dir: Path | None = None,
    from_agent: str = "codex",
    to_agent: str | None = None,
) -> dict:
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    bridge_root = tmp_path / "bridge"
    claims_dir.mkdir()
    _write_json(payload_path, payload)
    _write_events(events_path, events if events is not None else _base_events())
    return activate_idle_protocol(
        payload_path=payload_path,
        events_path=events_path,
        claims_dir=claims_dir,
        bridge_root=bridge_root,
        from_agent=from_agent,
        to_agent=to_agent,
        task_id=None,
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
        now_utc=NOW,
        emit=emit,
        receipt_out_dir=receipt_out_dir,
    )


def test_round_one_dry_run_requires_idle_and_does_not_emit(tmp_path: Path) -> None:
    report = _activate(tmp_path, _proposal())

    assert report["decision"] == "ready"
    assert report["emitted"] is False
    assert report["proposed_bridge_event"]["status"] == "idle_proposal"
    assert report["to"] == "claude"
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_one_accepts_regex_agent_ids_when_target_is_explicit(
    tmp_path: Path,
) -> None:
    report = _activate(
        tmp_path,
        _proposal(),
        from_agent="codex-2",
        to_agent="claude-1",
    )

    assert report["from_agent"] == "codex-2"
    assert report["to"] == "claude-1"
    assert report["proposed_bridge_event"]["agent"] == "codex-2"
    assert report["proposed_bridge_event"]["to"] == "claude-1"
    validate_event(report["proposed_bridge_event"])


def test_round_one_refuses_active_bridge_before_emitting(tmp_path: Path) -> None:
    active_events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            message="Recent substantive work means the bridge is not idle enough.",
        )
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _proposal(), events=active_events, emit=True)

    assert excinfo.value.report["decision"] == "active"
    assert "recent_agent_message" in excinfo.value.report["blockers"]
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_one_rate_limit_blocks_sixth_daily_instance(tmp_path: Path) -> None:
    events = _base_events() + _idle_instance_events(5)

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _proposal(), events=events, emit=True)

    assert excinfo.value.report["decision"] == "rate_limited"
    assert excinfo.value.report["rate_limit"] == {
        "max_instances_per_day": 5,
        "instances_today": 5,
        "utc_date": "2026-05-17",
    }
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_one_rate_limit_uses_utc_day_boundary(tmp_path: Path) -> None:
    events = _base_events() + _idle_instance_events(5, date="2026-05-16")

    report = _activate(tmp_path, _proposal(), events=events, emit=False)

    assert report["decision"] == "ready"
    assert report["emitted"] is False


def test_quota_counter_skips_malformed_timestamps() -> None:
    malformed = _event(
        ts_utc="not-a-date",
        status="idle_proposal",
        payload=_proposal("idle-prop-20260517-malformed"),
    )

    instances = activator._idle_instances_for_utc_day(
        [*_idle_instance_events(4), malformed],
        NOW,
    )

    assert instances == [
        "idle-prop-20260517-rate-000",
        "idle-prop-20260517-rate-001",
        "idle-prop-20260517-rate-002",
        "idle-prop-20260517-rate-003",
    ]


def test_quota_counter_only_counts_idle_payloads_inside_bridge_envelope() -> None:
    flat_payload = _proposal("idle-prop-20260517-flat")
    flat_payload["ts_utc"] = "2026-05-17T09:30:00Z"

    instances = activator._idle_instances_for_utc_day([flat_payload], NOW)

    assert instances == []


def test_emit_appends_bridge_event_outbox_and_last_file(tmp_path: Path) -> None:
    report = _activate(tmp_path, _proposal(), emit=True)

    events_path = tmp_path / "bridge" / "shared" / "events.jsonl"
    outbox_path = tmp_path / "bridge" / "outbox" / "codex" / "2026-05-17.jsonl"
    last_path = tmp_path / "bridge" / "shared" / "last_codex.json"
    emitted = json.loads(events_path.read_text(encoding="utf-8").strip())

    assert report["emitted"] is True
    assert outbox_path.exists()
    assert last_path.exists()
    assert emitted["status"] == "idle_proposal"
    assert emitted["payload"]["protocol_version"] == "idle-protocol.v1"
    assert "auto_execute" not in emitted["payload"]
    validate_event(emitted)
    assert _is_substantive_agent_message(emitted) is True


def test_receipt_out_dir_writes_verified_bundle_without_bridge_emit(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "idle-receipt-bundle"

    report = _activate(tmp_path, _proposal(), receipt_out_dir=out_dir)

    bundle = report["receipt_bundle"]
    assert report["emitted"] is False
    assert bundle["receipt_count"] == 1
    assert bundle["verifier_report"] == {
        "ok": True,
        "receipt_count": 1,
        "errors": [],
    }
    assert verify_manifest(out_dir / "manifest.json")["ok"] is True
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()

    payload = json.loads((out_dir / "payload-001-idle.json").read_text(encoding="utf-8"))
    evaluation = json.loads(
        (out_dir / "evaluation-001-idle.json").read_text(encoding="utf-8")
    )
    receipt = json.loads((out_dir / "receipt-001-idle.json").read_text(encoding="utf-8"))

    assert payload == report["proposed_bridge_event"]["payload"]
    assert evaluation["subject_type"] == "peer_review"
    assert evaluation["risk_class"] == "local_artifact"
    assert evaluation["actual_gate"] == "review"
    assert evaluation["target_digest"] == sha256_digest(payload)
    assert receipt["risk_class"] == "local_artifact"
    assert receipt["operator_gate_required"] is False
    assert receipt["prev_receipt_hash"] is None
    assert receipt["canonical_payload_digest"] == sha256_digest(payload)
    assert receipt["evaluation_result_digest"] == sha256_digest(evaluation)
    assert report["proposed_bridge_event"]["payload"]["proposal_id"] in receipt["event_id"]
    assert receipt["ts_utc"] == "2026-05-17T12:00:01Z"


def test_receipt_out_dir_refuses_existing_directory_before_emit(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "existing-bundle"
    out_dir.mkdir()

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _proposal(), emit=True, receipt_out_dir=out_dir)

    assert excinfo.value.report["decision"] == "invalid_receipt_bundle"
    assert any("must not exist" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_receipt_out_dir_verifier_failure_blocks_bridge_emit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out_dir = tmp_path / "bad-bundle"

    def fake_verify_manifest(path: Path) -> dict[str, object]:
        return {
            "ok": False,
            "receipt_count": 1,
            "errors": [f"manifest check failed at {path.name}"],
        }

    monkeypatch.setattr(activator, "verify_manifest", fake_verify_manifest)

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _proposal(), emit=True, receipt_out_dir=out_dir)

    assert excinfo.value.report["decision"] == "invalid_receipt_bundle"
    assert any(
        "receipt bundle verification failed" in error
        for error in excinfo.value.report["errors"]
    )
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_receipt_out_dir_with_emit_writes_bundle_and_bridge_event(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "emit-bundle"

    report = _activate(tmp_path, _proposal(), emit=True, receipt_out_dir=out_dir)

    assert report["emitted"] is True
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    assert (out_dir / "manifest.json").exists()
    events_path = tmp_path / "bridge" / "shared" / "events.jsonl"
    emitted = json.loads(events_path.read_text(encoding="utf-8").splitlines()[-1])
    payload = json.loads((out_dir / "payload-001-idle.json").read_text(encoding="utf-8"))
    assert emitted == report["proposed_bridge_event"]
    assert payload == emitted["payload"]


def test_privacy_canary_refuses_before_bridge_event_output(tmp_path: Path) -> None:
    payload = _proposal()
    payload["simulation_evidence"]["summary"] = "This payload contains _DO_NOT_LEAK and must fail."

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, payload, emit=True)

    assert excinfo.value.report["decision"] == "privacy_canary_detected"
    assert "proposed_bridge_event" not in excinfo.value.report
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_non_consensus_payload_cannot_carry_execution_control_fields(
    tmp_path: Path,
) -> None:
    payload = _proposal()
    payload["auto_execute"] = True
    payload["operator_gate_required"] = False

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, payload, emit=True)

    assert excinfo.value.report["decision"] == "invalid_payload"
    assert any("auto_execute" in error for error in excinfo.value.report["errors"])
    assert any("operator_gate_required" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_bridge_event_schema_is_validated_before_append(tmp_path: Path) -> None:
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    bridge_root = tmp_path / "bridge"
    claims_dir.mkdir()
    _write_json(payload_path, _proposal())
    _write_events(events_path, _base_events())

    with pytest.raises(ActivationError) as excinfo:
        activate_idle_protocol(
            payload_path=payload_path,
            events_path=events_path,
            claims_dir=claims_dir,
            bridge_root=bridge_root,
            from_agent="Mallory",
            to_agent="claude",
            task_id=None,
            idle_minutes=60,
            pending_ci_count=0,
            open_request_max_age_hours=12.0,
            now_utc=NOW,
            emit=True,
        )

    assert excinfo.value.report["decision"] == "invalid_bridge_event"
    assert not (bridge_root / "shared" / "events.jsonl").exists()


def test_outbox_append_failure_does_not_write_shared_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_append = activator._append_line_with_retry

    def fail_outbox(path: Path, line: str) -> None:
        if "outbox" in path.parts:
            raise PermissionError("simulated outbox failure")
        original_append(path, line)

    monkeypatch.setattr(activator, "_append_line_with_retry", fail_outbox)

    with pytest.raises(PermissionError):
        _activate(tmp_path, _proposal(), emit=True)

    bridge_root = tmp_path / "bridge"
    assert not (bridge_root / "shared" / "events.jsonl").exists()
    assert not (bridge_root / "shared" / "last_codex.json").exists()


def test_shared_append_failure_rolls_back_outbox_and_last_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_append = activator._append_line_with_retry

    def fail_shared(path: Path, line: str) -> None:
        if path.name == "events.jsonl":
            raise PermissionError("simulated shared failure")
        original_append(path, line)

    monkeypatch.setattr(activator, "_append_line_with_retry", fail_shared)

    with pytest.raises(PermissionError):
        _activate(tmp_path, _proposal(), emit=True)

    bridge_root = tmp_path / "bridge"
    assert not (bridge_root / "shared" / "events.jsonl").exists()
    assert not (bridge_root / "shared" / "last_codex.json").exists()
    assert not (bridge_root / "outbox" / "codex" / "2026-05-17.jsonl").exists()


def test_round_two_continues_after_prior_idle_event_even_when_bridge_is_active(
    tmp_path: Path,
) -> None:
    prior_payload = _proposal()
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:30:00Z",
            status="idle_proposal",
            payload=prior_payload,
        )
    ]

    report = _activate(tmp_path, _counter(), events=events, emit=True)

    assert report["decision"] == "ready"
    assert report["event_type"] == "idle_counter_proposal"
    assert report["emitted"] is True


def test_round_two_continues_when_daily_instance_limit_is_exhausted(
    tmp_path: Path,
) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T09:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        *_idle_instance_events(4),
    ]

    report = _activate(tmp_path, _counter(), events=events, emit=True)

    assert report["decision"] == "ready"
    assert report["event_type"] == "idle_counter_proposal"
    assert report["emitted"] is True


def test_round_two_requires_prior_idle_event(tmp_path: Path) -> None:
    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _counter(), emit=True)

    assert excinfo.value.report["decision"] == "missing_prior_idle_event"
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_duplicate_proposal_id_is_refused_before_emit(tmp_path: Path) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        )
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _proposal(), events=events, emit=True)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    assert any("already present" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_two_reference_must_exist_in_prior_idle_payloads(tmp_path: Path) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal("idle-prop-20260517-other"),
        )
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _counter(), events=events, emit=True)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    assert any("responds_to" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_four_plus_requires_prior_round_three_adversarial_review(
    tmp_path: Path,
) -> None:
    round_four = _counter()
    round_four["proposal_id"] = "idle-prop-20260517-004"
    round_four["round_number"] = 4
    round_four["responds_to"] = "idle-prop-20260517-002"
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T11:05:00Z",
            status="idle_counter_proposal",
            payload=_counter(),
        ),
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, round_four, events=events, emit=True)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    assert any("round-3 idle_adversarial_review" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_round_four_plus_continues_after_prior_adversarial_review(
    tmp_path: Path,
) -> None:
    round_four = _counter()
    round_four["proposal_id"] = "idle-prop-20260517-004"
    round_four["round_number"] = 4
    round_four["responds_to"] = "idle-prop-20260517-003"
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T11:05:00Z",
            status="idle_counter_proposal",
            payload=_counter(),
        ),
        _event(
            ts_utc="2026-05-17T11:10:00Z",
            status="idle_adversarial_review",
            payload=_adversarial(),
        ),
    ]

    report = _activate(tmp_path, round_four, events=events, emit=True)

    assert report["decision"] == "ready"
    assert report["event_type"] == "idle_counter_proposal"
    assert report["emitted"] is True


def test_round_four_requires_adversarial_review_in_same_instance(
    tmp_path: Path,
) -> None:
    proposal_a = _proposal("idle-prop-20260517-a01")
    counter_a = _counter()
    counter_a["proposal_id"] = "idle-prop-20260517-a02"
    counter_a["responds_to"] = "idle-prop-20260517-a01"
    adversarial_a = _adversarial("idle-prop-20260517-a03")
    adversarial_a["responds_to"] = "idle-prop-20260517-a02"
    proposal_b = _proposal("idle-prop-20260517-b01")
    counter_b = _counter()
    counter_b["proposal_id"] = "idle-prop-20260517-b02"
    counter_b["responds_to"] = "idle-prop-20260517-b01"
    round_four_b = _counter()
    round_four_b["proposal_id"] = "idle-prop-20260517-b04"
    round_four_b["round_number"] = 4
    round_four_b["responds_to"] = "idle-prop-20260517-b02"
    events = _base_events() + [
        _event(ts_utc="2026-05-17T10:40:00Z", status="idle_proposal", payload=proposal_a),
        _event(ts_utc="2026-05-17T10:45:00Z", status="idle_counter_proposal", payload=counter_a),
        _event(
            ts_utc="2026-05-17T10:50:00Z",
            status="idle_adversarial_review",
            payload=adversarial_a,
        ),
        _event(ts_utc="2026-05-17T10:55:00Z", status="idle_proposal", payload=proposal_b),
        _event(ts_utc="2026-05-17T10:58:00Z", status="idle_counter_proposal", payload=counter_b),
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, round_four_b, events=events, emit=True)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    assert any("same instance" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_prior_charter_violation_terminates_continuation_before_emit(
    tmp_path: Path,
) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T11:05:00Z",
            status="idle_counter_proposal",
            payload=_counter(),
        ),
        _event(
            ts_utc="2026-05-17T11:10:00Z",
            status="idle_charter_violation",
            payload=_charter_violation(),
        ),
    ]

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, _adversarial(), events=events, emit=True)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    assert any("terminated this instance" in error for error in excinfo.value.report["errors"])
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_prior_charter_violation_does_not_block_new_round_one_instance(
    tmp_path: Path,
) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T10:05:00Z",
            status="idle_counter_proposal",
            payload=_counter(),
        ),
        _event(
            ts_utc="2026-05-17T10:10:00Z",
            status="idle_charter_violation",
            payload=_charter_violation(),
        ),
    ]

    report = _activate(
        tmp_path,
        _proposal("idle-prop-20260517-new"),
        events=events,
        emit=False,
    )

    assert report["decision"] == "ready"
    assert report["event_type"] == "idle_proposal"


def test_consensus_report_is_operator_gated_and_not_auto_execute(tmp_path: Path) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T11:05:00Z",
            status="idle_counter_proposal",
            payload=_counter(),
        ),
        _event(
            ts_utc="2026-05-17T11:10:00Z",
            status="idle_adversarial_review",
            payload=_adversarial(),
        ),
        _event(
            ts_utc="2026-05-17T11:15:00Z",
            status="idle_consensus_reached",
            payload=_consensus("idle-prop-20260517-005a"),
        ),
    ]

    report = _activate(
        tmp_path,
        _consensus("idle-prop-20260517-005b"),
        events=events,
        emit=False,
    )

    assert report["convergence"]["status"] == "soft_convergence"
    assert report["convergence"]["operator_gate_required"] is True
    assert report["convergence"]["auto_execute"] is False


def test_consensus_receipt_bundle_requires_approval_gate(tmp_path: Path) -> None:
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=_proposal(),
        ),
        _event(
            ts_utc="2026-05-17T11:05:00Z",
            status="idle_counter_proposal",
            payload=_counter(),
        ),
        _event(
            ts_utc="2026-05-17T11:10:00Z",
            status="idle_adversarial_review",
            payload=_adversarial(),
        ),
        _event(
            ts_utc="2026-05-17T11:15:00Z",
            status="idle_consensus_reached",
            payload=_consensus("idle-prop-20260517-005a"),
        ),
    ]
    out_dir = tmp_path / "idle-consensus-receipt"

    report = _activate(
        tmp_path,
        _consensus("idle-prop-20260517-005b"),
        events=events,
        receipt_out_dir=out_dir,
    )

    evaluation = json.loads(
        (out_dir / "evaluation-001-idle.json").read_text(encoding="utf-8")
    )
    assert report["convergence"]["status"] == "soft_convergence"
    assert evaluation["actual_gate"] == "require_approval"
    assert evaluation["operator_required"] is True
    assert evaluation["verdict"] == "review"
    assert "convergence:soft_convergence" in evaluation["reason_codes"]
    receipt = json.loads((out_dir / "receipt-001-idle.json").read_text(encoding="utf-8"))
    payload = json.loads((out_dir / "payload-001-idle.json").read_text(encoding="utf-8"))
    assert payload["operator_gate_required"] is True
    assert receipt["operator_gate_required"] is True
    assert receipt["approval_id"] is None


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    _write_json(payload_path, _proposal())
    _write_events(events_path, _base_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_activate.py"),
            "--payload",
            str(payload_path),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--bridge-root",
            str(tmp_path / "bridge"),
            "--now",
            "2026-05-17T12:00:00Z",
            "--dry-run",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["decision"] == "ready"
    assert report["emitted"] is False


def test_cli_receipt_out_dir_writes_verified_bundle(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    out_dir = tmp_path / "cli-receipt-bundle"
    claims_dir.mkdir()
    _write_json(payload_path, _proposal())
    _write_events(events_path, _base_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_activate.py"),
            "--payload",
            str(payload_path),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--bridge-root",
            str(tmp_path / "bridge"),
            "--receipt-out-dir",
            str(out_dir),
            "--now",
            "2026-05-17T12:00:00Z",
            "--dry-run",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
    assert (out_dir / "manifest.json").exists()
    assert not (tmp_path / "bridge" / "shared" / "events.jsonl").exists()


def test_cli_rejects_dry_run_and_apply_together(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    payload_path = tmp_path / "payload.json"
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    bridge_root = tmp_path / "bridge"
    claims_dir.mkdir()
    _write_json(payload_path, _proposal())
    _write_events(events_path, _base_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_protocol_activate.py"),
            "--payload",
            str(payload_path),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--bridge-root",
            str(bridge_root),
            "--now",
            "2026-05-17T12:00:00Z",
            "--dry-run",
            "--apply",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert not (bridge_root / "shared" / "events.jsonl").exists()


# ---------------------------------------------------------------------------
# 2026-05-18 substrate-invariant #2: late agent join (round >= 6) requires
# rounds 1..5 to be bridge-resident AND validator-passing in same instance.
# ---------------------------------------------------------------------------


def _round_six_consensus(target_proposal_id: str = "idle-prop-20260517-002") -> dict:
    event = _proposal("idle-prop-20260517-006")
    event.update(
        {
            "event_type": "idle_consensus_reached",
            "round_number": 6,
            "proposes_substrate_change": False,
            "consensus_target_proposal_id": target_proposal_id,
            "operator_gate_required": True,
            "auto_execute": False,
        }
    )
    del event["proposal"]
    return event


def _full_chain_through_round_five() -> list[dict]:
    """Five payloads forming a complete valid 1->5 idle-protocol chain."""
    proposal = _proposal("idle-prop-20260517-001")
    counter = _counter()
    review = _adversarial()
    round_four = _counter()
    round_four["proposal_id"] = "idle-prop-20260517-004"
    round_four["round_number"] = 4
    round_four["responds_to"] = "idle-prop-20260517-003"
    consensus_a = _consensus("idle-prop-20260517-005a")
    return [proposal, counter, review, round_four, consensus_a]


def test_round_six_refuses_when_round_one_root_missing(tmp_path: Path) -> None:
    # consensus_target_proposal_id points to a proposal that itself was
    # never bridge-resident. The instance walker cannot reach a round-1 root.
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_consensus_reached",
            payload=_consensus("idle-prop-20260517-005a"),
        )
    ]
    late_payload = _round_six_consensus(target_proposal_id="idle-prop-orphan-999")

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, late_payload, events=events)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    error_text = "\n".join(excinfo.value.report["errors"])
    assert "late round 6" in error_text


def test_round_six_refuses_when_a_prior_round_is_validator_invalid(
    tmp_path: Path,
) -> None:
    # Round 2 in the instance is schema-invalid (missing alternative_proposal)
    chain = _full_chain_through_round_five()
    del chain[1]["alternative_proposal"]
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=payload,
        )
        for payload in chain
    ]
    late_payload = _round_six_consensus()

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, late_payload, events=events)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    error_text = "\n".join(excinfo.value.report["errors"])
    assert "late round 6" in error_text
    assert "validator-passing" in error_text
    # Round 2 was the bad one
    assert "2" in error_text


def test_round_six_refuses_when_prior_round_is_missing(tmp_path: Path) -> None:
    chain = [
        payload
        for payload in _full_chain_through_round_five()
        if payload["round_number"] != 4
    ]
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=payload,
        )
        for payload in chain
    ]
    late_payload = _round_six_consensus()

    with pytest.raises(ActivationError) as excinfo:
        _activate(tmp_path, late_payload, events=events)

    assert excinfo.value.report["decision"] == "invalid_sequence"
    error_text = "\n".join(excinfo.value.report["errors"])
    assert "late round 6" in error_text
    assert "rounds 1..5" in error_text
    assert "4" in error_text


def test_round_six_invariant_passes_when_chain_traces_to_round_one(
    tmp_path: Path,
) -> None:
    """A late-round event whose instance has a valid round-1 root and only
    validator-passing payloads passes the substrate-invariant-2 check."""
    chain = _full_chain_through_round_five()
    events = _base_events() + [
        _event(
            ts_utc="2026-05-17T11:00:00Z",
            status="idle_proposal",
            payload=payload,
        )
        for payload in chain
    ]
    late_payload = _round_six_consensus()

    # Activation may still fail on idle gate / quota / rate limit, but it
    # MUST NOT fail with invalid_sequence carrying the late-round message.
    try:
        report = _activate(tmp_path, late_payload, events=events)
        assert report["decision"] in {"ready", "active", "rate_limited"}
    except ActivationError as exc:
        if exc.report.get("decision") == "invalid_sequence":
            error_text = "\n".join(exc.report.get("errors") or [])
            assert "late round" not in error_text, (
                "round 6 should pass invariant when chain traces to round 1, "
                f"errors: {error_text}"
            )
