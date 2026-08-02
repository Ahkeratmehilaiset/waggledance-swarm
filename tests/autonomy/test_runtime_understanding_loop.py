# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from waggledance.core.autonomy.runtime import AutonomyRuntime


class RecordingGraph:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace

    def add_node(self, *_args, **_kwargs) -> None:
        self.trace.append("graph")


class RecordingWorldModel:
    def __init__(self, trace: list[str]) -> None:
        self.trace = trace
        self.graph = RecordingGraph(trace)

    def update_baseline(self, *_args, **_kwargs) -> None:
        self.trace.append("baseline")

    def register_entity(self, *_args, **_kwargs) -> None:
        self.trace.append("entity")


class RecordingLoop:
    def __init__(
        self,
        trace: list[str],
        *,
        prepare_error: Exception | None = None,
        complete_error: Exception | None = None,
    ) -> None:
        self.trace = trace
        self.prepare_error = prepare_error
        self.complete_error = complete_error
        self.prepared_observation = None

    def prepare_observation(self, observation):
        self.trace.append("prepare")
        self.prepared_observation = dict(observation)
        if self.prepare_error is not None:
            raise self.prepare_error
        return "prediction-ticket"

    def complete_numeric(self, ticket, value):
        self.trace.append("complete")
        assert ticket == "prediction-ticket"
        assert value == 35.2
        if self.complete_error is not None:
            raise self.complete_error

    def stats(self):
        return {
            "runtime_authority_applied": False,
            "routing_influence_applied": False,
        }

    def close(self) -> None:
        self.trace.append("close")


def _runtime(trace: list[str], loop=None) -> AutonomyRuntime:
    runtime = object.__new__(AutonomyRuntime)
    runtime.world_model = RecordingWorldModel(trace)
    runtime.understanding_loop = loop
    runtime._understanding_ingest_lock = None
    if loop is not None:
        import threading

        runtime._understanding_ingest_lock = threading.RLock()
    runtime._understanding_prepare_failure_total = 0
    runtime._understanding_complete_failure_total = 0
    runtime._understanding_last_failure_stage = None
    runtime._understanding_last_failure_type = None
    return runtime


def _observation() -> dict:
    return {
        "entity_id": "wd.synthetic.hive-1",
        "metric": "temperature",
        "unit": "Cel",
        "value": 35.2,
        "source": "mqtt",
        "quality": 0.9,
        "privacy_class": "synthetic",
        "source_seq": 1,
    }


def test_default_off_path_keeps_legacy_order_and_does_not_call_shadow() -> None:
    trace: list[str] = []
    runtime = _runtime(trace)

    assert runtime.ingest_sensor_observation(_observation()) is None

    assert trace == ["baseline", "entity", "graph"]
    assert runtime.understanding_shadow_stats()["enabled"] is False


def test_prediction_precedes_all_legacy_mutations_and_completion_follows() -> None:
    trace: list[str] = []
    loop = RecordingLoop(trace)
    runtime = _runtime(trace, loop)

    runtime.ingest_sensor_observation(_observation())

    assert trace == ["prepare", "baseline", "entity", "graph", "complete"]
    assert loop.prepared_observation == _observation()
    stats = runtime.understanding_shadow_stats()
    assert stats["audit_gap_total"] == 0
    assert stats["runtime_authority_applied"] is False
    assert stats["routing_influence_applied"] is False


def test_prepare_failure_is_visible_and_legacy_ingestion_continues(caplog) -> None:
    trace: list[str] = []
    loop = RecordingLoop(trace, prepare_error=OSError("private raw canary"))
    runtime = _runtime(trace, loop)

    with caplog.at_level(logging.WARNING, logger="waggledance.autonomy.runtime"):
        runtime.ingest_sensor_observation(_observation())

    assert trace == ["prepare", "baseline", "entity", "graph"]
    stats = runtime.understanding_shadow_stats()
    assert stats["prepare_failure_total"] == 1
    assert stats["audit_gap_total"] == 1
    assert stats["last_failure_type"] == "OSError"
    assert "private raw canary" not in caplog.text


def test_complete_failure_is_visible_after_legacy_ingestion(caplog) -> None:
    trace: list[str] = []
    loop = RecordingLoop(trace, complete_error=RuntimeError("private raw canary"))
    runtime = _runtime(trace, loop)

    with caplog.at_level(logging.WARNING, logger="waggledance.autonomy.runtime"):
        runtime.ingest_sensor_observation(_observation())

    assert trace == ["prepare", "baseline", "entity", "graph", "complete"]
    stats = runtime.understanding_shadow_stats()
    assert stats["complete_failure_total"] == 1
    assert stats["audit_gap_total"] == 1
    assert stats["last_failure_stage"] == "complete"
    assert "private raw canary" not in caplog.text


def test_legacy_failure_still_propagates_and_never_calls_completion() -> None:
    trace: list[str] = []
    loop = RecordingLoop(trace)
    runtime = _runtime(trace, loop)

    def fail_baseline(*_args, **_kwargs):
        trace.append("baseline")
        raise RuntimeError("legacy baseline failure")

    runtime.world_model.update_baseline = fail_baseline

    with pytest.raises(RuntimeError, match="legacy baseline failure"):
        runtime.ingest_sensor_observation(_observation())

    assert trace == ["prepare", "baseline"]
    assert runtime.understanding_shadow_stats()["audit_gap_total"] == 0


def test_none_ticket_is_counted_as_prepare_contract_failure_and_legacy_continues() -> None:
    trace: list[str] = []
    loop = RecordingLoop(trace)
    loop.prepare_observation = lambda _observation: None
    runtime = _runtime(trace, loop)

    runtime.ingest_sensor_observation(_observation())

    assert trace == ["baseline", "entity", "graph"]
    stats = runtime.understanding_shadow_stats()
    assert stats["prepare_failure_total"] == 1
    assert stats["last_failure_type"] == "TypeError"


def test_constructor_accepts_optional_loop_without_enabling_other_authority() -> None:
    loop = MagicMock()
    runtime = AutonomyRuntime(
        profile="TEST",
        enable_magma=False,
        enable_persistence=False,
        understanding_loop=loop,
    )

    stats = runtime.understanding_shadow_stats()

    assert stats["enabled"] is True
    assert stats["runtime_authority_applied"] is False
    assert stats["routing_influence_applied"] is False
    assert runtime._understanding_ingest_lock is not None


def test_stop_closes_the_optional_understanding_loop() -> None:
    trace: list[str] = []
    loop = RecordingLoop(trace)
    runtime = _runtime(trace, loop)
    runtime.resource_kernel = None
    runtime.world_model.save = lambda: trace.append("world-save")
    runtime._started = True

    runtime.stop()

    assert trace == ["world-save", "close"]
