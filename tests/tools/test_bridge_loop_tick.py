# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/bridge_loop_tick.py.

All bridge state is synthetic and the PR-status query is an injected fake, so
no real GitHub call fires and the live repo is never touched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from tools.bridge_loop_tick import (
    WAKEUP_ACT_NOW,
    WAKEUP_IN_FLIGHT,
    WAKEUP_QUIET,
    build_loop_tick,
    emit_peer_activation_event,
    evaluate_merge_ready,
    my_unmerged_rco_passes,
    peer_activation_recommendation,
    peer_has_active_pr_producing_claim,
)

NOW = datetime(2026, 5, 22, 14, 0, 0, tzinfo=timezone.utc)
HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def _rco_request(task: str, *, frm: str = "codex", to: str = "claude", ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": frm,
        "to": to,
        "type": "handoff",
        "task_id": task,
        "status": "rco_requested",
        "message": "ready for RCO",
    }


def _rco_pass(task: str, *, pr: int, head: str, ts: str, frm: str = "claude") -> dict:
    return {
        "ts_utc": ts,
        "agent": frm,
        "to": "codex,operator",
        "type": "decision",
        "task_id": task,
        "status": "rco_pass",
        "message": "PASS",
        "payload": {"pr": pr, "head": head},
    }


def _done_merged(task: str, *, pr: int, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": "claude",
        "type": "done",
        "task_id": task,
        "status": "merged",
        "payload": {"pr": pr},
    }


def _heartbeat(agent: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "heartbeat",
        "task_id": f"{agent}-heartbeat",
        "status": "active",
        "message": f"{agent} heartbeat",
    }


def _finding(agent: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "finding",
        "task_id": f"{agent}-finding",
        "status": "reported",
        "message": "substantive work",
    }


def _peer_activation_sent(agent: str, peer: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "to": peer,
        "type": "handoff",
        "task_id": f"peer-activation-{peer}-wd-advantage-scout-2026-05-22-13-59",
        "status": "scout_requested",
        "message": "please scout",
    }


def _green_snapshot(pr: int, head: str = HEAD) -> dict:
    return {
        "pr_number": pr,
        "head_sha": head,
        "mergeable": "MERGEABLE",
        "checks": [
            {"name": "test", "state": "", "status": "COMPLETED", "conclusion": "SUCCESS"}
        ],
    }


# --- my_unmerged_rco_passes -------------------------------------------------

def test_unmerged_rco_pass_detected():
    events = [
        _rco_request("t1", ts="2026-05-22T13:00:00Z"),
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
    ]
    out = my_unmerged_rco_passes(events, agent="claude", now_utc=NOW)
    assert len(out) == 1
    assert out[0]["pr"] == 900 and out[0]["approved_head"] == HEAD


def test_merged_rco_pass_excluded():
    events = [
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        _done_merged("t1", pr=900, ts="2026-05-22T13:40:00Z"),
    ]
    assert my_unmerged_rco_passes(events, agent="claude", now_utc=NOW) == []


def test_stale_rco_pass_excluded_by_age():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-18T10:00:00Z")]
    # >48h before NOW
    assert my_unmerged_rco_passes(events, agent="claude", now_utc=NOW) == []


