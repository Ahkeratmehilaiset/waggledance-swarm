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
    """Dot-source the classifier and return its request-like / answer verdicts."""
    if not CLASSIFIER.is_file():
        pytest.skip(f"classifier not found at {CLASSIFIER}")
    script = (
        f". '{CLASSIFIER}'\n"
        "$e = $input | ConvertFrom-Json\n"
        "$r = [bool](Test-BridgeRequestLikeEvent -Event $e)\n"
        "$a = [bool](Test-BridgeAnswerEvent -Event $e)\n"
        "[pscustomobject]@{ request_like = $r; answer = $a } "
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
    [("decision", "rco_pass"), ("done", "merged_observed")],
)
def test_closure_events_still_count_as_answers(
    event_type: str, status: str
) -> None:
    verdict = _classify(_event(event_type, status))
    assert verdict["request_like"] is False
    assert verdict["answer"] is True


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


# --- missing / non-string status (Fable 48631d99 confirmed: StrictMode throws on a row without top-level status) ----
# One malformed row must not deny all routing, and a missing or non-string status must never fabricate a request,
# an answer status, an ACK or a requester closure: it reads exactly as an empty status in both shells.
_SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
_LEAD = {"agent": "codex-lead-1", "agent_uuid": "uuid-lead-1", "session_id": "sess-a", "run_id": "run-a"}


def _classify_in(shell: str, events: list[dict[str, object]]) -> list[dict[str, object]]:
    script = (
        f". '{CLASSIFIER}'\n"
        "$rows = @($input | ConvertFrom-Json | ForEach-Object { $_ })\n"
        "@(foreach ($e in $rows) { try { [pscustomobject]@{ request_like = [bool](Test-BridgeRequestLikeEvent -Event $e);"
        " answer = [bool](Test-BridgeAnswerEvent -Event $e); ack = [bool](Test-BridgeAckEvent -Event $e);"
        " closure = [bool](Test-BridgeRequesterClosureEvent -Event $e); error = $null } }"
        " catch { [pscustomobject]@{ request_like = $null; answer = $null; ack = $null; closure = $null;"
        " error = $_.Exception.Message } } }) | ConvertTo-Json -Compress -AsArray\n"
    ) if "pwsh" in shell.lower() else (
        f". '{CLASSIFIER}'\n"
        "$rows = @($input | ConvertFrom-Json | ForEach-Object { $_ })\n"
        "$out = @(foreach ($e in $rows) { try { [pscustomobject]@{ request_like = [bool](Test-BridgeRequestLikeEvent -Event $e);"
        " answer = [bool](Test-BridgeAnswerEvent -Event $e); ack = [bool](Test-BridgeAckEvent -Event $e);"
        " closure = [bool](Test-BridgeRequesterClosureEvent -Event $e); error = $null } }"
        " catch { [pscustomobject]@{ request_like = $null; answer = $null; ack = $null; closure = $null;"
        " error = $_.Exception.Message } } })\n"
        "ConvertTo-Json -InputObject $out -Compress\n"
    )
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], input=json.dumps(events),
                          capture_output=True, text=True, check=False, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


def _row(kind: str, **over: object) -> dict[str, object]:
    row: dict[str, object] = dict(_LEAD, ts_utc="2026-10-01T23:50:00Z", type=kind, task_id="codex-lead-1/t",
                                  to="codex-tools-1", message="m")
    row.update(over)
    return row


_KINDS = ["wake_request", "message", "done", "decision", "ownership_proposal"]
_BAD_STATUS = {"null": None, "list_request": ["request"], "list_answered": ["answered"], "list_cancelled": ["cancelled"],
               "bool": True, "object": {"status": "request"}, "number": 7}


@pytest.mark.parametrize("shell", _SHELLS)
def test_missing_or_non_string_status_classifies_exactly_like_an_empty_status(shell: str) -> None:
    rows, twins = [], []
    for kind in _KINDS:
        for extra in ({}, {"request_id": "req-1"}):
            rows.append({k: v for k, v in _row(kind, **extra).items()})                    # no status key at all
            twins.append(_row(kind, status="", **extra))
            for value in _BAD_STATUS.values():
                rows.append(_row(kind, status=value, **extra))
                twins.append(_row(kind, status="", **extra))
    got, want = _classify_in(shell, rows), _classify_in(shell, twins)
    assert [g["error"] for g in got] == [None] * len(got), got
    assert got == want


