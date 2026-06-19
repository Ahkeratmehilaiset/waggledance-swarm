# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from waggledance.bootstrap.container import Container
from waggledance.core.magma.runtime_summary_receipt import (
    build_handle_query_runtime_summary,
)


ROOT = Path(__file__).resolve().parents[2]


class _Settings:
    runtime_primary = "waggledance"
    compatibility_mode = False
    night_stall_threshold = 10
    db_path = "data/test-runtime-receipt.db"
    ollama_host = "http://localhost:11434"
    chat_model = "phi4-mini"
    ollama_timeout_seconds = 1.0

    def __init__(self, extras: dict[str, Any] | None = None) -> None:
        self._extras = extras or {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._extras.get(key, default)

    def get_profile(self) -> str:
        return "HOME"

    def get_hardware_tier(self) -> str:
        return "standard"


def _runtime_summary() -> dict[str, Any]:
    return build_handle_query_runtime_summary(
        query="private runtime query DO_NOT_LEAK",
        context={"operator_note": "context secret DO_NOT_LEAK"},
        profile="HOME",
        intent="detect",
        quality_path="gold",
        capability_id="detect.fixture",
        action_id="action-fixture-1",
        approved=True,
        executed=True,
        needs_approval=False,
        decision_reason="private decision DO_NOT_LEAK",
        elapsed_ms=12.34,
        snapshot_id="snapshot:fixture",
        case_id="case:autonomy_runtime:fixture",
        verifier_passed=True,
        verifier_confidence=0.91,
        result_keys=["approved", "executed", "intent"],
        solver_call_trace=[
            {
                "stage": "solver_call",
                "status": "selected",
                "intent": "detect",
                "capability_id": "detect.fixture",
                "selected_index": 0,
                "quality_path": "gold",
                "execution_boundary": "safe_action_bus",
            }
        ],
    )


def test_container_leaves_runtime_receipt_sink_disabled_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    container = Container(settings=_Settings(), stub=True)
    runtime = container.autonomy_service._runtime

    assert runtime.runtime_receipt_sink is None
    assert runtime.runtime_receipt_metrics_snapshot()["sink_configured"] is False


def test_container_runtime_receipt_sink_returns_path_free_verified_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    receipt_root = tmp_path / "runtime-receipts"
    settings = _Settings(
        {
            "runtime.runtime_receipt_sink": {
                "enabled": True,
                "out_dir": str(receipt_root),
                "evaluation_version": "magma.evaluation_result.v1",
            }
        }
    )

    container = Container(settings=settings, stub=True)
    runtime = container.autonomy_service._runtime
    sink = runtime.runtime_receipt_sink

    assert sink is not None
    report = sink(_runtime_summary())

    assert report == {
        "receipt_count": 1,
        "verifier_report": {
            "ok": True,
            "receipt_count": 1,
            "errors": [],
        },
        "local_artifacts_written": True,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "paths_returned": False,
    }
    serialized_report = json.dumps(report, sort_keys=True)
    assert str(receipt_root) not in serialized_report
    assert "manifest" not in serialized_report
    assert "out_dir" not in serialized_report

    bundles = list(receipt_root.iterdir())
    assert len(bundles) == 1
    assert (bundles[0] / "manifest.json").is_file()


def test_settings_yaml_keeps_runtime_receipt_sink_default_off() -> None:
    cfg = yaml.safe_load((ROOT / "configs" / "settings.yaml").read_text())

    sink_cfg = cfg["runtime"]["runtime_receipt_sink"]

    assert sink_cfg["enabled"] is False
    assert sink_cfg["out_dir"] == "data/runtime/runtime_summary_receipts"
    assert sink_cfg["evaluation_version"] == "magma.evaluation_result.v1"
