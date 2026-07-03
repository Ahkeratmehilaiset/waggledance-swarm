# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Deterministic benchmark for the hex ROUTING mesh (sprint seed #11).

Measures what turning ``hex_mesh.enabled`` on actually does to query
resolution, using the REAL ``HexNeighborAssist`` routing pipeline and the
production ``configs/hex_cells.yaml`` topology, with everything
non-deterministic replaced by fixtures:

- synthetic agents matched to each cell's domain selectors (no live roster);
- a deterministic fake LLM (canned per-category answers, zero network);
- a fixed workload of categorized queries (no randomness, no wall-clock in
  the RESULT payload - runtime duration is reported separately so reruns
  stay byte-comparable).

This benchmarks the routing layer only (escalation ladder local -> neighbor
-> global, per-cell distribution, efficiency counters) - NOT answer quality
and NOT the out-of-scope shadow subdivision runtime (core/hex_topology).

Usage:
    python tools/run_hex_routing_benchmark.py \
        [--queries-per-category 6] [--output-json PATH] [--output-md PATH]

Output: a JSON result document (stdout by default) and optionally a small
markdown report, intended for docs/benchmarks/.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from waggledance.application.services.hex_health_monitor import (  # noqa: E402
    HexHealthMonitor,
)
from waggledance.application.services.hex_neighbor_assist import (  # noqa: E402
    HexNeighborAssist,
)
from waggledance.application.services.hex_topology_registry import (  # noqa: E402
    HexTopologyRegistry,
)


# One synthetic-agent domain token per production cell cluster
# (configs/hex_cells.yaml domain_selectors).
CELL_DOMAINS = (
    "general",      # hub
    "apiary",       # bee_ops
    "weather",      # environment
    "energy",       # home_comfort
    "safety",       # safety/security cluster
    "production",   # industrial cluster
    "logistics",    # operations cluster
)

# Deterministic workload: category -> query templates. Chosen to spread
# across the topology's domains; wording is fixed so the fake LLM (and the
# service's internal confidence heuristics) behave identically on rerun.
WORKLOAD = {
    "apiary": "How many varroa treatments does the apiary schedule need?",
    "weather": "What is the frost risk for the coming week at the site?",
    "energy": "Which hours have the lowest energy price for heating?",
    "safety": "Is the fire alarm loop in a healthy state?",
    "production": "What is the quality inspection backlog in the lab?",
    "logistics": "When is the next supply delivery scheduled?",
    "general": "Summarize the overall system status.",
}


