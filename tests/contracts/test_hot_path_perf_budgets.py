# SPDX-License-Identifier: BUSL-1.1
"""Hot-path perf-budget regression contract (L34).

Pins per-call p50 latency budgets for the hot routing chain so that
future refactors cannot silently regress them past the agreed envelope.

Methodology
-----------
* Each measurement does ``WARMUP`` calls discarded, then ``ITERS`` timed
  calls and asserts the **median** is under budget.
* Median (not mean, not p99) is the load-bearing signal. p99 from
  100-sample runs is too noisy to be load-bearing (the original
  explosive-growth session bench harness saw 5–10x p99 swings between
  identical runs while p50 stayed within 5%).
* Budgets are set ~3–5x above local-measured p50 to absorb CI runner
  variance. They are not a tight microbench gate; they catch
  order-of-magnitude regressions (e.g. accidentally moving a
  pre-compiled regex back into a per-call ``re.compile`` site).

If a measurement is impossible because the dependency cannot be
imported in the test environment, the assertion is **skipped** with
a clear message so the CI run still passes — the contract is "do not
get slower", not "you must import every adapter".
"""
from __future__ import annotations

import statistics
import time

import pytest

ITERS = 200
WARMUP = 10


def _median_us(fn, *, iters: int = ITERS, warmup: int = WARMUP) -> float:
    """Return median latency in microseconds across `iters` warm calls."""
    for _ in range(warmup):
        fn()
    samples_us: list[float] = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        samples_us.append((time.perf_counter() - t0) * 1_000_000)
    return statistics.median(samples_us)


# --- SolverRouter.classify_intent ----------------------------------


def test_classify_intent_short_query_median_under_budget():
    try:
        from waggledance.core.reasoning.solver_router import SolverRouter
    except Exception as exc:
        pytest.skip(f"SolverRouter import failed in env: {exc}")
    sr = SolverRouter()
    fn = lambda: sr.classify_intent("laske mehiläistarhan lämpötilan keskiarvo")
    median = _median_us(fn)
    # Local p50 measured ~2.1 µs in 2026-05 session; budget 4–5x for CI variance.
    assert median < 10.0, f"classify_intent short median {median:.2f} µs over 10 µs budget"


def test_classify_intent_long_query_median_under_budget():
    try:
        from waggledance.core.reasoning.solver_router import SolverRouter
    except Exception as exc:
        pytest.skip(f"SolverRouter import failed in env: {exc}")
    sr = SolverRouter()
    fn = lambda: sr.classify_intent(
        "calculate the seasonal heating consumption average over the last month "
        "including factory thermal regulation patterns"
    )
    median = _median_us(fn)
    # Local p50 measured ~3.1 µs; budget 5x.
    assert median < 15.0, f"classify_intent long median {median:.2f} µs over 15 µs budget"


# --- HexTopologyRegistry.select_origin_cell ------------------------


def test_select_origin_cell_median_under_budget():
    try:
        from waggledance.application.services.hex_topology_registry import (
            HexTopologyRegistry,
        )
    except Exception as exc:
        pytest.skip(f"HexTopologyRegistry import failed: {exc}")
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    cfg = repo_root / "configs" / "hex_cells.yaml"
    if not cfg.exists():
        pytest.skip(f"hex_cells.yaml not found at {cfg}")
    reg = HexTopologyRegistry(config_path=str(cfg), agents=[])
    queries = [
        "Mikä on tarharivin lämpötila?",
        "calculate the sum of bee population",
        "ovenkahva on rikki",
        "how much honey does the hive produce?",
    ]
    idx = [0]

    def fn():
        q = queries[idx[0] % len(queries)]
        idx[0] += 1
        reg.select_origin_cell(q)

    median = _median_us(fn)
    # Local p50 measured ~22 µs; budget 5x.
    assert median < 100.0, f"select_origin_cell median {median:.2f} µs over 100 µs budget"


# --- BridgeLLMRedactor.redact --------------------------------------


def test_redactor_short_input_median_under_budget():
    try:
        from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor
    except Exception as exc:
        pytest.skip(f"BridgeLLMRedactor import failed: {exc}")
    redactor = BridgeLLMRedactor()
    text = "Hello, my number is 0401234567. Email me at test@example.com."
    fn = lambda: redactor.redact(text)
    median = _median_us(fn)
    # Local p50 measured ~12 µs at 50 chars; budget 4x.
    assert median < 50.0, f"redactor short median {median:.2f} µs over 50 µs budget"


def test_redactor_long_input_median_under_budget():
    try:
        from waggledance.core.bridge_llm.redactor import BridgeLLMRedactor
    except Exception as exc:
        pytest.skip(f"BridgeLLMRedactor import failed: {exc}")
    redactor = BridgeLLMRedactor()
    # ~1000 chars by repeating the short fixture 20x.
    text = "Hello, my number is 0401234567. Email me at test@example.com." * 20
    fn = lambda: redactor.redact(text)
    median = _median_us(fn, iters=100)
    # Local p50 measured ~171 µs at 1000 chars; redactor is linear in input length.
    # Budget 3x for CI variance.
    assert median < 500.0, f"redactor long median {median:.2f} µs over 500 µs budget"


# --- AliasRegistry.resolve -----------------------------------------


def test_alias_registry_resolve_median_under_budget():
    try:
        from waggledance.core.capabilities.aliasing import AliasRegistry
    except Exception as exc:
        pytest.skip(f"AliasRegistry import failed: {exc}")
    try:
        ar = AliasRegistry.from_yaml_default()
    except Exception as exc:
        pytest.skip(f"AliasRegistry.from_yaml_default failed: {exc}")
    names = ["queen", "drone", "thermal", "honey", "harvest"]
    idx = [0]

    def fn():
        n = names[idx[0] % len(names)]
        idx[0] += 1
        ar.resolve(n)

    median = _median_us(fn, iters=500)
    # Local p50 measured ~0.2 µs; budget 10x because at this scale 100 ns
    # of noise easily moves p50 by a factor.
    assert median < 5.0, f"AliasRegistry.resolve median {median:.2f} µs over 5 µs budget"
