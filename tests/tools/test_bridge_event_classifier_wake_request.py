# SPDX-License-Identifier: BUSL-1.1
"""wake_request routing parity for the PowerShell bridge classifier.

The agents poll the bridge through `.agent-bridge/bin/Get-BridgeNextAction.ps1`,
which classifies events with `BridgeEventClassifier.ps1`. Before this fix the PS
classifier bucketed `wake_request` as infrastructure (alongside heartbeat /
liveness), so every directed nudge — operator "read the bridge" and peer
"review this PR" — was dropped instead of routing to `answer_incoming`. The
Python `bridge_next_action.py` already lists `wake_request` in `REQUEST_TYPES`
(#1101); these tests lock the PowerShell consumer to the same contract:

* `wake_request` with an open/request status is request-like (actionable),
* `wake_request` is never an answer/closure event (a nudge must not mark another
  agent's open request as answered),
* heartbeat / liveness remain pure infrastructure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLASSIFIER = ROOT / ".agent-bridge" / "bin" / "BridgeEventClassifier.ps1"
NEXT_ACTION = ROOT / ".agent-bridge" / "bin" / "Get-BridgeNextAction.ps1"
STATUS = ROOT / ".agent-bridge" / "bin" / "Get-AgentBridgeStatus.ps1"
READ = ROOT / ".agent-bridge" / "bin" / "Read-AgentBridge.ps1"


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for the bridge classifier tests")
    return executable


def _classify(event: dict[str, str]) -> dict[str, bool]:
    """Return PowerShell request, answer, and requester-closure verdicts."""
    if not CLASSIFIER.is_file():
        pytest.skip(f"classifier not found at {CLASSIFIER}")
    script = (
        f". '{CLASSIFIER}'\n"
        "$e = $input | ConvertFrom-Json\n"
        "$r = [bool](Test-BridgeRequestLikeEvent -Event $e)\n"
        "$a = [bool](Test-BridgeAnswerEvent -Event $e)\n"
        "$c = [bool](Test-BridgeRequesterClosureEvent -Event $e)\n"
        "[pscustomobject]@{ request_like = $r; answer = $a; requester_closure = $c } "
        "| ConvertTo-Json -Compress\n"
    )
    completed = subprocess.run(  # noqa: S603
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.strip())


def _is_ack(event: dict[str, str]) -> bool:
    """Return the PowerShell ACK classifier verdict."""
    if not CLASSIFIER.is_file():
        pytest.skip(f"classifier not found at {CLASSIFIER}")
    script = (
        f". '{CLASSIFIER}'\n"
        "$e = $input | ConvertFrom-Json\n"
        "[bool](Test-BridgeAckEvent -Event $e) | ConvertTo-Json -Compress\n"
    )
    completed = subprocess.run(  # noqa: S603
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", script],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return bool(json.loads(completed.stdout.strip()))


def _event(event_type: str, status: str) -> dict[str, str]:
    return {
        "type": event_type,
        "status": status,
        "task_id": "wake-parity-task",
        "to": "fable-5",
        "agent": "codex-lead-1",
        "ts_utc": "2026-06-13T00:00:00Z",
        "message": "",
    }


def _next_action(
    tmp_path: Path,
    events: list[dict[str, str]],
    *,
    agent: str = "fable-5",
    now: str = "2026-06-13T03:00:00Z",
    suppressed_agents: dict[str, str] | None = None,
) -> dict[str, object]:
    if not NEXT_ACTION.is_file():
        pytest.skip(f"next-action script not found at {NEXT_ACTION}")
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    if suppressed_agents:
        suppression_path = bridge_root / "shared" / "production_liveness_suppression.json"
        suppression_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "suppressed_agents": {
                        agent_id: {"reason": reason}
                        for agent_id, reason in suppressed_agents.items()
                    },
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    completed = subprocess.run(  # noqa: S603
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(NEXT_ACTION),
            "-Agent",
            agent,
            "-Now",
            now,
            "-Json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _write_runtime_events(
    tmp_path: Path,
    events: list[dict[str, str]],
) -> Path:
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )
    return bridge_root


def _status(tmp_path: Path, events: list[dict[str, str]]) -> dict[str, object]:
    if not STATUS.is_file():
        pytest.skip(f"status script not found at {STATUS}")
    bridge_root = _write_runtime_events(tmp_path, events)
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    completed = subprocess.run(  # noqa: S603
        [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(STATUS),
            "-Tail",
            "100",
            "-Json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _read(
    tmp_path: Path,
    events: list[dict[str, str]],
    *,
    agent: str,
    show_liveness: bool = False,
    no_ack_received: bool = True,
) -> str:
    if not READ.is_file():
        pytest.skip(f"reader script not found at {READ}")
    bridge_root = _write_runtime_events(tmp_path, events)
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    command = [
            _powershell(),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(READ),
            "-Agent",
            agent,
            "-Tail",
            "100",
            "-ContinuityTail",
            "100",
        ]
    if no_ack_received:
        command.append("-NoAckReceived")
    if show_liveness:
        command.append("-ShowLiveness")
    completed = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.skipif(os.name != "nt", reason="bridge writer is Windows-only")
@pytest.mark.parametrize(
    "status",
    [
        "ack",
        "acknowledged",
        "received",
        "seen",
        "wake_ack",
        "received_with_context",
    ],
)
def test_read_does_not_emit_duplicate_received_after_existing_ack(
    tmp_path: Path,
    status: str,
) -> None:
    request = _event("message", "request")
    request.update(
        {
            "agent": "codex",
            "to": "smoke-1",
            "task_id": "read-ack-dedup",
            "ts_utc": "2026-06-13T00:00:00Z",
        }
    )
    ack = _event("message", status)
    ack.update(
        {
            "agent": "smoke-1",
            "to": "codex",
            "task_id": "read-ack-dedup",
            "ts_utc": "2026-06-13T00:01:00Z",
            "payload": {"request_ts_utc": request["ts_utc"]},
        }
    )

    _read(
        tmp_path,
        [request, ack],
        agent="smoke-1",
        no_ack_received=False,
    )

    events_path = tmp_path / ".agent-bridge" / "shared" / "events.jsonl"
    assert len(events_path.read_text(encoding="utf-8").splitlines()) == 2


@pytest.mark.parametrize(
    "status",
    ["open", "review_requested", "rco_requested", "changes_requested"],
)
def test_wake_request_is_actionable_incoming(status: str) -> None:
    verdict = _classify(_event("wake_request", status))
    assert verdict["request_like"] is True
    assert verdict["answer"] is False


def test_wake_request_ack_is_not_actionable() -> None:
    # An ack-status wake_request is a read receipt, not a fresh request.
    verdict = _classify(_event("wake_request", "received"))
    assert verdict["request_like"] is False
    assert verdict["answer"] is False


@pytest.mark.parametrize("event_type", ["heartbeat", "liveness"])
def test_pure_infrastructure_events_are_neither(event_type: str) -> None:
    verdict = _classify(_event(event_type, "active"))
    assert verdict["request_like"] is False
    assert verdict["answer"] is False


def test_directed_message_request_still_routes() -> None:
    verdict = _classify(_event("message", "request"))
    assert verdict["request_like"] is True
    assert verdict["answer"] is False


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "requested"),
        ("message", "required"),
        ("message", "needed"),
        ("message", "fix_required"),
        ("message", "evidence_missing"),
        ("message", "still_open"),
        ("message", "active"),
        ("message", "fix_pushed"),
        ("peer_review_request", "review_requested"),
        ("review_verdict", "review_requested"),
        ("wake_request", "requested"),
    ],
)
def test_request_classifier_uses_shared_open_status_tokens(
    event_type: str,
    status: str,
) -> None:
    verdict = _classify(_event(event_type, status))

    assert verdict["request_like"] is True
    if event_type in {"review_verdict"}:
        assert verdict["answer"] is True
    else:
        assert verdict["answer"] is False


@pytest.mark.parametrize("status", ["nonproposal", "proposalized"])
def test_request_classifier_does_not_match_proposal_substrings(status: str) -> None:
    verdict = _classify(_event("review_verdict", status))

    assert verdict["request_like"] is False
    assert verdict["answer"] is True


def test_taskless_directed_request_preserves_legacy_routing() -> None:
    event = _event("message", "request")
    event["task_id"] = ""

    verdict = _classify(event)

    assert verdict["request_like"] is True
    assert verdict["answer"] is False


def test_next_action_routes_taskless_directed_request(tmp_path: Path) -> None:
    event = _event("message", "request")
    event["task_id"] = ""

    report = _next_action(
        tmp_path,
        [event],
        now="2026-06-13T00:02:00Z",
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_custom_open_event_is_both_request_and_substantive_answer() -> None:
    verdict = _classify(_event("ownership_proposal", "open"))

    assert verdict == {
        "request_like": True,
        "answer": True,
        "requester_closure": False,
    }


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("decision", "rco_pass"),
        ("done", "merged_observed"),
        ("done", "request_closed"),
        ("done", "done_request"),
    ],
)
def test_closure_events_still_count_as_answers(
    event_type: str, status: str
) -> None:
    verdict = _classify(_event(event_type, status))
    assert verdict["request_like"] is False
    assert verdict["answer"] is True


def test_taskless_custom_request_is_not_routed_without_task_binding() -> None:
    event = _event("review_verdict", "review_requested")
    event["task_id"] = ""

    verdict = _classify(event)

    assert verdict["request_like"] is False
    assert verdict["answer"] is False


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("status", "superseded"),
        ("status", "completed_after_review"),
        ("status", "changes_requested_retracted"),
        ("status", "changes_requested_resolved"),
        ("status", "changes_requested_withdrawn"),
        ("done", "closed_current_main_reconciled"),
        ("decision", "rco_finding_withdrawn"),
    ],
)
def test_requester_terminal_closure_taxonomy(
    event_type: str,
    status: str,
) -> None:
    verdict = _classify(_event(event_type, status))

    assert verdict["request_like"] is False
    assert verdict["requester_closure"] is True


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("status", "received"),
        ("status", "seen"),
        ("status", "acknowledged"),
        ("status", "working"),
        ("decision", "done_not"),
        ("decision", "changes_requested_resolved_pending"),
    ],
)
def test_requester_nonterminal_taxonomy(
    event_type: str,
    status: str,
) -> None:
    verdict = _classify(_event(event_type, status))

    assert verdict["requester_closure"] is False


@pytest.mark.parametrize(
    "event_type",
    ["message", "done", "decision", "finding", "handoff", "blocked", "wake_request"],
)
def test_request_shaped_event_is_not_answer_or_closure(
    event_type: str,
) -> None:
    verdict = _classify(_event(event_type, "request"))

    assert verdict == {
        "request_like": True,
        "answer": False,
        "requester_closure": False,
    }


def test_next_action_target_follow_up_request_does_not_close_original(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    follow_up = _event("decision", "request")
    follow_up.update(
        {
            "agent": "fable-5",
            "to": "codex-lead-1",
            "ts_utc": "2026-06-13T00:01:00Z",
        }
    )

    report = _next_action(
        tmp_path,
        [request, follow_up],
        now="2026-06-13T00:02:00Z",
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_target_status_is_never_a_generic_answer() -> None:
    verdict = _classify(_event("status", "superseded"))

    assert verdict["answer"] is False


def test_compound_wake_ack_is_neither_request_answer_nor_closure() -> None:
    verdict = _classify(
        _event("message", "wake_ack_corrected_review_already_posted")
    )

    assert verdict == {
        "request_like": False,
        "answer": False,
        "requester_closure": False,
    }


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("message", "ack"),
        ("done", "ack"),
        ("message", "received_with_context"),
        ("done", "done_received"),
    ],
)
def test_ack_status_tokens_are_nonterminal(
    event_type: str,
    status: str,
) -> None:
    event = _event(event_type, status)

    assert _is_ack(event) is True
    assert _classify(event) == {
        "request_like": False,
        "answer": False,
        "requester_closure": False,
    }


@pytest.mark.parametrize("status", ["unacknowledged", "foreseen", "prereceived"])
def test_ack_classifier_does_not_match_status_substrings(status: str) -> None:
    assert _is_ack(_event("message", status)) is False


def test_next_action_requester_status_superseded_closes_request(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:01:00Z"

    report = _next_action(tmp_path, [request, closure], now="2026-06-13T00:02:00Z")

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_next_action_later_request_reopens_after_requester_status(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    request["ts_utc"] = "2026-06-13T00:00:00.0000000Z"
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:00:01Z"
    reopened = _event("message", "review_requested")
    reopened["ts_utc"] = "2026-06-13T00:00:01.1000000Z"
    reopened["message"] = "new exact-head request"

    report = _next_action(
        tmp_path,
        [request, closure, reopened],
        now="2026-06-13T00:01:00Z",
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


@pytest.mark.parametrize(
    ("request_identity", "closure_identity", "is_closed"),
    [
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            True,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-stale-session",
            },
            False,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            {
                "agent_uuid": "99999999-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            False,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            {},
            False,
        ),
        (
            {},
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            True,
        ),
        (
            {"agent_uuid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"},
            {
                "agent_uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                "session_id": "closure-extra-session",
            },
            True,
        ),
        (
            {"session_id": "requester-current-session"},
            {
                "agent_uuid": "99999999-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            True,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            {"agent_uuid": "11111111-2222-3333-4444-555555555555"},
            False,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
            },
            {"session_id": "requester-current-session"},
            False,
        ),
        (
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
                "run_id": "request-run",
            },
            {
                "agent_uuid": "11111111-2222-3333-4444-555555555555",
                "session_id": "requester-current-session",
                "run_id": "closure-run",
            },
            True,
        ),
    ],
)
def test_next_action_requester_closure_binds_to_request_identity(
    tmp_path: Path,
    request_identity: dict[str, str],
    closure_identity: dict[str, str],
    is_closed: bool,
) -> None:
    request = _event("message", "request")
    request.update(request_identity)
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:01:00Z"
    closure.update(closure_identity)

    report = _next_action(
        tmp_path,
        [request, closure],
        now="2026-06-13T00:02:00Z",
    )

    assert report["open_incoming_count"] == (0 if is_closed else 1)
    assert report["action"] == (
        "claim_unblocked_work" if is_closed else "answer_incoming"
    )


@pytest.mark.parametrize("same_identity", [False, True])
def test_status_and_reader_bind_requester_closure_to_request_identity(
    tmp_path: Path,
    same_identity: bool,
) -> None:
    request = _event("message", "request")
    request.update(
        {
            "agent_uuid": "11111111-2222-3333-4444-555555555555",
            "session_id": "requester-current-session",
        }
    )
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:01:00Z"
    closure.update(
        {
            "agent_uuid": "11111111-2222-3333-4444-555555555555",
            "session_id": (
                "requester-current-session"
                if same_identity
                else "requester-stale-session"
            ),
        }
    )
    events = [request, closure]

    status = _status(tmp_path / "status", events)
    incoming = _read(tmp_path / "incoming", events, agent="fable-5")
    outgoing = _read(tmp_path / "outgoing", events, agent="codex-lead-1")

    unresolved = status["unresolved_requests"]
    assert isinstance(unresolved, list)
    assert len(unresolved) == (0 if same_identity else 1)
    if same_identity:
        assert "closed-by-requester wake-parity-task" in incoming
        assert "closed-by-codex-lead-1 wake-parity-task" in outgoing
    else:
        assert "OPEN wake-parity-task" in incoming
        assert "WAITING-FOR-fable-5 wake-parity-task" in outgoing


def test_status_and_reader_keep_mixed_precision_reopened_request_open(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    request["ts_utc"] = "2026-06-13T00:00:00.0000000Z"
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:00:01Z"
    reopened = _event("message", "review_requested")
    reopened["ts_utc"] = "2026-06-13T00:00:01.1000000Z"
    reopened["message"] = "new exact-head request in the same second"
    events = [request, closure, reopened]

    status = _status(tmp_path / "status", events)
    incoming = _read(tmp_path / "incoming", events, agent="fable-5")
    outgoing = _read(tmp_path / "outgoing", events, agent="codex-lead-1")

    unresolved = status["unresolved_requests"]
    assert isinstance(unresolved, list)
    assert len(unresolved) == 1
    assert unresolved[0]["message"] == "new exact-head request in the same second"
    assert "OPEN wake-parity-task" in incoming
    assert "WAITING-FOR-fable-5 wake-parity-task" in outgoing


def test_powershell_consumers_order_adjacent_100ns_ticks(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    request["ts_utc"] = "2026-06-13T00:00:00.0000001Z"
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:00:00.0000002Z"
    events = [request, closure]

    next_action = _next_action(
        tmp_path / "next",
        events,
        now="2026-06-13T00:01:00Z",
    )
    status = _status(tmp_path / "status", events)
    incoming = _read(tmp_path / "incoming", events, agent="fable-5")
    outgoing = _read(tmp_path / "outgoing", events, agent="codex-lead-1")

    assert next_action["open_incoming_count"] == 0
    assert status["unresolved_requests"] == []
    assert "closed-by-requester wake-parity-task" in incoming
    assert "closed-by-codex-lead-1 wake-parity-task" in outgoing


@pytest.mark.parametrize(
    ("closure_first", "expected_open"),
    [(False, 0), (True, 1)],
)
def test_powershell_consumers_use_append_order_for_equal_timestamps(
    tmp_path: Path,
    closure_first: bool,
    expected_open: int,
) -> None:
    request = _event("message", "request")
    request["ts_utc"] = "2026-06-13T00:00:00.0000001Z"
    closure = _event("status", "superseded")
    closure["ts_utc"] = "2026-06-13T00:00:00.0000001Z"
    events = [closure, request] if closure_first else [request, closure]

    next_action = _next_action(
        tmp_path / "next",
        events,
        now="2026-06-13T00:01:00Z",
    )
    status = _status(tmp_path / "status", events)
    incoming = _read(tmp_path / "incoming", events, agent="fable-5")
    outgoing = _read(tmp_path / "outgoing", events, agent="codex-lead-1")

    assert next_action["open_incoming_count"] == expected_open
    assert len(status["unresolved_requests"]) == expected_open
    if expected_open:
        assert "OPEN wake-parity-task" in incoming
        assert "WAITING-FOR-fable-5 wake-parity-task" in outgoing
    else:
        assert "closed-by-requester wake-parity-task" in incoming
        assert "closed-by-codex-lead-1 wake-parity-task" in outgoing


def test_powershell_third_party_merge_receipt_does_not_close_request(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    receipt = _event("decision", "autonomous_merge_receipt")
    receipt.update(
        {
            "ts_utc": "2026-06-13T00:01:00Z",
            "agent": "claude-rco-1",
            "to": "codex-lead-1,fable-5",
        }
    )

    report = _next_action(
        tmp_path,
        [request, receipt],
        now="2026-06-13T00:02:00Z",
    )

    assert report["open_incoming_count"] == 1
    assert report["action"] == "answer_incoming"


def test_reader_liveness_orders_mixed_precision_activity(
    tmp_path: Path,
) -> None:
    wake = _event("wake_request", "open")
    wake["ts_utc"] = "2026-06-13T00:00:00Z"
    wake["message"] = "mixed precision wake should be closed by activity"
    wake["severity"] = ""
    activity = _event("heartbeat", "active")
    activity.update(
        {
            "ts_utc": "2026-06-13T00:00:00.1000000Z",
            "agent": "fable-5",
            "to": "codex-lead-1",
            "message": "target activity after wake",
        }
    )

    output = _read(
        tmp_path,
        [wake, activity],
        agent="codex-lead-1",
        show_liveness=True,
    )

    assert "OPEN WAKE_REQUESTS" in output
    liveness_section = output.split("OPEN WAKE_REQUESTS", 1)[1].split(
        "RECENT EVENTS", 1
    )[0]
    assert "mixed precision wake should be closed by activity" not in liveness_section


def test_powershell_consumers_reject_invalid_closure_timestamp(
    tmp_path: Path,
) -> None:
    request = _event("message", "request")
    closure = _event("status", "superseded")
    closure["ts_utc"] = "not-a-timestamp"
    events = [request, closure]

    next_action = _next_action(
        tmp_path / "next",
        events,
        now="2026-06-13T00:01:00Z",
    )
    status = _status(tmp_path / "status", events)
    incoming = _read(tmp_path / "incoming", events, agent="fable-5")
    outgoing = _read(tmp_path / "outgoing", events, agent="codex-lead-1")

    assert next_action["open_incoming_count"] == 1
    assert len(status["unresolved_requests"]) == 1
    assert "OPEN wake-parity-task" in incoming
    assert "WAITING-FOR-fable-5 wake-parity-task" in outgoing


def test_next_action_ages_out_old_operator_wake_request(tmp_path: Path) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "operator-wake-request-20260612"
    event["ts_utc"] = "2026-06-12T12:00:00Z"

    report = _next_action(tmp_path, [event])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 1


def test_next_action_keeps_recent_wake_request_actionable(tmp_path: Path) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "operator-wake-request-20260613"
    event["ts_utc"] = "2026-06-13T02:30:00Z"

    report = _next_action(tmp_path, [event])

    assert report["action"] == "answer_incoming"
    assert report["task_id"] == "operator-wake-request-20260613"
    assert report["open_incoming_count"] == 1
    assert report["stale_incoming_count"] == 0


def test_next_action_ignores_recent_bridge_follow_nudge(tmp_path: Path) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "bridge-follow-nudge-20260613"
    event["ts_utc"] = "2026-06-13T02:30:00Z"

    report = _next_action(tmp_path, [event])

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
    assert report["stale_incoming_count"] == 0


def test_next_action_reports_suppressed_agent_instead_of_follow_nudge(
    tmp_path: Path,
) -> None:
    event = _event("wake_request", "open")
    event["agent"] = "operator"
    event["task_id"] = "bridge-follow-nudge-20260613"
    event["ts_utc"] = "2026-06-13T02:30:00Z"

    report = _next_action(
        tmp_path,
        [event],
        suppressed_agents={"fable-5": "operator reported lane unavailable"},
    )

    assert report["action"] == "agent_suppressed_unavailable"
    assert report["task_id"] == "agent-suppressed-unavailable"
    assert report["open_incoming_count"] == 0
    assert report["suppression_reason"] == "operator reported lane unavailable"
