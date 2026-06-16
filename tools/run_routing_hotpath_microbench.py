#!/usr/bin/env python3
"""Routing hot-path micro-benchmark — offline, deterministic.

Drives ``core.smart_router_v2.SmartRouterV2.route()`` over the canonical
``configs/benchmarks.yaml`` corpus and a synthetic scaling track, isolating the
per-request routing/classification cost that the existing Phase-17B tracks do
not measure on its own (they isolate capability-lookup / e2e / restart /
producer-fabric). See
``docs/benchmarks/ROUTING_HOTPATH_EFFICIENCY_FINDINGS_2026_06_16.md`` for the
motivating findings.

What it measures
----------------
* Representative run: routing latency (p50/p95/p99/mean) over the 30-query
  corpus against a real capsule profile, plus which router step resolved each
  query (cache / capsule decision / keyword / fallback) and the route (layer)
  distribution.
* Scaling track: how ``match_decision`` cost grows with the capsule
  decision-set size K (synthetic capsules whose keywords never match, forcing a
  full scan — the worst case that bounds the cost).

This is an engineering record. It runs fully offline — no model or cloud calls —
and emits a release-gate style JSON envelope with a forbidden-vocabulary guard.
It does not assert WaggleDance is faster or superior to any external system.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import yaml  # noqa: E402

from core.domain_capsule import DomainCapsule  # noqa: E402
from core.smart_router_v2 import SmartRouterV2  # noqa: E402

# Mirrors tools/run_phase17b_local_efficiency_benchmark.py; the rendered MD/JSON
# summary must not contain any of these phrases.
FORBIDDEN_VOCABULARY: tuple[str, ...] = (
    "conscious", "sentient", "aware", "alive", "AGI",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
)

VALID_LAYERS = (
    "rule_constraints", "model_based", "statistical",
    "retrieval", "llm_reasoning",
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile; q in [0, 1]."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * q
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def load_corpus(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return list(data.get("queries", []))


def _bench(router: SmartRouterV2, queries: list[str], repeats: int) -> dict[str, Any]:
    """Run route() over the queries `repeats` times; aggregate timing + paths."""
    measured_ms: list[float] = []      # external wall time per route() call
    internal_ms: list[float] = []      # router's own routing_time_ms
    reasons: dict[str, int] = {}       # which step resolved the route
    layers: dict[str, int] = {}        # route (layer) distribution
    cold_ms: float | None = None       # first call (import/JIT warm-up effects)

    for _ in range(repeats):
        for q in queries:
            t0 = time.perf_counter()
            res = router.route(q)
            dt = (time.perf_counter() - t0) * 1000.0
            if cold_ms is None:
                cold_ms = dt
            measured_ms.append(dt)
            internal_ms.append(float(getattr(res, "routing_time_ms", 0.0)))
            reasons[res.reason] = reasons.get(res.reason, 0) + 1
            layers[res.layer] = layers.get(res.layer, 0) + 1

    # warm = steady-state (drop the single cold call)
    warm = measured_ms[1:] if len(measured_ms) > 1 else measured_ms
    return {
        "samples": len(measured_ms),
        "measured_ms": {
            "p50": round(_percentile(measured_ms, 0.50), 5),
            "p95": round(_percentile(measured_ms, 0.95), 5),
            "p99": round(_percentile(measured_ms, 0.99), 5),
            "mean": round(sum(measured_ms) / len(measured_ms), 5) if measured_ms else 0.0,
            "max": round(max(measured_ms), 5) if measured_ms else 0.0,
        },
        "internal_routing_ms": {
            "p50": round(_percentile(internal_ms, 0.50), 5),
            "p95": round(_percentile(internal_ms, 0.95), 5),
            "p99": round(_percentile(internal_ms, 0.99), 5),
        },
        "cold_ms": round(cold_ms, 5) if cold_ms is not None else 0.0,
        "warm_p50_ms": round(_percentile(warm, 0.50), 5),
        "resolved_by_step": dict(sorted(reasons.items())),
        "route_distribution": dict(sorted(layers.items())),
    }


def _synth_capsule(k: int) -> dict[str, Any]:
    """A capsule with K decisions whose keywords never match real queries.

    Forces match_decision to scan all K decisions (worst case), so the scaling
    track measures the O(K) cost rather than an early keyword hit.
    """
    decisions = [
        {
            "id": f"synthetic_decision_{i}",
            "keywords": [f"zzqx{i}alpha", f"zzqx{i}beta"],
            "primary_layer": "model_based",
        }
        for i in range(k)
    ]
    layers = {
        name: {"enabled": True, "priority": p + 1}
        for p, name in enumerate(VALID_LAYERS)
    }
    return {
        "domain": "synthetic_scaling",
        "version": "1.0",
        "layers": layers,
        "key_decisions": decisions,
        "default_fallback": "llm_reasoning",
    }


def scaling_track(queries: list[str], ks: list[int], repeats: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for k in ks:
        capsule = DomainCapsule(_synth_capsule(k))
        router = SmartRouterV2(capsule)
        agg = _bench(router, queries, repeats)
        out.append({
            "k_decisions": k,
            "measured_p50_ms": agg["measured_ms"]["p50"],
            "measured_p95_ms": agg["measured_ms"]["p95"],
            "measured_p99_ms": agg["measured_ms"]["p99"],
            "measured_mean_ms": agg["measured_ms"]["mean"],
        })
    return out


class _StubHotCache:
    """Deterministic offline stand-in for the route()-relevant HotCache surface.

    route() step 1 only needs ``.get(query)`` to return non-None on a hit, which
    short-circuits to the cached layer. The real ``core.fast_memory.HotCache``
    adds Voikko-normalized Finnish key matching (a heavy, host-dependent
    dependency) — out of scope for an offline latency measurement. Here a hit is
    an exact-query match against a pre-seeded set, so the measurement isolates
    the *route()-level short-circuit benefit*, not the cache's key-matching.
    """

    def __init__(self, seeded: list[str]):
        self._seeded = set(seeded)

    def get(self, query: str):
        return {"answer": "cached", "score": 1.0} if query in self._seeded else None


def cache_effectiveness(
    profile: str, queries: list[str], repeats: int, fractions: list[float],
) -> list[dict[str, Any]]:
    """Measure the route() HotCache short-circuit benefit vs seeded hit-rate.

    For each target fraction f, seed a stub cache with the first round(f*N)
    corpus queries, run route() over the whole corpus x repeats, and report the
    observed hit-rate plus hit/miss/overall p50 latency. f=0 is the no-cache
    baseline.
    """
    capsule = DomainCapsule.load(profile)
    n = len(queries)
    rows: list[dict[str, Any]] = []
    for f in fractions:
        seed_count = int(round(f * n))
        cache = _StubHotCache(queries[:seed_count]) if seed_count > 0 else None
        router = SmartRouterV2(capsule, hot_cache=cache)
        hit_ms: list[float] = []
        miss_ms: list[float] = []
        all_ms: list[float] = []
        hits = 0
        for _ in range(repeats):
            for q in queries:
                t0 = time.perf_counter()
                res = router.route(q)
                dt = (time.perf_counter() - t0) * 1000.0
                all_ms.append(dt)
                if res.reason == "hot_cache_hit":
                    hits += 1
                    hit_ms.append(dt)
                else:
                    miss_ms.append(dt)
        total = max(repeats * n, 1)
        rows.append({
            "seeded_fraction": round(f, 3),
            "observed_hit_rate": round(hits / total, 4),
            "hit_p50_ms": round(_percentile(hit_ms, 0.50), 5) if hit_ms else None,
            "miss_p50_ms": round(_percentile(miss_ms, 0.50), 5) if miss_ms else None,
            "overall_p50_ms": round(_percentile(all_ms, 0.50), 5),
        })
    return rows


def build_envelope(
    *, profile: str, repeats: int, ks: list[int],
    representative: dict[str, Any], scaling: list[dict[str, Any]],
    cache: list[dict[str, Any]], fractions: list[float],
    corpus_size: int,
) -> dict[str, Any]:
    return {
        "benchmark": "routing_hotpath_microbench",
        "schema_version": 1,
        "generated_utc": _utc_iso(),
        "host_note": "local engineering run; absolute numbers are host-dependent",
        "inputs": {
            "profile": profile,
            "corpus": "configs/benchmarks.yaml",
            "corpus_size": corpus_size,
            "repeats": repeats,
            "scale_k": ks,
            "cache_fractions": fractions,
        },
        "representative": representative,
        "scaling": scaling,
        "cache_effectiveness": cache,
        "invariants": {
            "no_cloud_api_calls_this_session": True,
            "no_pull_or_download_this_session": True,
            "deterministic_offline": True,
            "no_superiority_claim": True,
            "forbidden_vocabulary_excluded": list(FORBIDDEN_VOCABULARY),
        },
    }


def render_summary(env: dict[str, Any]) -> str:
    rep = env["representative"]
    lines = [
        "Routing hot-path micro-benchmark",
        f"  profile={env['inputs']['profile']} corpus_size={env['inputs']['corpus_size']} repeats={env['inputs']['repeats']}",
        f"  representative routing latency (ms): p50={rep['measured_ms']['p50']} p95={rep['measured_ms']['p95']} p99={rep['measured_ms']['p99']} mean={rep['measured_ms']['mean']}",
        f"  cold={rep['cold_ms']}ms warm_p50={rep['warm_p50_ms']}ms",
        f"  resolved_by_step={rep['resolved_by_step']}",
        f"  route_distribution={rep['route_distribution']}",
        "  scaling (match_decision worst case):",
    ]
    for row in env["scaling"]:
        lines.append(
            f"    K={row['k_decisions']:>5}  p50={row['measured_p50_ms']}ms  p95={row['measured_p95_ms']}ms  mean={row['measured_mean_ms']}ms"
        )
    if env.get("cache_effectiveness"):
        lines.append("  HotCache effectiveness (route() short-circuit, stub cache):")
        for row in env["cache_effectiveness"]:
            lines.append(
                f"    seeded={row['seeded_fraction']:<5} hit_rate={row['observed_hit_rate']:<6} "
                f"hit_p50={row['hit_p50_ms']}ms miss_p50={row['miss_p50_ms']}ms overall_p50={row['overall_p50_ms']}ms"
            )
    return "\n".join(lines)


def assert_vocabulary_clean(text: str) -> None:
    low = text.lower()
    hit = [p for p in FORBIDDEN_VOCABULARY if p.lower() in low]
    if hit:
        raise SystemExit(f"forbidden vocabulary in rendered summary: {hit}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="apiary",
                    help="capsule profile to load for the representative run")
    ap.add_argument("--repeats", type=int, default=50,
                    help="repeats of the full corpus for the representative run")
    ap.add_argument("--scale", type=int, nargs="*", default=[10, 100, 1000],
                    help="capsule decision-set sizes K for the scaling track")
    ap.add_argument("--scale-repeats", type=int, default=20,
                    help="repeats of the corpus per K in the scaling track")
    ap.add_argument("--cache-fractions", type=float, nargs="*",
                    default=[0.0, 0.25, 0.5, 1.0],
                    help="seeded HotCache hit fractions for the cache-effectiveness track")
    ap.add_argument("--cache-repeats", type=int, default=20,
                    help="repeats of the corpus per fraction in the cache track")
    ap.add_argument("--corpus", default="configs/benchmarks.yaml")
    ap.add_argument("--out-dir", default=None,
                    help="if set, write the JSON envelope to <out-dir>/routing_hotpath_microbench.json")
    args = ap.parse_args(argv)

    corpus_path = REPO_ROOT / args.corpus
    corpus = load_corpus(corpus_path)
    queries = [str(c["query"]) for c in corpus]

    capsule = DomainCapsule.load(args.profile)
    router = SmartRouterV2(capsule)
    representative = _bench(router, queries, args.repeats)
    scaling = scaling_track(queries, list(args.scale), args.scale_repeats)
    cache = cache_effectiveness(
        args.profile, queries, args.cache_repeats, list(args.cache_fractions),
    )

    env = build_envelope(
        profile=args.profile, repeats=args.repeats, ks=list(args.scale),
        representative=representative, scaling=scaling,
        cache=cache, fractions=list(args.cache_fractions),
        corpus_size=len(queries),
    )

    summary = render_summary(env)
    assert_vocabulary_clean(summary)
    print(summary)

    if args.out_dir:
        out_dir = REPO_ROOT / args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "routing_hotpath_microbench.json"
        out_path.write_text(json.dumps(env, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
