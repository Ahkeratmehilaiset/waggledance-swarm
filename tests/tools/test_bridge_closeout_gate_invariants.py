"""Requester-closeout invariants of the merge gates, on synthetic events only (Lead B8, sprint24).

PR1924 writes the requester closeout (Write-AgentEvent -CloseRequestEventJson) as a message instead of a decision.
Its writer tests run only on Windows, so Linux CI skips them. These tests check the gate side on every platform:
they feed synthetic event dicts to the real gates, with no writer, PowerShell, live bridge or data. The RCO3
enrollment context is pinned to an empty-root unknown one, so no environment variable or bundle manifest is read.

1. A message closeout of an RCO's own request after that RCO's exact-head rco_pass changes neither gate.
2. The same closeout written as a decision is that RCO's block in the peer gate and a veto after its pass in the
   slot gate: the hazard the message type avoids. These assertions describe the current gates; they do not ask
   for a gate change.
3. A message closeout never lifts a real RCO veto: the veto stays the latest block and stays listed.
"""
from __future__ import annotations

from typing import Any

import pytest

from tools import check_bridge_changes_requested as peer_gate
from tools import check_rco_pass_present as slot_gate
from tools.bridge_rco_enrollment import load_rco_enrollment_context
from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
from waggledance.core.bridge_request_contract import reply_matches_request

TASK = "fable-5/synthetic-closeout-gate-invariants"
HEAD = "0123456789abcdef0123456789abcdef01234567"
LEAD = "codex-lead-1"
AUTHOR = "codex-tools-1"
RCOS = ("claude-rco-1", "claude-rco-2")
CLOSEOUT_STATUSES = ("superseded", "cancelled")
REGISTRY = load_bridge_identity_registry()


