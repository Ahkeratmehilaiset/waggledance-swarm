# SPDX-License-Identifier: Apache-2.0
"""Runtime-facing mission scheduler wiring tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from waggledance.core.autonomy import (  # noqa: E402
    kernel_state as ks,
    mission_queue as mq,
    policy_core as pc,
)
from waggledance.core.autonomy.runtime import AutonomyRuntime  # noqa: E402


class _RecordingAudit:
    def __init__(self) -> None:
        self.entries = []

    def record(self, entry) -> None:
        self.entries.append(entry)


class _RecordingReplay:
    def __init__(self) -> None:
        self.events = []

    def record_mission_event(self, goal_id: str, event_type: str,
                             payload: dict | None = None,
                             step_order: int = 0, **kwargs) -> None:
        self.events.append({
            "goal_id": goal_id,
            "event_type": event_type,
            "payload": payload or {},
            "step_order": step_order,
            **kwargs,
        })


def _state() -> ks.KernelState:
    state = ks.initial_state(
        constitution_id="wd_autonomy_constitution_v1",
        constitution_sha256="sha256:" + "a" * 64,
    )
    return ks.with_tick(state, ts_iso="t1")


def _hard_rules() -> tuple[pc.HardRule, ...]:
    return pc.load_hard_rules(
        ROOT / "waggledance" / "core" / "autonomy" / "constitution.yaml"
    )


def test_runtime_schedule_missions_emits_lifecycle_without_execution():
    sensitive = "runtime_scheduler_secret_marker_DO_NOT_LEAK"
    mission = mq.make_mission(
        kind="ingest_request",
        lane="ingestion",
        priority=0.9,
        intent=sensitive,
        rationale=f"{sensitive} rationale",
        created_tick_id=1,
    )
    runtime = AutonomyRuntime(profile="TEST")
    runtime.audit = _RecordingAudit()
    runtime.replay = _RecordingReplay()
    runtime.execute_mission = MagicMock(
        side_effect=AssertionError("scheduler wrapper must not execute missions")
    )

    report = runtime.schedule_missions(
        state=_state(),
        missions=[mission],
        hard_rules=_hard_rules(),
        max_dispatched=1,
    )

    runtime.execute_mission.assert_not_called()
    assert report.selected_missions[0].lifecycle_status == "scheduled"

    assert len(runtime.audit.entries) == 1
    audit_entry = runtime.audit.entries[0]
    assert audit_entry.event_type == "mission.lifecycle_changed"
    assert audit_entry.goal_id == mission.mission_id
    assert audit_entry.payload["mission_id"] == mission.mission_id
    assert audit_entry.payload["from_status"] == "queued"
    assert audit_entry.payload["to_status"] == "scheduled"

    assert len(runtime.replay.events) == 1
    replay_event = runtime.replay.events[0]
    assert replay_event["goal_id"] == mission.mission_id
    assert replay_event["event_type"] == "mission.lifecycle_changed"
    assert replay_event["payload"]["to_status"] == "scheduled"

    encoded = json.dumps({
        "audit": audit_entry.to_dict(),
        "replay": runtime.replay.events,
    }, sort_keys=True)
    assert sensitive not in encoded
