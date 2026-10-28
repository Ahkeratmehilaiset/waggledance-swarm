# SPDX-License-Identifier: BUSL-1.1
"""P1 lifecycle regressions on an isolated fixture bridge, never the live bridge."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_bridge_write_agent_event import (
    WINDOWS_APPEND_V1,
    _CloseoutWorld,
    _native_shell,
)


@WINDOWS_APPEND_V1
def test_p1_completed_bound_request_is_not_superseded(tmp_path: Path) -> None:
    """A correlated substantive answer ends the request; a new revision is no reason to close it."""
    world = _CloseoutWorld(
        tmp_path,
        _native_shell("powershell"),
        requirements={
            "nonce": "p1-synthetic-nonce",
            "task_revision": 1,
            "request_lifecycle": "wd.request-lifecycle.p1",
        },
    )
    completed = world.write(
        {
            **world.tools,
            "-Type": "message",
            "-Status": "answered",
            "-TaskId": world.request["task_id"],
            "-To": "codex-lead-1",
            "-ReplyToEventJson": json.dumps(world.request),
            "-Message": "synthetic completed delivery",
        },
        world.tools_frame,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    before = world.events()
    assert len(before) == 2
    assert before[-1]["in_reply_to_request_id"] == world.request["request_id"]

    closeout = world.close(world.closeout_arguments(
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2})
    ))
    assert closeout.returncode != 0, (
        "P1 must refuse to supersede an already answered request before accepting another event",
        closeout.stdout,
        closeout.stderr,
    )
    assert world.events() == before
    assert "answered or closed revision" in closeout.stderr


@WINDOWS_APPEND_V1
def test_p1_a_second_superseded_closeout_adds_no_event(tmp_path: Path) -> None:
    """One obsolete open revision receives one correlated closure, not duplicate closure traffic."""
    world = _CloseoutWorld(
        tmp_path,
        _native_shell("powershell"),
        requirements={
            "nonce": "p1-synthetic-nonce",
            "task_revision": 1,
            "request_lifecycle": "wd.request-lifecycle.p1",
        },
    )
    first = world.close(world.closeout_arguments(
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2})
    ))
    assert first.returncode == 0, first.stdout + first.stderr
    before = world.events()
    assert len(before) == 2
    assert before[-1]["status"] == "superseded"

    duplicate = world.close(world.closeout_arguments(
        PayloadJson=json.dumps({"superseded_by_request_id": "fixture-p1-successor-v2",
                                "superseded_by_task_revision": 2})
    ))
    assert duplicate.returncode != 0, (
        "P1 must refuse a second closure of a request that is no longer open",
        duplicate.stdout,
        duplicate.stderr,
    )
    assert world.events() == before
    assert "answered or closed revision" in duplicate.stderr


def _p1_world(tmp_path: Path, **metadata) -> _CloseoutWorld:
    return _CloseoutWorld(
        tmp_path,
        _native_shell("powershell"),
        requirements={
            "nonce": "p1-synthetic-nonce",
            "task_revision": 1,
            "request_lifecycle": "wd.request-lifecycle.p1",
            **metadata,
        },
    )


def _successor_arguments(world, **metadata) -> dict:
    return {
        **world.lead,
        "-Type": "message",
        "-Status": "requested",
        "-TaskId": world.request["task_id"],
        "-To": world.request["to"],
        "-Message": "synthetic P1 successor",
        "-RequestId": "fixture-p1-successor-v2",
        "-PayloadJson": json.dumps({
            "nonce": "p1-synthetic-nonce-v2",
            "task_revision": 2,
            "request_lifecycle": "wd.request-lifecycle.p1",
            "previous_request_id": world.request["request_id"],
            "previous_request_digest": world.request["request_digest"],
            **metadata,
        }),
    }


def _p1_close(world):
    return world.close(world.closeout_arguments(
        PayloadJson=json.dumps({
            "superseded_by_request_id": "fixture-p1-successor-v2",
            "superseded_by_task_revision": 2,
        })
    ))


@WINDOWS_APPEND_V1
def test_p1_open_request_blocks_an_unclosed_successor(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    before = world.events()
    result = world.write(_successor_arguments(world), world.lead_frame)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert "already active for this logical task" in result.stderr


@WINDOWS_APPEND_V1
def test_p1_explicit_close_then_linked_successor_preserves_both_receipts(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    result = world.write(_successor_arguments(world), world.lead_frame)
    assert result.returncode == 0, result.stdout + result.stderr
    events = world.events()
    assert len(events) == 3
    assert events[1]["type"] == "message"
    assert events[1]["status"] == "superseded"
    assert events[1]["in_reply_to_request_id"] == world.request["request_id"]
    successor = events[2]
    for field in ("request_lifecycle", "previous_request_id", "previous_request_digest",
                  "action_required", "fyi"):
        assert successor[field] == successor["payload"][field]
    assert successor["request_id"] == "fixture-p1-successor-v2"
    assert successor["action_required"] is True and successor["fyi"] is False
    assert successor["payload"]["task_revision"] == 2


@WINDOWS_APPEND_V1
def test_p1_answered_predecessor_can_link_without_a_superseded_event(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    answer = world.write({
        **world.tools, "-Type": "message", "-Status": "answered",
        "-TaskId": world.request["task_id"], "-To": "codex-lead-1",
        "-ReplyToEventJson": json.dumps(world.request),
    }, world.tools_frame)
    assert answer.returncode == 0, answer.stdout + answer.stderr
    result = world.write(_successor_arguments(world), world.lead_frame)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(world.events()) == 3
    assert all(row["status"] != "superseded" for row in world.events())


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("metadata", [
    {"previous_request_digest": "f" * 64},
    {"previous_request_id": "not-the-predecessor"},
    {"task_revision": 3},
], ids=["digest", "id", "skipped-revision"])
def test_p1_link_mismatch_fails_before_acceptance(tmp_path: Path, metadata) -> None:
    world = _p1_world(tmp_path)
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    before = world.events()
    result = world.write(_successor_arguments(world, **metadata), world.lead_frame)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert "predecessor ID, digest or revision" in result.stderr


@WINDOWS_APPEND_V1
def test_p1_unknown_accepted_queue_state_holds_without_a_new_event(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    pending = world.runtime_root / "spool" / "accepted-v1" / "pending"
    pending.mkdir(parents=True, exist_ok=True)
    blocker = pending / "unknown-p1-queue-entry"
    blocker.write_text("synthetic opaque accepted-state blocker", encoding="utf-8")
    before = world.events()
    result = _p1_close(world)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert blocker.read_text(encoding="utf-8") == "synthetic opaque accepted-state blocker"


@WINDOWS_APPEND_V1
def test_p1_partial_canonical_history_is_unknown_not_open(tmp_path: Path) -> None:
    world = _p1_world(tmp_path)
    log = world.shared / "events.jsonl"
    before = log.read_bytes() + b'{"unfinished":'
    log.write_bytes(before)
    result = _p1_close(world)
    assert result.returncode != 0, result.stdout + result.stderr
    assert log.read_bytes() == before


@WINDOWS_APPEND_V1
def test_p1_descriptive_fyi_cannot_hide_a_bound_request(tmp_path: Path) -> None:
    from test_bridge_write_agent_event import _closes

    world = _p1_world(tmp_path, action_required=False, fyi=True)
    assert world.request["action_required"] is False
    assert world.request["fyi"] is True
    assert not _closes(world.request, world.events())


@WINDOWS_APPEND_V1
@pytest.mark.parametrize("metadata", [
    {"action_required": "false", "fyi": True},
    {"action_required": False, "fyi": False},
    {"artifact": {"path": "relative.json", "bytes": 1, "sha256": "a" * 64, "owner": "codex-tools-1"}},
    {"artifact": {"path": "C:\\synthetic\\report.json", "bytes": True,
                  "sha256": "a" * 64, "owner": "codex-tools-1"}},
    {"artifact": {"path": "C:\\synthetic\\report.json", "bytes": 1,
                  "sha256": "short", "owner": "codex-tools-1"}},
], ids=["string-flag", "inconsistent-flags", "relative-artifact", "bool-size", "short-hash"])
def test_p1_invalid_metadata_is_refused(tmp_path: Path, metadata) -> None:
    world = _p1_world(tmp_path)
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    before = world.events()
    result = world.write(_successor_arguments(world, **metadata), world.lead_frame)
    assert result.returncode != 0, result.stdout + result.stderr
    assert world.events() == before
    assert "P1 HOLD" in result.stderr


@WINDOWS_APPEND_V1
def test_p1_artifact_descriptor_is_bound_and_discoverable_without_dereference(tmp_path: Path) -> None:
    from test_bridge_write_agent_event import _run_bridge_script

    artifact = {
        "path": str(tmp_path / "not-opened-by-the-writer.json"),
        "bytes": 123,
        "sha256": "a" * 64,
        "owner": "codex-tools-1",
    }
    world = _p1_world(tmp_path, artifact=artifact)
    assert world.request["artifact"] == world.request["payload"]["artifact"] == artifact
    assert not Path(artifact["path"]).exists()  # Descriptor is a claim, not a verified file receipt.
    inventory = _run_bridge_script(
        world.root, world.runtime_root, "Get-BridgeRequestInventory.ps1",
        "-Agent", "codex-lead-1", "-RequestId", world.request["request_id"],
        "-NoCache", shell=world.shell,
    )
    assert inventory.returncode == 0, inventory.stdout + inventory.stderr
    result = json.loads(inventory.stdout)
    assert result["matched_count"] == 1
    assert result["requests"][0]["artifact"] == artifact
    assert result["requests"][0]["answer_state"] == "not_evaluated"


@WINDOWS_APPEND_V1
def test_p1_closeout_does_not_clear_a_recognized_rco_veto(tmp_path: Path, monkeypatch) -> None:
    from test_bridge_write_agent_event import _peer_gate

    world = _p1_world(tmp_path)
    veto = {
        "ts_utc": world.request["ts_utc"], "agent": "claude-rco-2",
        "agent_uuid": "76739997-0058-41a2-8514-78ff295537aa",
        "type": "finding", "status": "changes_requested",
        "task_id": world.request["task_id"], "to": "codex-lead-1",
        "message": "synthetic retained veto", "payload": {"head": "a" * 40},
    }
    before = _peer_gate([veto], monkeypatch)
    assert not before["clear_to_merge"]
    closed = _p1_close(world)
    assert closed.returncode == 0, closed.stdout + closed.stderr
    after = _peer_gate([veto, *world.events()], monkeypatch)
    assert not after["clear_to_merge"]
    assert after["latest_blocking_event"] == before["latest_blocking_event"]


@WINDOWS_APPEND_V1
def test_p1_legacy_closeout_contract_is_not_silently_redefined(tmp_path: Path) -> None:
    world = _CloseoutWorld(tmp_path, _native_shell("powershell"))
    first = world.close(world.closeout_arguments())
    assert first.returncode == 0, first.stdout + first.stderr
    second = world.close(world.closeout_arguments())
    assert second.returncode == 0, second.stdout + second.stderr
    assert "request_lifecycle" not in world.events()[-1]

