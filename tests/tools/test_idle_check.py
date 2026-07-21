# SPDX-License-Identifier: BUSL-1.1
"""Tests for the opt-in bridge idle detection primitive."""
from __future__ import annotations

import importlib
import io
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from contextlib import redirect_stdout
from typing import Any

import pytest


NOW = "2026-05-17T12:00:00Z"
BRIDGE_AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}


def _event(
    *,
    ts_utc: str,
    agent: str = "codex",
    type: str = "message",
    task_id: str = "idle-smoke",
    status: str = "note",
    to: str = "claude",
    message: str = "Substantive bridge content that should count as agent activity.",
    payload: dict[str, object] | None = None,
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


def _write_events(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )


def _base_idle_events() -> list[dict[str, object]]:
    return [
        _event(
            ts_utc="2026-05-17T10:30:00Z",
            type="done",
            status="merged_postmerge_green",
            message="PR merged with postmerge verification green.",
        ),
        _event(
            ts_utc="2026-05-17T10:31:00Z",
            agent="claude",
            type="message",
            status="scout_recommendation",
            message="A substantive design response with concrete scope, tests, and risks.",
        ),
        _event(
            ts_utc="2026-05-17T10:32:00Z",
            agent="operator",
            type="message",
            status="operator_note",
            to="codex",
            message="Operator-directed bridge note older than the idle window.",
        ),
    ]


def _run(
    tmp_path: Path,
    events: list[dict[str, object]],
    *args: str,
    claims: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    mod = importlib.import_module("tools.idle_check")
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir(exist_ok=True)
    _write_events(events_path, events)
    for index, claim in enumerate(claims or []):
        (claims_dir / f"claim-{index}.json").write_text(
            json.dumps(claim, sort_keys=True),
            encoding="utf-8",
        )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mod.main(
            [
                "--events",
                str(events_path),
                "--claims-dir",
                str(claims_dir),
                "--now",
                NOW,
                "--json",
                *args,
            ]
        )

    assert rc == 0
    return json.loads(stdout.getvalue())


def test_idle_when_all_predicates_are_quiet_for_the_window(tmp_path: Path) -> None:
    payload = _run(tmp_path, _base_idle_events())

    assert payload["decision"] == "idle"
    assert payload["idle"] is True
    assert payload["blockers"] == []


def test_pending_ci_count_keeps_bridge_active(tmp_path: Path) -> None:
    payload = _run(tmp_path, _base_idle_events(), "--pending-ci-count", "1")

    assert payload["decision"] == "active"
    assert payload["idle"] is False
    assert "pending_ci" in payload["blockers"]


def test_open_work_claim_keeps_bridge_active_without_pr_or_rco(
    tmp_path: Path,
) -> None:
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "long-running-implementation",
                "summary": "Agent is still implementing without a PR yet.",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    assert payload["decision"] == "active"
    assert "open_work_claims" in payload["blockers"]
    assert payload["criteria"]["open_work_claims"]["task_ids"] == [
        "long-running-implementation"
    ]


def test_active_work_claim_kind_is_not_misclassified_as_deliberation(
    tmp_path: Path,
) -> None:
    """2026-05-18 substrate-invariant #1 (phase A): an explicit active_work
    claim must NOT be read as a scout/rco deliberation request even if its
    free text happens to contain 'rco' or 'scout'.
    """
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "implementing-rco-feedback-from-pr999",
                "summary": "Incorporating scout review notes into the impl.",
                "claim_kind": "active_work",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    # It still counts as an open work claim (blocks idle), but it must NOT
    # appear as an open_scout_requests or open_rco_requests deliberation lock.
    assert payload["criteria"]["open_scout_requests"]["task_ids"] == []
    assert payload["criteria"]["open_rco_requests"]["task_ids"] == []
    assert payload["criteria"]["open_work_claims"]["task_ids"] == [
        "implementing-rco-feedback-from-pr999"
    ]


def test_deliberation_claim_kind_with_subtype_is_authoritative(
    tmp_path: Path,
) -> None:
    """An explicit deliberation claim with deliberation_kind=rco is read as an
    rco request regardless of free text.
    """
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "neutral-task-name",
                "summary": "No keyword in this summary at all.",
                "claim_kind": "deliberation",
                "deliberation_kind": "rco",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    assert payload["decision"] == "active"
    assert payload["criteria"]["open_rco_requests"]["task_ids"] == [
        "neutral-task-name"
    ]


def test_deliberation_claim_kind_fails_closed_to_rco_without_subtype(
    tmp_path: Path,
) -> None:
    """A claim explicitly declared claim_kind=deliberation with no
    deliberation_kind and no scout/rco free-text hint fails closed to an rco
    deliberation lock (keeps the bridge active), rather than silently dropping
    the lock. This is intentional per _claim_kind's documented semantics.
    """
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "neutral-task-no-keyword",
                "summary": "Summary with no keyword and no deliberation_kind.",
                "claim_kind": "deliberation",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    assert payload["decision"] == "active"
    assert payload["criteria"]["open_rco_requests"]["task_ids"] == [
        "neutral-task-no-keyword"
    ]
    assert payload["criteria"]["open_scout_requests"]["task_ids"] == []


def test_deliberation_claim_kind_scout_text_hint_without_subtype(
    tmp_path: Path,
) -> None:
    """deliberation claim with no deliberation_kind but 'scout' in free text
    resolves to scout (text hint precedes the rco fail-closed default).
    """
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "claude-scout-something-deliberation",
                "summary": "A scout deliberation with no explicit subtype.",
                "claim_kind": "deliberation",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    assert payload["decision"] == "active"
    assert payload["criteria"]["open_scout_requests"]["task_ids"] == [
        "claude-scout-something-deliberation"
    ]
    assert payload["criteria"]["open_rco_requests"]["task_ids"] == []


def test_legacy_claim_without_claim_kind_uses_text_heuristic(
    tmp_path: Path,
) -> None:
    """Backward compatibility: a claim with no claim_kind field still uses the
    free-text scout/rco substring heuristic, so existing writers are
    unaffected.
    """
    payload = _run(
        tmp_path,
        _base_idle_events(),
        claims=[
            {
                "task_id": "claude-scout-something",
                "summary": "Legacy scout claim with no claim_kind field.",
                "claimed_at_utc": "2026-05-17T10:45:00Z",
            }
        ],
    )

    assert payload["decision"] == "active"
    assert payload["criteria"]["open_scout_requests"]["task_ids"] == [
        "claude-scout-something"
    ]


def test_open_scout_or_rco_request_keeps_bridge_active_until_answered(tmp_path: Path) -> None:
    open_scout = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            task_id="claude-scout-idle-dreaming",
            status="request_scout",
            message="Please scout idle dreaming risks.",
        )
    ]
    payload = _run(tmp_path, open_scout)
    assert payload["decision"] == "active"
    assert "open_scout_requests" in payload["blockers"]

    answered_scout = open_scout + [
        _event(
            ts_utc="2026-05-17T10:10:00Z",
            agent="claude",
            task_id="claude-scout-idle-dreaming",
            status="scout_answered",
            message="Scout answered with risks and smallest safe scope.",
        )
    ]
    payload = _run(tmp_path, answered_scout)
    assert payload["decision"] == "idle"

    recommended_scout = open_scout + [
        _event(
            ts_utc="2026-05-17T10:10:00Z",
            agent="claude",
            task_id="claude-scout-idle-dreaming",
            status="scout_recommendation",
            message="Scout recommends the smallest safe implementation slice.",
        )
    ]
    payload = _run(tmp_path, recommended_scout)
    assert payload["decision"] == "idle"

    open_rco = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            type="handoff",
            task_id="claude-rco-pr999",
            status="rco_requested",
            message="RCO requested for PR #999.",
        )
    ]
    payload = _run(tmp_path, open_rco)
    assert payload["decision"] == "active"
    assert "open_rco_requests" in payload["blockers"]


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("decision", "completed"),
        ("blocked", "note"),
        ("release", "released"),
    ],
)
def test_legacy_rco_target_terminal_reply_shapes_still_close(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    task_id = f"legacy-rco-{event_type}-{status}"
    request = _event(
        ts_utc="2026-05-17T10:00:00Z",
        agent="claude",
        type="handoff",
        task_id=task_id,
        status="rco_requested",
        to="codex",
        message="Legacy RCO review requested.",
    )
    response = _event(
        ts_utc="2026-05-17T10:10:00Z",
        agent="codex",
        type=event_type,
        task_id=task_id,
        status=status,
        to="claude",
        message="Legacy target emitted a substantive terminal reply.",
    )

    report = _run(tmp_path, _base_idle_events() + [request, response])

    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        ("rco", "not_done"),
        ("rco", "never_done"),
        ("rco", "rco_not_pass"),
        ("rco", "rco_pass_failed"),
        ("rco", "rco_pass_failure"),
        ("rco", "cannot_pass"),
        ("rco", "working_done"),
        ("rco", "pending_done"),
        ("rco", "unresolved_done"),
        ("rco", "incomplete_done"),
        ("scout", "scout_not_answered"),
    ],
)
def test_legacy_target_message_with_negated_status_does_not_close(
    tmp_path: Path,
    kind: str,
    status: str,
) -> None:
    task_id = f"legacy-{kind}-negative-{status}"
    request_status = "request_scout" if kind == "scout" else "rco_requested"
    request = _event(
        ts_utc="2026-05-17T10:00:00Z",
        agent="codex",
        type="handoff",
        task_id=task_id,
        status=request_status,
        to="claude",
        message=f"Legacy {kind} request.",
    )
    negated_reply = _event(
        ts_utc="2026-05-17T10:10:00Z",
        agent="claude",
        type="message",
        task_id=task_id,
        status=status,
        to="codex",
        message="This status explicitly negates completion.",
    )

    report = _run(tmp_path, _base_idle_events() + [request, negated_reply])

    criterion = f"open_{kind}_requests"
    assert report["criteria"][criterion]["task_ids"] == [task_id]


