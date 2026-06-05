# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/check_rco_pass_present.py.

Covers the exact forged cases required for the RCO pass presence gate
(Rule 9a: RCO absence = NO merge). All tests offline/deterministic, no network.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_rco_pass_present.py"

sys.path.insert(0, str(ROOT))

from tools.check_rco_pass_present import (  # noqa: E402
    check_rco_pass_present as _check_rco_pass_present,
    DEFAULT_EVENTS_PATH,
)


def _seed_events(tmp_path: Path, events: list[dict]) -> Path:
    """Write a minimal events.jsonl under a temp .agent-bridge for CLI tests."""
    bridge = tmp_path / ".agent-bridge"
    shared = bridge / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")
    return events_path


def _rco_event(
    *,
    ts: str = "2026-06-03T12:00:00Z",
    agent: str = "claude-rco-1",
    type_: str = "decision",
    status: str = "rco_pass",
    task_id: str = "waggledance/grok-scout-1/rco-pass-presence-gate-20260603",
    message: str = "",
    payload: dict | None = None,
) -> dict:
    ev = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": message,
        "payload": payload or {},
        "severity": "",
        "to": "",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }
    return ev


HEAD = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_HEAD = "0000000000000000000000000000000000000000"
TASK = "waggledance/grok-scout-1/rco-pass-presence-gate-20260603"
AUTHOR = "codex-lead-1"


def check_rco_pass_present(*args, **kwargs):
    kwargs.setdefault("author_agent", AUTHOR)
    return _check_rco_pass_present(*args, **kwargs)


# --- unit tests on the library function -----------------------------------


def test_pass_at_head_present_returns_ok() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD} for the task.",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["latest_rco_is_veto"] is False
    assert result["rco_pass_event"] is not None
    # All claim gates false per hard rule
    for key in (
        "claim_gate_satisfied",
        "claim_safe",
        "literal_future_claim_safe",
        "controls_present",
        "runtime_authority_granted",
        "external_writes_applied",
        "required_runtime_evidence_present",
    ):
        assert result[key] is False


def test_no_pass_silence_refuses() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_requested",  # not a pass
            type_="handoff",
            message="review requested",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] in {"no_qualifying_pass", "rco_pass_absent"}
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_pass_at_different_old_head_is_stale_refuse() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {OTHER_HEAD}.",  # different head
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False
    assert result["decision"] == "no_qualifying_pass"


def test_changes_requested_after_pass_refuses() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="changes_requested",
            type_="decision",
            message="found issues after review",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True
    assert (
        result["has_qualifying_rco_pass_at_head"] is True
    )  # pass existed but superseded


def test_pass_present_no_later_veto_ok() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"approved at head {HEAD}",
        ),
        # a later non-veto non-pass signal does not supersede
        _rco_event(
            ts="2026-06-03T10:10:00Z",
            status="rco_pass_pending_ci",
            type_="decision",
            message="noted CI pending but already passed at head",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["decision"] == "rco_pass_present"


def test_wrong_rco_agent_identity_not_counted() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="codex-tools-1",  # wrong identity
            status="rco_pass",
            type_="decision",
            message=f"at head {HEAD}",
        ),
        _rco_event(
            ts="2026-06-03T10:01:00Z",
            agent="claude-rco-1",
            status="rco_requested",
            type_="handoff",
            message="request only",
        ),
    ]
    result = check_rco_pass_present(
        events=events, task_id=TASK, head=HEAD, rco_agent="claude-rco-1"
    )
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_backup_rco_pass_satisfies_default_rco_set() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"backup RCO_PASS at exact head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["satisfying_rco_agent"] == "claude-rco-2"
    assert result["eligible_rco_agents"] == ["claude-rco-1", "claude-rco-2"]


def test_author_rco_self_pass_is_excluded_fail_closed() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"self RCO_PASS at exact head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(
        events=events,
        task_id=TASK,
        head=HEAD,
        author_agent="claude-rco-2",
    )
    assert result["ok"] is False
    assert result["decision"] == "no_qualifying_pass"
    assert result["eligible_rco_agents"] == ["claude-rco-1"]
    assert result["satisfying_rco_agent"] is None


def test_veto_from_other_recognized_rco_blocks_backup_set() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-1",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
        ),
        _rco_event(
            ts="2026-06-03T10:01:00Z",
            agent="claude-rco-2",
            status="changes_requested",
            type_="finding",
            message="backup RCO veto at same head",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["blocking_rco_agents"] == ["claude-rco-2"]


def test_veto_then_fresh_pass_at_head_allows() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T09:00:00Z",
            status="changes_requested",
            type_="finding",
            message="initial block",
        ),
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="rco_review",
            message=f"re-reviewed; RCO_PASS at exact head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["latest_rco_is_veto"] is False


def test_type_blocked_counts_as_veto_after_pass() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"pass at {HEAD}",
        ),
        {
            "ts_utc": "2026-06-03T10:05:00Z",
            "agent": "claude-rco-1",
            "type": "blocked",
            "status": "blocked",
            "task_id": TASK,
            "message": "veto via blocked type",
            "payload": {},
        },
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"


def test_non_decision_type_pass_ignored() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="message",  # not decision/rco_review
            message=f"pass at head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_head_must_be_in_message_for_qualifying() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message="RCO_PASS (head not mentioned in text)",
            payload={
                "head": HEAD
            },  # payload does not count for this gate per spec "message"
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_other_task_events_ignored() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"pass at {HEAD}",
            task_id="some/other-task",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False


def test_wrong_status_not_pass() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass_pending_ci",  # not the strict {rco_pass}
            type_="decision",
            message=f"pass at {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


# --- CLI tests (subprocess, exit codes, output) ---------------------------


def test_cli_exit_0_when_pass_at_head_present(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--rco-agent",
            "claude-rco-1",
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, f"stderr={res.stderr} stdout={res.stdout}"
    assert "RCO_PASS present at exact head" in res.stdout


def test_cli_refuse_on_no_pass_silence(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(status="rco_requested", type_="handoff", message="request"),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    assert "REFUSED" in (res.stderr or "") or "REFUSED" in (res.stdout or "")


def test_cli_refuse_on_stale_head(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"pass at old head {OTHER_HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["has_qualifying_rco_pass_at_head"] is False
    for key in CLAIM_GATES:
        assert payload[key] is False


def test_cli_refuse_on_changes_requested_after_pass(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                ts="2026-06-03T10:00Z",
                status="rco_pass",
                type_="decision",
                message=f"pass {HEAD}",
            ),
            _rco_event(
                ts="2026-06-03T10:05Z",
                status="changes_requested",
                type_="decision",
                message="veto",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["decision"] == "vetoed_after_pass"
    assert payload["ok"] is False


def test_cli_exit_0_on_pass_present_no_later_veto(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"good at {HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0


def test_cli_wrong_rco_agent_not_counted(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                agent="someone-else",
                status="rco_pass",
                type_="decision",
                message=f"pass {HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--rco-agent",
            "claude-rco-1",
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["has_qualifying_rco_pass_at_head"] is False


def test_cli_missing_events_file_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / ".agent-bridge" / "shared" / "events.jsonl"
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(missing),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    # may be 3 or 2; must not be 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["claim_gate_satisfied"] is False


def test_cli_invalid_head_rejected(tmp_path: Path) -> None:
    events_path = _seed_events(tmp_path, [])
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            "not-a-40-char-sha",
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 2
    assert "40-char" in (res.stderr or "")


CLAIM_GATES = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)
