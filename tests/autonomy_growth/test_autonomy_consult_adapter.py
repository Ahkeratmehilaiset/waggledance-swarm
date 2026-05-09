# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.autonomy_growth.autonomy_consult_adapter.

N+4 scout pick (Claude solo while Codex sleeps).
autonomy_consult_adapter is the only sanctioned wiring between
SolverRouter (production reasoning) and the RuntimeQueryRouter
(low-risk autonomy lane). Production reasoning code MUST NOT
import RuntimeQueryRouter directly — the indirection keeps the
inner-loop autonomy lane swappable.

A drift in this adapter would either:
- silently bypass the autonomy lane (returning None when it
  should serve), or
- corrupt the RuntimeQuery envelope (passing wrong types to
  RuntimeQueryRouter and crashing the inner loop), or
- mis-translate RuntimeRouteResult fields back into
  AutonomyConsultOutcome (losing solver_id, signal_id, etc.).

Pinned invariants:

- Missing/empty `family_kind` in hint -> consult returns None
  (production code falls back to its existing capability path).
- `inputs` not a dict -> AutonomyConsultOutcome with
  served=False, source="consult_skipped",
  miss_reason="hint_inputs_not_dict".
- `features` present but not a dict -> AutonomyConsultOutcome
  with miss_reason="hint_features_not_dict".
- Missing `inputs` defaults to {} (not None) and DOES proceed to
  RuntimeQueryRouter.route().
- spec_seed kept only if it is a dict; non-dict spec_seed is
  passed as None.
- weight defaults to 1.0; explicit weight is float-coerced.
- All RuntimeRouteResult fields (served/source/output/solver_id/
  solver_name/artifact_id/signal_id/miss_reason) translate
  faithfully into AutonomyConsultOutcome.
"""
from __future__ import annotations

from dataclasses import dataclass

from waggledance.core.autonomy_growth.autonomy_consult_adapter import (
    build_autonomy_consult,
)
from waggledance.core.autonomy_growth.runtime_query_router import (
    RuntimeQuery,
    RuntimeRouteResult,
)


# --- helpers -------------------------------------------------------

class _RecordingRouter:
    """Stand-in for RuntimeQueryRouter that records the last query
    and returns a configurable RuntimeRouteResult."""

    def __init__(self, result: RuntimeRouteResult):
        self._result = result
        self.last_query: RuntimeQuery | None = None
        self.call_count = 0

    def route(self, query: RuntimeQuery) -> RuntimeRouteResult:
        self.last_query = query
        self.call_count += 1
        return self._result


def _ok_result() -> RuntimeRouteResult:
    return RuntimeRouteResult(
        served=True, source="auto_promoted_solver",
        output=42.0, solver_id=7, solver_name="celsius_to_kelvin_v1",
        artifact_id="art-1", signal_id=99,
    )


# --- short-circuits: missing family_kind --------------------------

def test_missing_family_kind_returns_none():
    """A hint without family_kind must return None (caller falls
    back to its own path). The router MUST NOT be called."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({"inputs": {"x": 1}})
    assert out is None
    assert router.call_count == 0


def test_empty_family_kind_returns_none():
    """Empty string family_kind is falsy -> same as missing."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": "", "inputs": {"x": 1}})
    assert out is None
    assert router.call_count == 0


def test_none_family_kind_returns_none():
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": None, "inputs": {"x": 1}})
    assert out is None
    assert router.call_count == 0


# --- malformed-inputs: AutonomyConsultOutcome short-circuits -----

def test_inputs_not_dict_returns_consult_skipped_outcome():
    """When inputs is present but isn't a dict, return a structured
    AutonomyConsultOutcome (served=False, miss_reason=
    'hint_inputs_not_dict') — NEVER call the router with a bad
    envelope."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    for bad in ["string", 42, [1, 2], (1, 2)]:
        out = consult({"family_kind": "f", "inputs": bad})
        assert out is not None
        assert out.served is False
        assert out.source == "consult_skipped"
        assert out.miss_reason == "hint_inputs_not_dict"
    assert router.call_count == 0


def test_features_not_dict_returns_consult_skipped_outcome():
    """features is optional; if present and not a dict, return the
    structured skip outcome (different miss_reason)."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({
        "family_kind": "f", "inputs": {"x": 1},
        "features": "not_a_dict",
    })
    assert out is not None
    assert out.served is False
    assert out.source == "consult_skipped"
    assert out.miss_reason == "hint_features_not_dict"
    assert router.call_count == 0


def test_features_none_proceeds_to_router():
    """features is optional. None must NOT trigger the
    consult_skipped path; the router gets called normally."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({
        "family_kind": "f", "inputs": {"x": 1},
        # features key absent — should default to None and proceed
    })
    assert out is not None
    assert out.served is True
    assert router.call_count == 1


# --- inputs default {} for missing key ---------------------------

