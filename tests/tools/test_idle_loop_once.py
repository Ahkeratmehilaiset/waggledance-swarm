# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.idle_loop_once import deferred_lift_state, evaluate_idle_loop_tick


NOW = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
OLD_TS = "2026-01-01T00:00:00Z"
RECENT_TS = "2026-05-20T11:55:00Z"


def _assert_deferred_lift_state(state: dict) -> None:
    assert state["source"] == "docs/architecture/IDLE_PROTOCOL_V1.md#deferred"
    authority = state["authority"]
    assert authority["read_only_report"] is True
    for key in (
        "emits_bridge_events",
        "claims_work",
        "creates_tasks",
        "creates_branches",
        "creates_pull_requests",
        "merges",
        "skips_gates",
    ):
        assert authority[key] is False

    items = state["items"]
    assert (
        items["production_two_agent_activation_loop"]["state"]
        == "partial_read_only_ready"
    )
    assert items["automatic_payload_generation"]["state"] == "deferred"
    assert (
        items["auto_conversion_consensus_to_implementation_work"]["state"]
        == "report_only_partial"
    )


def _bridge_event(
    *,
    ts_utc: str = OLD_TS,
    agent: str = "codex",
    type_: str = "heartbeat",
    status: str = "active",
    task_id: str = "baseline",
    to: str = "",
    message: str = "background heartbeat",
    payload: dict | None = None,
) -> dict:
    event = {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type_,
        "task_id": task_id,
        "status": status,
        "severity": "",
        "to": to,
        "message": message,
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }
    if payload is not None:
        event["payload"] = payload
    return event


def _write_events(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return path


def _empty_claims_dir(tmp_path: Path) -> Path:
    claims = tmp_path / "claims"
    claims.mkdir(exist_ok=True)
    return claims


def _base_idle_payload(proposal_id: str, round_number: int, event_type: str) -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": event_type,
        "proposal_id": proposal_id,
        "round_number": round_number,
        "proposes_substrate_change": True,
        "problem_statement": (
            "Strategic idle deliberation needs an operator gated next step."
        ),
        "tradeoff_axis": (
            "Structured peer deliberation versus unmanaged idle discussion drift."
        ),
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": (
                "A fixed transcript demonstrates the next event selection "
                "without writes."
            ),
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": (
                "The session summary never executes actions or converts "
                "consensus to work."
            ),
        },
    }


def _proposal(proposal_id: str = "idle-prop-20260518-001") -> dict:
    payload = _base_idle_payload(proposal_id, 1, "idle_proposal")
    payload["proposal"] = (
        "Use session status to request the next idle protocol step without "
        "creating implementation work."
    )
    return payload


def _counter(
    proposal_id: str = "idle-prop-20260518-002",
    *,
    responds_to: str = "idle-prop-20260518-001",
    round_number: int = 2,
) -> dict:
    payload = _base_idle_payload(proposal_id, round_number, "idle_counter_proposal")
    payload["responds_to"] = responds_to
    payload["alternative_proposal"] = (
        "Ask the peer agent for an explicit alternative proposal while keeping "
        "operator approval separate."
    )
    payload["reasoning_points"] = [
        "If the next request is wrong, the following activation test should fail.",
        "When no bridge write is requested, the tool must return only a report.",
        "If consensus is reached, the result requires operator approval.",
    ]
    return payload


def _adversarial(proposal_id: str = "idle-prop-20260518-003") -> dict:
    payload = _base_idle_payload(proposal_id, 3, "idle_adversarial_review")
    payload["responds_to"] = "idle-prop-20260518-002"
    payload["counterexamples"] = [
        "If the proposal hides automatic execution, the session must escalate instead.",
        "When evidence is missing, quality validation should block the payload.",
    ]
    return payload


def _consensus(proposal_id: str) -> dict:
    payload = _base_idle_payload(proposal_id, 5, "idle_consensus_reached")
    payload["proposes_substrate_change"] = False
    payload["consensus_target_proposal_id"] = "idle-prop-20260518-002"
    payload["operator_gate_required"] = True
    payload["auto_execute"] = False
    return payload


def _charter_violation() -> dict:
    payload = _base_idle_payload(
        "idle-prop-20260518-004", 4, "idle_charter_violation"
    )
    payload["proposes_substrate_change"] = False
    payload["violating_proposal_id"] = "idle-prop-20260518-002"
    payload["violation_reason"] = (
        "The reviewed proposal would bypass the operator approval gate."
    )
    payload["terminate_protocol"] = True
    payload["operator_escalation_required"] = True
    payload["charter_alignment"] = {
        "compatible": False,
        "reasoning": (
            "Bypassing operator approval violates the idle protocol charter."
        ),
    }
    return payload


def _low_quality() -> dict:
    payload = _base_idle_payload(
        "idle-prop-20260518-low", 2, "idle_low_quality_response"
    )
    payload["proposes_substrate_change"] = False
    payload["rejected_event_id"] = "idle-prop-20260518-002"
    payload["quality_errors"] = ["simulation_evidence.summary: must contain evidence"]
    payload["operator_escalation_required"] = True
    return payload