def test_legacy_rco_pass_message_can_close_without_message_body(
    tmp_path: Path,
) -> None:
    task_id = "legacy-rco-empty-pass-message"
    request = _event(
        ts_utc="2026-05-17T10:00:00Z",
        agent="codex",
        type="handoff",
        task_id=task_id,
        status="rco_requested",
        to="claude",
        message="Legacy RCO request.",
    )
    response = _event(
        ts_utc="2026-05-17T10:10:00Z",
        agent="claude",
        type="message",
        task_id=task_id,
        status="rco_pass",
        to="codex",
        message="",
    )

    report = _run(tmp_path, _base_idle_events() + [request, response])

    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_canonical_wake_review_request_blocks_for_each_addressed_rco(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/bridge-reader-c0-20260720"
    head = "d0a8b4ce8b13ae4c6a0548df0990deee2c41af91"
    request = _canonical_rco_request(
        task_id=task_id,
        head=head,
        to="codex-tools-1,claude-rco-1,claude-rco-2",
        eligible_agents=("claude-rco-1", "claude-rco-2"),
    )
    rco_1_pass = _event(
        ts_utc="2026-05-17T11:10:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        to="codex-lead-1",
        message=f"RCO PASS at exact head {head}.",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )
    rco_2_changes = _event(
        ts_utc="2026-05-17T11:11:00Z",
        agent="claude-rco-2",
        type="finding",
        task_id=task_id,
        status="changes_requested",
        to="codex-lead-1",
        message=f"RCO changes requested at exact head {head}.",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )

    open_report = _run(tmp_path, _base_idle_events() + [request])
    one_response_report = _run(
        tmp_path,
        _base_idle_events() + [request, rco_1_pass],
    )
    closed_report = _run(
        tmp_path,
        _base_idle_events() + [request, rco_1_pass, rco_2_changes],
    )

    assert open_report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert one_response_report["criteria"]["open_rco_requests"]["task_ids"] == [
        task_id
    ]
    assert closed_report["criteria"]["open_rco_requests"] == {
        "ok": True,
        "task_ids": [],
    }
    assert closed_report["decision"] == "idle"


def _canonical_rco_request(
    *,
    task_id: str,
    head: str,
    ts_utc: str = "2026-05-17T11:00:00Z",
    to: str = "codex-tools-1,claude-rco-1",
    eligible_agents: tuple[str, ...] = ("claude-rco-1",),
    pr: int = 1544,
) -> dict[str, object]:
    return _event(
        ts_utc=ts_utc,
        agent="codex-lead-1",
        type="wake_request",
        task_id=task_id,
        status="review_requested",
        to=to,
        message=f"Exact-head formal consensus review requested at {head}.",
        payload={
            "schema": "wd.exact_head_consensus_request.v1",
            "request_only": True,
            "approval_asserted": False,
            "pr": pr,
            "canonical_task_id": task_id,
            "head": head,
            "operator_review_required": True,
            "required_signals": {
                "build_tools": {
                    "agent": "codex-tools-1",
                    "type": "decision",
                    "status": "build_consensus_pass",
                    "task_id": task_id,
                    "payload_head": head,
                },
                "rco": {
                    "eligible_agents": list(eligible_agents),
                    "type": "decision",
                    "status": "rco_pass",
                    "task_id": task_id,
                    "message_must_contain_head": head,
                    "payload": {
                        "head": head,
                        "canonical_task_id": task_id,
                        "operator_gated": True,
                    },
                },
            },
        },
    )


def _canonical_tools_review_request(
    *,
    task_id: str,
    head: str,
    ts_utc: str = "2026-05-17T11:00:00Z",
    to: str = "codex-tools-1",
) -> dict[str, object]:
    return _event(
        ts_utc=ts_utc,
        agent="codex-lead-1",
        type="wake_request",
        task_id=task_id,
        status="review_requested",
        to=to,
        message=f"Exact-head Tools review requested at {head}.",
        payload={
            "pr": 1551,
            "branch": task_id,
            "schema": "wd.exact_head_tools_review_request.v1",
            "base_ref": "main",
            "deployment_authority": False,
            "request_only": True,
            "base_head": "66a6ca1fb1d1b0501a0c0ed92b1870685d746e72",
            "approval_asserted": False,
            "canonical_task_id": task_id,
            "merge_authority": False,
            "head": head,
            "operator_gated": True,
        },
    )


def _direct_rco_request(
    *,
    task_id: str,
    head: str,
    ts_utc: str = "2026-05-17T11:00:00Z",
    to: str = "claude-rco-1,claude-rco-2",
    pr: int = 1551,
    base_head: str = "main",
    agent: str = "codex-lead-1",
) -> dict[str, object]:
    event = _event(
        ts_utc=ts_utc,
        agent=agent,
        type="message",
        task_id=task_id,
        status="rco_pass_or_block_requested",
        to=to,
        message=f"Direct RCO pass-or-block review required at exact head {head}.",
        payload={
            "schema": "wd.rco_direct_pass_block_request.v1",
            "request": "rco pass or block required",
            "request_only": True,
            "approval_asserted": False,
            "canonical_task_id": task_id,
            "pr": pr,
            "head": head,
            "base_head": base_head,
            "operator_gated": True,
            "merge_authority_granted": False,
            "deployment_authority_granted": False,
        },
    )
    event["agent_uuid"] = BRIDGE_AGENT_UUIDS[agent]
    return event


def _direct_rco_response(
    *,
    task_id: str,
    head: str,
    agent: str,
    ts_utc: str,
    type: str = "decision",
    status: str = "rco_pass",
    to: str = "codex-lead-1",
    pr: int = 1551,
) -> dict[str, object]:
    event = _event(
        ts_utc=ts_utc,
        agent=agent,
        type=type,
        task_id=task_id,
        status=status,
        to=to,
        message=f"RCO verdict for exact head {head}.",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "pr": pr,
            "operator_gated": True,
        },
    )
    event["agent_uuid"] = BRIDGE_AGENT_UUIDS.get(agent, "")
    return event


def test_direct_rco_contract_tracks_each_target_until_exact_head_response(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/direct-rco-contract"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    request = _direct_rco_request(task_id=task_id, head=head)
    rco_1_pass = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:05:00Z",
    )
    rco_2_changes = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-2",
        ts_utc="2026-05-17T11:06:00Z",
        type="finding",
        status="changes_requested",
    )

    open_report = _run(tmp_path, _base_idle_events() + [request])
    one_response_report = _run(
        tmp_path, _base_idle_events() + [request, rco_1_pass]
    )
    closed_report = _run(
        tmp_path, _base_idle_events() + [request, rco_1_pass, rco_2_changes]
    )

    assert open_report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert one_response_report["criteria"]["open_rco_requests"]["task_ids"] == [
        task_id
    ]
    assert closed_report["criteria"]["open_rco_requests"] == {
        "ok": True,
        "task_ids": [],
    }


def test_direct_rco_contract_enforces_identity_head_order_and_reopen(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/direct-rco-ordering"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    new_head = "1" * 40
    wrong_head = "0" * 40
    early_responses = [
        _direct_rco_response(
            task_id=task_id,
            head=head,
            agent=agent,
            ts_utc=f"2026-05-17T10:5{index}:00Z",
        )
        for index, agent in enumerate(("claude-rco-1", "claude-rco-2"))
    ]
    request = _direct_rco_request(task_id=task_id, head=head)
    wrong_head_response = _direct_rco_response(
        task_id=task_id,
        head=wrong_head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:02:00Z",
    )
    wrong_recipient_response = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-2",
        ts_utc="2026-05-17T11:03:00Z",
        to="someone-else",
    )
    valid_responses = [
        _direct_rco_response(
            task_id=task_id,
            head=head,
            agent=agent,
            ts_utc=f"2026-05-17T11:1{index}:00Z",
        )
        for index, agent in enumerate(("claude-rco-1", "claude-rco-2"))
    ]
    reopened = _direct_rco_request(
        task_id=task_id,
        head=new_head,
        ts_utc="2026-05-17T11:20:00Z",
    )
    duplicate_old_request = _direct_rco_request(
        task_id=task_id,
        head=head,
        ts_utc="2026-05-17T11:21:00Z",
    )

    still_open = _run(
        tmp_path,
        _base_idle_events()
        + early_responses
        + [request, wrong_head_response, wrong_recipient_response],
    )
    closed = _run(
        tmp_path,
        _base_idle_events() + [request] + valid_responses,
    )
    reopened_report = _run(
        tmp_path,
        _base_idle_events()
        + [request]
        + valid_responses
        + [reopened, duplicate_old_request],
    )

    assert still_open["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert closed["criteria"]["open_rco_requests"]["task_ids"] == []
    assert reopened_report["criteria"]["open_rco_requests"]["task_ids"] == [
        task_id
    ]


def test_direct_rco_contract_rejects_spoofed_or_authoritative_responses(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/direct-rco-response-security"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    request = _direct_rco_request(task_id=task_id, head=head)
    wrong_uuid = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:05:00Z",
    )
    wrong_uuid["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    authority_response = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-2",
        ts_utc="2026-05-17T11:06:00Z",
    )
    authority_payload = authority_response["payload"]
    assert isinstance(authority_payload, dict)
    authority_payload["merge_authority_granted"] = True
    authority_payload["deployment_authority_granted"] = True
    aliased_authority = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:06:30Z",
    )
    aliased_authority_payload = aliased_authority["payload"]
    assert isinstance(aliased_authority_payload, dict)
    aliased_authority_payload["merge_allowed"] = True
    aliased_authority_payload["operator_approved"] = True
    missing_pr = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-2",
        ts_utc="2026-05-17T11:06:45Z",
    )
    missing_pr_payload = missing_pr["payload"]
    assert isinstance(missing_pr_payload, dict)
    del missing_pr_payload["pr"]
    requester_done = _event(
        ts_utc="2026-05-17T11:07:00Z",
        agent="codex-lead-1",
        type="done",
        task_id=task_id,
        status="completed",
        to="claude-rco-1,claude-rco-2",
        message=f"Implementation completed at {head}.",
        payload={"head": head, "canonical_task_id": task_id},
    )
    requester_done["agent_uuid"] = BRIDGE_AGENT_UUIDS["codex-lead-1"]

    report = _run(
        tmp_path,
        _base_idle_events()
        + [
            request,
            wrong_uuid,
            authority_response,
            aliased_authority,
            missing_pr,
            requester_done,
        ],
    )
    assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]


