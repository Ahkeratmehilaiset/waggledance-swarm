"""Offline tests for tools/run_routing_hotpath_microbench.py.

Hermetic: uses synthetic capsules only (no profile-file or model dependency),
tiny repeat counts, and asserts structure + the release-gate invariants rather
than absolute timing (which is host-dependent).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "routing_hotpath_microbench",
    REPO_ROOT / "tools" / "run_routing_hotpath_microbench.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]

from core.domain_capsule import DomainCapsule  # noqa: E402
from core.smart_router_v2 import SmartRouterV2  # noqa: E402

QUERIES = ["how much honey this season", "varroa treatment timing", "asdf qwerty"]


def test_percentile_interpolation_and_edges():
    assert mod._percentile([], 0.5) == 0.0
    assert mod._percentile([7.0], 0.99) == 7.0
    assert abs(mod._percentile([1.0, 2.0, 3.0, 4.0], 0.5) - 2.5) < 1e-9


def test_synth_capsule_builds_and_routes():
    capsule = DomainCapsule(mod._synth_capsule(5))
    router = SmartRouterV2(capsule)
    agg = mod._bench(router, QUERIES, repeats=2)
    assert agg["samples"] == len(QUERIES) * 2
    for key in ("p50", "p95", "p99", "mean"):
        assert isinstance(agg["measured_ms"][key], float)
        assert agg["measured_ms"][key] >= 0.0
    assert isinstance(agg["resolved_by_step"], dict)
    assert isinstance(agg["route_distribution"], dict)
    # every routed query is accounted for in the distribution
    assert sum(agg["route_distribution"].values()) == agg["samples"]


def test_scaling_track_structure():
    rows = mod.scaling_track(QUERIES, [2, 8], repeats=2)
    assert [r["k_decisions"] for r in rows] == [2, 8]
    for r in rows:
        for key in ("measured_p50_ms", "measured_p95_ms", "measured_mean_ms"):
            assert isinstance(r[key], float)
            assert r[key] >= 0.0


def test_stub_hotcache_hit_and_miss():
    cache = mod._StubHotCache(["alpha", "beta"])
    assert cache.get("alpha") is not None
    assert cache.get("beta") is not None
    assert cache.get("gamma") is None


def test_cache_effectiveness_structure_and_hitrate():
    # apiary is an in-repo profile; deterministic and offline.
    rows = mod.cache_effectiveness("apiary", QUERIES, repeats=2, fractions=[0.0, 1.0])
    assert [r["seeded_fraction"] for r in rows] == [0.0, 1.0]
    # 0% seeded -> no hits; 100% seeded -> every query hits.
    assert rows[0]["observed_hit_rate"] == 0.0
    assert rows[1]["observed_hit_rate"] == 1.0
    # a hit short-circuits, so its p50 is <= the miss p50 of the no-cache row.
    assert rows[1]["hit_p50_ms"] is not None
    assert rows[0]["miss_p50_ms"] is not None
    assert rows[1]["hit_p50_ms"] <= rows[0]["miss_p50_ms"]


def test_envelope_invariants_and_clean_summary():
    rep = mod._bench(SmartRouterV2(DomainCapsule(mod._synth_capsule(3))), QUERIES, 1)
    scaling = mod.scaling_track(QUERIES, [2], 1)
    cache = mod.cache_effectiveness("apiary", QUERIES, repeats=1, fractions=[0.0, 1.0])
    env = mod.build_envelope(
        profile="synthetic_scaling", repeats=1, ks=[2],
        representative=rep, scaling=scaling,
        cache=cache, fractions=[0.0, 1.0], corpus_size=len(QUERIES),
    )
    assert env["cache_effectiveness"] == cache
    inv = env["invariants"]
    assert inv["no_cloud_api_calls_this_session"] is True
    assert inv["deterministic_offline"] is True
    assert inv["no_superiority_claim"] is True
    assert list(inv["forbidden_vocabulary_excluded"]) == list(mod.FORBIDDEN_VOCABULARY)

    summary = mod.render_summary(env)
    # must not raise
    mod.assert_vocabulary_clean(summary)
    low = summary.lower()
    for phrase in mod.FORBIDDEN_VOCABULARY:
        assert phrase.lower() not in low
