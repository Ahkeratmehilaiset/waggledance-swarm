#!/usr/bin/env python3
"""WD-Native Parallel Proof Harness — Diamond Run P1.

Tests internal parallel dispatch paths directly (no HTTP needed).
Measures wall-time, counter increments, and quality.

Usage:
    python tools/parallel_proof_harness.py [--runs N]
"""

import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from waggledance.adapters.config.settings_loader import WaggleSettings


async def run_scenario_a(container, runs: int = 3):
    """Scenario A: Round Table via Chat Service (escalation path).

    Send a long query → route confidence=0.6 → needs_round_table=True
    → run_round_table dispatches N agents in parallel batch.
    """
    from waggledance.application.dto.chat_dto import ChatRequest

    chat = container.chat_service
    dispatcher = container.parallel_dispatcher

    print("\n=== SCENARIO A: Round Table Escalation via Chat ===")
    print(f"  Parallel enabled: {dispatcher.enabled}")
    print(f"  Agents loaded: {len(container.orchestrator._agents)}")

    results = []
    for i in range(runs):
        m_before = dispatcher.get_metrics()
        t0 = time.monotonic()

        req = ChatRequest(
            query=f"Explain the detailed differences between supervised and unsupervised machine learning algorithms and their practical applications in agriculture run{i}",
            profile="HOME",
            language="en",
        )
        result = await chat.handle(req)

        elapsed = (time.monotonic() - t0) * 1000
        m_after = dispatcher.get_metrics()

        batch_delta = m_after["completed_parallel_batches"] - m_before["completed_parallel_batches"]
        dispatch_delta = m_after["total_dispatched"] - m_before["total_dispatched"]

        results.append({
            "run": i + 1,
            "wall_ms": round(elapsed, 1),
            "source": result.source,
            "confidence": result.confidence,
            "round_table": result.round_table,
            "response_len": len(result.response),
            "batch_delta": batch_delta,
            "dispatch_delta": dispatch_delta,
        })
        print(f"  Run {i+1}: {elapsed:.0f}ms, source={result.source}, "
              f"rt={result.round_table}, batches_delta={batch_delta}, "
              f"dispatched_delta={dispatch_delta}")

    return {"scenario": "A_round_table_escalation", "results": results,
            "final_metrics": dispatcher.get_metrics()}


async def run_scenario_b(container, runs: int = 3):
    """Scenario B: Direct dispatch_batch call.

    Directly call parallel_dispatcher.dispatch_batch() with 6 prompts.
    This tests the dispatcher itself, independent of chat routing.
    """
    dispatcher = container.parallel_dispatcher

    print("\n=== SCENARIO B: Direct dispatch_batch (6 prompts) ===")
    print(f"  Parallel enabled: {dispatcher.enabled}")

    prompts = [
        ("What is photosynthesis?", "default", 0.7, 150),
        ("Explain mitosis briefly.", "default", 0.7, 150),
        ("What causes rain?", "default", 0.7, 150),
        ("How do bees navigate?", "default", 0.7, 150),
        ("Why is the sky blue?", "default", 0.7, 150),
        ("What is soil pH?", "default", 0.7, 150),
    ]

    results = []
    for i in range(runs):
        m_before = dispatcher.get_metrics()
        t0 = time.monotonic()

        responses = await dispatcher.dispatch_batch(prompts)

        elapsed = (time.monotonic() - t0) * 1000
        m_after = dispatcher.get_metrics()

        batch_delta = m_after["completed_parallel_batches"] - m_before["completed_parallel_batches"]
        dispatch_delta = m_after["total_dispatched"] - m_before["total_dispatched"]
        non_empty = sum(1 for r in responses if r.strip())

        results.append({
            "run": i + 1,
            "wall_ms": round(elapsed, 1),
            "batch_delta": batch_delta,
            "dispatch_delta": dispatch_delta,
            "non_empty_responses": non_empty,
            "total_responses": len(responses),
        })
        print(f"  Run {i+1}: {elapsed:.0f}ms, batches_delta={batch_delta}, "
              f"dispatched_delta={dispatch_delta}, non_empty={non_empty}/6")

    return {"scenario": "B_direct_dispatch_batch", "results": results,
            "final_metrics": dispatcher.get_metrics()}


async def run_scenario_c(container, runs: int = 3):
    """Scenario C: Sequential vs Parallel comparison.

    Same 6 prompts, sequential (dispatch disabled) vs parallel (dispatch enabled).
    """
    dispatcher = container.parallel_dispatcher
    llm = container.llm

    prompts_text = [
        "What is photosynthesis?",
        "Explain mitosis briefly.",
        "What causes rain?",
        "How do bees navigate?",
        "Why is the sky blue?",
        "What is soil pH?",
    ]

    print("\n=== SCENARIO C: Sequential vs Parallel Wall-Time ===")

    # Sequential: direct LLM calls one at a time
    seq_times = []
    for i in range(runs):
        t0 = time.monotonic()
        for p in prompts_text:
            await llm.generate(prompt=p, temperature=0.7, max_tokens=150)
        elapsed = (time.monotonic() - t0) * 1000
        seq_times.append(elapsed)
        print(f"  Sequential run {i+1}: {elapsed:.0f}ms")

    # Parallel: dispatch_batch
    par_times = []
    batch = [(p, "default", 0.7, 150) for p in prompts_text]
    for i in range(runs):
        t0 = time.monotonic()
        await dispatcher.dispatch_batch(batch)
        elapsed = (time.monotonic() - t0) * 1000
        par_times.append(elapsed)
        print(f"  Parallel run {i+1}: {elapsed:.0f}ms")

    seq_mean = sum(seq_times) / len(seq_times)
    par_mean = sum(par_times) / len(par_times)
    improvement = ((seq_mean - par_mean) / seq_mean) * 100 if seq_mean > 0 else 0

    print(f"\n  Sequential mean: {seq_mean:.0f}ms")
    print(f"  Parallel mean:   {par_mean:.0f}ms")
    print(f"  Improvement:     {improvement:+.1f}%")

    return {
        "scenario": "C_sequential_vs_parallel",
        "sequential_times_ms": [round(t, 1) for t in seq_times],
        "parallel_times_ms": [round(t, 1) for t in par_times],
        "seq_mean_ms": round(seq_mean, 1),
        "par_mean_ms": round(par_mean, 1),
        "improvement_pct": round(improvement, 1),
        "final_metrics": dispatcher.get_metrics(),
    }