def _wrap(payload: dict, *, agent: str = "codex", ts_utc: str = OLD_TS) -> dict:
    return _bridge_event(
        ts_utc=ts_utc,
        agent=agent,
        type_="message",
        status=payload["event_type"],
        task_id="idle-session-test",
        to="claude" if agent == "codex" else "codex",
        message="Idle protocol test event with substantive content.",
        payload=payload,
    )


def _tick(
    tmp_path: Path,
    events: list[dict],
    *,
    now_utc: datetime = NOW,
    idle_minutes: int = 60,
    agent: str | None = None,
) -> dict:
    return evaluate_idle_loop_tick(
        events_path=_write_events(tmp_path, events),
        claims_dir=_empty_claims_dir(tmp_path),
        now_utc=now_utc,
        idle_minutes=idle_minutes,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
        operator_last_activity_utc=None,
        agent=agent,
    )


# ---------------------------------------------------------------------------
# decision branches
# ---------------------------------------------------------------------------


def test_unknown_when_events_file_missing(tmp_path: Path) -> None:
    report = evaluate_idle_loop_tick(
        events_path=tmp_path / "does_not_exist.jsonl",
        claims_dir=_empty_claims_dir(tmp_path),
        now_utc=NOW,
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )

    assert report["decision"] == "unknown"
    assert report["next_action"] == "operator_handles"
    assert report["exit_code"] == 2
    assert "missing bridge events file" in report["reason"]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_not_idle_when_pending_ci(tmp_path: Path) -> None:
    report = evaluate_idle_loop_tick(
        events_path=_write_events(tmp_path, [_bridge_event()]),
        claims_dir=_empty_claims_dir(tmp_path),
        now_utc=NOW,
        idle_minutes=60,
        pending_ci_count=1,
        open_request_max_age_hours=12.0,
    )

    assert report["decision"] == "not_idle"
    assert report["next_action"] == "wait_for_quiet"
    assert "pending_ci" in report["blockers"]
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_not_idle_when_recent_agent_message(tmp_path: Path) -> None:
    substantive = _bridge_event(
        ts_utc=RECENT_TS,
        agent="claude",
        type_="message",
        status="proposal",
        task_id="design-thread",
        to="codex",
        message=(
            "This is a substantive proposal long enough to count as agent "
            "activity for the recent-message blocker."
        ),
    )
    report = _tick(tmp_path, [substantive])

    assert report["decision"] == "not_idle"
    assert "recent_agent_message" in report["blockers"]


def test_no_session_when_idle_and_no_idle_events(tmp_path: Path) -> None:
    report = _tick(tmp_path, [_bridge_event()])

    assert report["decision"] == "no_session"
    assert report["next_action"] == "emit_round_1"
    assert "run_idle_protocol_once.py" in report["recommended_command"]
    assert report["session_summary"]["status"] == "no_session"
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_mid_protocol_after_one_proposal(tmp_path: Path) -> None:
    report = _tick(tmp_path, [_bridge_event(), _wrap(_proposal())])

    assert report["decision"] == "mid_protocol"
    assert report["next_action"] == "generate_next_round_payload"
    assert report["session_summary"]["latest_round"] == 1


def test_mid_protocol_after_counter_requires_adversarial(tmp_path: Path) -> None:
    report = _tick(
        tmp_path,
        [_bridge_event(), _wrap(_proposal()), _wrap(_counter(), agent="claude")],
    )

    assert report["decision"] == "mid_protocol"
    assert (
        report["session_summary"]["next_required_event"]["event_type"]
        == "idle_adversarial_review"
    )


# A timestamp old enough to leave the bridge idle (>60 min before NOW) but
# fresh enough NOT to trip the 12h stale-convergence recycle.
MID_TS = "2026-05-20T09:00:00Z"


def test_convergence_reached_on_fresh_soft_convergence(tmp_path: Path) -> None:
    events = [
        _bridge_event(),
        _wrap(_proposal(), ts_utc=MID_TS),
        _wrap(_counter(), agent="claude", ts_utc=MID_TS),
        _wrap(_adversarial(), ts_utc=MID_TS),
        _wrap(_consensus("idle-prop-20260518-005a"), ts_utc=MID_TS),
        _wrap(_consensus("idle-prop-20260518-005b"), agent="claude", ts_utc=MID_TS),
    ]
    report = _tick(tmp_path, events)

    assert report["decision"] == "convergence_reached"
    assert report["next_action"] == "route_to_implementer_chain"
    assert report["session_summary"]["status"] == "soft_convergence"
    assert any(
        "deferred in IDLE_PROTOCOL_V1.md" in note for note in report["notes"]
    )


