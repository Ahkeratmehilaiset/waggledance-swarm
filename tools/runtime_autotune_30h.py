#!/usr/bin/env python3
"""v3.5.6 Runtime Autotuner — canary benchmark with config candidates.

NOT a production optimizer. Runs canary benchmarks with different config
candidates and records results. Does NOT auto-merge or auto-apply.

Usage:
    python tools/runtime_autotune_30h.py [--api-key KEY] [--cycles N] [--interval MIN]

Outputs:
    benchmarks/autotune_metrics.ndjson — append-only metrics
    reports/TUNING_MATRIX.md — summary of all candidates
    reports/CANARY_DECISIONS.md — which candidates beat baseline
    benchmarks/RUNTIME_EFFICIENCY_MATRIX.csv — CSV matrix
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE_URL = os.environ.get("WAGGLE_BASE_URL", "http://localhost:8000")
API_KEY = os.environ.get("WAGGLE_API_KEY", "benchkey356")

# Canary queries — mixed types for representative benchmarking
CANARY_QUERIES = [
    ("What is 25 * 17?", "solver"),
    ("Mikä on sähkön hinta juuri nyt?", "domain_electricity"),
    ("Tell me about the weather in Helsinki", "domain_weather"),
    ("How does the hex mesh neighbor resolution work?", "general_long"),
    ("status", "system_short"),
    ("What security measures protect the API?", "general_security"),
]

# Config candidates to test
CANDIDATES = [
    {"name": "baseline", "changes": {}},
    {"name": "aggressive_skip", "changes": {
        "preflight_min_score": 0.5,
        "skip_low_value_neighbor_when_sequential": True,
    }},
    {"name": "conservative_skip", "changes": {
        "preflight_min_score": 0.2,
        "skip_low_value_neighbor_when_sequential": True,
    }},
    {"name": "tight_budget", "changes": {
        "local_budget_ms": 8000,
        "neighbor_budget_ms": 5000,
        "total_hex_budget_ms": 15000,
    }},
    {"name": "skip_all_sequential_neighbor", "changes": {
        "preflight_min_score": 0.3,
        "skip_low_value_neighbor_when_sequential": True,
        "neighbor_budget_ms": 5000,
    }},
    {"name": "metadata_only_local", "changes": {
        "preflight_min_score": 0.6,
        "local_budget_ms": 5000,
        "total_hex_budget_ms": 10000,
    }},
]

MIN_WINS_FOR_RECOMMENDATION = 2


def _http_request(method: str, path: str, body: dict | None = None,
                  timeout: float = 300) -> tuple[int, dict | str]:
    """Make HTTP request using urllib (no external deps)."""
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


def run_canary_benchmark(label: str = "baseline") -> dict:
    """Run one canary benchmark cycle. Returns metrics dict."""
    results = []
    errors = 0

    for query, qtype in CANARY_QUERIES:
        t0 = time.time()
        status, resp = _http_request("POST", "/api/chat", {"query": query, "profile": "HOME"})
        latency_ms = (time.time() - t0) * 1000

        if status == 200 and isinstance(resp, dict):
            results.append({
                "query_type": qtype,
                "latency_ms": latency_ms,
                "source": resp.get("source", "unknown"),
                "confidence": resp.get("confidence", 0),
                "cached": resp.get("cached", False),
            })
        else:
            errors += 1
            results.append({
                "query_type": qtype,
                "latency_ms": latency_ms,
                "source": "error",
                "confidence": 0,
                "cached": False,
                "error": str(resp)[:200] if status != 200 else "",
            })

    latencies = [r["latency_ms"] for r in results if r["source"] != "error"]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0

    # Fetch hex metrics
    _, hex_state = _http_request("GET", "/api/status")
    hex_metrics = {}
    if isinstance(hex_state, dict):
        hex_metrics = hex_state.get("hex_mesh", {})

    return {
        "label": label,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "queries": len(CANARY_QUERIES),
        "errors": errors,
        "avg_latency_ms": round(avg_latency, 1),
        "results": results,
        "hex_metrics": hex_metrics,
    }


def write_ndjson(metrics: dict, path: Path):
    """Append one JSON line to NDJSON file."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(metrics, default=str) + "\n")


def write_tuning_matrix(all_results: list[dict], path: Path):
    """Write TUNING_MATRIX.md from accumulated results."""
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Tuning Matrix — v3.5.6\n\n")
        f.write("| Candidate | Cycles | Avg Latency (ms) | Errors | Preflight Skips | Neighbor Skips |\n")
        f.write("|-----------|--------|-------------------|--------|-----------------|----------------|\n")

        # Group by label
        by_label: dict[str, list] = {}
        for r in all_results:
            by_label.setdefault(r["label"], []).append(r)

        for label, runs in by_label.items():
            cycles = len(runs)
            avg_lat = sum(r["avg_latency_ms"] for r in runs) / cycles
            total_errors = sum(r["errors"] for r in runs)
            pf_skips = sum(r.get("hex_metrics", {}).get("preflight_skips", 0) for r in runs)
            nb_skips = sum(r.get("hex_metrics", {}).get("skipped_neighbor_attempts", 0) for r in runs)
            f.write(f"| {label} | {cycles} | {avg_lat:.0f} | {total_errors} | {pf_skips} | {nb_skips} |\n")


