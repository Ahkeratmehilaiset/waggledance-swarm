#!/usr/bin/env python3
"""Run v1.18.0 benchmark corpus through legacy SmartRouterV2 and save artifacts.

Usage:
    python tools/run_benchmark.py [--yaml configs/benchmarks.yaml]
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path

# Project root on sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_router():
    """Load SmartRouterV2 with DomainCapsule."""
    from core.smart_router_v2 import SmartRouterV2, DomainCapsule
    capsule = DomainCapsule.load("cottage")
    return SmartRouterV2(capsule)


def _run_benchmark(yaml_path: str):
    from tools.benchmark_harness import BenchmarkHarness, BenchmarkReport

    harness = BenchmarkHarness.from_yaml(yaml_path)
    router = _load_router()

    print(f"Benchmark: {len(harness.queries)} queries from {yaml_path}")
    print(f"Router: SmartRouterV2 + DomainCapsule(cottage)")
    print("-" * 60)

    results = []
    passed = 0
    failed = 0
    errors = 0
    latencies = []

    for q in harness.queries:
        t0 = time.monotonic()
        try:
            route_result = router.route(q.query)
            ms = (time.monotonic() - t0) * 1000
            route_type = route_result.layer if hasattr(route_result, 'layer') else str(route_result)
            confidence = getattr(route_result, 'confidence', 0.0)
            matched_kw = getattr(route_result, 'matched_keywords', []) or []
            reason = getattr(route_result, 'reason', '')

            matched_expected = (route_type == q.expected_route) if q.expected_route else True

            status = "PASS" if matched_expected else "FAIL"
            if matched_expected:
                passed += 1
            else:
                failed += 1

            results.append({
                "query_id": q.id,
                "query": q.query,
                "domain": q.domain,
                "language": q.language,
                "expected_route": q.expected_route,
                "actual_route": route_type,
                "confidence": confidence,
                "matched_keywords": matched_kw,
                "reason": reason,
                "latency_ms": round(ms, 2),
                "matched_expected": matched_expected,
                "error": "",
            })
            latencies.append(ms)

            print(f"  {status:4s} {q.id:30s} expected={q.expected_route:16s} "
                  f"actual={route_type:16s} {ms:6.1f}ms")

        except Exception as e:
            ms = (time.monotonic() - t0) * 1000
            errors += 1
            latencies.append(ms)
            results.append({
                "query_id": q.id,
                "query": q.query,
                "domain": q.domain,
                "language": q.language,
                "expected_route": q.expected_route,
                "actual_route": "",
                "confidence": 0.0,
                "matched_keywords": [],
                "reason": "",
                "latency_ms": round(ms, 2),
                "matched_expected": False,
                "error": str(e),
            })
            print(f"  ERR  {q.id:30s} {e}")

    total = len(harness.queries)
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    pass_rate = passed / total if total else 0

    # Route distribution
    route_dist = {}
    for r in results:
        rt = r["actual_route"] or "error"
        route_dist[rt] = route_dist.get(rt, 0) + 1

    report = {
        "version": "v1.18.0",
        "timestamp": datetime.now().isoformat(),
        "corpus": yaml_path,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": round(pass_rate, 4),
        "avg_latency_ms": round(avg_latency, 2),
        "route_distribution": route_dist,
        "results": results,
    }

    # Save JSON
    json_path = ROOT / "data" / "benchmark_v1_18.json"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Save markdown summary
    md_path = ROOT / "data" / "benchmark_v1_18_summary.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# WaggleDance v1.18.0 Benchmark Report\n\n")
        f.write(f"**Date:** {report['timestamp']}\n")
        f.write(f"**Corpus:** {yaml_path} ({total} queries)\n\n")
        f.write(f"## Summary\n\n")
        f.write(f"| Metric | Value |\n")
        f.write(f"|--------|-------|\n")
        f.write(f"| Total queries | {total} |\n")
        f.write(f"| Passed | {passed} |\n")
        f.write(f"| Failed | {failed} |\n")
        f.write(f"| Errors | {errors} |\n")
        f.write(f"| Pass rate | {pass_rate*100:.1f}% |\n")
        f.write(f"| Avg latency | {avg_latency:.1f}ms |\n\n")
        f.write(f"## Route Distribution\n\n")
        f.write(f"| Route | Count |\n")
        f.write(f"|-------|-------|\n")
        for rt, cnt in sorted(route_dist.items()):
            f.write(f"| {rt} | {cnt} |\n")
        f.write(f"\n## Per-Query Results\n\n")
        f.write(f"| ID | Domain | Expected | Actual | Match | Latency |\n")
        f.write(f"|----|--------|----------|--------|-------|---------|\n")
        for r in results:
            match_icon = "PASS" if r["matched_expected"] else "FAIL"
            if r["error"]:
                match_icon = "ERR"
            f.write(f"| {r['query_id']} | {r['domain']} | "
                    f"{r['expected_route']} | {r['actual_route']} | "
                    f"{match_icon} | {r['latency_ms']:.1f}ms |\n")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed ({pass_rate*100:.1f}%), "
          f"{failed} failed, {errors} errors")
    print(f"Avg latency: {avg_latency:.1f}ms")
    print(f"Route distribution: {route_dist}")
    print(f"\nArtifacts saved:")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Run v1.18.0 benchmark")
    parser.add_argument("--yaml", default="configs/benchmarks.yaml",
                        help="Path to benchmark YAML corpus")
    args = parser.parse_args()
    _run_benchmark(args.yaml)
