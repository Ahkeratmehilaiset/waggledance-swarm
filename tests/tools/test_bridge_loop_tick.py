# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/bridge_loop_tick.py.

All bridge state is synthetic and the PR-status query is an injected fake, so
no real GitHub call fires and the live repo is never touched.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tools.bridge_loop_tick import (
    WAKEUP_ACT_NOW,
    WAKEUP_QUIET,
    build_loop_tick,
    evaluate_merge_ready,
    my_unmerged_rco_passes,
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


def test_loop_tick_quiet_long_wakeup(tmp_path):
    report = build_loop_tick(
        agent="claude", events=[], claims=[], inbox_dir=tmp_path,
        now_utc=NOW, snapshot_fn=None,
    )
    assert report["next_action"] == "claim_unblocked_work"
    assert report["merge_ready"] == []
    assert report["recommended_wakeup_seconds"] == WAKEUP_QUIET


def test_loop_tick_open_peer_rco_is_answer_incoming(tmp_path):
    events = [_rco_request("t2", ts="2026-05-22T13:50:00Z")]
    report = build_loop_tick(
        agent="claude", events=events, claims=[], inbox_dir=tmp_path,
        now_utc=NOW, snapshot_fn=None,
    )
    assert report["next_action"] == "answer_incoming"
    assert report["recommended_wakeup_seconds"] == WAKEUP_ACT_NOW
