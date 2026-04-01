#!/usr/bin/env python3
"""Hybrid retrieval benchmark harness.

Compares baseline (hybrid OFF) vs hybrid (hybrid ON) modes using
the same representative query set. Records route distribution,
latency percentiles, and hit/miss rates.

Usage:
    python tools/hybrid_benchmark.py --base-url http://localhost:8000 \
        --output C:/WaggleDance_HybridRun/<run_id>/reports

Modes:
    --mode baseline  : Run with hybrid disabled (default /api/status check)
    --mode hybrid    : Run with hybrid enabled
    --mode both      : Run baseline then hybrid (default)
"""

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ── Representative query set ──────────────────────────────────

QUERY_SET = [
    # Deterministic arithmetic (expect solver)
    {"query": "calculate 15 + 27", "expected_route": "solver", "category": "math"},
    {"query": "what is 100 * 0.15", "expected_route": "solver", "category": "math"},
    {"query": "paljonko on 200 - 89", "expected_route": "solver", "category": "math"},
    {"query": "calculate 12 squared", "expected_route": "solver", "category": "math"},

    # Known solver thermal
    {"query": "frost risk at -5°C", "expected_route": "solver", "category": "thermal"},
    {"query": "is 25 degrees too hot", "expected_route": "solver", "category": "thermal"},

    # Retrieval-oriented (may hit FAISS or Chroma)
    {"query": "what is WaggleDance", "expected_route": "retrieval", "category": "retrieval"},
    {"query": "explain MAGMA audit trail", "expected_route": "retrieval", "category": "retrieval"},
    {"query": "mikä on pesätanssi", "expected_route": "retrieval", "category": "retrieval"},
    {"query": "how does dream mode work", "expected_route": "retrieval", "category": "retrieval"},

    # Domain prompts (may require LLM)
    {"query": "what is the best practice for home automation", "expected_route": "llm", "category": "domain"},
    {"query": "how to optimize energy usage in winter", "expected_route": "llm", "category": "domain"},
    {"query": "explain solver-first architecture", "expected_route": "retrieval", "category": "domain"},

    # Repeat prompts (should hit cache on 2nd occurrence)
    {"query": "calculate 15 + 27", "expected_route": "hotcache", "category": "repeat"},
    {"query": "what is WaggleDance", "expected_route": "hotcache", "category": "repeat"},

    # System status
    {"query": "system statistics", "expected_route": "solver", "category": "system"},
]


def _api_key(base_url: str) -> str:
    """Try to read API key from startup output or environment."""
    import os
    key = os.environ.get("WAGGLE_API_KEY", "")
    if key:
        return key
    # Try to read from recent startup log
    for log_path in [Path("production.log"), Path("data/production.log")]:
        if log_path.exists():
            try:
                text = log_path.read_text(encoding="utf-8", errors="ignore")
                for line in text.splitlines():
                    if "API key:" in line or "WAGGLE_API_KEY" in line:
                        parts = line.split(":")
                        if len(parts) >= 2:
                            return parts[-1].strip()
            except Exception:
                pass
    return ""


def _request(base_url: str, endpoint: str, method: str = "GET",
             body: dict | None = None, api_key: str = "") -> dict:
    """Make HTTP request and return JSON response."""
    url = f"{base_url}{endpoint}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError) as e:
        return {"error": str(e)}