def test_missing_inputs_defaults_to_empty_dict():
    """When the inputs key is absent, the adapter must still call
    the router with inputs={} — not None, not crash."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": "f"})
    assert out is not None
    assert router.call_count == 1
    assert router.last_query is not None
    assert router.last_query.inputs == {}


def test_explicit_none_inputs_treated_as_empty_dict():
    """`hint.get('inputs') or {}` collapses None to {} (Python
    truthiness)."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": "f", "inputs": None})
    assert out is not None  # None is falsy, hint_inputs_not_dict NOT triggered
    assert router.call_count == 1
    assert router.last_query.inputs == {}


# --- spec_seed: kept only if dict --------------------------------

def test_spec_seed_dict_passed_through():
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    seed = {"factor": 1.0, "offset": 273.15}
    consult({"family_kind": "f", "inputs": {"x": 0},
                "spec_seed": seed})
    assert router.last_query.spec_seed == seed


def test_spec_seed_non_dict_replaced_with_none():
    """The adapter is defensive: a non-dict spec_seed (string,
    list, int) MUST be replaced with None — never passed through
    to RuntimeQueryRouter."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    for bad in ["bad", [1, 2], 42, "not_a_dict"]:
        consult({"family_kind": "f", "inputs": {"x": 0},
                    "spec_seed": bad})
        assert router.last_query.spec_seed is None


# --- RuntimeQuery envelope construction --------------------------

def test_runtime_query_carries_all_hint_fields():
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    consult({
        "family_kind": "scalar_unit_conversion",
        "inputs": {"x": 100},
        "cell_coord": "cell:hex_a",
        "intent_seed": "convert",
        "features": {"unit": "C"},
        "spec_seed": {"factor": 1.0},
        "weight": 0.7,
    })
    q = router.last_query
    assert q.family_kind == "scalar_unit_conversion"
    assert q.inputs == {"x": 100}
    assert q.cell_coord == "cell:hex_a"
    assert q.intent_seed == "convert"
    assert q.features == {"unit": "C"}
    assert q.spec_seed == {"factor": 1.0}
    assert q.weight == 0.7


def test_runtime_query_default_weight_is_one():
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    consult({"family_kind": "f", "inputs": {"x": 1}})
    assert router.last_query.weight == 1.0


def test_runtime_query_weight_float_coerced():
    """weight is explicitly float()-cast — int input must become
    float."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    consult({"family_kind": "f", "inputs": {"x": 1}, "weight": 3})
    assert isinstance(router.last_query.weight, float)
    assert router.last_query.weight == 3.0


def test_runtime_query_family_kind_str_coerced():
    """family_kind is str()-cast for safety even though normal
    callers pass strings."""
    router = _RecordingRouter(_ok_result())
    consult = build_autonomy_consult(router)
    consult({"family_kind": 12345, "inputs": {"x": 1}})
    # 12345 is truthy (passes the falsy guard), then str()-cast
    assert router.last_query.family_kind == "12345"


# --- RuntimeRouteResult -> AutonomyConsultOutcome translation ---

def test_outcome_carries_all_route_result_fields():
    """Every field of RuntimeRouteResult must translate into the
    matching field on AutonomyConsultOutcome — losing one would
    mean production reasoning code sees stale data."""
    rr = RuntimeRouteResult(
        served=True, source="auto_promoted_solver",
        output={"value": 273.15},
        solver_id=42, solver_name="celsius_to_kelvin_v1",
        artifact_id="art-x", signal_id=7, miss_reason=None,
    )
    router = _RecordingRouter(rr)
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": "f", "inputs": {"x": 0}})
    assert out.served is True
    assert out.source == "auto_promoted_solver"
    assert out.output == {"value": 273.15}
    assert out.solver_id == 42
    assert out.solver_name == "celsius_to_kelvin_v1"
    assert out.artifact_id == "art-x"
    assert out.signal_id == 7
    assert out.miss_reason is None


def test_outcome_carries_miss_reason_when_router_misses():
    """When the router serves no result (e.g. family_not_low_risk
    or gap_emitted), the miss_reason must round-trip into the
    outcome so the production caller can act on it."""
    rr = RuntimeRouteResult(
        served=False, source="family_not_low_risk",
        miss_reason="not_in_LOW_RISK_FAMILY_KINDS",
    )
    router = _RecordingRouter(rr)
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": "exotic_family", "inputs": {"x": 1}})
    assert out.served is False
    assert out.source == "family_not_low_risk"
    assert out.miss_reason == "not_in_LOW_RISK_FAMILY_KINDS"


def test_outcome_for_gap_emitted_route():
    rr = RuntimeRouteResult(
        served=False, source="gap_emitted", signal_id=200,
    )
    router = _RecordingRouter(rr)
    consult = build_autonomy_consult(router)
    out = consult({"family_kind": "f", "inputs": {"x": 0}})
    assert out.served is False
    assert out.source == "gap_emitted"
    assert out.signal_id == 200