@pytest.fixture(autouse=True)
def _unknown_enrollment(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty root and pin give the unknown context without reading the environment or any file."""
    context = load_rco_enrollment_context(bundle_root="", expected_manifest_sha256="")
    assert context["status"] == "unknown", context
    for module in (peer_gate, slot_gate):
        monkeypatch.setattr(module, "load_rco_enrollment_context", lambda: context)


def _identity(agent: str) -> dict[str, str]:
    return {"agent": agent, "agent_uuid": REGISTRY[agent], "session_id": agent + "-session", "run_id": agent + "-run"}


def _ts(second: int) -> str:
    return f"2026-10-24T12:00:{second:02d}Z"


def _rco_pass(rco: str) -> dict[str, Any]:
    return {**_identity(rco), "ts_utc": _ts(1), "type": "decision", "status": "rco_pass", "task_id": TASK,
            "to": LEAD, "message": "RCO_PASS at exact head " + HEAD, "payload": {"head": HEAD, "exact_head": HEAD}}


def _request(rco: str, event_type: str = "message", status: str = "requested") -> dict[str, Any]:
    """An RCO's bound request to Lead on the task; with a veto type and status it is itself a real veto."""
    return {**_identity(rco), "ts_utc": _ts(2), "type": event_type, "status": status, "task_id": TASK, "to": LEAD,
            "message": "please re-review the exact head", "request_id": "synthetic-" + rco + "-request-v1",
            "request_digest": "d" * 64, "payload": {"nonce": "n-" + rco}}


def _closeout(request: dict[str, Any], event_type: str, status: str) -> dict[str, Any]:
    """The requester's closeout with the correlation the writer route copies (request id, digest, ts, nonce)."""
    requester = {key: request[key] for key in ("agent", "agent_uuid", "session_id", "run_id")}
    closeout = {**requester, "ts_utc": _ts(3), "type": event_type, "status": status, "task_id": request["task_id"],
                "to": request["to"], "message": "obsolete: replaced by a newer request",
                "payload": {"in_reply_to_request_id": request["request_id"],
                            "in_reply_to_request_digest": request["request_digest"],
                            "in_reply_to_requester": requester, "request_ts_utc": request["ts_utc"],
                            "nonce": request["payload"]["nonce"]}}
    # The fixture is a real requester closure of that request, not an uncorrelated message.
    assert reply_matches_request(request, closeout, LEAD, requester_closure=True)
    return closeout


def _peer(events: list[dict[str, Any]]) -> dict[str, Any]:
    return peer_gate.check_bridge_clear_to_merge(events=events, task_id=TASK, merging_agent=LEAD,
                                                 author_agent=AUTHOR, identity_registry=REGISTRY)


def _slot(events: list[dict[str, Any]]) -> dict[str, Any]:
    return slot_gate.check_rco_pass_present(events=events, task_id=TASK, head=HEAD, author_agent=AUTHOR,
                                            identity_registry=REGISTRY)


def _scan(events: list[dict[str, Any]]) -> dict[str, Any]:
    return slot_gate.scan_recognized_rco_vetoes(events=events, task_id=TASK, author_agent=AUTHOR,
                                                identity_registry=REGISTRY)


def _shapes(scan: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(event["agent"], event["type"], event["status"]) for event in scan["veto_events"]]


def _assert_pass_baseline(rco: str, events: list[dict[str, Any]]) -> tuple[dict, dict]:
    """An identity-verified exact-head pass that both gates accept, and no veto."""
    peer, slot, scan = _peer(events), _slot(events), _scan(events)
    assert peer["clear_to_merge"] and peer["decision"] == "clear", peer
    assert peer["latest_approval_event"]["status"] == "rco_pass", peer
    assert peer["ignored_identity_mismatch_events"] == [], peer
    assert slot["ok"] and slot["decision"] == "rco_pass_present", slot
    assert slot["satisfying_rco_agent"] == rco and slot["ignored_identity_mismatch_events"] == [], slot
    assert scan["ok"] and scan["veto_events"] == [], scan
    return peer, slot


@pytest.mark.parametrize("status", CLOSEOUT_STATUSES)
@pytest.mark.parametrize("rco", RCOS)
def test_a_message_closeout_after_an_exact_head_rco_pass_changes_neither_gate(rco: str, status: str) -> None:
    request = _request(rco)
    before = [_rco_pass(rco), request]
    peer, slot = _assert_pass_baseline(rco, before)
    after = before + [_closeout(request, "message", status)]
    peer_after, slot_after, scan_after = _peer(after), _slot(after), _scan(after)
    assert peer_after["clear_to_merge"] and peer_after["decision"] == "clear", peer_after
    assert peer_after["latest_blocking_event"] is None and peer_after["unverified_rco_block_events"] == [], peer_after
    assert peer_after["latest_approval_event"] == peer["latest_approval_event"], peer_after
    assert slot_after["ok"] and slot_after["decision"] == "rco_pass_present", slot_after
    assert slot_after["rco_pass_event"] == slot["rco_pass_event"], slot_after
    assert slot_after["blocking_rco_agents"] == [], slot_after
    assert scan_after["ok"] and scan_after["veto_events"] == [], scan_after


@pytest.mark.parametrize("status", CLOSEOUT_STATUSES)
@pytest.mark.parametrize("rco", RCOS)
def test_the_same_closeout_as_a_decision_is_that_rcos_block_and_veto(rco: str, status: str) -> None:
    request = _request(rco)
    before = [_rco_pass(rco), request]
    _assert_pass_baseline(rco, before)
    events = before + [_closeout(request, "decision", status)]
    peer, slot, scan = _peer(events), _slot(events), _scan(events)
    assert not peer["clear_to_merge"] and peer["decision"] == "blocked", peer
    blocking = peer["latest_blocking_event"]
    assert (blocking["agent"], blocking["type"], blocking["status"]) == (rco, "decision", status), peer
    assert not slot["ok"] and slot["decision"] == "vetoed_after_pass", slot
    assert slot["blocking_rco_agents"] == [rco], slot
    assert not scan["ok"] and _shapes(scan) == [(rco, "decision", status)], scan


@pytest.mark.parametrize("status", CLOSEOUT_STATUSES)
@pytest.mark.parametrize("veto_type", ["finding", "decision"])
@pytest.mark.parametrize("rco", RCOS)
def test_a_message_closeout_never_lifts_a_real_rco_veto(rco: str, veto_type: str, status: str) -> None:
    rco_pass = _rco_pass(rco)
    _assert_pass_baseline(rco, [rco_pass])
    veto = _request(rco, veto_type, "changes_requested")
    before = [rco_pass, veto]
    peer, slot, scan = _peer(before), _slot(before), _scan(before)
    assert not peer["clear_to_merge"], peer
    latched = peer["latest_blocking_event"]
    assert (latched["agent"], latched["type"], latched["status"]) == (rco, veto_type, "changes_requested"), peer
    assert not slot["ok"] and slot["decision"] == "vetoed_after_pass" and slot["blocking_rco_agents"] == [rco], slot
    assert _shapes(scan) == [(rco, veto_type, "changes_requested")], scan
    after = before + [_closeout(veto, "message", status)]
    peer_after, slot_after, scan_after = _peer(after), _slot(after), _scan(after)
    assert not peer_after["clear_to_merge"] and peer_after["latest_blocking_event"] == latched, peer_after
    assert not slot_after["ok"] and slot_after["decision"] == "vetoed_after_pass", slot_after
    assert slot_after["blocking_rco_agents"] == [rco], slot_after
    assert not scan_after["ok"] and _shapes(scan_after) == _shapes(scan), scan_after