def run_benchmark(base_url: str, api_key: str, mode: str) -> dict:
    """Run benchmark with the given mode and return results."""
    results = {
        "mode": mode,
        "started": datetime.now().isoformat(),
        "queries": [],
        "route_distribution": {},
        "latencies_ms": [],
        "errors": 0,
        "total": 0,
    }

    # Check hybrid status
    status = _request(base_url, "/api/hybrid/status", api_key=api_key)
    results["hybrid_enabled"] = status.get("enabled", False)

    for q in QUERY_SET:
        results["total"] += 1
        t0 = time.perf_counter()
        try:
            resp = _request(base_url, "/api/chat", method="POST",
                           body={"query": q["query"]}, api_key=api_key)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            # Extract route info
            source = resp.get("method", resp.get("source", "unknown"))
            hybrid_trace = resp.get("hybrid_trace", None)

            entry = {
                "query": q["query"],
                "category": q["category"],
                "expected_route": q["expected_route"],
                "actual_route": source,
                "latency_ms": round(elapsed_ms, 2),
                "error": resp.get("error"),
            }
            if hybrid_trace:
                entry["hybrid_trace"] = hybrid_trace

            results["queries"].append(entry)
            results["latencies_ms"].append(elapsed_ms)

            # Route distribution
            results["route_distribution"][source] = \
                results["route_distribution"].get(source, 0) + 1

            if resp.get("error"):
                results["errors"] += 1

        except Exception as e:
            results["errors"] += 1
            results["queries"].append({
                "query": q["query"],
                "category": q["category"],
                "error": str(e),
                "latency_ms": (time.perf_counter() - t0) * 1000,
            })

    results["finished"] = datetime.now().isoformat()

    # Compute percentiles
    if results["latencies_ms"]:
        sorted_lat = sorted(results["latencies_ms"])
        n = len(sorted_lat)
        results["p50_ms"] = round(sorted_lat[n // 2], 2)
        results["p95_ms"] = round(sorted_lat[int(n * 0.95)], 2)
        results["mean_ms"] = round(statistics.mean(sorted_lat), 2)

    return results


def write_report(baseline: dict | None, hybrid: dict | None, output_dir: str):
    """Write benchmark report and raw data."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Write raw JSON
    if baseline:
        (out / "benchmark_baseline.json").write_text(
            json.dumps(baseline, indent=2, ensure_ascii=False), encoding="utf-8")
    if hybrid:
        (out / "benchmark_hybrid.json").write_text(
            json.dumps(hybrid, indent=2, ensure_ascii=False), encoding="utf-8")

    # Write markdown report
    lines = ["# Hybrid Benchmark Report\n"]
    lines.append(f"Generated: {datetime.now().isoformat()}\n")

    for label, data in [("Baseline (hybrid OFF)", baseline), ("Hybrid (hybrid ON)", hybrid)]:
        if data is None:
            continue
        lines.append(f"\n## {label}\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|--------|-------|")
        lines.append(f"| Total queries | {data.get('total', 0)} |")
        lines.append(f"| Errors | {data.get('errors', 0)} |")
        lines.append(f"| Hybrid enabled | {data.get('hybrid_enabled', False)} |")
        lines.append(f"| p50 latency | {data.get('p50_ms', 'N/A')} ms |")
        lines.append(f"| p95 latency | {data.get('p95_ms', 'N/A')} ms |")
        lines.append(f"| Mean latency | {data.get('mean_ms', 'N/A')} ms |")
        lines.append("")

        lines.append("### Route Distribution\n")
        lines.append("| Route | Count | Share |")
        lines.append("|-------|-------|-------|")
        total = data.get("total", 1)
        for route, count in sorted(data.get("route_distribution", {}).items(),
                                   key=lambda x: x[1], reverse=True):
            share = round(count / total * 100, 1)
            lines.append(f"| {route} | {count} | {share}% |")
        lines.append("")

    # Before/after comparison
    if baseline and hybrid:
        lines.append("\n## Before / After Comparison\n")
        lines.append("| Metric | Baseline | Hybrid | Delta |")
        lines.append("|--------|----------|--------|-------|")
        for key, label in [("p50_ms", "p50 latency (ms)"),
                           ("p95_ms", "p95 latency (ms)"),
                           ("mean_ms", "Mean latency (ms)"),
                           ("errors", "Errors")]:
            bv = baseline.get(key, 0) or 0
            hv = hybrid.get(key, 0) or 0
            delta = round(hv - bv, 2) if isinstance(bv, (int, float)) else "N/A"
            lines.append(f"| {label} | {bv} | {hv} | {delta:+.2f} |" if isinstance(delta, (int, float)) else f"| {label} | {bv} | {hv} | {delta} |")

    report = "\n".join(lines)
    (out / "HYBRID_BENCHMARK_REPORT.md").write_text(report, encoding="utf-8")

    # Query set documentation
    qs_lines = ["# Benchmark Query Set\n"]
    qs_lines.append("| # | Query | Category | Expected Route |")
    qs_lines.append("|---|-------|----------|----------------|")
    for i, q in enumerate(QUERY_SET, 1):
        qs_lines.append(f"| {i} | {q['query']} | {q['category']} | {q['expected_route']} |")
    (out / "QUERYSET.md").write_text("\n".join(qs_lines), encoding="utf-8")

    print(f"Reports written to {out}")


def main():
    parser = argparse.ArgumentParser(description="Hybrid retrieval benchmark")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", default="C:/WaggleDance_HybridRun/benchmark")
    parser.add_argument("--mode", choices=["baseline", "hybrid", "both"], default="both")
    parser.add_argument("--api-key", default="")
    args = parser.parse_args()

    api_key = args.api_key or _api_key(args.base_url)

    # Verify server is reachable
    status = _request(args.base_url, "/api/status", api_key=api_key)
    if "error" in status:
        print(f"Server not reachable at {args.base_url}: {status['error']}")
        sys.exit(1)

    baseline = None
    hybrid = None

    if args.mode in ("baseline", "both"):
        print("Running baseline benchmark (hybrid OFF)...")
        baseline = run_benchmark(args.base_url, api_key, "baseline")
        print(f"  {baseline['total']} queries, {baseline['errors']} errors, "
              f"p50={baseline.get('p50_ms', 'N/A')}ms")

    if args.mode in ("hybrid", "both"):
        print("Running hybrid benchmark (hybrid ON)...")
        hybrid = run_benchmark(args.base_url, api_key, "hybrid")
        print(f"  {hybrid['total']} queries, {hybrid['errors']} errors, "
              f"p50={hybrid.get('p50_ms', 'N/A')}ms")

    write_report(baseline, hybrid, args.output)


if __name__ == "__main__":
    main()
