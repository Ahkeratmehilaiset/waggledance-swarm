# SPDX-License-Identifier: BUSL-1.1
"""Opt-in hex-mesh canary mirror wiring in AutonomyRuntime (slice 2).

The mirror is a read-only shadow: OFF by default with zero behaviour
change; ON it records mesh-vs-production route comparisons without ever
altering the production decision, and a mirror failure never breaks the
query path.
"""
from __future__ import annotations

import pytest

from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
)
from waggledance.core.hex_topology.canary_mirror import (
    CANARY_CLASSIFICATIONS,
    CANARY_MIRROR_REPORT_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


class _Selection:
    def __init__(self, capabilities) -> None:
        self.selected = list(capabilities)


class _Consult:
    output = "42.0"
    source = "low_risk_autogrowth"
    solver_name = "celsius_to_kelvin_v1"
    solver_id = 7
    artifact_id = "artifact:fixture"
    miss_reason = None


class _RouteResult:
    def __init__(self, capabilities, *, consult=None, served=False) -> None:
        self.selection = _Selection(capabilities)
        self.quality_path = "gold"
        self.autonomy_consult = consult
        self.autonomy_served = served
        self.solver_call_trace = []


class _Executor:
    available = True

    def execute(self, **_payload):
        return {"success": True, "value": 42}


def _runtime(*, mirror: bool, route_result=None) -> AutonomyRuntime:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="detect.fixture",
        category=CapabilityCategory.DETECT,
        description="Fixture detector",
        success_criteria=["success"],
    )
    registry.register(capability)
    registry.register_executor("detect.fixture", _Executor())
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_magma=False,
        enable_persistence=False,
        enable_hex_canary_mirror=mirror,
    )
    resolved = route_result or _RouteResult([capability])
    runtime.solver_router.route = lambda _i, _q, _c: resolved
    runtime.action_bus.register_executor(
        "detect.fixture", lambda _action: {"success": True, "value": 42}
    )
    return runtime


def test_default_off_is_zero_behaviour_change():
    runtime = _runtime(mirror=False)
    result = runtime.handle_query("calculate the heating formula")
    assert result["executed"] is True
    snapshot = runtime.hex_canary_mirror_snapshot()
    assert snapshot["enabled"] is False
    assert snapshot["mirror_failure_count"] == 0
    assert snapshot["report"]["sample_count"] == 0


def test_opt_in_records_comparison_without_altering_response():
    secret = "private operator query about pakkanen DO_NOT_SHOW"
    runtime = _runtime(mirror=True)
    result = runtime.handle_query(secret)

    assert result["executed"] is True  # production path unchanged
    snapshot = runtime.hex_canary_mirror_snapshot()
    assert snapshot["enabled"] is True
    report = snapshot["report"]
    assert report["schema_version"] == CANARY_MIRROR_REPORT_SCHEMA
    assert report["sample_count"] == 1
    assert report["no_runtime_mutation"] is True
    assert report["runtime_authority_granted"] is False
    # the single record carries the production decision verbatim + digest
    record = list(runtime._canary_comparisons)[0]
    assert record["production_capability_id"] == "detect.fixture"
    assert record["quality_path"] == "gold"
    assert record["query_digest"] == sha256_digest({"query": secret})
    assert record["classification"] in CANARY_CLASSIFICATIONS
    # privacy: the raw query never appears in mirror artifacts
    assert secret not in repr(record)
    assert secret not in repr(report)


def test_consult_served_query_is_mirrored_before_early_return():
    consult_route = _RouteResult([], consult=_Consult(), served=True)
    runtime = _runtime(mirror=True, route_result=consult_route)
    result = runtime.handle_query("convert 21 celsius to kelvin")

    assert result["quality_path"] == "autonomy_consult"
    record = list(runtime._canary_comparisons)[0]
    assert record["production_capability_id"] == (
        "autonomy_consult:celsius_to_kelvin_v1"
    )
    assert record["quality_path"] == "autonomy_consult"


def test_no_capability_path_is_mirrored():
    runtime = _runtime(mirror=True, route_result=_RouteResult([]))
    result = runtime.handle_query("anything at all")

    assert result["error"] == "No capabilities available"
    record = list(runtime._canary_comparisons)[0]
    assert record["production_capability_id"] == "none"
    assert record["quality_path"] == "bronze"


def test_mirror_failure_never_breaks_the_query_path(monkeypatch):
    import waggledance.core.hex_topology.canary_mirror as canary_mirror

    def _boom(**_kwargs):
        raise RuntimeError("mirror exploded")

    monkeypatch.setattr(
        canary_mirror, "build_canary_route_comparison", _boom
    )
    runtime = _runtime(mirror=True)
    result = runtime.handle_query("calculate the heating formula")

    assert result["executed"] is True  # fail-open isolation
    snapshot = runtime.hex_canary_mirror_snapshot()
    assert snapshot["mirror_failure_count"] == 1
    assert snapshot["report"]["sample_count"] == 0


def test_buffer_is_bounded_and_snapshot_digest_rederives():
    runtime = _runtime(mirror=True)
    route = _RouteResult(
        [runtime.capability_registry.get("detect.fixture")]
    )
    for index in range(300):
        runtime._canary_mirror_safe(f"query number {index}", "math", route)

    assert len(runtime._canary_comparisons) == 256  # bounded deque
    snapshot = runtime.hex_canary_mirror_snapshot()
    report = snapshot["report"]
    assert report["sample_count"] == 256
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    assert report["canonical_digest"] == sha256_digest(core)
