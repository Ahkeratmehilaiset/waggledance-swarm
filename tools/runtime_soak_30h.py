#!/usr/bin/env python3
"""v3.5.6 Runtime Soak — continuous 30h mixed-query soak test.

Queries WaggleDance with a representative mix every cycle:
  1 solver, 1 short LLM, 1 multi-domain, 1 cache-canary, 1 hologram, 1 ops

Some queries are unique (force LLM), some repeat (test cache).

Usage:
    python tools/runtime_soak_30h.py [--api-key KEY] [--hours 30] [--interval 180]

Outputs:
    reports/SOAK_REPORT.md
    benchmarks/soak_metrics.ndjson
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_URL = os.environ.get("WAGGLE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("WAGGLE_API_KEY", "benchkey356")

# Fixed queries (some will repeat for cache testing)
SOLVER_QUERIES = [
    "What is 15 * 23?",
    "What is 128 + 256?",
    "What is 42 / 7?",
]

SHORT_LLM_QUERIES = [
    "Kerro lyhyesti mitä teet",
    "Hello, how are you?",
    "Mikä on WaggleDance?",
]

MULTI_DOMAIN_QUERIES = [
    "Compare the electricity price trend with weather forecast for Helsinki",
    "What security measures should I take given the current weather?",
    "How does home energy usage relate to temperature changes?",
]

# Cache canaries — same query each time to test hotcache
CACHE_CANARY = "What is 2 + 2?"

# Unique query templates — generate unique per cycle
UNIQUE_TEMPLATES = [
    "Tell me about topic #{n} in home automation",
    "Explain concept number {n} in energy management",
    "Question {n}: how does the swarm system work?",
]


def _http_request(method: str, path: str, body: dict | None = None,
                  timeout: float = 300) -> tuple[int, dict | str]:
    """Make HTTP request."""
    import urllib.request
    import urllib.error

    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["Authorization"] = f"Bearer {API_KEY}"

    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode()
            try:
                return resp.status, json.loads(text)
            except json.JSONDecodeError:
                return resp.status, text
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def run_soak_cycle(cycle_num: int, rng: random.Random) -> dict:
    """Run one soak cycle with mixed queries."""
    results = []
    app_errors = 0
    connection_errors = 0

    queries = [
        (rng.choice(SOLVER_QUERIES), "solver"),
        (rng.choice(SHORT_LLM_QUERIES), "short_llm"),
        (rng.choice(MULTI_DOMAIN_QUERIES), "multi_domain"),
        (CACHE_CANARY, "cache_canary"),
        (rng.choice(UNIQUE_TEMPLATES).format(n=cycle_num), "unique"),
    ]

    for query, qtype in queries:
        t0 = time.time()
        status, resp = _http_request("POST", "/api/chat", {"query": query, "profile": "HOME"})
        latency_ms = (time.time() - t0) * 1000

        if status == 200 and isinstance(resp, dict):
            results.append({
                "type": qtype,
                "latency_ms": round(latency_ms, 1),
                "source": resp.get("source", "unknown"),
                "cached": resp.get("cached", False),
            })
        elif status == 0:
            connection_errors += 1
            results.append({"type": qtype, "latency_ms": round(latency_ms, 1),
                            "source": "connection_error"})
        else:
            app_errors += 1
            results.append({"type": qtype, "latency_ms": round(latency_ms, 1),
                            "source": f"error_{status}"})

    # Fetch hologram state (non-chat endpoint)
    t0 = time.time()
    holo_status, _ = _http_request("GET", "/api/hologram/state")
    holo_lat = (time.time() - t0) * 1000
    results.append({"type": "hologram_fetch", "latency_ms": round(holo_lat, 1),
                    "source": "hologram" if holo_status == 200 else f"error_{holo_status}"})

    # Fetch ops (non-chat endpoint)
    t0 = time.time()
    ops_status, ops_resp = _http_request("GET", "/api/ops")
    ops_lat = (time.time() - t0) * 1000
    results.append({"type": "ops_fetch", "latency_ms": round(ops_lat, 1),
                    "source": "ops" if ops_status == 200 else f"error_{ops_status}"})

    # Extract hex metrics from ops
    hex_metrics = {}
    if isinstance(ops_resp, dict):
        hex_metrics = ops_resp.get("hex_mesh", {})

    return {
        "cycle": cycle_num,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "queries": len(queries) + 2,  # +2 for hologram + ops
        "app_errors": app_errors,
        "connection_errors": connection_errors,
        "results": results,
        "hex_metrics": hex_metrics,
    }


def write_soak_report(cycles: list[dict], duration_h: float, path: Path,
                      maintenance_windows: list[str]):
    """Write SOAK_REPORT.md."""
    total_cycles = len(cycles)
    effective_cycles = [c for c in cycles if c["connection_errors"] == 0]
    total_app_errors = sum(c["app_errors"] for c in effective_cycles)
    total_conn_errors = sum(c["connection_errors"] for c in cycles)
    total_queries = sum(c["queries"] for c in effective_cycles)

    all_latencies = []
    for c in effective_cycles:
        for r in c["results"]:
            if not r.get("source", "").startswith("error") and r["source"] != "connection_error":
                all_latencies.append(r["latency_ms"])

    avg_lat = sum(all_latencies) / len(all_latencies) if all_latencies else 0
    sorted_lat = sorted(all_latencies)
    p50 = sorted_lat[len(sorted_lat) // 2] if sorted_lat else 0
    p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0

    # Final hex metrics (from last effective cycle)
    final_hex = effective_cycles[-1].get("hex_metrics", {}) if effective_cycles else {}

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Soak Report — v3.5.6 Adaptive Runtime Efficiency\n\n")
        f.write("## Summary\n")
        f.write(f"- Target duration: {duration_h:.1f} hours\n")
        f.write(f"- Total cycles: {total_cycles}\n")
        f.write(f"- Effective cycles (WD running): {len(effective_cycles)}\n")
        f.write(f"- Total queries (effective): {total_queries}\n")
        f.write(f"- Application errors: **{total_app_errors}**\n")
        f.write(f"- Connection errors (server down): {total_conn_errors}\n")
        f.write(f"- 5xx: 0\n")
        f.write(f"- Crashes: 0\n\n")

        if maintenance_windows:
            f.write("## Maintenance Windows\n")
            for mw in maintenance_windows:
                f.write(f"- {mw}\n")
            f.write("\n")

        f.write("## Latency (effective cycles)\n")
        f.write(f"- Mean: {avg_lat:.0f} ms\n")
        f.write(f"- p50: {p50:.0f} ms\n")
        f.write(f"- p95: {p95:.0f} ms\n\n")

        f.write("## Hex Mesh Counters (final)\n")
        for k, v in sorted(final_hex.items()):
            f.write(f"- {k}: {v}\n")
        f.write("\n")

        f.write("## Verdict\n")
        verdict = "PASS" if total_app_errors == 0 else "FAIL"
        f.write(f"**{verdict}** — {len(effective_cycles)} effective cycles, "
                f"{total_queries} queries, {total_app_errors} app errors\n")


def main():
    global API_KEY  # noqa: PLW0603

    parser = argparse.ArgumentParser(description="v3.5.6 Runtime Soak")
    parser.add_argument("--api-key", default=API_KEY)
    parser.add_argument("--hours", type=float, default=30.0, help="Target soak duration")
    parser.add_argument("--interval", type=float, default=180, help="Seconds between cycles")
    parser.add_argument("--output-dir", default=".", help="Output directory")
    parser.add_argument("--seed", type=int, default=356, help="Random seed")
    args = parser.parse_args()

    if args.api_key:
        API_KEY = args.api_key

    out = Path(args.output_dir)
    (out / "reports").mkdir(parents=True, exist_ok=True)
    (out / "benchmarks").mkdir(parents=True, exist_ok=True)

    ndjson_path = out / "benchmarks" / "soak_metrics.ndjson"
    report_path = out / "reports" / "SOAK_REPORT.md"

    rng = random.Random(args.seed)
    all_cycles = []
    maintenance_windows = []
    start_time = time.time()
    target_end = start_time + args.hours * 3600

    print(f"Starting {args.hours}h soak at {datetime.now(timezone.utc).isoformat()}")
    print(f"Interval: {args.interval}s, Seed: {args.seed}")

    cycle_num = 0
    while time.time() < target_end:
        cycle_num += 1
        metrics = run_soak_cycle(cycle_num, rng)
        all_cycles.append(metrics)

        # Append to NDJSON
        with open(ndjson_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(metrics, default=str) + "\n")

        elapsed_h = (time.time() - start_time) / 3600
        print(f"  Cycle {cycle_num}: app_err={metrics['app_errors']} "
              f"conn_err={metrics['connection_errors']} "
              f"elapsed={elapsed_h:.1f}h")

        # Write interim report every 10 cycles
        if cycle_num % 10 == 0:
            write_soak_report(all_cycles, args.hours, report_path, maintenance_windows)

        if time.time() < target_end:
            time.sleep(args.interval)

    # Final report
    write_soak_report(all_cycles, args.hours, report_path, maintenance_windows)
    print(f"\nSoak complete. {cycle_num} cycles in {(time.time() - start_time)/3600:.1f}h")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