@pytest.mark.parametrize("shell", _SHELLS)
def test_known_exact_statuses_keep_their_meaning(shell: str) -> None:
    rows = [_row("wake_request", status="assigned", request_id="r"), _row("wake_request", status="request"),
            _row("message", status="answered"), _row("message", status="Answered"), _row("message", status="received"),
            _row("message", status="cancelled"), _row("done", status="done"), _row("message", status="queued")]
    got = _classify_in(shell, rows)
    assert [(g["request_like"], g["answer"], g["ack"], g["closure"]) for g in got] == [
        (True, False, False, False), (True, False, False, False), (False, True, False, False), (False, True, False, False),
        (False, False, True, False), (False, True, False, True), (False, True, False, True), (False, False, False, False)]


def _select(shell: str, tmp_path: Path, rows: list[dict[str, object]]) -> dict[str, object]:
    (tmp_path / "shared").mkdir(parents=True)
    (tmp_path / "shared/events.jsonl").write_text("".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows),
                                                 encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE_", "WD_", "PSMODULEPATH"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path)
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(NEXT_ACTION),
                           "-Agent", "codex-tools-1", "-Now", "2026-10-01T23:59:00Z", "-Json"],
                          env=env, capture_output=True, text=True, check=False, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _pending(rid: str, ts: str) -> dict[str, object]:
    return _row("wake_request", status="assigned", request_id=rid, request_digest="a" * 64, ts_utc=ts,
                payload={"task_revision": "r1"}, task_id="codex-lead-1/" + rid)


@pytest.mark.parametrize("shell", _SHELLS)
def test_status_less_junk_before_and_between_pending_requests_does_not_deny_routing(shell: str, tmp_path: Path) -> None:
    junk = {k: v for k, v in _row("message", task_id="codex-lead-1/r1").items()}
    rows = [junk, _pending("r1", "2026-10-01T23:50:00Z"), dict(junk, ts_utc="2026-10-01T23:51:00Z"),
            _pending("r2", "2026-10-01T23:52:00Z")]
    out = _select(shell, tmp_path, rows)
    assert out["action"] == "answer_incoming" and out["open_incoming_count"] == 2, out


@pytest.mark.parametrize("shell", _SHELLS)
def test_a_status_less_row_never_closes_or_cancels_a_pending_request(shell: str, tmp_path: Path) -> None:
    later = _row("message", task_id="codex-lead-1/r1", ts_utc="2026-10-01T23:55:00Z", to="codex-lead-1",
                 agent="codex-tools-1", in_reply_to_request_id="r1")
    out = _select(shell, tmp_path, [_pending("r1", "2026-10-01T23:50:00Z"), later])
    assert out["action"] == "answer_incoming" and out["open_incoming_count"] == 1, out
    assert out.get("cancelled_withheld_count", 0) == 0, out

# --- Test-BridgeWakeEligible: only an EXACT string ACK status suppresses a wake (Fable 8716) --------------------------
def _wake_eligible_in(shell: str, events: list[dict[str, object]]) -> list[object]:
    script = (
        f". '{CLASSIFIER}'\n"
        "$rows = @($input | ConvertFrom-Json | ForEach-Object { $_ })\n"
        "$out = @(foreach ($e in $rows) { try { [bool](Test-BridgeWakeEligible -Event $e) } catch { 'ERROR: ' + $_.Exception.Message } })\n"
        "ConvertTo-Json -InputObject $out -Compress\n"
    )
    done = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", script], input=json.dumps(events),
                          capture_output=True, text=True, check=False, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip())


@pytest.mark.parametrize("shell", _SHELLS)
def test_wake_eligibility_suppresses_only_exact_string_acks(shell: str) -> None:
    base = _row("message", to="codex-tools-1")
    rows, want = [], []
    for value in (["received"], ["seen"], ["acknowledged"], True, {"status": "received"}, 7, None, "Received", "custom_x",
                  "hold", "cancelled", "answered"):
        rows.append(dict(base, status=value))
        want.append(True)                                        # not an exact lower ACK string: stays eligible
    rows.append({k: v for k, v in base.items()})                 # no status key at all
    want.append(True)
    for value in ("received", "seen", "acknowledged"):
        rows.append(dict(base, status=value))
        want.append(False)                                       # exact ACKs stay suppressed
    rows.append(dict(base, type="liveness", status="active"))
    want.append(False)
    note = dict(base, status="informational", payload={"notification": "informational"})
    rows.append(note)
    want.append(False)                                           # the closed benign envelope stays quiet
    rows.append(dict(note, status=["informational"]))
    want.append(True)                                            # a list status is not that envelope
    assert _wake_eligible_in(shell, rows) == want
