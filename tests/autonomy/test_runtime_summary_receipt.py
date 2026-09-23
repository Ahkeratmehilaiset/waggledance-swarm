# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.autonomy.runtime import AutonomyRuntime
from waggledance.core.capabilities.registry import CapabilityRegistry
from waggledance.core.domain.autonomy import (
    CapabilityCategory,
    CapabilityContract,
)
from waggledance.core.magma.runtime_summary_receipt import (
    write_runtime_summary_receipt_bundle,
)


class _Selection:
    def __init__(
        self,
        capabilities: list[CapabilityContract],
        *,
        fallback_used: bool = False,
    ) -> None:
        self.selected = capabilities
        self.fallback_used = fallback_used


class _RouteResult:
    def __init__(
        self,
        capability: CapabilityContract | None = None,
        *,
        selected: list[CapabilityContract] | None = None,
        fallback_used: bool = False,
    ) -> None:
        capabilities = (
            selected
            if selected is not None
            else ([capability] if capability is not None else [])
        )
        self.selection = _Selection(capabilities, fallback_used=fallback_used)
        self.quality_path = "gold"
        self.autonomy_consult = None
        self.autonomy_served = False
        self.solver_call_trace = []
        if capabilities:
            self.solver_call_trace = [
                {
                    "stage": "solver_call",
                    "status": "selected",
                    "intent": "detect",
                    "capability_id": capabilities[0].capability_id,
                    "selected_index": 0,
                    "quality_path": "gold",
                    "execution_boundary": "safe_action_bus",
                }
            ]


class _Executor:
    available = True

    def execute(self, **_payload):
        return {"success": True, "value": 42}


def _runtime_with_receipt_sink(tmp_path: Path) -> tuple[AutonomyRuntime, list[dict]]:
    reports: list[dict] = []

    def sink(summary: dict) -> dict:
        report = write_runtime_summary_receipt_bundle(
            out_dir=tmp_path / f"runtime-summary-{len(reports) + 1}",
            summary_payload=summary,
            now_utc=datetime(2026, 5, 23, 3, 5, tzinfo=timezone.utc),
            verify_manifest=verify_manifest,
        )
        reports.append(report)
        return report

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
        enable_persistence=False,
        runtime_receipt_sink=sink,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(capability)
    )
    runtime.action_bus.register_executor(
        "detect.fixture",
        lambda _action: {"success": True, "value": 42},
    )
    return runtime, reports


def test_handle_query_emits_opt_in_runtime_summary_receipt(tmp_path: Path) -> None:
    runtime, reports = _runtime_with_receipt_sink(tmp_path)

    result = runtime.handle_query(
        "private runtime query DO_NOT_LEAK",
        context={"operator_note": "context secret DO_NOT_LEAK"},
    )

    assert result["executed"] is True
    assert result["runtime_receipt"]["verifier_report"]["ok"] is True
    assert reports[0]["receipt_count"] == 1
    emitted_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((tmp_path / "runtime-summary-1").rglob("*.json"))
    )
    assert result["solver_call_trace"] == [
        {
            "stage": "solver_call",
            "status": "selected",
            "intent": "detect",
            "capability_id": "detect.fixture",
            "selected_index": 0,
            "quality_path": "gold",
            "execution_boundary": "safe_action_bus",
        }
    ]
    assert "solver_call_trace" in emitted_text
    assert "solver_call_trace_digest" in emitted_text
    assert "private runtime query" not in emitted_text
    assert "context secret" not in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text
    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["sink_configured"] is True
    assert metrics["handle_query_total"] == 1
    assert metrics["solver_trace_present_total"] == 1
    assert metrics["attempt_total"] == 1
    assert metrics["success_total"] == 1
    assert metrics["failure_total"] == 0
    assert metrics["verifier_ok_total"] == 1
    assert metrics["verifier_not_ok_total"] == 0
    assert metrics["verified_receipt_query_total"] == 1
    assert metrics["receipt_count_total"] == 1
    assert metrics["last_verifier_ok"] is True
    assert metrics["last_receipt_count"] == 1
    assert metrics["coverage_ratio"] == 1.0
    assert metrics["solver_trace_presence_ratio"] == 1.0
    assert metrics["verifier_ok_ratio"] == 1.0
    assert metrics["deterministic_route_attempt_total"] == 1
    assert metrics["deterministic_route_selected_total"] == 1
    assert metrics["deterministic_route_fallback_total"] == 0
    assert metrics["deterministic_builtin_verified_success_total"] == 1
    assert metrics["deterministic_hint_skipped_builtin_precedence_total"] == 1
    assert metrics["deterministic_last_selected_count"] == 1
    assert metrics["deterministic_last_fallback_used"] is False
    assert metrics["deterministic_last_builtin_solver_succeeded"] is True
    assert metrics["deterministic_selected_ratio"] == 1.0
    assert metrics["deterministic_builtin_success_ratio"] == 1.0
    assert metrics["default_runtime_receipt_emission_changed"] is False
    assert metrics["runtime_authority_changed"] is False


