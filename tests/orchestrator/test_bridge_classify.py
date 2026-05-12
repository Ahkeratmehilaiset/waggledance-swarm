# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / ".orchestrator" / "bridge_classify.py"
    spec = importlib.util.spec_from_file_location("eig2_bridge_classify", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_invariant_break_detected() -> None:
    mod = _load_module()
    result = mod.classify("hard rule violated: compact card replaced raw MAGMA data")
    assert result == mod.RegressionClass.INVARIANT_BREAK


def test_semantic_regression_detected() -> None:
    mod = _load_module()
    result = mod.classify(
        "route decision differs: expected route hub, actual route factory"
    )
    assert result == mod.RegressionClass.SEMANTIC_REGRESSION


def test_human_prompt_detected_as_invariant_break() -> None:
    mod = _load_module()
    result = mod.classify("manual approval required before continuing")
    assert result == mod.RegressionClass.INVARIANT_BREAK


def test_property_test_overrides_classifier_when_invariant_failed() -> None:
    mod = _load_module()
    result = mod.classify(
        {
            "property_test_failed": True,
            "invariant_failed": True,
            "trace": "latency p99 increased",
        }
    )
    assert result == mod.RegressionClass.INVARIANT_BREAK


def test_single_invariant_failure_flags_are_invariant_breaks() -> None:
    mod = _load_module()
    for flag in (
        "property_test_failed",
        "property_failed",
        "invariant_test_failed",
        "invariant_failed",
        "safety_invariant_failed",
        "hard_rule_failed",
    ):
        assert mod.classify({flag: True}) == mod.RegressionClass.INVARIANT_BREAK


def test_hot_path_llm_precedes_latency_pattern() -> None:
    mod = _load_module()
    result = mod.classify("hot-path LLM call caused p99 routing regression")
    assert result == mod.RegressionClass.HOT_PATH_LLM_VIOLATION


def test_m0_scope_leak_detected_as_invariant_break() -> None:
    mod = _load_module()
    result = mod.classify("M0 scope leak: modified waggledance/core/reasoning/x.py")
    assert result == mod.RegressionClass.INVARIANT_BREAK


def test_rco_inventory_gap_detected_as_invariant_break() -> None:
    mod = _load_module()
    result = mod.classify(
        "adapter proposal references unlisted hook not present in M0-reality-check"
    )
    assert result == mod.RegressionClass.INVARIANT_BREAK


def test_write_without_backpressure_detected_as_invariant_break() -> None:
    mod = _load_module()
    result = mod.classify("new writer has breaker before backpressure")
    assert result == mod.RegressionClass.INVARIANT_BREAK


def test_version_collision_detected_as_invariant_break() -> None:
    mod = _load_module()
    result = mod.classify("release plan would reuse release version v3.12.0")
    assert result == mod.RegressionClass.INVARIANT_BREAK
