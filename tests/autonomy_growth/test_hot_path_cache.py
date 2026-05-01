# SPDX-License-Identifier: Apache-2.0
"""Hot-path cache + buffered signal sink tests (Phase 14 P3)."""

from __future__ import annotations

import json
import time

import pytest

from waggledance.core.autonomy_growth import (
    AutogrowthScheduler,
    BufferedSignalSink,
    DEFAULT_MAX_UNFLUSHED_AGE_MS,
    DEFAULT_MAX_UNFLUSHED_SIGNALS,
    GapSignal,
    HotPathCache,
    LowRiskGrower,
    ParsedArtifactCache,
    RuntimeGapDetector,
    WarmCapabilityIndex,
    digest_signals_into_intents,
    extract_features,
)
from waggledance.core.storage.control_plane import ControlPlaneDB


@pytest.fixture()
def cp(tmp_path):
    db = ControlPlaneDB(tmp_path / "cp.sqlite")
    db.migrate()
    g = LowRiskGrower(db)
    g.ensure_low_risk_policies()
    yield db
    db.close()


def _kelvin_seed():
    return {
        "spec": {"from_unit": "C", "to_unit": "K",
                  "factor": 1.0, "offset": 273.15},
        "validation_cases": [
            {"inputs": {"x": 0.0}, "expected": 273.15},
            {"inputs": {"x": 100.0}, "expected": 373.15},
        ],
        "shadow_samples": [{"x": float(i)} for i in range(10)],
        "solver_name_seed": "celsius_to_kelvin",
        "cell_id": "thermal",
    }


def _grow_one(cp: ControlPlaneDB) -> int:
    seed = _kelvin_seed()
    digest_signals_into_intents(
        cp,
        candidate_signals=[GapSignal(
            kind="runtime_miss", family_kind="scalar_unit_conversion",
            cell_coord="thermal", intent_seed="celsius_to_kelvin",
            spec_seed=seed,
        )],
        min_signals_per_intent=1, autoenqueue=True,
    )
    AutogrowthScheduler(cp).run_until_idle()
    rows = cp._conn.execute(  # type: ignore[attr-defined]
        "SELECT id FROM solvers WHERE status = 'auto_promoted' ORDER BY id DESC LIMIT 1"
    ).fetchall()
    return int(rows[0]["id"])


# ── WarmCapabilityIndex ────────────────────────────────────────────


def test_warm_capability_index_lookup_and_invalidate() -> None:
    idx = WarmCapabilityIndex()
    idx.store("scalar_unit_conversion", {"from_unit": "C", "to_unit": "K"}, 42)
    assert idx.lookup("scalar_unit_conversion",
                      {"from_unit": "C", "to_unit": "K"}) == 42
    assert idx.lookup("scalar_unit_conversion",
                      {"from_unit": "F", "to_unit": "C"}) is None
    # Invalidate by solver_id removes the row
    dropped = idx.invalidate_solver(42)
    assert dropped == 1
    assert idx.lookup("scalar_unit_conversion",
                      {"from_unit": "C", "to_unit": "K"}) is None


def test_warm_index_key_is_order_independent() -> None:
    idx = WarmCapabilityIndex()
    idx.store("threshold_rule",
              {"subject": "temperature_c", "operator": ">"}, 7)
    # Same key set, different dict iteration order
    assert idx.lookup("threshold_rule",
                      {"operator": ">", "subject": "temperature_c"}) == 7


def test_warm_index_size_and_clear() -> None:
    idx = WarmCapabilityIndex()
    for i in range(5):
        idx.store("lookup_table", {"domain": f"d{i}"}, i)
    assert idx.size == 5
    idx.clear()
    assert idx.size == 0


# ── ParsedArtifactCache ────────────────────────────────────────────


def test_parsed_artifact_cache_round_trip() -> None:
    cache = ParsedArtifactCache()
    parsed = {"kind": "scalar_unit_conversion", "factor": 1.0,
              "offset": 273.15}
    cache.store(42, "art_xyz", parsed, "celsius_to_kelvin_v1")
    got = cache.get(42)
    assert got is not None
    assert got[0] == "art_xyz"
    assert got[1] == parsed
    assert got[2] == "celsius_to_kelvin_v1"
    assert cache.invalidate_solver(42) is True
    assert cache.get(42) is None


# ── BufferedSignalSink ─────────────────────────────────────────────


def test_buffered_sink_flushes_on_size_bound(cp: ControlPlaneDB) -> None:
    detector = RuntimeGapDetector(cp)
    sink = BufferedSignalSink(
        detector, max_unflushed_signals=3, max_unflushed_age_ms=10_000_000,
        register_atexit=False,
    )
    for i in range(3):
        ok = sink.enqueue(GapSignal(
            kind="runtime_miss", family_kind="lookup_table",
            cell_coord="general", intent_seed=f"k{i}",
        ))
        assert ok is True
    # Reaching the bound should opportunistically flush
    ok4 = sink.enqueue(GapSignal(
        kind="runtime_miss", family_kind="lookup_table",
        cell_coord="general", intent_seed="k4_overflow",
    ))
    assert ok4 is True
    # Earlier entries already drained
    assert sink.pending_count() <= 1
    assert cp.count_runtime_gap_signals() >= 3