def test_direct_rco_contract_binds_requester_identity(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/direct-rco-correlation"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    lead_request = _direct_rco_request(
        task_id=task_id,
        head=head,
        to="claude-rco-1",
        pr=1551,
    )
    tools_request = _direct_rco_request(
        task_id=task_id,
        head=head,
        ts_utc="2026-05-17T11:01:00Z",
        to="claude-rco-1",
        pr=1551,
        agent="codex-tools-1",
    )
    lead_pass = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:02:00Z",
        pr=1551,
    )
    tools_pass = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:03:00Z",
        to="codex-tools-1",
        pr=1551,
    )

    one_open = _run(
        tmp_path,
        _base_idle_events() + [lead_request, tools_request, lead_pass],
    )
    closed = _run(
        tmp_path,
        _base_idle_events()
        + [lead_request, tools_request, lead_pass, tools_pass],
    )

    assert one_open["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert closed["criteria"]["open_rco_requests"]["task_ids"] == []


def test_direct_rco_contract_binds_response_pr(tmp_path: Path) -> None:
    task_id = "codex-lead-1/direct-rco-pr-correlation"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    first_request = _direct_rco_request(
        task_id=task_id,
        head=head,
        to="claude-rco-1",
        pr=1551,
    )
    second_request = _direct_rco_request(
        task_id=task_id,
        head=head,
        ts_utc="2026-05-17T11:01:00Z",
        to="claude-rco-1",
        pr=1552,
    )
    first_pass = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:02:00Z",
        pr=1551,
    )
    second_pass = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:03:00Z",
        pr=1552,
    )

    one_open = _run(
        tmp_path,
        _base_idle_events() + [first_request, second_request, first_pass],
    )
    closed = _run(
        tmp_path,
        _base_idle_events()
        + [first_request, second_request, first_pass, second_pass],
    )

    assert one_open["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert closed["criteria"]["open_rco_requests"]["task_ids"] == []


def test_direct_rco_contract_uses_append_order_not_untrusted_timestamps(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/direct-rco-append-order"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    earlier_line_future_response = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:30:00Z",
    )
    request = _direct_rco_request(task_id=task_id, head=head)
    later_line_backdated_response = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-2",
        ts_utc="2026-05-17T10:00:00Z",
    )
    rco_1_after_request = _direct_rco_response(
        task_id=task_id,
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T10:01:00Z",
    )

    one_open = _run(
        tmp_path,
        _base_idle_events()
        + [earlier_line_future_response, request, later_line_backdated_response],
    )
    closed = _run(
        tmp_path,
        _base_idle_events()
        + [
            earlier_line_future_response,
            request,
            later_line_backdated_response,
            rco_1_after_request,
        ],
    )

    assert one_open["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert closed["criteria"]["open_rco_requests"]["task_ids"] == []


def test_direct_append_order_does_not_change_canonical_replay_order(
    tmp_path: Path,
) -> None:
    task_id = "canonical-rco-append-replay"
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    request = _canonical_rco_request(task_id=task_id, head=head)
    response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        to="codex-lead-1",
        message=f"RCO PASS at exact head {head}.",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )
    appended_old_replay = _canonical_rco_request(task_id=task_id, head=head)

    report = _run(
        tmp_path,
        _base_idle_events() + [request, response, appended_old_replay],
    )

    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_direct_rco_contract_is_fail_closed_without_capturing_third_party(
    tmp_path: Path,
) -> None:
    head = "97b0bad684a5c0b8531b643f363002a184a34cd5"
    malformed: list[dict[str, object]] = []

    def add_malformed(suffix: str) -> dict[str, object]:
        event = _direct_rco_request(task_id=f"direct-invalid-{suffix}", head=head)
        malformed.append(event)
        payload = event["payload"]
        assert isinstance(payload, dict)
        return payload

    add_malformed("extra")["unexpected"] = False
    add_malformed("request")["request"] = "review requested"
    add_malformed("request-only")["request_only"] = 1
    add_malformed("approval")["approval_asserted"] = 0
    add_malformed("task")["canonical_task_id"] = "other-task"
    add_malformed("pr")["pr"] = True
    add_malformed("head")["head"] = head.upper()
    add_malformed("base")["base_head"] = "refs/heads/main"
    add_malformed("operator")["operator_gated"] = False
    add_malformed("merge")["merge_authority_granted"] = True
    add_malformed("deploy")["deployment_authority_granted"] = True

    bad_message = _direct_rco_request(task_id="direct-invalid-message", head=head)
    bad_message["message"] = "Direct RCO request without an exact head."
    malformed.append(bad_message)
    bad_type = _direct_rco_request(task_id="direct-invalid-type", head=head)
    bad_type["type"] = "MESSAGE"
    malformed.append(bad_type)
    bad_status = _direct_rco_request(task_id="direct-invalid-status", head=head)
    bad_status["status"] = "rco_pass_or_block_request"
    malformed.append(bad_status)
    missing_schema = _direct_rco_request(
        task_id="direct-invalid-missing-schema", head=head
    )
    missing_schema_payload = missing_schema["payload"]
    assert isinstance(missing_schema_payload, dict)
    del missing_schema_payload["schema"]
    malformed.append(missing_schema)
    null_schema = _direct_rco_request(
        task_id="direct-invalid-null-schema", head=head
    )
    null_schema_payload = null_schema["payload"]
    assert isinstance(null_schema_payload, dict)
    null_schema_payload["schema"] = None
    malformed.append(null_schema)
    misspelled_schema = _direct_rco_request(
        task_id="direct-invalid-misspelled-schema", head=head
    )
    misspelled_schema_payload = misspelled_schema["payload"]
    assert isinstance(misspelled_schema_payload, dict)
    misspelled_schema_payload["schema"] = (
        "wd.rco_direct_pass_block_requset.v1"
    )
    malformed.append(misspelled_schema)
    for index, schema in enumerate(
        (
            "wd.rco_direct_pass_blok_request.v1",
            "wd.rco_direct_pas_block_request.v1",
            "wd.rco_direct_passblock_request.v1",
        )
    ):
        internal_schema_typo = _direct_rco_request(
            task_id=f"direct-invalid-internal-schema-{index}",
            head=head,
        )
        internal_schema_payload = internal_schema_typo["payload"]
        assert isinstance(internal_schema_payload, dict)
        internal_schema_payload["schema"] = schema
        malformed.append(internal_schema_typo)
    missing_payload = _direct_rco_request(
        task_id="direct-invalid-missing-payload", head=head
    )
    del missing_payload["payload"]
    malformed.append(missing_payload)
    bad_recipient = _direct_rco_request(
        task_id="direct-invalid-recipient",
        head=head,
        to="claude-rco-1,codex-tools-1",
    )
    malformed.append(bad_recipient)
    duplicate_recipient = _direct_rco_request(
        task_id="direct-invalid-duplicate",
        head=head,
        to="claude-rco-1,claude-rco-1",
    )
    malformed.append(duplicate_recipient)
    wrong_requester_uuid = _direct_rco_request(
        task_id="direct-invalid-requester-uuid", head=head
    )
    wrong_requester_uuid["agent_uuid"] = (
        "00000000-0000-0000-0000-000000000000"
    )
    malformed.append(wrong_requester_uuid)
    unregistered_recipient = _direct_rco_request(
        task_id="direct-invalid-unregistered-rco",
        head=head,
        to="mallory-rco",
    )
    malformed.append(unregistered_recipient)

    for event in malformed:
        report = _run(tmp_path, _base_idle_events() + [event])
        assert report["criteria"]["open_rco_requests"]["task_ids"] == [
            event["task_id"]
        ]

    compound_malformed = []
    for schema_variant in ("missing", "null", "misspelled"):
        event = _direct_rco_request(
            task_id=f"direct-invalid-compound-{schema_variant}",
            head=head,
        )
        del event["task_id"]
        payload = event["payload"]
        assert isinstance(payload, dict)
        if schema_variant == "missing":
            del payload["schema"]
        elif schema_variant == "null":
            payload["schema"] = None
        else:
            payload["schema"] = "wd.rco_direct_pass_blok_request.v1"
        compound_malformed.append(event)
    no_task_or_payload = _direct_rco_request(
        task_id="direct-invalid-compound-payload", head=head
    )
    del no_task_or_payload["task_id"]
    del no_task_or_payload["payload"]
    compound_malformed.append(no_task_or_payload)

    for event in compound_malformed:
        report = _run(tmp_path, _base_idle_events() + [event])
        task_ids = report["criteria"]["open_rco_requests"]["task_ids"]
        assert len(task_ids) == 1
        assert task_ids[0].startswith("invalid-canonical-request-line-")

    requester_done = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="codex-lead-1",
        type="done",
        task_id="direct-invalid-extra",
        status="completed",
        to="claude-rco-1,claude-rco-2",
        message=f"Requester work completed at {head}.",
        payload={"head": head, "canonical_task_id": "direct-invalid-extra"},
    )
    requester_done["agent_uuid"] = BRIDGE_AGENT_UUIDS["codex-lead-1"]
    sticky_report = _run(
        tmp_path,
        _base_idle_events() + [malformed[0], requester_done],
    )
    assert sticky_report["criteria"]["open_rco_requests"]["task_ids"] == [
        "direct-invalid-extra"
    ]

    for malformed_envelope in (bad_type, bad_status):
        task_id = str(malformed_envelope["task_id"])
        apparent_responses = [
            _direct_rco_response(
                task_id=task_id,
                head=head,
                agent=agent,
                ts_utc=f"2026-05-17T11:1{index}:00Z",
            )
            for index, agent in enumerate(("claude-rco-1", "claude-rco-2"))
        ]
        unresolved_report = _run(
            tmp_path,
            _base_idle_events() + [malformed_envelope] + apparent_responses,
        )
        assert unresolved_report["criteria"]["open_rco_requests"][
            "task_ids"
        ] == [task_id]

    mallory_pass = _direct_rco_response(
        task_id="direct-invalid-unregistered-rco",
        head=head,
        agent="mallory-rco",
        ts_utc="2026-05-17T11:05:00Z",
    )
    mallory_pass["agent_uuid"] = "00000000-0000-0000-0000-000000000000"
    mallory_report = _run(
        tmp_path,
        _base_idle_events() + [unregistered_recipient, mallory_pass],
    )
    assert mallory_report["criteria"]["open_rco_requests"]["task_ids"] == [
        "direct-invalid-unregistered-rco"
    ]

    third_party = _direct_rco_request(task_id="third-party-direct", head=head)
    third_party_payload = third_party["payload"]
    assert isinstance(third_party_payload, dict)
    third_party_payload["schema"] = "third.party.rco_direct_pass_block_request.v1"
    response_echo = _direct_rco_response(
        task_id="direct-response-echo",
        head=head,
        agent="claude-rco-1",
        ts_utc="2026-05-17T11:05:00Z",
    )
    response_echo_payload = response_echo["payload"]
    assert isinstance(response_echo_payload, dict)
    response_echo_payload["schema"] = "wd.rco_direct_pass_block_request.v1"

    report = _run(tmp_path, _base_idle_events() + [third_party, response_echo])
    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_valid_canonical_tools_review_is_not_an_rco_lock(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/idle-tools-review-20260720"
    head = "23ea44bcb5070821bfcfdde84c1b314b48dc17dc"
    request = _canonical_tools_review_request(task_id=task_id, head=head)
    response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="codex-tools-1",
        type="decision",
        task_id=task_id,
        status="build_consensus_pass",
        to="codex-lead-1",
        message=f"Tools build consensus PASS at exact head {head}.",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )
    blocker_response = _event(
        ts_utc="2026-05-17T11:06:00Z",
        agent="codex-tools-1",
        type="decision",
        task_id=task_id,
        status="changes_requested",
        to="codex-lead-1",
        message=f"Tools changes requested at exact head {head}.",
        payload={
            "schema": "wd.exact_head_consensus_response.v1",
            "request_only": True,
            "head": head,
            "canonical_task_id": task_id,
            "decision": "changes_requested",
            "operator_gated": True,
            "required_signals": {
                "rco": {"type": "decision", "status": "rco_pass"}
            },
        },
    )
    echoed_pass_response = _event(
        ts_utc="2026-05-17T11:07:00Z",
        agent="codex-tools-1",
        type="decision",
        task_id=task_id,
        status="build_consensus_pass",
        to="codex-lead-1",
        message=f"Tools build consensus PASS at exact head {head}.",
        payload={
            "schema": "wd.exact_head_consensus_response.v1",
            "request_only": True,
            "head": head,
            "canonical_task_id": task_id,
            "required_signals": "rco",
        },
    )
    echoed_done_response = _event(
        ts_utc="2026-05-17T11:08:00Z",
        agent="codex-tools-1",
        type="done",
        task_id=task_id,
        status="completed",
        to="codex-lead-1",
        message=f"Tools review completed at exact head {head}.",
        payload={
            "schema": "wd.exact_head_consensus_response.v1",
            "request_only": True,
            "head": head,
            "canonical_task_id": task_id,
            "required_signals": [{"RCO": {}}],
        },
    )

    request_report = _run(tmp_path, _base_idle_events() + [request])
    response_report = _run(
        tmp_path,
        _base_idle_events() + [request, response],
    )
    echoed_response_reports = [
        _run(tmp_path, _base_idle_events() + [request, echoed_response])
        for echoed_response in (
            blocker_response,
            echoed_pass_response,
            echoed_done_response,
        )
    ]

    assert request_report["criteria"]["open_rco_requests"] == {
        "ok": True,
        "task_ids": [],
    }
    assert response_report["criteria"]["open_rco_requests"] == {
        "ok": True,
        "task_ids": [],
    }
    assert all(
        report["criteria"]["open_rco_requests"]
        == {"ok": True, "task_ids": []}
        for report in echoed_response_reports
    )