class DeterministicLLM:
    """Fake LLM: canned answer per query, deterministic, counts calls."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        prompt = str(kwargs.get("prompt") or (args[0] if args else ""))
        # Two deterministic answer bands so the benchmark exercises the whole
        # escalation ladder: hedged/short answers (low estimated confidence)
        # for two categories to trigger neighbor-assist, a substantive
        # answer (high confidence) for the rest to resolve locally.
        if "frost risk" in prompt or "supply delivery" in prompt:
            return "Possibly, but it is unclear and might vary."
        return (
            "Deterministic benchmark answer: the measured value is 42.0 "
            "units according to the fixture table, with no anomalies "
            "detected in the reference window."
        )


def _synthetic_agents(per_domain: int = 3) -> list[SimpleNamespace]:
    agents: list[SimpleNamespace] = []
    for domain in CELL_DOMAINS:
        for i in range(per_domain):
            agents.append(
                SimpleNamespace(
                    name=f"bench-{domain}-{i}",
                    domain=domain,
                    tags=[domain],
                    active=True,
                )
            )
    return agents


def _build_service(*, enabled: bool, llm: DeterministicLLM) -> HexNeighborAssist:
    registry = HexTopologyRegistry(
        config_path=str(REPO_ROOT / "configs" / "hex_cells.yaml"),
        agents=_synthetic_agents(),
    )
    return HexNeighborAssist(
        topology_registry=registry,
        health_monitor=HexHealthMonitor(),
        llm_service=llm,
        enabled=enabled,
        magma_trace_enabled=False,  # benchmark isolation: no audit writes
    )


def _run_mode(*, enabled: bool, queries_per_category: int) -> dict[str, Any]:
    llm = DeterministicLLM()
    service = _build_service(enabled=enabled, llm=llm)

    outcomes: dict[str, int] = {"resolved": 0, "fallback_none": 0}
    started = time.perf_counter()
    for round_index in range(queries_per_category):
        for category, template in sorted(WORKLOAD.items()):
            query = f"{template} (round {round_index})"
            result = asyncio.run(service.resolve(query))
            if result is None:
                outcomes["fallback_none"] += 1
            else:
                outcomes["resolved"] += 1
    duration_s = time.perf_counter() - started

    metrics = service.get_metrics()
    efficiency = service.get_efficiency_stats()
    return {
        "enabled": enabled,
        "workload": {
            "categories": sorted(WORKLOAD),
            "queries_per_category": queries_per_category,
            "total_queries": queries_per_category * len(WORKLOAD),
        },
        "outcomes": outcomes,
        "llm_calls": llm.calls,
        "metrics": metrics,
        "efficiency": efficiency,
        # Reported OUTSIDE the deterministic payload consumers should diff:
        "_runtime_seconds": round(duration_s, 3),
    }


class _HedgedLocalLLM:
    """Local LLM that returns a short/hedged answer -> low local confidence,
    so the origin cell cannot resolve on its own and the ladder reaches the
    neighbor-assist rung."""

    def __init__(self) -> None:
        self.calls = 0

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        return "Maybe. It is unclear and may vary."


class _FixedNeighborDispatcher:
    """A minimal enabled ParallelLLMDispatcher stand-in.

    Its presence makes HexNeighborAssist take the PARALLEL neighbor path
    (bypassing the sequential low-value skip), and it returns one strong,
    high-confidence answer per neighbor request so the merged neighbor
    confidence clears the neighbor threshold. Deterministic, no network.
    """

    enabled = True

    def __init__(self) -> None:
        self.batches = 0

    async def dispatch_batch(self, batch: list[Any]) -> list[str]:
        self.batches += 1
        strong = (
            "The definitive answer is 42.0 units, computed precisely from the "
            "reference dataset with full confidence and no ambiguity; the value "
            "is exact and verified across every measurement in the window."
        )
        return [strong for _ in batch]


def _run_neighbor_mode(*, queries_per_category: int) -> dict[str, Any]:
    """Exercise the neighbor-assist rung the enabled/disabled workload leaves
    at 0 (the documented v1 limitation): weak local answers + a fixed
    high-confidence neighbor dispatcher, with preflight gating opened so the
    ladder reaches neighbors. Isolated fixture, not the production default."""
    registry = HexTopologyRegistry(
        config_path=str(REPO_ROOT / "configs" / "hex_cells.yaml"),
        agents=_synthetic_agents(),
    )
    local = _HedgedLocalLLM()
    dispatcher = _FixedNeighborDispatcher()
    service = HexNeighborAssist(
        topology_registry=registry,
        health_monitor=HexHealthMonitor(),
        llm_service=local,
        parallel_dispatcher=dispatcher,
        enabled=True,
        magma_trace_enabled=False,
        preflight_min_score=0.0,  # open the preflight gate so the ladder runs
    )

    outcomes: dict[str, int] = {"resolved": 0, "fallback_none": 0}
    started = time.perf_counter()
    for round_index in range(queries_per_category):
        for _category, template in sorted(WORKLOAD.items()):
            result = asyncio.run(service.resolve(f"{template} (round {round_index})"))
            outcomes["resolved" if result is not None else "fallback_none"] += 1
    duration_s = time.perf_counter() - started

    metrics = service.get_metrics()
    return {
        "enabled": True,
        "scenario": "neighbor_assist_fixture",
        "workload": {
            "categories": sorted(WORKLOAD),
            "queries_per_category": queries_per_category,
            "total_queries": queries_per_category * len(WORKLOAD),
        },
        "outcomes": outcomes,
        "local_llm_calls": local.calls,
        "neighbor_batches": dispatcher.batches,
        "metrics": metrics,
        "efficiency": service.get_efficiency_stats(),
        "_runtime_seconds": round(duration_s, 3),
    }


def _deterministic_view(mode_result: dict[str, Any]) -> dict[str, Any]:
    """The rerun-comparable slice (drops wall-clock and timing averages)."""
    view = {k: v for k, v in mode_result.items() if not k.startswith("_")}
    # Efficiency stats contain measured latencies; keep only counters.
    view["efficiency"] = {
        k: v
        for k, v in dict(mode_result.get("efficiency") or {}).items()
        if isinstance(v, (int, bool, str))
    }
    metrics = dict(view.get("metrics") or {})
    view["metrics"] = {
        k: v for k, v in metrics.items() if isinstance(v, (int, bool, str))
    }
    return view


def run_benchmark(queries_per_category: int = 6) -> dict[str, Any]:
    baseline = _run_mode(enabled=False, queries_per_category=queries_per_category)
    routed = _run_mode(enabled=True, queries_per_category=queries_per_category)
    neighbor = _run_neighbor_mode(queries_per_category=queries_per_category)
    return {
        "benchmark": "hex_routing_enable_benchmark.v2",
        "topology_config": "configs/hex_cells.yaml",
        "modes": {
            "disabled": baseline,
            "enabled": routed,
            "neighbor_assist": neighbor,
        },
        "deterministic_views": {
            "disabled": _deterministic_view(baseline),
            "enabled": _deterministic_view(routed),
            "neighbor_assist": _deterministic_view(neighbor),
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    dis = result["modes"]["disabled"]
    ena = result["modes"]["enabled"]
    nbr = result["modes"]["neighbor_assist"]
    lines = [
        "# Hex routing benchmark (sprint seed #11)",
        "",
        f"Topology: `{result['topology_config']}` — "
        f"{ena['metrics'].get('cells_loaded', '?')} cells, synthetic agents, "
        "deterministic fake LLM (no network).",
        "",
        f"Workload: {ena['workload']['total_queries']} queries "
        f"({ena['workload']['queries_per_category']} per category × "
        f"{len(ena['workload']['categories'])} categories).",
        "",
        "| metric | hex_mesh disabled | hex_mesh enabled |",
        "|---|---|---|",
        f"| resolved by hex mesh | {dis['outcomes']['resolved']} | {ena['outcomes']['resolved']} |",
        f"| fell back to non-hex path | {dis['outcomes']['fallback_none']} | {ena['outcomes']['fallback_none']} |",
        f"| LLM calls | {dis['llm_calls']} | {ena['llm_calls']} |",
        f"| local-only resolutions | {dis['metrics'].get('local_only_resolutions', 0)} | {ena['metrics'].get('local_only_resolutions', 0)} |",
        f"| neighbor-assist resolutions | {dis['metrics'].get('neighbor_assist_resolutions', 0)} | {ena['metrics'].get('neighbor_assist_resolutions', 0)} |",
        f"| global escalations | {dis['metrics'].get('global_escalations', 0)} | {ena['metrics'].get('global_escalations', 0)} |",
        f"| runtime (s, informational) | {dis['_runtime_seconds']} | {ena['_runtime_seconds']} |",
        "",
        "Disabled mode returns `None` for every query (chat falls through to",
        "the ordinary pipeline), so the enabled column shows what the mesh",
        "actually adds: local resolution and the escalation ladder.",
        "",
        "## Neighbor-assist rung (v2)",
        "",
        "The v1 enabled workload never reached the neighbor-assist rung "
        "(counter stayed 0) because hedged local answers were low-preflight "
        "and got skipped straight to global. This fixture opens the preflight "
        "gate and supplies weak local answers + a fixed high-confidence "
        "neighbor dispatcher, so the ladder actually runs local → neighbor:",
        "",
        "| metric | neighbor-assist fixture |",
        "|---|---|",
        f"| resolved via neighbor path | {nbr['outcomes']['resolved']} |",
        f"| neighbor-assist resolutions | {nbr['metrics'].get('neighbor_assist_resolutions', 0)} |",
        f"| local-only resolutions | {nbr['metrics'].get('local_only_resolutions', 0)} |",
        f"| completed neighbor batches | {nbr['metrics'].get('completed_hex_neighbor_batches', 0)} |",
        f"| global escalations | {nbr['metrics'].get('global_escalations', 0)} |",
        "",
        "This is an isolated fixture (not the production default gating); it "
        "demonstrates the routing path exists and increments its counters, "
        "closing the v1 limitation.",
        "",
        "Rerun: `python tools/run_hex_routing_benchmark.py` — the",
        "`deterministic_views` block is byte-comparable between runs.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--queries-per-category", type=int, default=6)
    parser.add_argument("--output-json", default=None)
    parser.add_argument("--output-md", default=None)
    args = parser.parse_args(argv)

    result = run_benchmark(queries_per_category=max(1, args.queries_per_category))
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output_json:
        Path(args.output_json).write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    if args.output_md:
        Path(args.output_md).write_text(render_markdown(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
