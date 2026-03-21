#!/usr/bin/env python3
"""Run the v1.18.0 shadow validation corpus (50-100 queries).

Usage:
    python tools/run_shadow_validation.py [--output PATH]
"""

import asyncio
import json
import sys
import os
import time
import yaml

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.runtime_shadow_compare import (
    RuntimeShadowCompare, CompareRequest, ShadowReport,
)


def load_corpus(path: str = "configs/shadow_corpus.yaml") -> list[CompareRequest]:
    """Load queries from the shadow corpus YAML."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    queries = []
    for q in data.get("queries", []):
        queries.append(CompareRequest(
            query=q.get("query", ""),
            language=q.get("language", "auto"),
            profile="COTTAGE",
        ))
    return queries


_legacy_router = None

def _get_legacy_router():
    global _legacy_router
    if _legacy_router is None:
        from core.smart_router_v2 import SmartRouterV2
        from core.domain_capsule import DomainCapsule
        capsule = DomainCapsule.load("cottage")
        _legacy_router = SmartRouterV2(capsule)
    return _legacy_router


async def legacy_handler(query: str) -> dict:
    """Simulate legacy runtime routing via SmartRouterV2."""
    try:
        router = _get_legacy_router()
        result = router.route(query)
        return {
            "response": f"[legacy-stub] {result.layer}:{result.decision_id or 'none'}",
            "route_type": result.layer or "llm_reasoning",
            "confidence": result.confidence,
        }
    except Exception as e:
        return {
            "response": f"[legacy-error] {e}",
            "route_type": "error",
            "confidence": 0.0,
        }


async def hex_handler(query: str) -> dict:
    """Simulate hex runtime routing via routing_policy."""
    try:
        from waggledance.core.orchestration.routing_policy import (
            extract_features, select_route,
        )
        from core.shared_routing_helpers import probe_micromodel

        mm_hit, mm_conf = probe_micromodel(query)

        features = extract_features(
            query=query,
            hot_cache_hit=False,
            memory_score=0.0,
            matched_keywords=[],
            profile="COTTAGE",
            language="auto",
            micromodel_enabled=True,
            micromodel_hit=mm_hit,
            micromodel_confidence=mm_conf,
        )
        # Use a minimal config mock
        class _MinConfig:
            def get(self, key, default=None):
                return default
        route = select_route(features, _MinConfig())
        return {
            "response": f"[hex-stub] {route.route_type}",
            "route_type": route.route_type,
            "confidence": route.confidence,
        }
    except Exception as e:
        return {
            "response": f"[hex-error] {e}",
            "route_type": "error",
            "confidence": 0.0,
        }


def generate_summary(report: ShadowReport) -> str:
    """Generate a human-readable summary."""
    lines = [
        "# Shadow Validation Report — v1.18.0",
        f"**Date:** {time.strftime('%Y-%m-%d %H:%M')}",
        f"**Corpus:** {report.total_requests} queries",
        "",
        "## Summary",
        f"- Route match rate: {report.route_match_rate:.1%}",
        f"- Response match rate: {report.response_match_rate:.1%}",
        f"- Errors: {report.errors}",
        f"- Avg old latency: {report.avg_old_latency_ms:.2f} ms",
        f"- Avg new latency: {report.avg_new_latency_ms:.2f} ms",
        "",
        "## Route Distribution (new runtime)",
    ]
    for route, count in sorted(report.route_distribution.items(),
                                key=lambda x: -x[1]):
        pct = count / report.total_requests * 100 if report.total_requests else 0
        lines.append(f"- {route}: {count} ({pct:.0f}%)")

    # Notable mismatches
    mismatches = [r for r in report.results if not r.match and not r.error]
    if mismatches:
        lines.extend(["", "## Notable Mismatches"])
        for m in mismatches[:20]:
            lines.append(
                f"- `{m.query[:60]}...` — old: {m.old_route}, new: {m.new_route}")

    errors = [r for r in report.results if r.error]
    if errors:
        lines.extend(["", "## Errors"])
        for e in errors[:10]:
            lines.append(f"- `{e.query[:60]}...` — {e.error}")

    return "\n".join(lines)


async def main():
    output_json = "data/shadow_report_v1_18.json"
    output_md = "data/shadow_report_v1_18_summary.md"

    # Parse args
    for i, arg in enumerate(sys.argv[1:]):
        if arg == "--output" and i + 2 < len(sys.argv):
            output_json = sys.argv[i + 2]

    corpus = load_corpus()
    print(f"Loaded {len(corpus)} queries from shadow corpus")

    compare = RuntimeShadowCompare(mode="validate")
    report = await compare.run_corpus(corpus, legacy_handler, hex_handler)

    # Save JSON report
    RuntimeShadowCompare.save_report(report, output_json)
    print(f"JSON report saved to: {output_json}")

    # Save markdown summary
    summary = generate_summary(report)
    os.makedirs(os.path.dirname(output_md) or ".", exist_ok=True)
    with open(output_md, "w", encoding="utf-8") as f:
        f.write(summary)
    print(f"Summary saved to: {output_md}")

    # Print summary
    print(f"\n{summary}")


if __name__ == "__main__":
    asyncio.run(main())
