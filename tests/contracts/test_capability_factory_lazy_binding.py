# SPDX-License-Identifier: BUSL-1.1
"""Capability factory lazy binding contract (L54-reframed, ADR-041)."""
from __future__ import annotations

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ADR_PATH = PROJECT_ROOT / "docs" / "eig2" / "adr" / "041-capability-factory-lazy-binding.md"
CONTRACT_PATH = (
    PROJECT_ROOT / "docs" / "eig2" / "contracts" / "capability_factory_lazy_binding.json"
)

REQUIRED_INVARIANT_IDS = {f"CFB-00{i}" for i in range(1, 8)}


def test_adr_041_file_exists() -> None:
    assert ADR_PATH.exists()


def test_adr_041_marks_substrate_only_landing() -> None:
    assert "substrate-only landing" in ADR_PATH.read_text(encoding="utf-8").lower()


def test_machine_readable_contract_exists() -> None:
    assert CONTRACT_PATH.exists()


def test_new_api_method_pinned() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert contract["new_api"]["method"] == "register_executor_factory"


def test_empirical_baseline_pinned() -> None:
    """Pin the measured numbers so future regressions are visible.
    Source: .tmp-claude-1000-obs/bench-capability-loader.py + bench-per-adapter-import.py"""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    baseline = contract["empirical_baseline"]
    assert baseline["bind_executors_eager_median_ms"] == 6855
    assert baseline["per_adapter_import_only_total_ms"] == 467
    assert baseline["instantiation_overhead_pct"] == 93
    assert baseline["target_lazy_bind_ms"] == 50


def test_contract_invariants_match_required_set() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    ids = {item["id"] for item in contract["invariants"]}
    assert ids == REQUIRED_INVARIANT_IDS


def test_each_invariant_has_must_clauses() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    for item in contract["invariants"]:
        assert isinstance(item.get("must"), list) and item["must"]


def test_defer_both_import_and_construct() -> None:
    """CFB-002 is the critical invariant: deferring ONLY the import does
    not capture the 93% instantiation overhead. The factory must defer
    BOTH the import AND the construction."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    cfb002 = next((i for i in contract["invariants"] if i["id"] == "CFB-002"), None)
    must_text = " ".join(cfb002.get("must", [])).lower()
    assert "no `from waggledance.adapters.capabilities" in must_text.lower() or "lazy" in must_text
    assert "no adapterx() at register time" in must_text or "no instantiation" in must_text.lower() or "no construct" in must_text


def test_no_regression_on_eager_api_invariant() -> None:
    """CFB-006 preserves backward compat. Existing tests that use
    register_executor (eager) MUST continue to pass."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    cfb006 = next((i for i in contract["invariants"] if i["id"] == "CFB-006"), None)
    must_text = " ".join(cfb006.get("must", [])).lower()
    assert "old api preserved" in must_text or "no signature change" in must_text or "continue to pass" in must_text


def test_boot_saving_target_under_100ms() -> None:
    """CFB-007 sets the regression target. < 100 ms means ~99% reduction
    vs baseline 6855 ms."""
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    target = contract["empirical_baseline"]["target_lazy_bind_ms"]
    baseline = contract["empirical_baseline"]["bind_executors_eager_median_ms"]
    reduction_pct = (1 - target / baseline) * 100
    assert reduction_pct >= 98, (
        f"Target {target} ms vs baseline {baseline} ms is only {reduction_pct:.1f}% reduction. "
        "Should be >= 98% to capture the substantive win."
    )