def test_stale_soft_convergence_recycles_to_reseed(tmp_path: Path) -> None:
    """A converged instance whose latest payload is older than the stale
    window must recycle to a fresh round-1 emit instead of re-parking on the
    old consensus forever. Regression for the 2026-05-22 dream-mode stall
    where a 2026-05-18 soft_convergence kept the auto-emit loop parked.
    """
    events = [
        _bridge_event(),
        _wrap(_proposal()),  # OLD_TS (2026-01-01) -> well past the 12h window
        _wrap(_counter(), agent="claude"),
        _wrap(_adversarial()),
        _wrap(_consensus("idle-prop-20260518-005a")),
        _wrap(_consensus("idle-prop-20260518-005b"), agent="claude"),
    ]
    report = _tick(tmp_path, events)

    assert report["decision"] == "stale_convergence_reseed"
    assert report["next_action"] == "emit_round_1"
    assert report["session_summary"]["status"] == "soft_convergence"
    stale = report["session_summary"]["stale_terminal_session"]
    assert stale["stale"] is True
    assert any("must not keep the auto-emit loop parked" in n for n in report["notes"])


def test_operator_review_on_charter_violation(tmp_path: Path) -> None:
    events = [
        _bridge_event(),
        _wrap(_proposal()),
        _wrap(_counter(), agent="claude"),
        _wrap(_charter_violation(), ts_utc=RECENT_TS),
    ]
    report = _tick(tmp_path, events, idle_minutes=1)

    assert report["decision"] == "operator_review_required"
    assert report["next_action"] == "operator_handles"
    assert report["session_summary"]["status"] == "charter_violation"


def test_operator_review_on_low_quality(tmp_path: Path) -> None:
    events = [
        _bridge_event(),
        _wrap(_proposal()),
        _wrap(_low_quality(), agent="claude", ts_utc=RECENT_TS),
    ]
    report = _tick(tmp_path, events, idle_minutes=1)

    assert report["decision"] == "operator_review_required"
    assert report["session_summary"]["status"] == "operator_escalation"


def test_operator_review_on_invalid_payload(tmp_path: Path) -> None:
    bad = _proposal()
    del bad["simulation_evidence"]
    events = [_bridge_event(), _wrap(bad, ts_utc=RECENT_TS)]
    report = _tick(tmp_path, events, idle_minutes=1)

    assert report["decision"] == "operator_review_required"
    assert report["session_summary"]["status"] == "invalid_event"


def test_stale_terminal_invalid_payload_routes_to_agent_next_task(
    tmp_path: Path,
) -> None:
    bad = _proposal()
    del bad["simulation_evidence"]
    events = [_bridge_event(), _wrap(bad, ts_utc="2026-05-19T23:00:00Z")]

    report = _tick(tmp_path, events, agent="codex")

    assert report["decision"] == "stale_terminal_session"
    assert report["next_action"] == "run_agent_next_task"
    assert report["session_summary"]["status"] == "invalid_event"
    stale = report["session_summary"]["stale_terminal_session"]
    assert stale["stale"] is True
    assert stale["latest_idle_payload_utc"] == "2026-05-19T23:00:00Z"
    assert "agent_next_task.py --agent codex" in report["recommended_command"]


# ---------------------------------------------------------------------------
# read-only contract
# ---------------------------------------------------------------------------


def test_tick_does_not_write_bridge_events_or_claims(tmp_path: Path) -> None:
    events_path = _write_events(tmp_path, [_bridge_event()])
    claims_dir = _empty_claims_dir(tmp_path)
    events_before = events_path.read_text(encoding="utf-8")
    claims_before = sorted(p.name for p in claims_dir.iterdir())

    report = evaluate_idle_loop_tick(
        events_path=events_path,
        claims_dir=claims_dir,
        now_utc=NOW,
        idle_minutes=60,
        pending_ci_count=0,
        open_request_max_age_hours=12.0,
    )

    assert events_path.read_text(encoding="utf-8") == events_before
    assert sorted(p.name for p in claims_dir.iterdir()) == claims_before
    _assert_deferred_lift_state(report["deferred_lift_state"])


def test_deferred_lift_state_returns_copy() -> None:
    state = deferred_lift_state()
    _assert_deferred_lift_state(state)

    state["authority"]["claims_work"] = True

    fresh = deferred_lift_state()
    assert fresh["authority"]["claims_work"] is False


def test_tick_is_deterministic_for_identical_inputs(tmp_path: Path) -> None:
    events = [_bridge_event(), _wrap(_proposal())]
    first = _tick(tmp_path, events)
    second = _tick(tmp_path, events)

    assert first["decision"] == second["decision"]
    assert first["next_action"] == second["next_action"]
    assert first["session_summary"] == second["session_summary"]


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_main_emits_json(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    from tools.idle_loop_once import main

    events_path = _write_events(tmp_path, [_bridge_event()])
    claims_dir = _empty_claims_dir(tmp_path)

    exit_code = main(
        [
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--now",
            "2026-05-20T12:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed["decision"] == "no_session"
    assert parsed["next_action"] == "emit_round_1"
