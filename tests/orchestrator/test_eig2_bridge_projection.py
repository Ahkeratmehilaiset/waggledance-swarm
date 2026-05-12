# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / ".orchestrator" / "eig2_bridge_projection.py"
    spec = importlib.util.spec_from_file_location("eig2_bridge_projection", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projects_polymorphic_live_bridge_event() -> None:
    mod = _load_module()
    event = {
        "ts_utc": "2026-05-11T17:50:28Z",
        "agent": "claude",
        "type": "ownership_proposal",
        "task_id": "eig2-m0-ownership-split-2026-05-11",
        "status": "open",
        "to": "codex,operator",
        "message": "proposal",
        "payload": {"related_milestone": "M0", "parent_id": "root"},
    }
    projected = mod.project_live_event(event)
    assert projected["protocol_version"] == "eig2-bridge-v1"
    assert projected["message_type"] == "ownership_proposal"
    assert projected["id"] == "eig2-m0-ownership-split-2026-05-11"
    assert projected["timestamp"] == "2026-05-11T17:50:28Z"
    assert projected["author"] == "claude"
    assert projected["related_milestone"] == "M0"
    assert projected["parent_id"] == "root"
    assert len(projected["payload_hash"]) == 64


def test_empty_status_event_does_not_crash_projection() -> None:
    mod = _load_module()
    projected = mod.project_live_event(
        {
            "ts_utc": "2026-05-11T19:22:00Z",
            "agent": "codex",
            "type": "message",
            "task_id": "empty-status",
            "status": "",
            "payload": {},
        }
    )
    assert projected["status"] == ""
    assert projected["message_type"] == "message"


def test_message_answer_statuses_are_explicit() -> None:
    mod = _load_module()
    assert mod.is_message_answer({"type": "message", "status": "answered"})
    assert mod.is_message_answer(
        {"type": "message", "status": "answered_plus_reminder"}
    )
    assert mod.is_message_answer(
        {"type": "message", "status": "answered_after_recovery"}
    )
    assert not mod.is_message_answer({"type": "message", "status": "answered_later"})
    assert not mod.is_message_answer({"type": "ownership_proposal", "status": "open"})


def test_payload_hash_is_stable_for_key_order() -> None:
    mod = _load_module()
    assert mod.payload_hash({"a": 1, "b": 2}) == mod.payload_hash({"b": 2, "a": 1})
