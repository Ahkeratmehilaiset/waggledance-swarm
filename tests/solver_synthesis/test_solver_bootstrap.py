"""Targeted tests for Phase 10 P4 — solver bootstrap, throttler, LLM gen."""

from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.providers import (
    ProviderConfig,
    ProviderPlane,
    ProviderPlaneRegistry,
)
from waggledance.core.solver_synthesis.cold_shadow_throttler import (
    ColdShadowThrottler,
    ThrottleVerdict,
)
from waggledance.core.solver_synthesis.family_specs import EXAMPLE_SPECS, example_spec
from waggledance.core.solver_synthesis.llm_solver_generator import (
    GenerationRequest,
    build_provider_request_payload,
    generate as llm_generate,
)
from waggledance.core.solver_synthesis.solver_bootstrap import (
    BootstrapDecision,
    SolverBootstrap,
)
from waggledance.core.solver_synthesis.solver_family_registry import SolverFamilyRegistry
from waggledance.core.storage import ControlPlaneDB


# -----------------------------------------------------------------
#  ColdShadowThrottler
# -----------------------------------------------------------------


def test_throttler_admits_then_refills() -> None:
    clock = [0.0]
    th = ColdShadowThrottler(
        cold_capacity=2.0,
        cold_refill_per_second=1.0,
        shadow_capacity=2.0,
        shadow_refill_per_second=1.0,
        max_in_flight=10,
        clock=lambda: clock[0],
    )
    v1 = th.admit("cold")
    v2 = th.admit("cold")
    v3 = th.admit("cold")
    assert v1.admitted and v2.admitted
    assert not v3.admitted
    assert "tokens_exhausted" in v3.reason
    th.release("cold")
    th.release("cold")
    # Advance clock by 2 s → should refill 2 tokens.
    clock[0] = 2.0
    v4 = th.admit("cold")
    assert v4.admitted


def test_throttler_max_in_flight_caps_concurrency() -> None:
    th = ColdShadowThrottler(
        cold_capacity=100.0,
        shadow_capacity=100.0,
        max_in_flight=2,
    )
    assert th.admit("cold").admitted
    assert th.admit("shadow").admitted
    rejected = th.admit("cold")
    assert not rejected.admitted
    assert "max_in_flight" in rejected.reason
    th.release("cold")
    assert th.admit("cold").admitted


def test_throttler_unknown_lane_rejected() -> None:
    th = ColdShadowThrottler()
    with pytest.raises(ValueError):
        th.admit("warm")
    with pytest.raises(ValueError):
        th.release("warm")


def test_throttler_snapshot_includes_in_flight_total() -> None:
    th = ColdShadowThrottler(max_in_flight=5)
    th.admit("cold")
    th.admit("shadow")
    snap = th.snapshot()
    assert snap["in_flight_total"] == 2
    assert snap["cold"]["in_flight"] == 1
    assert snap["shadow"]["in_flight"] == 1


# -----------------------------------------------------------------
#  family_specs
# -----------------------------------------------------------------


def test_example_specs_round_trip() -> None:
    for kind in ("scalar_unit_conversion", "threshold_rule", "linear_arithmetic"):
        spec = example_spec(kind)
        assert isinstance(spec, dict)
    with pytest.raises(KeyError):
        example_spec("does_not_exist")


# -----------------------------------------------------------------
#  llm_solver_generator
# -----------------------------------------------------------------


def test_build_provider_request_payload_validates() -> None:
    req = GenerationRequest(
        gap_id="gap-001",
        cell_id="thermal",
        intent="Convert celsius to fahrenheit.",
        examples=({"in": 0, "out": 32},),
        family_hints=("scalar_unit_conversion",),
    )
    payload = build_provider_request_payload(
        req,
        branch_name="phase10/foundation-truth-builder-lane",
        base_commit_hash="8bf1869",
        section="P4-test",
    )
    assert payload["task_class"] == "code_or_repair"
    assert payload["no_runtime_mutation"] is True
    assert payload["section"] == "P4-test"


def test_llm_generate_against_dry_run_plane(tmp_path: Path) -> None:
    cp = ControlPlaneDB(db_path=tmp_path / "cp.db")
    try:
        registry = ProviderPlaneRegistry(control_plane=cp)
        plane = ProviderPlane(registry=registry, section="P4-test")
        result = llm_generate(
            plane,
            GenerationRequest(
                gap_id="gap-002",
                cell_id="general",
                intent="threshold rule for temperature alarm",
                family_hints=("threshold_rule",),
            ),
            branch_name="phase10/foundation-truth-builder-lane",
            base_commit_hash="8bf1869",
            section="P4-test",
        )
        assert result.parse_status == "dry_run"
        assert result.parsed_spec_payload is None
        assert "dry-run" in result.parse_error
    finally:
        cp.close()


# -----------------------------------------------------------------
#  SolverBootstrap orchestrator
# -----------------------------------------------------------------