def test_buffered_sink_flushes_on_age_bound(cp: ControlPlaneDB) -> None:
    """Manual clock to assert the age-bound flush path."""

    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    detector = RuntimeGapDetector(cp)
    sink = BufferedSignalSink(
        detector, max_unflushed_signals=999, max_unflushed_age_ms=100,
        clock=clock, register_atexit=False,
    )
    sink.enqueue(GapSignal(
        kind="runtime_miss", family_kind="lookup_table",
        cell_coord="general", intent_seed="aged",
    ))
    assert sink.pending_count() == 1
    # Age advances past the threshold; next enqueue appends, then the
    # post-append maybe-flush trips on the oldest entry's age and
    # drains the whole queue (including the freshly-added one). That
    # is the documented contract: when the age bound trips, the entire
    # queue is drained — we do not partially flush.
    fake_now[0] = 0.5  # 500 ms in the future
    sink.enqueue(GapSignal(
        kind="runtime_miss", family_kind="lookup_table",
        cell_coord="general", intent_seed="fresh",
    ))
    assert sink.pending_count() == 0
    assert cp.count_runtime_gap_signals() == 2


def test_buffered_sink_force_flush_drains_queue(cp: ControlPlaneDB) -> None:
    detector = RuntimeGapDetector(cp)
    sink = BufferedSignalSink(
        detector, max_unflushed_signals=999, max_unflushed_age_ms=999_999,
        register_atexit=False,
    )
    for i in range(5):
        sink.enqueue(GapSignal(
            kind="runtime_miss", family_kind="lookup_table",
            cell_coord="general", intent_seed=f"k{i}",
        ))
    assert sink.pending_count() == 5
    drained = sink.force_flush()
    assert drained == 5
    assert sink.pending_count() == 0
    assert cp.count_runtime_gap_signals() == 5


def test_buffered_sink_documented_hardkill_loss_bound() -> None:
    """The sink documents its bound; the value is enforced by config."""

    class _StubDet:
        def record(self, signal):  # pragma: no cover — unused
            return None

    sink = BufferedSignalSink(
        _StubDet(), max_unflushed_signals=250, max_unflushed_age_ms=400,
        register_atexit=False,
    )
    assert sink.hardkill_loss_bound_signals == 250
    assert sink.max_unflushed_signals == 250
    assert sink.max_unflushed_age_ms == 400


def test_buffered_sink_default_bounds_match_invariant() -> None:
    """Defaults must not exceed the prompt-mandated invariant
    (1000 signals / 500 ms)."""

    assert DEFAULT_MAX_UNFLUSHED_SIGNALS <= 1000
    assert DEFAULT_MAX_UNFLUSHED_AGE_MS <= 500


# ── HotPathCache.warm_dispatch end-to-end ──────────────────────────


def test_warm_dispatch_cold_then_warm(cp: ControlPlaneDB) -> None:
    _grow_one(cp)
    detector = RuntimeGapDetector(cp)
    cache = HotPathCache(control_plane=cp, detector=detector)
    features = {"from_unit": "C", "to_unit": "K"}
    inputs = {"x": 0.0}

    # First call: cold, then warmed
    r1 = cache.warm_dispatch("scalar_unit_conversion", features, inputs)
    assert r1.matched is True
    assert r1.source == "cold_then_warmed"
    assert r1.output == pytest.approx(273.15)

    # Second call: pure warm cache
    r2 = cache.warm_dispatch("scalar_unit_conversion", features, inputs)
    assert r2.matched is True
    assert r2.source == "warm_cache"
    assert cache.stats.warm_hits == 1
    assert cache.stats.cold_hits_warmed == 1


def test_warm_dispatch_miss_records_no_solver(cp: ControlPlaneDB) -> None:
    detector = RuntimeGapDetector(cp)
    cache = HotPathCache(control_plane=cp, detector=detector)
    r = cache.warm_dispatch(
        "scalar_unit_conversion",
        {"from_unit": "miles", "to_unit": "feet"},
        {"x": 1.0},
    )
    assert r.matched is False
    assert r.source == "miss"
    assert cache.stats.misses == 1


def test_invalidate_solver_drops_warm_index_and_artifact(
    cp: ControlPlaneDB,
) -> None:
    sid = _grow_one(cp)
    detector = RuntimeGapDetector(cp)
    cache = HotPathCache(control_plane=cp, detector=detector)
    features = {"from_unit": "C", "to_unit": "K"}
    cache.warm_dispatch("scalar_unit_conversion", features, {"x": 0.0})
    assert cache.capability_index.size > 0
    assert cache.artifact_cache.size > 0
    cache.invalidate_solver(sid)
    assert cache.capability_index.size == 0
    assert cache.artifact_cache.size == 0


def test_warm_path_avoids_sqlite_after_warmup(cp: ControlPlaneDB) -> None:
    """After the first cold lookup, repeated calls do NOT touch SQLite.

    We patch the find/get methods on the control plane to raise if
    called; the warm path must succeed without invoking them.
    """

    _grow_one(cp)
    detector = RuntimeGapDetector(cp)
    cache = HotPathCache(control_plane=cp, detector=detector)
    features = {"from_unit": "C", "to_unit": "K"}
    cache.warm_dispatch("scalar_unit_conversion", features, {"x": 0.0})

    # Replace the SQLite paths with sentinels that fail loudly.
    def _explode(*args, **kwargs):
        raise AssertionError("warm path must not touch SQLite")

    cp.find_auto_promoted_solvers_by_features = _explode  # type: ignore[assignment]
    cp.get_solver_artifact = _explode  # type: ignore[assignment]

    for _ in range(50):
        r = cache.warm_dispatch("scalar_unit_conversion", features, {"x": 25.0})
        assert r.source == "warm_cache"