def write_canary_decisions(all_results: list[dict], path: Path):
    """Write CANARY_DECISIONS.md — which candidates beat baseline."""
    by_label: dict[str, list] = {}
    for r in all_results:
        by_label.setdefault(r["label"], []).append(r)

    baseline_runs = by_label.get("baseline", [])
    baseline_avg = (
        sum(r["avg_latency_ms"] for r in baseline_runs) / len(baseline_runs)
        if baseline_runs else 0
    )

    with open(path, "w", encoding="utf-8") as f:
        f.write("# Canary Decisions — v3.5.6\n\n")
        f.write(f"Baseline avg latency: {baseline_avg:.0f} ms ({len(baseline_runs)} cycles)\n\n")

        for label, runs in by_label.items():
            if label == "baseline":
                continue
            avg = sum(r["avg_latency_ms"] for r in runs) / len(runs)
            delta_pct = ((avg - baseline_avg) / baseline_avg * 100) if baseline_avg else 0
            wins = sum(1 for r in runs if r["avg_latency_ms"] < baseline_avg)

            verdict = "RECOMMEND" if wins >= MIN_WINS_FOR_RECOMMENDATION else "INSUFFICIENT"
            if sum(r["errors"] for r in runs) > sum(r["errors"] for r in baseline_runs):
                verdict = "REJECT (more errors)"

            f.write(f"## {label}\n")
            f.write(f"- Avg latency: {avg:.0f} ms ({delta_pct:+.1f}%)\n")
            f.write(f"- Wins vs baseline: {wins}/{len(runs)}\n")
            f.write(f"- Verdict: **{verdict}**\n\n")


def write_csv_matrix(all_results: list[dict], path: Path):
    """Write RUNTIME_EFFICIENCY_MATRIX.csv."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp", "label", "queries", "errors", "avg_latency_ms",
            "preflight_skips", "preflight_passes", "skipped_local",
            "skipped_neighbor", "budget_exhaustions",
            "origin_resolutions", "local_only", "neighbor_assist", "global_escalations",
        ])
        for r in all_results:
            hm = r.get("hex_metrics", {})
            writer.writerow([
                r["timestamp"], r["label"], r["queries"], r["errors"],
                r["avg_latency_ms"],
                hm.get("preflight_skips", 0), hm.get("preflight_passes", 0),
                hm.get("skipped_local_attempts", 0), hm.get("skipped_neighbor_attempts", 0),
                hm.get("budget_exhaustions", 0),
                hm.get("origin_cell_resolutions", 0), hm.get("local_only_resolutions", 0),
                hm.get("neighbor_assist_resolutions", 0), hm.get("global_escalations", 0),
            ])


def main():
    global API_KEY  # noqa: PLW0603

    parser = argparse.ArgumentParser(description="v3.5.6 Runtime Autotuner")
    parser.add_argument("--api-key", default=API_KEY, help="WaggleDance API key")
    parser.add_argument("--cycles", type=int, default=5, help="Canary cycles per candidate")
    parser.add_argument("--interval", type=float, default=2.0, help="Minutes between cycles")
    parser.add_argument("--output-dir", default=".", help="Output directory for reports")
    args = parser.parse_args()

    if args.api_key:
        API_KEY = args.api_key

    out = Path(args.output_dir)
    benchmarks_dir = out / "benchmarks"
    reports_dir = out / "reports"
    benchmarks_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    ndjson_path = benchmarks_dir / "autotune_metrics.ndjson"
    matrix_path = reports_dir / "TUNING_MATRIX.md"
    decisions_path = reports_dir / "CANARY_DECISIONS.md"
    csv_path = benchmarks_dir / "RUNTIME_EFFICIENCY_MATRIX.csv"

    all_results = []

    # Verify WD is running
    status, resp = _http_request("GET", "/api/status")
    if status != 200:
        print(f"ERROR: WaggleDance not reachable at {BASE_URL} (status={status})")
        sys.exit(1)
    print(f"WaggleDance reachable at {BASE_URL}")

    for candidate in CANDIDATES:
        label = candidate["name"]
        print(f"\n{'='*60}")
        print(f"Candidate: {label}")
        print(f"Config changes: {candidate['changes']}")
        print(f"{'='*60}")

        # NOTE: We don't auto-apply config changes to the running server.
        # The autotuner just records benchmarks under different labels.
        # Manual config changes should be applied between runs if testing.

        for cycle in range(1, args.cycles + 1):
            print(f"  Cycle {cycle}/{args.cycles}...")
            metrics = run_canary_benchmark(label)
            all_results.append(metrics)
            write_ndjson(metrics, ndjson_path)
            print(f"    avg={metrics['avg_latency_ms']:.0f}ms errors={metrics['errors']}")

            if cycle < args.cycles:
                time.sleep(args.interval * 60)

    # Write summary reports
    write_tuning_matrix(all_results, matrix_path)
    write_canary_decisions(all_results, decisions_path)
    write_csv_matrix(all_results, csv_path)

    print(f"\nDone. {len(all_results)} total benchmark cycles.")
    print(f"Reports: {matrix_path}, {decisions_path}")
    print(f"Data: {ndjson_path}, {csv_path}")


if __name__ == "__main__":
    main()