def test_merged_by_pr_number_excluded_despite_task_id_mismatch():
    # rco_pass under one task id; done/merged for the SAME pr under a slightly
    # different task id (20260522 vs 2026-05-22). The PR must clear anyway.
    events = [
        _rco_pass("t-20260522", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        _done_merged("t-2026-05-22", pr=900, ts="2026-05-22T13:40:00Z"),
    ]
    assert my_unmerged_rco_passes(events, agent="claude", now_utc=NOW) == []


def test_pass_without_pr_skipped():
    ev = _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")
    ev["payload"] = {"head": HEAD}  # no pr
    assert my_unmerged_rco_passes([ev], agent="claude", now_utc=NOW) == []


# --- evaluate_merge_ready ---------------------------------------------------

def _candidate(pr=900, head=HEAD, task="t1"):
    return {"task_id": task, "pr": pr, "approved_head": head}


def test_merge_ready_when_green_clean_headmatch():
    events = [_rco_request("t1", ts="2026-05-22T13:00:00Z"),
              _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        _candidate(), events=events, agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is True
    assert r["merge_command"] == f"gh pr merge 900 --squash --match-head-commit={HEAD}"


def test_merge_ready_with_short_approved_head_prefix():
    # Real bridge rco_pass payloads carry a short head; gh returns the full sha.
    short = "862d34bd"
    full = "862d34bd27c15b870242faf333f7961629137cb8"
    events = [_rco_pass("t1", pr=566, head=short, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        {"task_id": "t1", "pr": 566, "approved_head": short},
        events=events, agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr, head=full),
    )
    assert r["ready"] is True
    # Merge command pins the FULL sha, not the short approved head.
    assert r["merge_command"] == f"gh pr merge 566 --squash --match-head-commit={full}"


def test_not_ready_when_head_moved():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        _candidate(head=HEAD), events=events, agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr, head=OTHER_HEAD),
    )
    assert r["ready"] is False
    assert "head_moved_since_rco_pass" in r["blockers"]


def test_not_ready_when_checks_red():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    snap = _green_snapshot(900)
    snap["checks"] = [{"name": "test", "state": "", "status": "COMPLETED", "conclusion": "FAILURE"}]
    r = evaluate_merge_ready(
        _candidate(), events=events, agent="claude", snapshot_fn=lambda pr: snap,
    )
    assert r["ready"] is False and "checks_not_green" in r["blockers"]


def test_not_ready_when_mergeable_dirty():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    snap = _green_snapshot(900)
    snap["mergeable"] = "CONFLICTING"
    r = evaluate_merge_ready(
        _candidate(), events=events, agent="claude", snapshot_fn=lambda pr: snap,
    )
    assert r["ready"] is False
    assert any(b.startswith("mergeable_not_clean") for b in r["blockers"])


