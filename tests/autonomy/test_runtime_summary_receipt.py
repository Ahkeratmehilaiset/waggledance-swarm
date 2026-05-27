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
    def __init__(self, capability: CapabilityContract) -> None:
        self.selected = [capability]


class _RouteResult:
    def __init__(self, capability: CapabilityContract) -> None:
        self.selection = _Selection(capability)
        self.quality_path = "gold"
        self.autonomy_consult = None
        self.autonomy_served = False
        self.solver_call_trace = [
            {
                "stage": "solver_call",
                "status": "selected",
                "intent": "detect",
                "capability_id": capability.capability_id,
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