async def run_scenario_d(container, runs: int = 3):
    """Scenario D: run_round_table directly.

    Directly call orchestrator.run_round_table() to verify counters.
    """
    from waggledance.core.domain.task import TaskRequest
    orchestrator = container.orchestrator
    dispatcher = container.parallel_dispatcher

    print("\n=== SCENARIO D: Direct run_round_table ===")
    print(f"  Agents: {len(orchestrator._agents)}")

    results = []
    for i in range(runs):
        task = TaskRequest(
            id=f"proof-d-{i}",
            query=f"Analyze the relationship between climate change and pollinator population decline in Nordic regions, considering temperature shifts and habitat loss run{i}",
            language="en",
            profile="HOME",
            user_id="harness",
            context=[],
            timestamp=time.time(),
        )

        m_before = dispatcher.get_metrics()
        t0 = time.monotonic()

        consensus = await orchestrator.run_round_table(task)

        elapsed = (time.monotonic() - t0) * 1000
        m_after = dispatcher.get_metrics()

        batch_delta = m_after["completed_parallel_batches"] - m_before["completed_parallel_batches"]
        dispatch_delta = m_after["total_dispatched"] - m_before["total_dispatched"]

        results.append({
            "run": i + 1,
            "wall_ms": round(elapsed, 1),
            "consensus_confidence": consensus.confidence,
            "consensus_len": len(consensus.consensus),
            "batch_delta": batch_delta,
            "dispatch_delta": dispatch_delta,
        })
        print(f"  Run {i+1}: {elapsed:.0f}ms, confidence={consensus.confidence:.2f}, "
              f"batches_delta={batch_delta}, dispatched_delta={dispatch_delta}")

    return {"scenario": "D_direct_round_table", "results": results,
            "final_metrics": dispatcher.get_metrics()}


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="WD-Native Parallel Proof Harness")
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    os.environ["WAGGLE_LLM_PARALLEL_ENABLED"] = "true"
    os.environ.setdefault("WAGGLE_PROFILE", "HOME")

    settings = WaggleSettings.from_env()
    print(f"Profile: {settings.get_profile()}")
    print(f"Parallel enabled: {settings.llm_parallel_enabled}")
    print(f"Max concurrent: {settings.llm_parallel_max_concurrent}")

    from waggledance.bootstrap.container import Container
    container = Container(settings=settings, stub=False)

    # Verify agents loaded
    agents = container.orchestrator._agents
    print(f"Orchestrator agents: {len(agents)}")
    if not agents:
        print("ERROR: No agents loaded — cannot test batch paths")
        sys.exit(1)

    all_results = {}

    # Scenario B first (simplest — direct dispatch_batch)
    all_results["B"] = await run_scenario_b(container, args.runs)

    # Scenario D (direct run_round_table)
    all_results["D"] = await run_scenario_d(container, args.runs)

    # Scenario C (sequential vs parallel comparison)
    all_results["C"] = await run_scenario_c(container, args.runs)

    # Scenario A (full chat path with escalation)
    all_results["A"] = await run_scenario_a(container, args.runs)

    # Final summary
    final_m = container.parallel_dispatcher.get_metrics()
    print("\n" + "=" * 60)
    print("FINAL METRICS:")
    print(f"  completed_parallel_batches: {final_m['completed_parallel_batches']}")
    print(f"  total_dispatched:          {final_m['total_dispatched']}")
    print(f"  total_completed:           {final_m['total_completed']}")
    print(f"  timeout_count:             {final_m['timeout_count']}")
    print(f"  cancelled_count:           {final_m['cancelled_count']}")
    print(f"  deduped_requests:          {final_m['deduped_requests']}")
    print(f"  degrade_to_sequential:     {final_m['degrade_to_sequential_count']}")

    gate5 = final_m["completed_parallel_batches"] > 0 and final_m["total_dispatched"] > 0
    print(f"\n  GATE #5 (counters > 0): {'PASS' if gate5 else 'FAIL'}")

    all_results["final_metrics"] = final_m
    all_results["gate5_counters_pass"] = gate5

    if args.output:
        with open(args.output, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"\nResults written to {args.output}")

    # Close LLM client
    llm = container.llm
    if hasattr(llm, "close"):
        await llm.close()
    elif hasattr(llm, "_client"):
        await llm._client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
