# SPDX-License-Identifier: Apache-2.0
"""AutonomyRuntime initialization side-effect gate tests."""

from __future__ import annotations

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


def test_runtime_can_disable_magma_and_persistence_init(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    runtime = AutonomyRuntime(
        profile="TEST",
        enable_magma=False,
        enable_persistence=False,
    )

    assert runtime.audit is None
    assert runtime.event_log is None
    assert runtime.trust is None
    assert runtime.replay is None
    assert runtime.provenance is None
    assert runtime.world_store is None
    assert runtime.procedural_store is None
    assert runtime.case_store is None
    assert runtime.verifier_store is None
    assert runtime.prediction_ledger is None
    assert runtime.capability_confidence is None
    assert not (tmp_path / "data").exists()


def test_runtime_schedule_missions_with_gates_disabled_has_no_side_effects(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    mission = mq.make_mission(
        kind="ingest_request",
        lane="ingestion",
        priority=0.9,
        intent="side-effect gate intent",
        rationale="side-effect gate rationale",
        created_tick_id=1,
    )
    runtime = AutonomyRuntime(
        profile="TEST",
        enable_magma=False,
        enable_persistence=False,
    )
    runtime.execute_mission = MagicMock(
        side_effect=AssertionError("schedule_missions must not execute")
    )

    report = runtime.schedule_missions(
        state=_state(),
        missions=[mission],
        hard_rules=_hard_rules(),
        max_dispatched=1,
    )

    runtime.execute_mission.assert_not_called()
    assert report.selected_missions[0].lifecycle_status == "scheduled"
    assert not (tmp_path / "data").exists()
