"""Phase 16A P3 — AutonomyService.handle_query upstream wiring tests.

Verifies that the upstream structured_request extractor runs above
AutonomyRuntime.handle_query (one layer up at the service boundary)
and that the lift behaves correctly under each branch:

* derives structured_request from a flat domain payload,
* leaves untouched contexts untouched (backwards compat),
* respects the builtin-precedence skip signal,
* refuses to overwrite a caller-supplied structured_request,
* refuses to bypass the runtime hint extractor (no
  ``low_risk_autonomy_query`` direct injection),
* isolates extractor errors (production path does not crash).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest

from waggledance.application.services.autonomy_service import AutonomyService
from waggledance.core.autonomy.compatibility import CompatibilityLayer
from waggledance.core.autonomy_growth.upstream_structured_request_extractor import (
    UPSTREAM_DERIVED,
    UPSTREAM_REJECTED_AMBIGUOUS,
    UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK,
    UPSTREAM_SKIPPED,
    UPSTREAM_SKIPPED_BUILTIN_PRECEDENCE,
)


class _RecordingCompat(CompatibilityLayer):
    """Compatibility shim that records what context flows through it."""

    def __init__(self) -> None:
        super().__init__(runtime=None, legacy=None, compatibility_mode=False)
        self.calls: List[Dict[str, Any]] = []

    def handle_query(
        self,
        query: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self.calls.append({"query": query, "context": dict(context or {})})
        return {
            "intent": "chat",
            "quality_path": "bronze",
            "result": {"answer": "ok"},
            "executed": True,
            "elapsed_ms": 0.0,
            "source": "autonomy",
        }


def _service_with_recording_compat() -> tuple[AutonomyService, _RecordingCompat]:
    compat = _RecordingCompat()
    svc = AutonomyService(compatibility=compat)
    # Disable admission throttling so the wiring test isn't gated by load.
    svc._admission = None  # type: ignore[assignment]

    # Re-route the .handle_query path past the AdmissionControl gate.
    # We swap in an AdmissionControl shim that always accepts.
    class _AlwaysAccept:
        def check(self, **_kw):
            from waggledance.core.autonomy.resource_kernel import (
                AdmissionDecision,
            )
            return type(
                "AR", (), {
                    "decision": AdmissionDecision.ACCEPT,
                    "reason": "",
                    "wait_ms": 0.0,
                },
            )()

        def record_enqueue(self): pass
        def record_dequeue(self): pass
        def stats(self): return {}

    svc._admission = _AlwaysAccept()  # type: ignore[assignment]
    return svc, compat


# ── Backwards compatibility: untouched contexts pass through ──


def test_context_without_operation_is_passed_through_unchanged():
    svc, compat = _service_with_recording_compat()
    result = svc.handle_query("free text", context={"profile": "default"})
    assert "structured_request" not in compat.calls[0]["context"]
    assert result.get("source") == "autonomy"
    stats = svc.upstream_structured_request_stats()
    assert stats["derived_total"] == 0
    assert stats["skipped_total"] == 1
    assert stats["rejected_total"] == 0


def test_no_context_at_all_is_safe():
    svc, compat = _service_with_recording_compat()
    result = svc.handle_query("free text")
    # The lift wrapper synthesizes an empty dict and does not derive
    assert "structured_request" not in compat.calls[0]["context"]
    assert "error" not in result
    stats = svc.upstream_structured_request_stats()
    assert stats["skipped_total"] == 1


# ── Derivation: lifted structured_request reaches downstream ──


def test_unit_conversion_lifted_into_context_for_downstream():
    svc, compat = _service_with_recording_compat()
    svc.handle_query(
        "convert 25C to F",
        context={
            "profile": "default",
            "operation": "unit_conversion",
            "from_unit": "C",
            "to_unit": "F",
            "value": 25,
        },
    )
    captured = compat.calls[0]["context"]
    assert captured["structured_request"] == {
        "unit_conversion": {"x": 25.0, "from": "C", "to": "F"},
    }
    # Original flat fields remain — caller's original context is not erased
    assert captured["operation"] == "unit_conversion"
    assert captured["from_unit"] == "C"
    stats = svc.upstream_structured_request_stats()
    assert stats["derived_total"] == 1


def test_threshold_check_lifted_for_downstream():
    svc, compat = _service_with_recording_compat()
    svc.handle_query(
        "co2 below 1000?",
        context={
            "operation": "threshold_check",
            "subject": "co2",
            "x": 950,
            "operator": "<=",
        },
    )
    captured = compat.calls[0]["context"]
    assert captured["structured_request"] == {
        "threshold_check": {"x": 950.0, "subject": "co2", "operator": "<="},
    }


# ── Skip / reject branches do not write structured_request ──


def test_builtin_precedence_skip_does_not_set_structured_request():
    svc, compat = _service_with_recording_compat()
    svc.handle_query(
        "convert 25C to F",
        context={
            "operation": "unit_conversion",
            "from_unit": "C",
            "to_unit": "F",
            "value": 25,
            "builtin_solver_succeeded": True,
        },
    )
    captured = compat.calls[0]["context"]
    assert "structured_request" not in captured
    stats = svc.upstream_structured_request_stats()
    assert stats["skipped_total"] == 1
    assert stats["derived_total"] == 0


def test_high_risk_operation_is_rejected_without_propagation():
    svc, compat = _service_with_recording_compat()
    svc.handle_query(
        "temporal-window",
        context={"operation": "temporal_window_check", "x": 0.5},
    )
    captured = compat.calls[0]["context"]
    assert "structured_request" not in captured
    stats = svc.upstream_structured_request_stats()
    assert stats["rejected_total"] == 1
    assert (
        stats["rejection_counts_by_kind"][
            UPSTREAM_REJECTED_FAMILY_NOT_LOW_RISK
        ]
        == 1
    )


def test_caller_supplied_structured_request_is_rejected_as_ambiguous():
    """If caller already passes structured_request, the upstream lift
    refuses to overwrite. This guarantees the proof contract: the
    upstream caller is the only legitimate producer of
    context["structured_request"]."""
    svc, compat = _service_with_recording_compat()
    svc.handle_query(
        "ambiguous",
        context={
            "operation": "unit_conversion",
            "from_unit": "C",
            "to_unit": "F",
            "value": 25,
            "structured_request": {"unit_conversion": {"x": 1, "from": "a", "to": "b"}},
        },
    )
    stats = svc.upstream_structured_request_stats()
    assert stats["rejected_total"] == 1
    assert (
        stats["rejection_counts_by_kind"][UPSTREAM_REJECTED_AMBIGUOUS] == 1
    )


def test_caller_supplied_low_risk_autonomy_query_is_rejected():
    svc, compat = _service_with_recording_compat()
    svc.handle_query(
        "no manual hint",
        context={
            "operation": "unit_conversion",
            "from_unit": "C",
            "to_unit": "F",
            "value": 25,
            "low_risk_autonomy_query": {"family_kind": "scalar_unit_conversion"},
        },
    )
    stats = svc.upstream_structured_request_stats()
    assert stats["rejected_total"] == 1
    assert (
        stats["rejection_counts_by_kind"][UPSTREAM_REJECTED_AMBIGUOUS] == 1
    )


# ── Extractor error isolation ──


def test_extractor_error_does_not_crash_production_path(monkeypatch):
    """If the upstream extractor raises (defensive), the request must
    still reach the compatibility layer and a normal response must be
    returned. Errors are counted on the service stats."""
    svc, compat = _service_with_recording_compat()

    def _boom(*_a, **_kw):
        raise RuntimeError("simulated extractor failure")

    monkeypatch.setattr(
        "waggledance.application.services.autonomy_service."
        "apply_upstream_structured_request",
        _boom,
    )

    result = svc.handle_query(
        "convert", context={"operation": "unit_conversion"},
    )
    assert "error" not in result  # downstream still produced a normal response
    assert compat.calls, "compatibility layer must still be invoked"
    assert svc._upstream_extractor_errors_total == 1


# ── Stats surface ──


def test_stats_includes_upstream_metrics_block():
    svc, _ = _service_with_recording_compat()
    snapshot = svc.stats()
    assert "upstream_structured_request" in snapshot
    block = snapshot["upstream_structured_request"]
    assert block["derived_total"] == 0
    assert block["rejected_total"] == 0
    assert block["skipped_total"] == 0
    assert block["extractor_errors_total"] == 0
    assert block["rejection_counts_by_kind"] == {}


def test_skipped_total_separates_from_skipped_builtin_precedence_in_kind_log():
    """skipped_total counts both skipped and skipped_builtin_precedence.
    Rejection counts log every non-derived non-skipped kind separately."""
    svc, _ = _service_with_recording_compat()
    # plain skipped (no operation)
    svc.handle_query("a", context={"profile": "x"})
    # skipped_builtin_precedence
    svc.handle_query(
        "b",
        context={
            "operation": "unit_conversion",
            "from_unit": "C",
            "to_unit": "F",
            "value": 1,
            "builtin_solver_succeeded": True,
        },
    )
    stats = svc.upstream_structured_request_stats()
    assert stats["skipped_total"] == 2
    assert stats["derived_total"] == 0
    assert stats["rejected_total"] == 0
