#!/usr/bin/env python3
"""Diamond Run P4 — Live Benchmark via HTTP API.

Modes:
A. phi4-mini baseline (parallel OFF at WD level — but WD has it ON, so we measure through API)
B-D. Gemma modes (skipped if gemma not enabled)
E. WD-native sequential (single chat, no round table trigger)
F. WD-native round table (complex query, triggers escalation+batch)
G. 4x concurrent external API calls (for comparison only)

Usage:
    python tools/diamond_live_benchmark.py --api-key KEY [--runs N] [--output PATH]
"""

import argparse
import asyncio
import json
import os
import sys
import time

# Use httpx for async HTTP
try:
    import httpx
except ImportError:
    print("Install httpx: pip install httpx")
    sys.exit(1)


BASE = "http://localhost:8000"
QUERYSET = [
    "Explain the ecological relationship between honeybees and native wildflowers in boreal forests",
    "What are the main risk factors for Varroa destructor resistance to common treatments",
    "How does soil pH affect nutrient availability for crop plants in Nordic agriculture",
    "Describe the impact of climate change on pollinator populations in Scandinavia",
]
SHORT_QUERIES = [
    "What time is it",
    "System status",
    "Tell about bees",
]


async def benchmark_single_queries(client, headers, queries, runs, label):
    """Send queries one at a time, measure individual latency."""
    print(f"\n=== {label} ===")
    all_results = []

    for run_i in range(runs):
        run_times = []
        for q in queries:
            t0 = time.monotonic()
            resp = await client.post(f"{BASE}/api/chat",
                                     json={"query": q, "profile": "HOME"},
                                     headers=headers, timeout=120)
            elapsed = (time.monotonic() - t0) * 1000
            data = resp.json()
            run_times.append({
                "query": q[:60],
                "wall_ms": round(elapsed, 1),
                "source": data.get("source", "?"),
                "confidence": data.get("confidence", 0),
                "round_table": data.get("round_table", False),
                "response_len": len(data.get("response", "")),
            })

        total = sum(r["wall_ms"] for r in run_times)
        rt_count = sum(1 for r in run_times if r.get("round_table"))
        print(f"  Run {run_i+1}: total={total:.0f}ms, "
              f"queries={len(run_times)}, round_table={rt_count}")
        all_results.append({"run": run_i + 1, "total_ms": round(total, 1),
                           "queries": run_times})

    return all_results


async def benchmark_concurrent(client, headers, queries, runs, label):
    """Send queries concurrently, measure total wall time."""
    print(f"\n=== {label} ===")
    all_results = []

    for run_i in range(runs):
        t0 = time.monotonic()

        async def send_one(q):
            r = await client.post(f"{BASE}/api/chat",
                                   json={"query": q, "profile": "HOME"},
                                   headers=headers, timeout=120)
            return r.json()

        responses = await asyncio.gather(*[send_one(q) for q in queries])
        elapsed = (time.monotonic() - t0) * 1000

        non_empty = sum(1 for r in responses if r.get("response", ""))
        rt_count = sum(1 for r in responses if r.get("round_table"))
        print(f"  Run {run_i+1}: {elapsed:.0f}ms, "
              f"non_empty={non_empty}/{len(queries)}, round_table={rt_count}")
        all_results.append({
            "run": run_i + 1,
            "wall_ms": round(elapsed, 1),
            "non_empty": non_empty,
            "round_table_count": rt_count,
        })

    return all_results


async def get_metrics(client):
    """Get current /api/status metrics."""
    resp = await client.get(f"{BASE}/api/status", timeout=10)
    return resp.json()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    headers = {"Authorization": f"Bearer {args.api_key}"}

    async with httpx.AsyncClient() as client:
        # Verify connection
        status = await get_metrics(client)
        print(f"WD status: {status['status']}")
        print(f"Parallel: {status['llm_parallel']['enabled']}")

        results = {}

        # Mode E: Short queries (no round table trigger)
        results["E_short_sequential"] = await benchmark_single_queries(
            client, headers, SHORT_QUERIES, args.runs,
            "Mode E: Short queries (no round table)")

        # Mode F: Complex queries (round table trigger expected)
        m_before = await get_metrics(client)
        results["F_complex_sequential"] = await benchmark_single_queries(
            client, headers, QUERYSET, args.runs,
            "Mode F: Complex queries (round table expected)")
        m_after = await get_metrics(client)
        results["F_parallel_metrics"] = {
            "batches_before": m_before["llm_parallel"]["completed_parallel_batches"],
            "batches_after": m_after["llm_parallel"]["completed_parallel_batches"],
            "dispatched_before": m_before["llm_parallel"]["total_dispatched"],
            "dispatched_after": m_after["llm_parallel"]["total_dispatched"],
        }
        print(f"  Batches: {m_before['llm_parallel']['completed_parallel_batches']} -> "
              f"{m_after['llm_parallel']['completed_parallel_batches']}")

        # Mode G: 4x concurrent external API calls (for comparison)
        results["G_concurrent_4x"] = await benchmark_concurrent(
            client, headers, QUERYSET, args.runs,
            "Mode G: 4x concurrent API calls (comparison)")

        # Final metrics
        final = await get_metrics(client)
        results["final_metrics"] = final["llm_parallel"]

        # Summary
        print("\n" + "=" * 60)
        print("SUMMARY")
        print(f"  completed_parallel_batches: {final['llm_parallel']['completed_parallel_batches']}")
        print(f"  total_dispatched: {final['llm_parallel']['total_dispatched']}")

        # Compute averages
        for mode in ["E_short_sequential", "F_complex_sequential"]:
            totals = [r["total_ms"] for r in results[mode]]
            avg = sum(totals) / len(totals) if totals else 0
            print(f"  {mode} avg total: {avg:.0f}ms")

        g_times = [r["wall_ms"] for r in results["G_concurrent_4x"]]
        g_avg = sum(g_times) / len(g_times) if g_times else 0
        print(f"  G_concurrent_4x avg wall: {g_avg:.0f}ms")

        if args.output:
            with open(args.output, "w") as f:
                json.dump(results, f, indent=2)
            print(f"\nResults: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