def test_tools_review_controls_without_rco_evidence_do_not_route_to_rco(
    tmp_path: Path,
) -> None:
    head = "23ea44bcb5070821bfcfdde84c1b314b48dc17dc"
    requests: list[dict[str, object]] = []

    def add_request(suffix: str) -> tuple[dict[str, object], dict[str, object]]:
        task_id = f"malformed-tools-review-{suffix}"
        request = _canonical_tools_review_request(task_id=task_id, head=head)
        payload = request["payload"]
        assert isinstance(payload, dict)
        requests.append(request)
        return request, payload

    _, payload = add_request("merge-authority")
    payload["merge_authority"] = True
    _, payload = add_request("deployment-authority")
    payload["deployment_authority"] = 0
    _, payload = add_request("approval-type")
    payload["approval_asserted"] = 0
    _, payload = add_request("request-only-type")
    payload["request_only"] = 1
    _, payload = add_request("operator-gated-type")
    payload["operator_gated"] = "true"
    _, payload = add_request("pr-type")
    payload["pr"] = True
    _, payload = add_request("base-head")
    payload["base_head"] = "not-a-head"
    _, payload = add_request("task-binding")
    payload["canonical_task_id"] = "different-task"
    request, _ = add_request("message-binding")
    request["message"] = "Tools review requested without the exact head."
    _, payload = add_request("extra-build-signal")
    payload["required_signals"] = {
        "build_tools": {"type": "decision", "status": "build_consensus_pass"}
    }
    _, payload = add_request("build-values-mention-rco")
    payload["required_signals"] = {
        "build_tools": {
            "task_id": "bridge/rco-delivery-fix",
            "agent": "claude-rco-1",
            "message": "RCO review remains a separate lane.",
        }
    }
    _, payload = add_request("build-list-rco-value")
    payload["required_signals"] = {"build_tools": ["rco"]}
    _, payload = add_request("schema-container")
    payload["schema"] = ["wd.exact_head_tools_review_request.v1"]

    for request in requests:
        task_id = str(request["task_id"])
        tools_response = _event(
            ts_utc="2026-05-17T11:05:00Z",
            agent="codex-tools-1",
            type="decision",
            task_id=task_id,
            status="build_consensus_pass",
            to="codex-lead-1",
            message=f"Tools build consensus PASS at exact head {head}.",
            payload={"head": head, "canonical_task_id": task_id},
        )
        report = _run(
            tmp_path,
            _base_idle_events() + [request, tools_response],
        )
        assert report["criteria"]["open_rco_requests"] == {
            "ok": True,
            "task_ids": [],
        }


def test_tools_schema_preserves_explicit_rco_recipient_and_status_routing(
    tmp_path: Path,
) -> None:
    head = "23ea44bcb5070821bfcfdde84c1b314b48dc17dc"
    recipient_task = "tools-schema-explicit-rco-recipient"
    recipient_request = _canonical_tools_review_request(
        task_id=recipient_task,
        head=head,
        to="codex-tools-1,claude-rco-1",
    )
    tools_response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="codex-tools-1",
        type="decision",
        task_id=recipient_task,
        status="build_consensus_pass",
        to="codex-lead-1",
        message=f"Tools build consensus PASS at exact head {head}.",
        payload={"head": head, "canonical_task_id": recipient_task},
    )
    rco_response = _event(
        ts_utc="2026-05-17T11:06:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=recipient_task,
        status="rco_pass",
        to="codex-lead-1",
        message=f"RCO PASS at exact head {head}.",
        payload={"head": head, "canonical_task_id": recipient_task},
    )

    tools_only_report = _run(
        tmp_path,
        _base_idle_events() + [recipient_request, tools_response],
    )
    rco_report = _run(
        tmp_path,
        _base_idle_events()
        + [recipient_request, tools_response, rco_response],
    )
    assert tools_only_report["criteria"]["open_rco_requests"]["task_ids"] == [
        recipient_task
    ]
    assert rco_report["criteria"]["open_rco_requests"]["task_ids"] == []

    status_task = "tools-recipient-explicit-rco-status"
    status_request = _event(
        ts_utc="2026-05-17T11:00:00Z",
        type="handoff",
        task_id=status_task,
        status="rco_requested",
        to="codex-tools-1",
        message="Explicit RCO status remains an RCO request.",
    )
    status_report = _run(
        tmp_path,
        _base_idle_events() + [status_request],
    )
    assert status_report["criteria"]["open_rco_requests"]["task_ids"] == [
        status_task
    ]