@pytest.fixture()
def family_registry() -> SolverFamilyRegistry:
    return SolverFamilyRegistry().register_defaults()


@pytest.fixture()
def cp(tmp_path: Path) -> ControlPlaneDB:
    db = ControlPlaneDB(db_path=tmp_path / "cp.db")
    yield db
    db.close()


def test_bootstrap_high_confidence_takes_u1_declarative(
    family_registry: SolverFamilyRegistry, cp: ControlPlaneDB
) -> None:
    bootstrap = SolverBootstrap(
        family_registry=family_registry,
        control_plane=cp,
        section="P4-test",
    )
    bootstrap.register_default_families()
    decision = bootstrap.bootstrap_from_gap(
        gap_id="abcdef0123456789",
        cell_id="thermal",
        intent="celsius to fahrenheit",
        family_match_confidence=0.95,
        family_kind="scalar_unit_conversion",
        spec_payload=example_spec("scalar_unit_conversion"),
    )
    assert decision.lane == "u1_declarative"
    assert decision.spec is not None
    assert decision.spec.family_kind == "scalar_unit_conversion"
    assert decision.control_plane_solver is not None
    assert decision.control_plane_solver.status == "draft"
    # Family was registered in the control plane.
    fam = cp.get_solver_family("scalar_unit_conversion")
    assert fam is not None and fam.status == "active"


def test_bootstrap_low_confidence_routes_to_u3_freeform(
    family_registry: SolverFamilyRegistry, cp: ControlPlaneDB
) -> None:
    registry = ProviderPlaneRegistry(control_plane=cp)
    plane = ProviderPlane(registry=registry, section="P4-test")
    bootstrap = SolverBootstrap(
        family_registry=family_registry,
        control_plane=cp,
        provider_plane=plane,
        section="P4-test",
    )
    decision = bootstrap.bootstrap_from_gap(
        gap_id="lowconf-gap-001",
        cell_id="general",
        intent="some unfamiliar pattern with no good family match",
        family_match_confidence=0.2,
        family_kind=None,
    )
    assert decision.lane == "u3_freeform"
    assert decision.generation_result is not None
    assert decision.generation_result.parse_status == "dry_run"


def test_bootstrap_rejects_u3_when_no_provider_plane(
    family_registry: SolverFamilyRegistry, cp: ControlPlaneDB
) -> None:
    bootstrap = SolverBootstrap(
        family_registry=family_registry,
        control_plane=cp,
        provider_plane=None,
        section="P4-test",
    )
    decision = bootstrap.bootstrap_from_gap(
        gap_id="lowconf-gap-002",
        cell_id="general",
        intent="something",
        family_match_confidence=0.1,
        family_kind=None,
    )
    assert decision.lane == "rejected"
    assert "no ProviderPlane" in decision.reason


def test_bootstrap_throttler_defers_when_exhausted(
    family_registry: SolverFamilyRegistry, cp: ControlPlaneDB
) -> None:
    th = ColdShadowThrottler(
        cold_capacity=1.0,
        cold_refill_per_second=0.0,
        shadow_capacity=1.0,
        shadow_refill_per_second=0.0,
    )
    bootstrap = SolverBootstrap(
        family_registry=family_registry,
        control_plane=cp,
        throttler=th,
        section="P4-test",
    )
    spec_payload = example_spec("scalar_unit_conversion")
    d1 = bootstrap.bootstrap_from_gap(
        gap_id="g1",
        cell_id="thermal",
        intent="x",
        family_match_confidence=0.95,
        family_kind="scalar_unit_conversion",
        spec_payload=spec_payload,
    )
    assert d1.lane == "u1_declarative"
    # Second call: cold tokens exhausted, no refill → deferred.
    d2 = bootstrap.bootstrap_from_gap(
        gap_id="g2",
        cell_id="thermal",
        intent="x",
        family_match_confidence=0.95,
        family_kind="scalar_unit_conversion",
        spec_payload=spec_payload,
    )
    assert d2.lane == "deferred_throttled"


def test_bootstrap_middle_band_falls_through_on_validation_failure(
    family_registry: SolverFamilyRegistry, cp: ControlPlaneDB
) -> None:
    registry = ProviderPlaneRegistry(control_plane=cp)
    plane = ProviderPlane(registry=registry, section="P4-test")
    bootstrap = SolverBootstrap(
        family_registry=family_registry,
        control_plane=cp,
        provider_plane=plane,
        section="P4-test",
    )
    # Middle-band confidence with intentionally invalid spec_payload
    # (missing required keys for scalar_unit_conversion).
    decision = bootstrap.bootstrap_from_gap(
        gap_id="midband-gap-001",
        cell_id="thermal",
        intent="ambiguous unit thing",
        family_match_confidence=0.65,
        family_kind="scalar_unit_conversion",
        spec_payload={"from_unit": "celsius"},  # missing to_unit, factor
    )
    assert decision.lane == "u3_freeform"
    assert "u1_failed_or_low_conf" in decision.reason