def test_not_ready_when_peer_block_standing():
    # Codex (peer) posted a later changes_requested -> preflight not clear.
    events = [
        _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z"),
        {"ts_utc": "2026-05-22T13:45:00Z", "agent": "codex", "type": "decision",
         "task_id": "t1", "status": "changes_requested", "message": "blocker"},
    ]
    r = evaluate_merge_ready(
        _candidate(), events=events, agent="claude",
        snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert r["ready"] is False
    assert "peer_block_or_changes_requested" in r["blockers"]


def test_no_snapshot_fn_is_unchecked_not_ready():
    events = [_rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    r = evaluate_merge_ready(
        _candidate(), events=events, agent="claude", snapshot_fn=None,
    )
    assert r["ready"] is False and "pr_status_unchecked" in r["blockers"]


# --- build_loop_tick + adaptive wakeup -------------------------------------

def test_loop_tick_merge_ready_short_wakeup(tmp_path):
    events = [_rco_request("t1", ts="2026-05-22T13:00:00Z"),
              _rco_pass("t1", pr=900, head=HEAD, ts="2026-05-22T13:30:00Z")]
    report = build_loop_tick(
        agent="claude", events=events, claims=[], inbox_dir=tmp_path,
        now_utc=NOW, snapshot_fn=lambda pr: _green_snapshot(pr),
    )
    assert report["ok"] is True
    ready = [m for m in report["merge_ready"] if m["ready"]]
    assert len(ready) == 1 and ready[0]["pr"] == 900
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW


def test_loop_tick_genuinely_quiet_uses_long_wakeup(tmp_path):
    report = build_loop_tick(
        agent="claude", events=[], claims=[], inbox_dir=tmp_path,
        now_utc=NOW, snapshot_fn=None,
    )
    assert report["next_action"] == "claim_unblocked_work"
    assert report["merge_ready"] == []
    assert report["recommended_wakeup_seconds"] == WAKEUP_QUIET
    assert report["wakeup_reason"] == "quiet; no pending bridge work"


def test_loop_tick_open_operator_pack_does_not_park_unblocked_work(tmp_path):
    (tmp_path / "pack.yaml").write_text(
        """\
schema_version: waggledance.operator_decision_pack.v1
decision_id: torch-cuda-vs-cpu
category: dependency_security
created_utc: 2026-05-22T14:00:00Z
author_agent: claude
options:
  - id: A1_cpu_only
  - id: A2_cu126
operator_signoff:
  signed_by: ""
  chosen_option: ""
""",
        encoding="utf-8",
    )

    report = build_loop_tick(
        agent="claude", events=[], claims=[], inbox_dir=tmp_path,
        now_utc=NOW, snapshot_fn=None,
    )

    assert [p["decision_id"] for p in report["open_operator_packs"]] == [
        "torch-cuda-vs-cpu"
    ]
    assert report["next_action"] == "claim_unblocked_work"
    assert report["recommended_wakeup_seconds"] == WAKEUP_IN_FLIGHT
    assert report["wakeup_reason"] == "operator pack open; check unblocked work soon"


def test_loop_tick_open_peer_rco_is_answer_incoming(tmp_path):
    events = [_rco_request("t2", ts="2026-05-22T13:50:00Z")]
    report = build_loop_tick(
        agent="claude", events=events, claims=[], inbox_dir=tmp_path,
        now_utc=NOW, snapshot_fn=None,
    )
    assert report["next_action"] == "answer_incoming"
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW


# --- peer activation --------------------------------------------------------

def test_peer_activation_needed_for_heartbeat_only_peer():
    events = [
        _finding("claude", "2026-05-22T13:00:00Z"),
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is True
    assert rec["peer"] == "claude"
    assert rec["reason"] == "peer_heartbeat_only_without_recent_substantive_work"
    assert rec["bridge_event"]["to"] == "claude"
    assert rec["bridge_event"]["status"] == "scout_requested"
    assert "highest-value PR candidate" in rec["bridge_event"]["message"]


def test_peer_activation_uses_operator_pack_safe_scout_when_packs_open():
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    packs = [{"decision_id": "torch-cuda-vs-cpu", "category": "dependency_security"}]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=packs,
        now_utc=NOW,
    )

    assert rec["needed"] is True
    assert "torch-cuda-vs-cpu" in rec["bridge_event"]["message"]
    assert "must stay fail-closed" in rec["bridge_event"]["message"]


def test_peer_activation_not_needed_when_peer_has_recent_substantive_work():
    events = [
        _finding("claude", "2026-05-22T13:45:00Z"),
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is False
    assert rec["reason"] == "peer_recently_substantive"


def test_peer_activation_not_needed_when_peer_has_claim():
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    claims = [{"agent": "claude", "task_id": "active"}]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=claims,
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is False
    assert rec["reason"] == "peer_has_active_claim"


def test_peer_activation_not_duplicated_when_recent_handoff_exists():
    events = [
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
        _peer_activation_sent("codex", "claude", "2026-05-22T13:59:00Z"),
    ]

    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    assert rec["needed"] is False
    assert rec["reason"] == "peer_activation_recently_sent"
    assert rec["last_activation_sent_at_utc"] == "2026-05-22T13:59:00Z"


def test_emit_peer_activation_writes_valid_bridge_event(tmp_path):
    events = [_heartbeat("claude", "2026-05-22T13:55:00Z")]
    rec = peer_activation_recommendation(
        agent="codex",
        events=events,
        claims=[],
        open_packs=[],
        now_utc=NOW,
    )

    events_path = emit_peer_activation_event(
        bridge_root=tmp_path,
        agent="codex",
        event_spec=rec["bridge_event"],
        now_utc=NOW,
    )

    line = events_path.read_text(encoding="utf-8").strip()
    event = json.loads(line)
    assert event["agent"] == "codex"
    assert event["to"] == "claude"
    assert event["type"] == "handoff"
    assert event["status"] == "scout_requested"
    assert event["payload"]["source"] == "bridge_loop_tick.peer_activation"
    assert (tmp_path / "outbox" / "codex" / "2026-05-22.jsonl").exists()
    assert json.loads((tmp_path / "shared" / "last_codex.json").read_text())[
        "task_id"
    ] == event["task_id"]


def test_loop_tick_short_wakeup_when_peer_activation_needed(tmp_path):
    events = [
        _finding("claude", "2026-05-22T13:00:00Z"),
        _heartbeat("claude", "2026-05-22T13:55:00Z"),
    ]
    report = build_loop_tick(
        agent="codex",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_activation"]["needed"] is True
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW
    assert report["wakeup_reason"] == "peer activation needed"


# --- peer active PR-producing claim ----------------------------------------

def _claim_active(agent: str, task: str, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "claim",
        "task_id": task,
        "status": "active",
        "message": f"{agent} working on {task}",
    }


def test_peer_active_claim_detected_when_recent_and_unclosed():
    events = [_claim_active("codex", "magma-slice-1", ts="2026-05-22T13:55:00Z")]

    result = peer_has_active_pr_producing_claim(
        events, agent="claude", now_utc=NOW
    )

    assert result["active"] is True
    assert result["peer"] == "codex"
    assert result["task_id"] == "magma-slice-1"
    assert result["event_type"] == "claim"
    assert result["event_status"] == "active"
    assert result["reason"] == "peer_has_active_pr_producing_claim"


def test_peer_active_claim_cleared_by_done_event():
    events = [
        _claim_active("codex", "magma-slice-1", ts="2026-05-22T13:55:00Z"),
        {
            "ts_utc": "2026-05-22T13:58:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": "magma-slice-1",
            "status": "merged",
            "payload": {"pr": 700},
        },
    ]

    result = peer_has_active_pr_producing_claim(
        events, agent="claude", now_utc=NOW
    )

    assert result["active"] is False
    assert result["reason"] == "peer_claim_closed_by_done"


def test_peer_active_claim_survives_intervening_non_terminal_event():
    """Per Codex RCO on PR #592: a later decision/clarification/finding from
    the same peer MUST NOT clear an active PR-producing claim. Only a
    terminal close (``type=done`` or status in PEER_TERMINAL_STATUSES) on
    the same task_id clears it."""
    events = [
        _claim_active("codex", "magma-slice-1", ts="2026-05-22T13:55:00Z"),
        {
            "ts_utc": "2026-05-22T13:57:00Z",
            "agent": "codex",
            "type": "decision",
            "task_id": "magma-slice-1",
            "status": "clarification",
            "message": "interim note, claim still open",
        },
        # An unrelated finding on a different task_id from the same peer
        # must also not clear the claim.
        _finding("codex", "2026-05-22T13:58:00Z"),
    ]

    result = peer_has_active_pr_producing_claim(
        events, agent="claude", now_utc=NOW
    )

    assert result["active"] is True
    assert result["task_id"] == "magma-slice-1"
    assert result["reason"] == "peer_has_active_pr_producing_claim"


def test_peer_active_claim_too_old_does_not_anticipate():
    events = [_claim_active("codex", "magma-slice-1", ts="2026-05-22T13:20:00Z")]

    result = peer_has_active_pr_producing_claim(
        events, agent="claude", now_utc=NOW
    )

    assert result["active"] is False
    assert result["reason"] == "peer_claim_event_too_old"


def test_loop_tick_in_flight_when_peer_has_active_pr_producing_claim(tmp_path):
    events = [_claim_active("codex", "magma-slice-2", ts="2026-05-22T13:55:00Z")]

    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_active_claim"]["active"] is True
    assert report["recommended_wakeup_seconds"] == WAKEUP_IN_FLIGHT
    assert (
        report["wakeup_reason"]
        == "peer has active PR-producing claim; anticipate"
    )


def test_loop_tick_quiet_when_peer_claim_already_done(tmp_path):
    events = [
        _claim_active("codex", "magma-slice-2", ts="2026-05-22T13:55:00Z"),
        {
            "ts_utc": "2026-05-22T13:58:00Z",
            "agent": "codex",
            "type": "done",
            "task_id": "magma-slice-2",
            "status": "merged",
            "payload": {"pr": 701},
        },
    ]

    report = build_loop_tick(
        agent="claude",
        events=events,
        claims=[],
        inbox_dir=tmp_path,
        now_utc=NOW,
        snapshot_fn=None,
    )

    assert report["peer_active_claim"]["active"] is False
    assert report["recommended_wakeup_seconds"] == WAKEUP_QUIET