def test_canonical_rco_response_must_be_fresh_same_task_identity_and_head(
    tmp_path: Path,
) -> None:
    task_id = "codex-lead-1/python-v1-writer-migration-20260720"
    head = "599de71a93bfd46ca133e3fbf7eae1f37d21f149"
    request = _canonical_rco_request(
        task_id=task_id,
        head=head,
        to="codex-tools-1,claude-rco-1",
        pr=1546,
    )
    invalid_responses = [
        _event(
            ts_utc="2026-05-17T10:59:59Z",
            agent="claude-rco-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            payload={"head": head},
        ),
        _event(
            ts_utc="2026-05-17T11:02:00Z",
            agent="codex-tools-1",
            type="decision",
            task_id=task_id,
            status="build_consensus_pass",
            payload={"head": head},
        ),
        _event(
            ts_utc="2026-05-17T11:03:00Z",
            agent="claude-rco-1",
            type="decision",
            task_id="codex-lead-1/different-review-task",
            status="rco_pass",
            payload={"head": head},
        ),
        _event(
            ts_utc="2026-05-17T11:04:00Z",
            agent="claude-rco-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            payload={"head": "0000000000000000000000000000000000000000"},
        ),
        _event(
            ts_utc="2026-05-17T11:05:00Z",
            agent="claude-rco-1",
            type="message",
            task_id=task_id,
            status="received",
            message=f"Received exact-head request {head}.",
            payload={"head": head},
        ),
        _event(
            ts_utc="2026-05-17T11:06:00Z",
            agent="claude-rco-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            message=f"RCO PASS at exact head {head}.",
            payload={"head": head, "canonical_task_id": "different-task"},
        ),
    ]

    report = _run(tmp_path, _base_idle_events() + invalid_responses + [request])

    assert report["decision"] == "active"
    assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]


def test_rco_target_contract_mismatch_holds_fail_closed(tmp_path: Path) -> None:
    targetless = _event(
        ts_utc="2026-05-16T10:00:00Z",
        type="handoff",
        task_id="targetless-rco-request",
        status="rco_requested",
        to="",
        message="RCO review requested, but the target is missing.",
    )
    mismatch_task = "canonical-rco-target-mismatch"
    mismatch_head = "1111111111111111111111111111111111111111"
    canonical_mismatch = _canonical_rco_request(
        task_id=mismatch_task,
        head=mismatch_head,
        to="codex-tools-1",
    )
    partial_task = "canonical-rco-partial-target-mismatch"
    partial_mismatch = _canonical_rco_request(
        task_id=partial_task,
        head=mismatch_head,
        to="codex-tools-1,claude-rco-1,claude-rco-2",
        eligible_agents=("claude-rco-1",),
    )
    partial_response = _event(
        ts_utc="2026-05-17T11:10:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=partial_task,
        status="rco_pass",
        message=f"RCO PASS at exact head {mismatch_head}.",
        payload={"head": mismatch_head, "canonical_task_id": partial_task},
    )

    targetless_report = _run(tmp_path, _base_idle_events() + [targetless])
    mismatch_report = _run(tmp_path, _base_idle_events() + [canonical_mismatch])
    partial_report = _run(
        tmp_path,
        _base_idle_events() + [partial_mismatch, partial_response],
    )

    assert targetless_report["criteria"]["open_rco_requests"]["task_ids"] == [
        "targetless-rco-request"
    ]
    assert "targetless-rco-request" not in targetless_report["criteria"][
        "stale_open_requests_ignored"
    ]["task_ids"]
    assert mismatch_report["criteria"]["open_rco_requests"]["task_ids"] == [
        mismatch_task
    ]
    assert partial_report["criteria"]["open_rco_requests"]["task_ids"] == [
        partial_task
    ]


def test_malformed_canonical_rco_contracts_hold_unresolved(tmp_path: Path) -> None:
    head = "6666666666666666666666666666666666666666"

    def request(task_id: str) -> dict[str, Any]:
        return json.loads(
            json.dumps(_canonical_rco_request(task_id=task_id, head=head))
        )

    missing_head = request("canonical-missing-head")
    missing_head["payload"].pop("head")

    tools_as_rco = request("canonical-tools-as-rco")
    tools_as_rco["to"] = "codex-tools-1"
    tools_as_rco["payload"]["required_signals"]["rco"]["eligible_agents"] = [
        "codex-tools-1"
    ]

    duplicate_eligible = request("canonical-duplicate-eligible")
    duplicate_eligible["payload"]["required_signals"]["rco"][
        "eligible_agents"
    ] = ["claude-rco-1", "claude-rco-1"]

    weak_signal = request("canonical-weak-signal")
    weak_rco = weak_signal["payload"]["required_signals"]["rco"]
    weak_rco["type"] = "message"
    weak_rco["status"] = "acknowledged"
    weak_rco["message_must_contain_head"] = "ack"

    non_mapping_template = request("canonical-non-mapping-template")
    non_mapping_template["payload"]["required_signals"]["rco"]["payload"] = [
        head
    ]
    missing_operator_template = request("canonical-missing-operator-template")
    missing_operator_template["payload"]["required_signals"]["rco"][
        "payload"
    ].pop("operator_gated")
    false_operator_template = request("canonical-false-operator-template")
    false_operator_template["payload"]["required_signals"]["rco"]["payload"][
        "operator_gated"
    ] = False
    unsafe_template_value = request("canonical-unsafe-template-value")
    unsafe_template_value["payload"]["required_signals"]["rco"]["payload"][
        "review_scope"
    ] = ["source", "tests"]

    mismatched_task = request("canonical-event-task")
    mismatch_payload = mismatched_task["payload"]
    mismatch_payload["canonical_task_id"] = "canonical-different-task"
    mismatch_rco = mismatch_payload["required_signals"]["rco"]
    mismatch_rco["task_id"] = "canonical-different-task"
    mismatch_rco["payload"]["canonical_task_id"] = "canonical-different-task"

    wrong_type = request("canonical-wrong-type")
    wrong_type["type"] = "finding"
    wrong_status = request("canonical-wrong-status")
    wrong_status["status"] = "note"

    missing_message = request("canonical-missing-message")
    missing_message.pop("message")
    non_string_message = request("canonical-non-string-message")
    non_string_message["message"] = 42
    list_message = request("canonical-list-message")
    list_message["message"] = [head]
    message_without_head = request("canonical-message-without-head")
    message_without_head["message"] = "Exact-head consensus review requested."

    missing_agent = request("canonical-missing-agent")
    missing_agent.pop("agent")
    missing_agent["author"] = "codex-lead-1"

    uppercase_schema = request("canonical-uppercase-schema")
    uppercase_schema["payload"]["schema"] = "WD.EXACT_HEAD_CONSENSUS_REQUEST.V1"

    unsafe_task = request("../unsafe")

    uppercase_to = request("canonical-uppercase-to")
    uppercase_to["to"] = "codex-tools-1,CLAUDE-RCO-1"
    spaced_to = request("canonical-spaced-to")
    spaced_to["to"] = "codex-tools-1, claude-rco-1"

    alpha_head = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
    uppercase_head = _canonical_rco_request(
        task_id="canonical-uppercase-head",
        head=alpha_head.upper(),
    )
    wrapped_head = _canonical_rco_request(
        task_id="canonical-wrapped-head",
        head=alpha_head,
    )
    wrapped_head["payload"]["head"] = f" {alpha_head} "

    uppercase_signal = _canonical_rco_request(
        task_id="canonical-uppercase-signal",
        head=alpha_head,
    )
    uppercase_signal_rco = uppercase_signal["payload"]["required_signals"][
        "rco"
    ]
    uppercase_signal_rco["type"] = "DECISION"
    uppercase_signal_rco["status"] = "RCO_PASS"
    uppercase_signal_rco["message_must_contain_head"] = alpha_head.upper()
    uppercase_signal_rco["payload"]["head"] = alpha_head.upper()

    wrapped_task = request(" canonical-wrapped-task ")

    for malformed in (
        missing_head,
        tools_as_rco,
        duplicate_eligible,
        weak_signal,
        non_mapping_template,
        missing_operator_template,
        false_operator_template,
        unsafe_template_value,
        mismatched_task,
        wrong_type,
        wrong_status,
        missing_message,
        non_string_message,
        list_message,
        message_without_head,
        missing_agent,
        uppercase_schema,
        unsafe_task,
        uppercase_to,
        spaced_to,
        uppercase_head,
        wrapped_head,
        uppercase_signal,
        wrapped_task,
    ):
        task_id = str(malformed["task_id"])
        ack = _event(
            ts_utc="2026-05-17T11:05:00Z",
            agent="claude-rco-1",
            type="message",
            task_id=task_id,
            status="acknowledged",
            message=f"Acknowledged request at {head}.",
            payload={"head": head, "canonical_task_id": task_id},
        )
        tools_pass = _event(
            ts_utc="2026-05-17T11:06:00Z",
            agent="codex-tools-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            message=f"RCO PASS at exact head {head}.",
            payload={"head": head, "canonical_task_id": task_id},
        )

        report = _run(
            tmp_path,
            _base_idle_events() + [malformed, ack, tools_pass],
        )

        assert report["decision"] == "active"
        assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]

    missing_task = request("canonical-missing-event-task")
    missing_task["task_id"] = ""
    missing_task_report = _run(tmp_path, _base_idle_events() + [missing_task])
    missing_task_ids = missing_task_report["criteria"]["open_rco_requests"][
        "task_ids"
    ]
    assert missing_task_report["decision"] == "active"
    assert len(missing_task_ids) == 1
    assert missing_task_ids[0].startswith("invalid-canonical-request-line-")

    whitespace_task = request("canonical-whitespace-event-task")
    whitespace_task["task_id"] = "   "
    whitespace_task_report = _run(
        tmp_path,
        _base_idle_events() + [whitespace_task],
    )
    whitespace_task_ids = whitespace_task_report["criteria"][
        "open_rco_requests"
    ]["task_ids"]
    assert whitespace_task_report["decision"] == "active"
    assert len(whitespace_task_ids) == 1
    assert whitespace_task_ids[0].startswith("invalid-canonical-request-line-")


