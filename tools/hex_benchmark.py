#!/usr/bin/env python3
"""Hex Mesh Benchmark Harness — v3.5.4

Runs queryset against WD with hex_mesh ON and OFF,
captures WD-native metrics, writes CSV + reports.
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
API_KEY = os.environ.get("WAGGLE_API_KEY", "benchtest")


def api_get(path: str, timeout: int = 30) -> dict | None:
    """GET JSON from WD API."""
    try:
        req = urllib.request.Request(f"{BASE}{path}")
        req.add_header("Authorization", f"Bearer {API_KEY}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        print(f"  [WARN] GET {path} failed: {e}", file=sys.stderr)
        return None


def api_post(path: str, body: dict, timeout: int = 180) -> dict | None:
    """POST JSON to WD API."""
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
        print(f"  [WARN] POST {path} failed: {e}", file=sys.stderr)
        return None


# ── Query definitions ──────────────────────────────────────────

QUERYSET = [
    # (category, query, expected_domain)
    ("solver_math", "What is 15 * 7 + 3?", "math"),
    ("solver_thermal", "What is the frost risk at -5°C?", "thermal"),
    ("short_llm", "What is the capital of Finland?", "general"),
    ("short_llm", "Explain what a REST API is in one sentence.", "general"),
    ("multi_domain", "How does weather affect my home heating and what should I set the thermostat to?", "environment+home"),
    ("multi_domain", "Plan a schedule for checking bee hives considering weather forecast and equipment needs.", "bee_ops+environment+logistics"),
    ("multi_domain", "Are there any security camera alerts and how do they relate to recent weather events?", "safety+environment"),
    ("degraded_cell", "What maintenance tasks are needed for the factory production line?", "production"),
]


def run_queryset(label: str) -> list[dict]:
    """Run full queryset, return per-query results."""
    results = []
    for cat, query, domain in QUERYSET:
        t0 = time.monotonic()
        resp = api_post("/api/chat", {"query": query, "language": "en"})
        elapsed = (time.monotonic() - t0) * 1000

        row = {
            "label": label,
            "category": cat,
            "query": query[:80],
            "expected_domain": domain,
            "latency_ms": round(elapsed, 1),
            "source": resp.get("source", "error") if resp else "error",
            "confidence": resp.get("confidence", 0.0) if resp else 0.0,
            "cached": resp.get("cached", False) if resp else False,
            "error": resp is None,
        }
        print(f"  [{cat}] {elapsed:.0f}ms source={row['source']} conf={row['confidence']:.2f}")
        results.append(row)
    return results


def capture_metrics() -> dict:
    """Capture /api/status and /api/ops snapshots."""
    status = api_get("/api/status") or {}
    ops = api_get("/api/ops") or {}
    return {
        "status": status,
        "ops": ops,
        "hex_mesh": status.get("hex_mesh", {}),
        "parallel": status.get("parallel_dispatch", {}),
    }


def run_benchmark(out_dir: str):
    """Run full benchmark suite."""
    os.makedirs(out_dir, exist_ok=True)

    # Check WD is alive
    status = api_get("/api/status")
    if not status:
        print("ERROR: WD not responding on port 8000", file=sys.stderr)
        sys.exit(1)

    hex_enabled = (status.get("hex_mesh", {}).get("enabled", False))
    label = "hex_ON" if hex_enabled else "hex_OFF"
    print(f"\n=== Benchmark: {label} ===")
    print(f"WD status: OK, hex_mesh.enabled={hex_enabled}")

    # Pre-metrics
    pre = capture_metrics()

    # Run queryset
    print(f"\nRunning {len(QUERYSET)} queries...")
    results = run_queryset(label)

    # Post-metrics
    post = capture_metrics()

    # Write CSV
    csv_path = os.path.join(out_dir, f"benchmark_{label}.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=results[0].keys())
        w.writeheader()
        w.writerows(results)
    print(f"\nCSV written: {csv_path}")

    # Write metrics snapshot
    snap_path = os.path.join(out_dir, f"metrics_{label}.json")
    with open(snap_path, "w") as f:
        json.dump({
            "label": label,
            "hex_enabled": hex_enabled,
            "pre_metrics": pre,
            "post_metrics": post,
            "query_count": len(results),
            "errors": sum(1 for r in results if r["error"]),
            "avg_latency_ms": round(sum(r["latency_ms"] for r in results) / len(results), 1),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }, f, indent=2)
    print(f"Metrics written: {snap_path}")

    # Summary
    errors = sum(1 for r in results if r["error"])
    avg_lat = sum(r["latency_ms"] for r in results) / len(results)
    print(f"\nSummary: {len(results)} queries, {errors} errors, avg {avg_lat:.0f}ms")

    if hex_enabled:
        hm = post.get("hex_mesh", {})
        print(f"  hex_mesh.origin_cell_resolutions: {hm.get('origin_cell_resolutions', 0)}")
        print(f"  hex_mesh.local_only_resolutions: {hm.get('local_only_resolutions', 0)}")
        print(f"  hex_mesh.neighbor_assist_resolutions: {hm.get('neighbor_assist_resolutions', 0)}")
        print(f"  hex_mesh.global_escalations: {hm.get('global_escalations', 0)}")
        print(f"  hex_mesh.llm_last_resolutions: {hm.get('llm_last_resolutions', 0)}")
        print(f"  hex_mesh.completed_hex_neighbor_batches: {hm.get('completed_hex_neighbor_batches', 0)}")
        print(f"  hex_mesh.quarantined_cells: {hm.get('quarantined_cells', 0)}")

    return results, pre, post


def compare_benchmarks(out_dir: str):
    """Compare hex_OFF vs hex_ON results if both exist."""
    off_path = os.path.join(out_dir, "metrics_hex_OFF.json")
    on_path = os.path.join(out_dir, "metrics_hex_ON.json")

    if not (os.path.exists(off_path) and os.path.exists(on_path)):
        print("Need both hex_OFF and hex_ON runs for comparison.")
        return

    with open(off_path) as f:
        off = json.load(f)
    with open(on_path) as f:
        on = json.load(f)

    print("\n=== COMPARISON: hex_OFF vs hex_ON ===")
    print(f"  avg_latency: {off['avg_latency_ms']:.0f}ms -> {on['avg_latency_ms']:.0f}ms")
    print(f"  errors: {off['errors']} -> {on['errors']}")

    # WD-native hex metrics
    hm = on.get("post_metrics", {}).get("hex_mesh", {})
    print(f"\n  WD-Native Hex Metrics (ON run):")
    for k in ["origin_cell_resolutions", "local_only_resolutions",
              "neighbor_assist_resolutions", "global_escalations",
              "llm_last_resolutions", "completed_hex_neighbor_batches",
              "quarantined_cells", "self_heal_events"]:
        print(f"    {k}: {hm.get(k, 'N/A')}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Hex Mesh Benchmark")
    p.add_argument("--out", default="C:/WaggleDance_HexRun/20260406_115541/benchmarks",
                   help="Output directory")
    p.add_argument("--compare", action="store_true", help="Compare OFF vs ON")
    args = p.parse_args()

    if args.compare:
        compare_benchmarks(args.out)
    else:
        run_benchmark(args.out)
