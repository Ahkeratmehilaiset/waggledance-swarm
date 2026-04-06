#!/usr/bin/env python3
"""Hex Mesh Continuous Soak — v3.5.4

Runs continuous cycles for N hours. Each cycle:
1. 1 solver query
2. 1 short LLM query
3. 1 multi-domain query (triggers neighbor assist)
4. 1 degraded-cell query
5. /api/status snapshot
6. /api/ops snapshot

Writes NDJSON metrics per cycle + final summary.
"""

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
API_KEY = os.environ.get("WAGGLE_API_KEY", "benchtest")


def api_get(path: str, timeout: int = 30) -> dict | None:
    try:
        req = urllib.request.Request(f"{BASE}{path}")
        req.add_header("Authorization", f"Bearer {API_KEY}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


def api_post(path: str, body: dict, timeout: int = 180) -> dict | None:
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{BASE}{path}", data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return None


CYCLE_QUERY_TEMPLATES = [
    ("solver", "What is {a} * {b} + {c}?"),
    ("short_llm", "Tell me about {topic} in one sentence."),
    ("multi_domain", "How does {factor} affect home {system} and what should I adjust?"),
    ("degraded_cell", "What {task_type} tasks are needed for the {area}?"),
]

_TOPICS = ["Finland", "Python", "electricity", "solar panels", "insulation", "bees", "recycling",
           "Nordic weather", "heat pumps", "smart home", "energy storage", "wind power"]
_FACTORS = ["weather", "electricity price", "outdoor temperature", "humidity", "wind speed", "season"]
_SYSTEMS = ["heating", "cooling", "ventilation", "lighting", "energy use", "comfort"]
_TASK_TYPES = ["maintenance", "inspection", "cleaning", "optimization", "safety", "repair"]
_AREAS = ["factory production line", "home HVAC system", "solar panel array", "bee hives",
          "security cameras", "garden irrigation"]


def _build_queries(cycle_num: int) -> list[tuple[str, str]]:
    """Build unique queries per cycle to avoid hot-cache hits."""
    import random
    rng = random.Random(cycle_num)
    a, b, c = rng.randint(2, 50), rng.randint(2, 50), rng.randint(1, 20)
    return [
        ("solver", f"What is {a} * {b} + {c}?"),
        ("short_llm", f"Tell me about {rng.choice(_TOPICS)} in one sentence."),
        ("multi_domain", f"How does {rng.choice(_FACTORS)} affect home {rng.choice(_SYSTEMS)} and what should I adjust?"),
        ("degraded_cell", f"What {rng.choice(_TASK_TYPES)} tasks are needed for the {rng.choice(_AREAS)}?"),
    ]


def run_cycle(cycle_num: int) -> dict:
    """Run one soak cycle. Returns metrics dict."""
    cycle_start = time.monotonic()
    errors = 0
    status_5xx = 0
    latencies = []
    sources = []
    queries = _build_queries(cycle_num)

    for cat, query in queries:
        t0 = time.monotonic()
        resp = api_post("/api/chat", {"query": query, "language": "en"})
        elapsed = (time.monotonic() - t0) * 1000
        latencies.append(elapsed)

        if resp is None:
            errors += 1
            status_5xx += 1
            sources.append("error")
        else:
            sources.append(resp.get("source", "unknown"))

    # Capture snapshots
    status = api_get("/api/status") or {}
    ops = api_get("/api/ops") or {}

    hex_mesh = status.get("hex_mesh", {})
    parallel = status.get("parallel_dispatch", {})

    cycle_ms = (time.monotonic() - cycle_start) * 1000

    return {
        "cycle": cycle_num,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cycle_ms": round(cycle_ms, 1),
        "errors": errors,
        "status_5xx": status_5xx,
        "queries": len(queries),
        "sources": sources,
        "latency_min": round(min(latencies), 1) if latencies else 0,
        "latency_max": round(max(latencies), 1) if latencies else 0,
        "latency_mean": round(statistics.mean(latencies), 1) if latencies else 0,
        "latency_p50": round(statistics.median(latencies), 1) if latencies else 0,
        # Hex mesh counters
        "hex_local": hex_mesh.get("local_only_resolutions", 0),
        "hex_neighbor": hex_mesh.get("neighbor_assist_resolutions", 0),
        "hex_global": hex_mesh.get("global_escalations", 0),
        "hex_llm": hex_mesh.get("llm_last_resolutions", 0),
        "hex_quarantined": hex_mesh.get("quarantined_cells", 0),
        "hex_self_heal": hex_mesh.get("self_heal_events", 0),
        "hex_batches": hex_mesh.get("completed_hex_neighbor_batches", 0),
        "hex_origin": hex_mesh.get("origin_cell_resolutions", 0),
        # Parallel dispatch
        "pd_dispatched": parallel.get("dispatched", 0),
        "pd_completed": parallel.get("completed", 0),
        "pd_batches": parallel.get("completed_parallel_batches", 0),
        "pd_timeouts": parallel.get("timeouts", 0),
        "pd_cancels": parallel.get("cancels", 0),
        "pd_degrades": parallel.get("degrades", 0),
    }


def run_soak(hours: float, interval_s: int, out_dir: str):
    """Run continuous soak."""
    os.makedirs(out_dir, exist_ok=True)
    ndjson_path = os.path.join(out_dir, "soak_cycles.ndjson")
    summary_path = os.path.join(out_dir, "soak_summary.json")

    # Check WD alive
    status = api_get("/api/status")
    if not status:
        print("ERROR: WD not responding on port 8000", file=sys.stderr)
        sys.exit(1)

    hex_enabled = status.get("hex_mesh", {}).get("enabled", False)
    print(f"Soak starting: {hours}h, interval={interval_s}s, hex={hex_enabled}")

    end_time = time.monotonic() + hours * 3600
    cycle = 0
    total_errors = 0
    total_5xx = 0
    all_latencies = []

    with open(ndjson_path, "a") as f:
        while time.monotonic() < end_time:
            cycle += 1
            metrics = run_cycle(cycle)
            total_errors += metrics["errors"]
            total_5xx += metrics["status_5xx"]
            all_latencies.append(metrics["latency_mean"])

            f.write(json.dumps(metrics) + "\n")
            f.flush()

            print(
                f"  Cycle {cycle}: {metrics['cycle_ms']:.0f}ms, "
                f"errors={metrics['errors']}, "
                f"hex_local={metrics['hex_local']}, "
                f"hex_neighbor={metrics['hex_neighbor']}, "
                f"hex_batches={metrics['hex_batches']}, "
                f"pd_batches={metrics['pd_batches']}"
            )

            # Sleep until next cycle
            remaining = end_time - time.monotonic()
            if remaining > interval_s:
                time.sleep(interval_s)
            elif remaining > 0:
                time.sleep(remaining)

    # Write summary
    summary = {
        "hex_enabled": hex_enabled,
        "duration_hours": hours,
        "interval_s": interval_s,
        "total_cycles": cycle,
        "total_queries": cycle * 4,  # 4 queries per cycle
        "total_errors": total_errors,
        "total_5xx": total_5xx,
        "latency_p50": round(statistics.median(all_latencies), 1) if all_latencies else 0,
        "latency_p95": round(sorted(all_latencies)[int(len(all_latencies) * 0.95)] if all_latencies else 0, 1),
        "latency_mean": round(statistics.mean(all_latencies), 1) if all_latencies else 0,
        "final_status": api_get("/api/status") or {},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"\nSoak complete: {cycle} cycles, {total_errors} errors, {total_5xx} 5xx")
    print(f"Summary: {summary_path}")
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Hex Mesh Continuous Soak")
    p.add_argument("--hours", type=float, default=4.0, help="Soak duration in hours")
    p.add_argument("--interval", type=int, default=100, help="Seconds between cycles")
    p.add_argument("--out", default="C:/WaggleDance_HexRun/20260406_115541/soak",
                   help="Output directory")
    args = p.parse_args()
    run_soak(args.hours, args.interval, args.out)