def test_canonical_aliases_cannot_supply_task_or_agent_identity(
    tmp_path: Path,
) -> None:
    head = "7777777777777777777777777777777777777777"

    request_with_id_alias = _canonical_rco_request(
        task_id="canonical-request-id-alias",
        head=head,
    )
    request_with_id_alias["task_id"] = ""
    request_with_id_alias["id"] = "canonical-request-id-alias"
    request_alias_report = _run(
        tmp_path,
        _base_idle_events() + [request_with_id_alias],
    )
    alias_task_ids = request_alias_report["criteria"]["open_rco_requests"][
        "task_ids"
    ]
    assert request_alias_report["decision"] == "active"
    assert len(alias_task_ids) == 1
    assert alias_task_ids[0].startswith("invalid-canonical-request-line-")

    request_with_author_alias = _canonical_rco_request(
        task_id="canonical-request-author-alias",
        head=head,
    )
    request_with_author_alias["author"] = request_with_author_alias.pop("agent")
    request_author_report = _run(
        tmp_path,
        _base_idle_events() + [request_with_author_alias],
    )
    assert request_author_report["criteria"]["open_rco_requests"][
        "task_ids"
    ] == ["canonical-request-author-alias"]

    task_id = "canonical-response-aliases"
    valid_request = _canonical_rco_request(task_id=task_id, head=head)
    id_alias_response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id="",
        status="rco_pass",
        message=f"RCO PASS at exact head {head}.",
        payload={"head": head, "canonical_task_id": task_id},
    )
    id_alias_response["id"] = task_id
    author_alias_response = _event(
        ts_utc="2026-05-17T11:06:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        message=f"RCO PASS at exact head {head}.",
        payload={"head": head, "canonical_task_id": task_id},
    )
    author_alias_response["author"] = author_alias_response.pop("agent")

    response_alias_report = _run(
        tmp_path,
        _base_idle_events()
        + [valid_request, id_alias_response, author_alias_response],
    )
    assert response_alias_report["criteria"]["open_rco_requests"][
        "task_ids"
    ] == [task_id]


def test_near_canonical_requests_hold_unresolved_without_false_legacy_target(
    tmp_path: Path,
) -> None:
    head = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    near_requests: list[dict[str, Any]] = []

    misspelled_schema = _canonical_rco_request(
        task_id="near-canonical-misspelled-schema",
        head=head,
    )
    misspelled_schema["payload"]["schema"] = (
        "wd.exact_head_consensus_requset.v1"
    )
    near_requests.append(misspelled_schema)

    non_string_schema = _canonical_rco_request(
        task_id="near-canonical-non-string-schema",
        head=head,
    )
    non_string_schema["payload"]["schema"] = [
        "wd.exact_head_consensus_request.v1"
    ]
    near_requests.append(non_string_schema)

    missing_schema = _canonical_rco_request(
        task_id="near-canonical-missing-schema",
        head=head,
    )
    missing_schema["payload"].pop("schema")
    near_requests.append(missing_schema)

    tools_only_contract = _canonical_rco_request(
        task_id="near-canonical-tools-only",
        head=head,
        to="codex-tools-1",
    )
    tools_only_contract["payload"]["schema"] = "wd.exact_head_consensus.v1"
    near_requests.append(tools_only_contract)

    bundled_tools_contract = _event(
        ts_utc="2026-05-17T11:00:00Z",
        type="wake_request",
        task_id="near-canonical-bundled-tools-only",
        status="review_requested",
        to="codex-tools-1",
        message="Tools-only request carries a bundled canonical contract.",
        payload={
            "schema": "wd.exact_head_consensus_requset.v1",
            "request_only": True,
            "canonical_task_id": "near-canonical-bundled-tools-only",
            "head": head,
        },
    )
    near_requests.append(bundled_tools_contract)

    malformed_schema_rco_target = _event(
        ts_utc="2026-05-17T11:00:00Z",
        type="wake_request",
        task_id="near-canonical-malformed-schema-rco-target",
        status="review_requested",
        to="claude-rco-1",
        message="RCO-targeted wake carries a malformed schema marker.",
        payload={"schema": "wd.exact_head_consensus_requset.v1"},
    )
    near_requests.append(malformed_schema_rco_target)

    for suffix, required_rco in (
        ("lowercase-list", {"rco": []}),
        ("lowercase-string", {"rco": "bad"}),
        ("uppercase-mapping", {"RCO": {}}),
        ("spaced-list", {" rco ": []}),
        ("scalar-string", "rco"),
        ("list-token", ["rco"]),
        ("list-mapping-key", [{"RCO": {}}]),
        ("nested-mapping-key", {"build_tools": {"RCO": {}}}),
    ):
        malformed_required_rco = _event(
            ts_utc="2026-05-17T11:00:00Z",
            type="wake_request",
            task_id=f"near-canonical-required-rco-{suffix}",
            status="review_requested",
            to="codex-tools-1",
            message="Tools-only request carries a malformed RCO signal.",
            payload={
                "schema": "wd.exact_head_tools_review_request.v1",
                "required_signals": required_rco,
            },
        )
        near_requests.append(malformed_required_rco)

    for suffix, schema_value in (
        ("list-token", ["wd.exact_head_consensus_request.v1"]),
        ("mapping-token", {"value": "wd.exact_head_consensus_request.v1"}),
        (
            "mixed-tools-rco-token",
            [
                "wd.exact_head_tools_review_request.v1",
                "wd.exact_head_consensus_request.v1",
            ],
        ),
    ):
        schema_container_request = _event(
            ts_utc="2026-05-17T11:00:00Z",
            type="wake_request",
            task_id=f"near-canonical-schema-{suffix}",
            status="review_requested",
            to="codex-tools-1",
            message="Tools-only request carries a canonical schema token.",
            payload={"schema": schema_value},
        )
        near_requests.append(schema_container_request)

    for request in near_requests:
        task_id = str(request["task_id"])
        apparent_response = _event(
            ts_utc="2026-05-17T11:05:00Z",
            agent="claude-rco-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            to="codex-lead-1",
            message=f"RCO PASS at exact head {head}.",
            payload={"head": head, "canonical_task_id": task_id},
        )
        report = _run(
            tmp_path,
            _base_idle_events() + [request, apparent_response],
        )
        assert report["criteria"]["open_rco_requests"]["task_ids"] == [
            task_id
        ]

    legacy_rco_task = "legacy-explicit-rco-target"
    legacy_rco_request = _event(
        ts_utc="2026-05-17T11:00:00Z",
        type="wake_request",
        task_id=legacy_rco_task,
        status="review_requested",
        to="claude-rco-1",
        message="Genuine legacy RCO-targeted wake has no canonical evidence.",
        payload={},
    )
    legacy_rco_response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=legacy_rco_task,
        status="completed",
        to="codex",
        message="Legacy RCO target completed the review.",
    )
    legacy_rco_report = _run(
        tmp_path,
        _base_idle_events() + [legacy_rco_request, legacy_rco_response],
    )
    assert legacy_rco_report["criteria"]["open_rco_requests"]["task_ids"] == []

    ordinary_tools_payloads = [
        {},
        {"schema": "third.party.tools.review.v1"},
        {"schema": 42},
        {"schema": None},
        {"schema": ["third.party.tools.review.v1"]},
        {"request_only": True},
        {"approval_asserted": False},
        {
            "request_only": True,
            "approval_asserted": False,
            "canonical_task_id": "ordinary-tools-control-bundle",
            "head": head,
        },
        {
            "schema": "third.party.exact_head_tools_review.v1",
            "request_only": True,
            "approval_asserted": False,
            "canonical_task_id": "ordinary-third-party-tools-review",
            "head": head,
        },
        {
            "request_only": True,
            "approval_asserted": False,
            "canonical_task_id": 7,
            "head": head,
        },
        {
            "request_only": True,
            "approval_asserted": False,
            "canonical_task_id": "ordinary-tools-type-confused-head",
            "head": 7,
        },
        {
            "required_signals": {
                "build_tools": {"type": "decision", "status": "pass"}
            }
        },
        {
            "required_signals": {
                "build_tools": {"task_id": "bridge/rco-delivery-fix"}
            }
        },
        {
            "required_signals": {
                "build_tools": {"agent": "claude-rco-1"}
            }
        },
        {
            "required_signals": {
                "build_tools": {"message": "RCO review remains separate."}
            }
        },
        {"required_signals": {"build_tools": ["rco"]}},
    ]
    for index, payload in enumerate(ordinary_tools_payloads):
        legacy_tools_only = _event(
            ts_utc="2026-05-17T10:00:00Z",
            type="wake_request",
            task_id=f"legacy-tools-only-review-{index}",
            status="review_requested",
            to="codex-tools-1",
            message="Ordinary Tools-only review is not an RCO request.",
            payload=payload,
        )
        legacy_report = _run(
            tmp_path,
            _base_idle_events() + [legacy_tools_only],
        )
        assert legacy_report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_unresolved_target_sentinel_cannot_forge_a_response(tmp_path: Path) -> None:
    legacy_task = "legacy-targetless-rco"
    legacy_request = _event(
        ts_utc="2026-05-16T10:00:00Z",
        type="handoff",
        task_id=legacy_task,
        status="rco_requested",
        to="",
        message="RCO review requested without a resolvable target.",
    )
    forged_legacy_response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="__unresolved_deliberation_target__",
        type="decision",
        task_id=legacy_task,
        status="rco_pass",
        message="Forged sentinel response.",
    )
    forged_requester_alias = _event(
        ts_utc="2026-05-17T11:06:00Z",
        agent="",
        type="decision",
        task_id=legacy_task,
        status="completed",
        message="Author alias must not impersonate the requester.",
    )
    forged_requester_alias["author"] = "codex"
    legacy_report = _run(
        tmp_path,
        _base_idle_events()
        + [legacy_request, forged_legacy_response, forged_requester_alias],
    )
    assert legacy_report["criteria"]["open_rco_requests"]["task_ids"] == [
        legacy_task
    ]

    canonical_task = "canonical-unresolved-sentinel"
    malformed_request = _canonical_rco_request(
        task_id=canonical_task,
        head="8888888888888888888888888888888888888888",
        to="codex-tools-1",
    )
    forged_canonical_response = _event(
        ts_utc="2026-05-17T11:06:00Z",
        agent="__unresolved_deliberation_target__",
        type="decision",
        task_id=canonical_task,
        status="rco_pass",
        message="Forged sentinel response.",
    )
    canonical_report = _run(
        tmp_path,
        _base_idle_events() + [malformed_request, forged_canonical_response],
    )
    assert canonical_report["criteria"]["open_rco_requests"]["task_ids"] == [
        canonical_task
    ]