def test_handle_query_runtime_receipt_verifier_not_ok_metrics() -> None:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="detect.fixture",
        category=CapabilityCategory.DETECT,
        description="Fixture detector",
        success_criteria=["success"],
    )
    registry.register(capability)

    def verifier_not_ok(_summary: dict) -> dict:
        return {
            "receipt_count": 0,
            "verifier_report": {
                "ok": False,
                "receipt_count": 0,
                "errors": ["verifier_error:public"],
            },
            "paths_returned": False,
            "payloads_returned": False,
        }

    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_persistence=False,
        runtime_receipt_sink=verifier_not_ok,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(capability)
    )
    runtime.action_bus.register_executor(
        "detect.fixture",
        lambda _action: {"success": True},
    )

    result = runtime.handle_query("receipt verifier not ok")

    assert result["runtime_receipt"]["verifier_report"]["ok"] is False
    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["failure_total"] == 0
    assert metrics["verifier_ok_total"] == 0
    assert metrics["verifier_not_ok_total"] == 1
    assert metrics["verified_receipt_query_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["last_result_present"] is True
    assert metrics["last_verifier_ok"] is False
    assert metrics["last_receipt_count"] == 0
    assert metrics["coverage_ratio"] == 0.0
    assert metrics["verifier_ok_ratio"] == 0.0


def test_runtime_receipt_coverage_requires_verified_nonzero_top_level_count(
    tmp_path: Path,
) -> None:
    runtime, _reports = _runtime_with_receipt_sink(tmp_path)
    runtime.runtime_receipt_sink = lambda _summary: {
        "receipt_count": 0,
        "verifier_report": {"ok": True, "receipt_count": 1},
    }

    runtime.handle_query("verified but empty receipt result")

    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["verifier_ok_total"] == 1
    assert metrics["verified_receipt_query_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["coverage_ratio"] == 0.0


@pytest.mark.parametrize(
    "receipt_result",
    [
        {"verifier_report": {"ok": True, "receipt_count": 1}},
        {"receipt_count": "1", "verifier_report": {"ok": True}},
        {"receipt_count": True, "verifier_report": {"ok": True}},
        {"receipt_count": 1.5, "verifier_report": {"ok": True}},
        {"receipt_count": -1, "verifier_report": {"ok": True}},
    ],
)
def test_runtime_receipt_coverage_rejects_missing_or_malformed_top_level_count(
    tmp_path: Path,
    receipt_result: dict,
) -> None:
    runtime, _reports = _runtime_with_receipt_sink(tmp_path)
    runtime.runtime_receipt_sink = lambda _summary: receipt_result

    runtime.handle_query("malformed receipt count")

    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["verifier_ok_total"] == 1
    assert metrics["verified_receipt_query_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["coverage_ratio"] == 0.0


def test_runtime_receipt_coverage_requires_literal_verifier_true(
    tmp_path: Path,
) -> None:
    runtime, _reports = _runtime_with_receipt_sink(tmp_path)
    runtime.runtime_receipt_sink = lambda _summary: {
        "receipt_count": 1,
        "verifier_report": {"ok": "true", "receipt_count": 1},
    }

    runtime.handle_query("malformed verifier ok")

    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["verifier_ok_total"] == 0
    assert metrics["verifier_not_ok_total"] == 1
    assert metrics["verified_receipt_query_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["coverage_ratio"] == 0.0


@pytest.mark.parametrize(
    "receipt_result",
    [
        {
            "receipt_count": 2,
            "verifier_report": {"ok": True, "receipt_count": 0},
        },
        {
            "receipt_count": 1,
            "verifier_report": {"ok": True, "receipt_count": 2},
        },
        {"receipt_count": 1, "verifier_report": {"ok": True}},
        {
            "receipt_count": 1,
            "verifier_report": {"ok": True, "receipt_count": "1"},
        },
        {
            "receipt_count": 1,
            "verifier_report": {"ok": True, "receipt_count": True},
        },
    ],
)
def test_runtime_receipt_coverage_rejects_missing_malformed_or_mismatched_verifier_count(
    tmp_path: Path,
    receipt_result: dict,
) -> None:
    runtime, _reports = _runtime_with_receipt_sink(tmp_path)
    runtime.runtime_receipt_sink = lambda _summary: receipt_result

    runtime.handle_query("contradictory verifier receipt count")

    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["verifier_ok_total"] == 1
    assert metrics["verified_receipt_query_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["last_receipt_count"] == 0
    assert metrics["coverage_ratio"] == 0.0


def test_runtime_receipt_metrics_prefer_top_level_receipt_count(
    tmp_path: Path,
) -> None:
    runtime, _reports = _runtime_with_receipt_sink(tmp_path)

    runtime.runtime_receipt_sink = lambda _summary: {
        "receipt_count": 0,
        "verifier_report": {"ok": False, "receipt_count": 1},
    }

    result = runtime.handle_query("receipt count disagreement path")

    assert result["runtime_receipt"]["receipt_count"] == 0
    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["verifier_ok_total"] == 0
    assert metrics["verifier_not_ok_total"] == 1
    assert metrics["receipt_count_total"] == 0
    assert metrics["last_verifier_ok"] is False
    assert metrics["last_receipt_count"] == 0
    assert metrics["verifier_ok_ratio"] == 0.0


def test_runtime_receipt_metrics_do_not_count_unverified_fallback_receipts(
    tmp_path: Path,
) -> None:
    runtime, _reports = _runtime_with_receipt_sink(tmp_path)

    runtime.runtime_receipt_sink = lambda _summary: {
        "verifier_report": {"ok": False, "receipt_count": 1},
    }

    result = runtime.handle_query("unverified fallback receipt count")

    assert result["runtime_receipt"]["verifier_report"]["ok"] is False
    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["success_total"] == 1
    assert metrics["verifier_ok_total"] == 0
    assert metrics["verifier_not_ok_total"] == 1
    assert metrics["receipt_count_total"] == 0
    assert metrics["last_verifier_ok"] is False
    assert metrics["last_receipt_count"] == 0
    assert metrics["verifier_ok_ratio"] == 0.0


def test_handle_query_without_runtime_receipt_sink_records_default_off_metrics() -> None:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="detect.fixture",
        category=CapabilityCategory.DETECT,
        description="Fixture detector",
        success_criteria=["success"],
    )
    registry.register(capability)
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_persistence=False,
        runtime_receipt_sink=None,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(capability)
    )
    runtime.action_bus.register_executor(
        "detect.fixture",
        lambda _action: {"success": True},
    )

    result = runtime.handle_query("receipt metrics default off")

    assert result["executed"] is True
    assert "runtime_receipt" not in result
    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["sink_configured"] is False
    assert metrics["handle_query_total"] == 1
    assert metrics["solver_trace_present_total"] == 1
    assert metrics["sink_not_configured_total"] == 1
    assert metrics["attempt_total"] == 0
    assert metrics["success_total"] == 0
    assert metrics["failure_total"] == 0
    assert metrics["verifier_ok_total"] == 0
    assert metrics["verifier_not_ok_total"] == 0
    assert metrics["verified_receipt_query_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["last_verifier_ok"] is False
    assert metrics["last_receipt_count"] == 0
    assert metrics["coverage_ratio"] == 0.0
    assert metrics["solver_trace_presence_ratio"] == 1.0
    assert metrics["verifier_ok_ratio"] == 0.0
    assert metrics["deterministic_route_attempt_total"] == 1
    assert metrics["deterministic_route_selected_total"] == 1
    assert metrics["deterministic_route_fallback_total"] == 0
    assert metrics["deterministic_builtin_verified_success_total"] == 1
    assert metrics["deterministic_hint_skipped_builtin_precedence_total"] == 1
    assert metrics["deterministic_last_selected_count"] == 1
    assert metrics["deterministic_last_fallback_used"] is False
    assert metrics["deterministic_last_builtin_solver_succeeded"] is True
    assert metrics["deterministic_selected_ratio"] == 1.0
    assert metrics["deterministic_builtin_success_ratio"] == 1.0


def test_handle_query_records_fallback_route_without_selected_capability() -> None:
    registry = CapabilityRegistry(load_builtins=False)
    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_persistence=False,
        runtime_receipt_sink=None,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(
            selected=[],
            fallback_used=True,
        )
    )

    result = runtime.handle_query("receipt metrics no selected route")

    assert result["error"] == "No capabilities available"
    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["handle_query_total"] == 0
    assert metrics["deterministic_route_attempt_total"] == 1
    assert metrics["deterministic_route_selected_total"] == 0
    assert metrics["deterministic_route_fallback_total"] == 1
    assert metrics["deterministic_builtin_verified_success_total"] == 0
    assert metrics["deterministic_hint_skipped_builtin_precedence_total"] == 0
    assert metrics["deterministic_last_selected_count"] == 0
    assert metrics["deterministic_last_fallback_used"] is True
    assert metrics["deterministic_last_builtin_solver_succeeded"] is False
    assert metrics["deterministic_selected_ratio"] == 0.0
    assert metrics["deterministic_builtin_success_ratio"] == 0.0


def test_handle_query_runtime_receipt_sink_failure_blocks_opt_in_path(
    tmp_path: Path,
) -> None:
    registry = CapabilityRegistry(load_builtins=False)
    capability = CapabilityContract(
        capability_id="detect.fixture",
        category=CapabilityCategory.DETECT,
        description="Fixture detector",
        success_criteria=["success"],
    )
    registry.register(capability)

    def boom(_summary: dict) -> None:
        raise RuntimeError("runtime receipt sink boom")

    runtime = AutonomyRuntime(
        capability_registry=registry,
        enable_persistence=False,
        runtime_receipt_sink=boom,
    )
    runtime.solver_router.route = (
        lambda _intent, _query, _context: _RouteResult(capability)
    )
    runtime.action_bus.register_executor(
        "detect.fixture",
        lambda _action: {"success": True},
    )

    with pytest.raises(RuntimeError, match="runtime receipt sink boom"):
        runtime.handle_query("receipt failure path")

    metrics = runtime.runtime_receipt_metrics_snapshot()
    assert metrics["sink_configured"] is True
    assert metrics["handle_query_total"] == 1
    assert metrics["solver_trace_present_total"] == 1
    assert metrics["attempt_total"] == 1
    assert metrics["success_total"] == 0
    assert metrics["failure_total"] == 1
    assert metrics["verifier_ok_total"] == 0
    assert metrics["verifier_not_ok_total"] == 0
    assert metrics["receipt_count_total"] == 0
    assert metrics["last_result_present"] is False
    assert metrics["last_verifier_ok"] is False
    assert metrics["last_receipt_count"] == 0
