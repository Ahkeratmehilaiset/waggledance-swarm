# SPDX-License-Identifier: BUSL-1.1
"""Closure ordering and identity binding for the PS bridge resolver.

``Get-BridgeNextAction.ps1``'s ``Test-BridgeRequestStillOpen`` previously
decided "answered/closed" from a raw string ``ts_utc`` comparison and matched
requester closures by agent name + task_id alone. Confirmed exposures
(PR #1612/#1613 thread, 2026-08-20): a post-dated closure appended to the log
before the request suppressed it, and any writer sharing the requester's
agent name could close a request bound to a specific agent_uuid/session_id.

These tests lock the PowerShell consumer to the composite both-orders rule
(append order AND parsed timestamp order, with an append-order fallback for
unparseable timestamps) and to request-side identity binding, mirroring the
Python ``tools/bridge_next_action.py`` semantics
(``_closure_occurs_after_request`` / ``_requester_identity_matches``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
NEXT_ACTION = ROOT / ".agent-bridge" / "bin" / "Get-BridgeNextAction.ps1"

TASK = "ps-closure-parity-task"
AGENT = "fable-5"
REQUESTER = "codex-lead-1"
NOW = "2026-08-20T06:00:00Z"


def _powershell() -> str:
    executable = (
        shutil.which("pwsh")
        or shutil.which("powershell")
        or shutil.which("powershell.exe")
    )
    if executable is None:
        pytest.skip("PowerShell is required for the bridge resolver tests")
    return executable


def _request(ts: str = "2026-08-20T04:00:00Z", **extra: str) -> dict[str, str]:
    event = {
        "type": "message",
        "status": "request",
        "task_id": TASK,
        "to": AGENT,
        "agent": REQUESTER,
        "ts_utc": ts,
        "message": "please review",
    }
    event.update(extra)
    return event


def _requester_closure(
    ts: str = "2026-08-20T05:00:00Z", **extra: str
) -> dict[str, str]:
    event = {
        "type": "done",
        "status": "done",
        "task_id": TASK,
        "to": "",
        "agent": REQUESTER,
        "ts_utc": ts,
        "message": "requester closes its own request",
    }
    event.update(extra)
    return event


def _own_answer(ts: str = "2026-08-20T05:00:00Z", **extra: str) -> dict[str, str]:
    event = {
        "type": "decision",
        "status": "review_pass",
        "task_id": TASK,
        "to": REQUESTER,
        "agent": AGENT,
        "ts_utc": ts,
        "message": "target agent answers",
    }
    event.update(extra)
    return event


def _next_action(tmp_path: Path, events: list[dict[str, str]]) -> dict[str, object]:
    if not NEXT_ACTION.is_file():
        pytest.skip(f"next-action script not found at {NEXT_ACTION}")
    bridge_root = tmp_path / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
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
            AGENT,
            "-Now",
            NOW,
            "-Json",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_post_dated_requester_closure_appended_before_request_stays_open(
    tmp_path: Path,
) -> None:
    # The closure carries a later timestamp but sits earlier in the log than
    # the request it claims to close: it cannot have answered a request that
    # did not exist yet. The previous string ts_utc comparison closed this.
    report = _next_action(
        tmp_path,
        [_requester_closure(ts="2026-08-20T05:00:00Z"), _request()],
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_requester_closure_after_request_in_both_orders_closes(
    tmp_path: Path,
) -> None:
    report = _next_action(
        tmp_path,
        [_request(), _requester_closure(ts="2026-08-20T05:00:00Z")],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_post_dated_own_answer_appended_before_request_stays_open(
    tmp_path: Path,
) -> None:
    report = _next_action(
        tmp_path,
        [_own_answer(ts="2026-08-20T05:00:00Z"), _request()],
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_own_answer_after_request_in_both_orders_closes(tmp_path: Path) -> None:
    report = _next_action(
        tmp_path,
        [_request(), _own_answer(ts="2026-08-20T05:00:00Z")],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_unparseable_closure_timestamp_falls_back_to_append_order(
    tmp_path: Path,
) -> None:
    # Malformed timestamps keep the legacy append-order behavior in both
    # directions instead of silently never (or always) closing.
    closes = _next_action(
        tmp_path / "closure-after-request",
        [_request(), _requester_closure(ts="not-a-timestamp")],
    )
    assert closes["open_incoming_count"] == 0

    stays_open = _next_action(
        tmp_path / "closure-before-request",
        [_requester_closure(ts="not-a-timestamp"), _request()],
    )
    assert stays_open["open_incoming_count"] == 1


def test_higher_precision_closure_timestamp_still_closes(tmp_path: Path) -> None:
    # Raw string comparison ordered "…04:00:00.0000001Z" BEFORE
    # "…04:00:00Z" ('.' < 'Z'), so a genuinely later high-precision closure
    # was ignored. Parsed comparison must close it.
    report = _next_action(
        tmp_path,
        [
            _request(ts="2026-08-20T04:00:00Z"),
            _requester_closure(ts="2026-08-20T04:00:00.0000001Z"),
        ],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_requester_closure_bound_to_request_uuid_and_session(
    tmp_path: Path,
) -> None:
    # A writer that only shares the requester's agent name must not close a
    # request bound to a specific agent_uuid/session_id.
    report = _next_action(
        tmp_path,
        [
            _request(
                agent_uuid="11111111-1111-1111-1111-111111111111",
                session_id="wd-session-alpha",
            ),
            _requester_closure(
                ts="2026-08-20T05:00:00Z",
                agent_uuid="22222222-2222-2222-2222-222222222222",
                session_id="wd-session-beta",
            ),
        ],
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_requester_closure_matching_identity_closes(tmp_path: Path) -> None:
    report = _next_action(
        tmp_path,
        [
            _request(
                agent_uuid="11111111-1111-1111-1111-111111111111",
                session_id="wd-session-alpha",
            ),
            _requester_closure(
                ts="2026-08-20T05:00:00Z",
                agent_uuid="11111111-1111-1111-1111-111111111111",
                session_id="wd-session-alpha",
            ),
        ],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0


def test_uuid_binding_is_case_insensitive(tmp_path: Path) -> None:
    report = _next_action(
        tmp_path,
        [
            _request(agent_uuid="ABCDEF00-1111-1111-1111-111111111111"),
            _requester_closure(
                ts="2026-08-20T05:00:00Z",
                agent_uuid="abcdef00-1111-1111-1111-111111111111",
            ),
        ],
    )

    assert report["open_incoming_count"] == 0


def test_session_binding_is_case_sensitive(tmp_path: Path) -> None:
    report = _next_action(
        tmp_path,
        [
            _request(session_id="WD-Session-Alpha"),
            _requester_closure(
                ts="2026-08-20T05:00:00Z",
                session_id="wd-session-alpha",
            ),
        ],
    )

    assert report["action"] == "answer_incoming"
    assert report["open_incoming_count"] == 1


def test_legacy_request_without_identity_fields_closes_by_name(
    tmp_path: Path,
) -> None:
    # Requests written before identity fields existed must keep closing on
    # agent name + task_id alone, whatever identity the closure carries.
    report = _next_action(
        tmp_path,
        [
            _request(),
            _requester_closure(
                ts="2026-08-20T05:00:00Z",
                agent_uuid="22222222-2222-2222-2222-222222222222",
                session_id="wd-session-beta",
            ),
        ],
    )

    assert report["action"] == "claim_unblocked_work"
    assert report["open_incoming_count"] == 0