def test_negative_requester_statuses_do_not_close_canonical_request(
    tmp_path: Path,
) -> None:
    task_id = "canonical-negative-requester-status"
    head = "9999999999999999999999999999999999999999"
    request = _canonical_rco_request(task_id=task_id, head=head)
    statuses = (
        "not_resolved",
        "never_resolved",
        "not_closed",
        "not_done",
        "not_merged",
        "not_superseded",
    )
    negative_events = [
        _event(
            ts_utc=f"2026-05-17T11:{index:02d}:00Z",
            agent="codex-lead-1",
            type="decision",
            task_id=task_id,
            status=status,
            message=f"Negative requester state: {status}.",
        )
        for index, status in enumerate(statuses, start=1)
    ]
    negative_events.extend(
        [
            _event(
                ts_utc="2026-05-17T11:10:00Z",
                agent="codex-lead-1",
                type="release",
                task_id=task_id,
                status="not_done",
                message="Release is explicitly not done.",
            ),
            _event(
                ts_utc="2026-05-17T11:11:00Z",
                agent="codex-lead-1",
                type="done",
                task_id=task_id,
                status="never_resolved",
                message="Done marker is explicitly negated.",
            ),
            _event(
                ts_utc="2026-05-17T11:12:00Z",
                agent="codex-lead-1",
                type="done",
                task_id=task_id,
                status="working",
                message="Done type alone is not a closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:13:00Z",
                agent="codex-lead-1",
                type="release",
                task_id=task_id,
                status="working",
                message="Release type alone is not a closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:14:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="cannot_be_resolved",
                message="This request cannot be resolved.",
            ),
            _event(
                ts_utc="2026-05-17T11:15:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="notyet_resolved",
                message="This request is not yet resolved.",
            ),
            _event(
                ts_utc="2026-05-17T11:16:00Z",
                agent="codex-lead-1",
                type="message",
                task_id=task_id,
                status="resolved",
                message="Message/resolved is not an approved requester closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:17:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="done_not",
                message="A terminal stem with a negative suffix is not closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:18:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="completed_working",
                message="A working suffix contradicts terminal completion.",
            ),
            _event(
                ts_utc="2026-05-17T11:19:00Z",
                agent="codex-lead-1",
                type="message",
                task_id=task_id,
                status="cancelled_not",
                message="A negative cancellation suffix is not closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:20:00Z",
                agent="codex-lead-1",
                type="DECISION",
                task_id=task_id,
                status="completed",
                message="Uppercase event types are not canonical closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:21:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="COMPLETED",
                message="Uppercase statuses are not canonical closure.",
            ),
            _event(
                ts_utc="2026-05-17T11:22:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="done_cannot_complete",
                message="Cannot-complete suffix contradicts done.",
            ),
            _event(
                ts_utc="2026-05-17T11:23:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="completed_in_progress",
                message="In-progress suffix contradicts completed.",
            ),
            _event(
                ts_utc="2026-05-17T11:24:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="completed_failed",
                message="Failed suffix contradicts completed.",
            ),
            _event(
                ts_utc="2026-05-17T11:25:00Z",
                agent="codex-lead-1",
                type="decision",
                task_id=task_id,
                status="approved_failure",
                message="Failure suffix contradicts approved.",
            ),
        ]
    )

    report = _run(tmp_path, _base_idle_events() + [request] + negative_events)

    assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("decision", "completed"),
        ("decision", "cancelled"),
        ("decision", "superseded_requested"),
        ("message", "cancelled_by_requester"),
    ],
)
def test_exact_positive_requester_closure_shapes_close_canonical_request(
    tmp_path: Path,
    event_type: str,
    status: str,
) -> None:
    task_id = f"canonical-requester-{event_type}-{status}"
    head = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    request = _canonical_rco_request(task_id=task_id, head=head)
    closure = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="codex-lead-1",
        type=event_type,
        task_id=task_id,
        status=status,
        message="Requester emitted an approved terminal closure shape.",
    )

    report = _run(tmp_path, _base_idle_events() + [request, closure])

    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_canonical_response_requires_strict_raw_head_type_and_status(
    tmp_path: Path,
) -> None:
    task_id = "canonical-strict-response-fields"
    head = "abcdefabcdefabcdefabcdefabcdefabcdefabcd"
    request = _canonical_rco_request(task_id=task_id, head=head)
    uppercase_head_response = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        message=f"RCO PASS at exact head {head.upper()}.",
        payload={"head": head.upper(), "canonical_task_id": task_id},
    )
    uppercase_signal_response = _event(
        ts_utc="2026-05-17T11:06:00Z",
        agent="claude-rco-1",
        type="DECISION",
        task_id=task_id,
        status="RCO_PASS",
        message=f"RCO PASS at exact head {head}.",
        payload={"head": head, "canonical_task_id": task_id},
    )
    wrapped_task_response = _event(
        ts_utc="2026-05-17T11:07:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=f" {task_id} ",
        status="rco_pass",
        message=f"RCO PASS at exact head {head}.",
        payload={"head": head, "canonical_task_id": task_id},
    )
    non_string_message_response = _event(
        ts_utc="2026-05-17T11:08:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        to="codex-lead-1",
        message="placeholder",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )
    non_string_message_response["message"] = [head]
    empty_message_response = _event(
        ts_utc="2026-05-17T11:08:30Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        to="codex-lead-1",
        message="",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )
    invalid_to_responses = [
        _event(
            ts_utc=f"2026-05-17T11:{minute:02d}:00Z",
            agent="claude-rco-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            to=recipient,
            message=f"RCO PASS at exact head {head}.",
            payload={
                "head": head,
                "canonical_task_id": task_id,
                "operator_gated": True,
            },
        )
        for minute, recipient in enumerate(
            ("", "operator", "CODEX-LEAD-1", "codex-lead-1,operator"),
            start=9,
        )
    ]

    report = _run(
        tmp_path,
        _base_idle_events()
        + [
            request,
            uppercase_head_response,
            uppercase_signal_response,
            wrapped_task_response,
            non_string_message_response,
            empty_message_response,
            *invalid_to_responses,
        ],
    )

    assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]


def test_canonical_response_binds_every_required_payload_literal(
    tmp_path: Path,
) -> None:
    task_id = "canonical-response-payload-template"
    head = "cccccccccccccccccccccccccccccccccccccccc"
    request = _canonical_rco_request(task_id=task_id, head=head)
    template = request["payload"]["required_signals"]["rco"]["payload"]
    template["review_tier"] = 1
    template["review_label"] = "formal"

    invalid_payloads = [
        {
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
            "review_tier": 1,
        },
        {
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
            "review_tier": True,
            "review_label": "formal",
        },
        {
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
            "review_tier": 1,
            "review_label": "informal",
        },
        {
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": False,
            "review_tier": 1,
            "review_label": "formal",
        },
    ]
    for index, payload in enumerate(invalid_payloads, start=1):
        response = _event(
            ts_utc=f"2026-05-17T11:{index:02d}:00Z",
            agent="claude-rco-1",
            type="decision",
            task_id=task_id,
            status="rco_pass",
            to="codex-lead-1",
            message=f"RCO PASS at exact head {head}.",
            payload=payload,
        )
        report = _run(tmp_path, _base_idle_events() + [request, response])
        assert report["criteria"]["open_rco_requests"]["task_ids"] == [
            task_id
        ]

    valid_response = _event(
        ts_utc="2026-05-17T11:10:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        to="codex-lead-1",
        message=f"RCO PASS at exact head {head}.",
        payload={
            "head": head,
            "canonical_task_id": task_id,
            "operator_gated": True,
            "review_tier": 1,
            "review_label": "formal",
        },
    )
    valid_report = _run(
        tmp_path,
        _base_idle_events() + [request, valid_response],
    )
    assert valid_report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_exact_head_rco_request_does_not_age_out(tmp_path: Path) -> None:
    task_id = "canonical-rco-sticky-hold"
    head = "2222222222222222222222222222222222222222"
    request = _canonical_rco_request(
        ts_utc="2026-05-16T10:00:00Z",
        task_id=task_id,
        head=head,
    )

    report = _run(tmp_path, _base_idle_events() + [request])

    assert report["decision"] == "active"
    assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]
    assert task_id not in report["criteria"]["stale_open_requests_ignored"][
        "task_ids"
    ]


def test_resolved_finding_is_not_reopened_as_rco_request(tmp_path: Path) -> None:
    resolved = _event(
        ts_utc="2026-05-17T11:00:00Z",
        agent="claude-rco-2",
        type="finding",
        task_id="rco-lane-failover-scout-resolved",
        status="rco1_active_no_failover_needed_resolved",
        to="claude-rco-1",
        message="RCO1 is active; no failover is needed and the scout is resolved.",
    )

    report = _run(tmp_path, _base_idle_events() + [resolved])

    assert report["decision"] == "idle"
    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_requester_can_cancel_exact_head_rco_without_repeating_head(
    tmp_path: Path,
) -> None:
    task_id = "canonical-rco-cancelled"
    head = "3333333333333333333333333333333333333333"
    request = _canonical_rco_request(
        task_id=task_id,
        head=head,
    )
    cancellation = _event(
        ts_utc="2026-05-17T11:05:00Z",
        agent="codex-lead-1",
        type="decision",
        task_id=task_id,
        status="cancelled_by_requester",
        to="claude-rco-1",
        message="The formal review request is explicitly cancelled.",
    )

    report = _run(tmp_path, _base_idle_events() + [request, cancellation])

    assert report["decision"] == "idle"
    assert report["criteria"]["open_rco_requests"]["task_ids"] == []


def test_new_exact_head_request_invalidates_prior_same_task_response(
    tmp_path: Path,
) -> None:
    task_id = "canonical-rco-reissued"
    first_head = "4444444444444444444444444444444444444444"
    second_head = "5555555555555555555555555555555555555555"

    first_pass = _event(
        ts_utc="2026-05-17T10:55:00Z",
        agent="claude-rco-1",
        type="decision",
        task_id=task_id,
        status="rco_pass",
        to="codex-lead-1",
        message=f"RCO PASS at exact head {first_head}.",
        payload={
            "head": first_head,
            "canonical_task_id": task_id,
            "operator_gated": True,
        },
    )
    events = _base_idle_events() + [
        _canonical_rco_request(
            task_id=task_id,
            head=first_head,
            ts_utc="2026-05-17T10:50:00Z",
        ),
        first_pass,
        _canonical_rco_request(task_id=task_id, head=second_head),
    ]

    report = _run(tmp_path, events)

    assert report["decision"] == "active"
    assert report["criteria"]["open_rco_requests"]["task_ids"] == [task_id]


def test_retroactive_stale_rco_closure_does_not_count_as_recent_merge(
    tmp_path: Path,
) -> None:
    events = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T10:00:00Z",
            type="handoff",
            task_id="stale-rco-pr508",
            status="rco_requested",
            message="RCO requested for PR #508.",
        ),
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            agent="claude",
            type="done",
            task_id="stale-rco-pr508",
            status="merged",
            to="codex,operator",
            message=(
                "Retroactive close of stale RCO handoff for task stale-rco-pr508. "
                "PR #508 was merged earlier into main (merge commit 1e6ec5b0). "
                "Structural fix follows as a separate PR."
            ),
        ),
    ]

    payload = _run(tmp_path, events)

    assert payload["decision"] == "idle"
    assert payload["blockers"] == []
    assert payload["criteria"]["open_rco_requests"]["ok"] is True
    assert payload["criteria"]["recent_merge"]["ok"] is True


def test_stale_unclosed_request_is_reported_but_does_not_block_idle(
    tmp_path: Path,
) -> None:
    stale_scout = _base_idle_events() + [
        _event(
            ts_utc="2026-05-16T12:00:00Z",
            task_id="claude-scout-stale-historical-record",
            status="request_scout",
            message="Historical scout request missing its terminal bridge event.",
        )
    ]

    payload = _run(tmp_path, stale_scout)

    assert payload["decision"] == "idle"
    assert payload["criteria"]["stale_open_requests_ignored"]["task_ids"] == [
        "claude-scout-stale-historical-record"
    ]


def test_waived_invalid_timestamp_line_does_not_block_idle(tmp_path: Path) -> None:
    mod = importlib.import_module("tools.idle_check")
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    valid_events = _base_idle_events()
    bad_event = _event(
        ts_utc="2026-06-20T20.34.44.2307986Z",
        status="passed_with_hygiene_warning",
        message="Historical bad timestamp event covered by an append-only waiver.",
    )
    bad_line = json.dumps(bad_event, sort_keys=True)
    events_path.write_text(
        "\n".join(
            [
                json.dumps(valid_events[0], sort_keys=True),
                bad_line,
                json.dumps(valid_events[1], sort_keys=True),
                json.dumps(valid_events[2], sort_keys=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    waivers_path = tmp_path / "waivers.json"
    waivers_path.write_text(
        json.dumps(
            {
                "schema_version": "agent-bridge-event-waivers.v1",
                "waivers": [
                    {
                        "line_no": 2,
                        "raw_line_sha256": "sha256:"
                        + hashlib.sha256(bad_line.encode("utf-8")).hexdigest(),
                        "error": "line 2: ts_utc: Value error, ts_utc must be ISO-8601",
                        "reason": "append-only historical event is covered by waiver",
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mod.main(
            [
                "--events",
                str(events_path),
                "--claims-dir",
                str(claims_dir),
                "--now",
                NOW,
                "--waivers",
                str(waivers_path),
                "--json",
            ]
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["decision"] == "idle"
    assert payload["criteria"]["invalid_events"] == {"ok": True, "invalid_lines": 0}


def test_unwaived_invalid_timestamp_line_blocks_idle(tmp_path: Path) -> None:
    mod = importlib.import_module("tools.idle_check")
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    valid_events = _base_idle_events()
    bad_event = _event(
        ts_utc="2026-06-20T20.34.44.2307986Z",
        status="passed_with_hygiene_warning",
        message="Historical bad timestamp event without a matching waiver.",
    )
    events_path.write_text(
        "\n".join(
            [
                json.dumps(valid_events[0], sort_keys=True),
                json.dumps(bad_event, sort_keys=True),
                json.dumps(valid_events[1], sort_keys=True),
                json.dumps(valid_events[2], sort_keys=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mod.main(
            [
                "--events",
                str(events_path),
                "--claims-dir",
                str(claims_dir),
                "--now",
                NOW,
                "--no-waivers",
                "--json",
            ]
        )

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["decision"] == "active"
    assert "invalid_events" in payload["blockers"]
    assert payload["criteria"]["invalid_events"] == {"ok": False, "invalid_lines": 1}


def test_short_cron_poll_is_ignored_but_recent_substantive_message_is_active(
    tmp_path: Path,
) -> None:
    cron_only = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:50:00Z",
            agent="claude",
            status="cron_poll",
            message="cron poll heartbeat",
        )
    ]
    payload = _run(tmp_path, cron_only)
    assert payload["decision"] == "idle"

    substantive = cron_only + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            agent="codex",
            status="request_scout",
            message="Substantive idle dreaming implementation note with concrete scope and risks.",
        )
    ]
    payload = _run(tmp_path, substantive)
    assert payload["decision"] == "active"
    assert "recent_agent_message" in payload["blockers"]

    multi_agent = cron_only + [
        _event(
            ts_utc="2026-05-17T11:55:00Z",
            agent="codex-2",
            status="request_scout",
            message="Substantive multi-agent bridge note with concrete scope and risks.",
        )
    ]
    payload = _run(tmp_path, multi_agent)
    assert payload["decision"] == "active"
    assert "recent_agent_message" in payload["blockers"]


def test_recent_merge_and_recent_operator_activity_keep_bridge_active(
    tmp_path: Path,
) -> None:
    recent_merge = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:30:00Z",
            type="done",
            status="merged_postmerge_green",
            message="PR merged thirty minutes ago.",
        )
    ]
    payload = _run(tmp_path, recent_merge)
    assert payload["decision"] == "active"
    assert "recent_merge" in payload["blockers"]

    recent_operator = _base_idle_events() + [
        _event(
            ts_utc="2026-05-17T11:45:00Z",
            agent="operator",
            type="message",
            status="operator_note",
            to="codex",
            message="Operator bridge note fifteen minutes ago.",
        )
    ]
    payload = _run(tmp_path, recent_operator)
    assert payload["decision"] == "active"
    assert "recent_operator_activity" in payload["blockers"]


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    claims_dir.mkdir()
    _write_events(events_path, _base_idle_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_check.py"),
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--now",
            NOW,
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["decision"] == "idle"


def test_cli_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = importlib.import_module("tools.idle_check")
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_claims = runtime_bridge / "work_queue" / "claims"
    runtime_events.parent.mkdir(parents=True)
    runtime_claims.mkdir(parents=True)
    _write_events(runtime_events, _base_idle_events())

    shadow_root = tmp_path / "shadow"
    shadow_bridge = shadow_root / ".agent-bridge"
    shadow_events = shadow_bridge / "shared" / "events.jsonl"
    shadow_claims = shadow_bridge / "work_queue" / "claims"
    shadow_events.parent.mkdir(parents=True)
    shadow_claims.mkdir(parents=True)
    _write_events(
        shadow_events,
        _base_idle_events()
        + [
            _event(
                ts_utc="2026-05-17T11:55:00Z",
                type="done",
                status="merged_postmerge_green",
                message="Cwd-local shadow bridge would keep idle_check active.",
            )
        ],
    )

    monkeypatch.chdir(shadow_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = mod.main(["--now", NOW, "--json"])

    assert rc == 0
    payload = json.loads(stdout.getvalue())
    assert payload["decision"] == "idle"
    assert payload["events_path"] == str(runtime_events)
    assert payload["claims_dir"] == str(runtime_claims)


def test_empty_bridge_returns_unknown_error(tmp_path: Path) -> None:
    mod = importlib.import_module("tools.idle_check")
    events_path = tmp_path / "events.jsonl"
    claims_dir = tmp_path / "claims"
    events_path.write_text("", encoding="utf-8")
    claims_dir.mkdir()

    rc = mod.main(
        [
            "--events",
            str(events_path),
            "--claims-dir",
            str(claims_dir),
            "--now",
            NOW,
            "--json",
        ]
    )

    assert rc == 2
