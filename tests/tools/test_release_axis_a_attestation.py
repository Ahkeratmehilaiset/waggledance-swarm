# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os

import pytest

from tools.release_axis_a_attestation import (
    AXIS_A_ALLOWED_FAMILIES,
    AXIS_A_DESCRIPTORS_PER_FAMILY,
    AXIS_A_DESCRIPTORS_PER_HEX_CELL,
    AXIS_A_EXPECTED_SOURCES,
    AXIS_A_HEX_CELLS,
    evaluate_axis_a_attestation,
)


COMMIT = "d204299440af5b1c2d3e4f5a6b7c8d9e0f1a2b3c"

# The pinned deterministic maps are the fixture's source of truth, so a
# drifting pin cannot quietly agree with a drifting fixture.
DESCRIPTORS_PER_FAMILY = dict(AXIS_A_DESCRIPTORS_PER_FAMILY)
DESCRIPTORS_PER_HEX_CELL = dict(AXIS_A_DESCRIPTORS_PER_HEX_CELL)

NOT_FINITE = ["NaN", "Infinity", float("nan"), float("inf"), True, "0.5", None]


def _lf_sha256(path) -> str:
    import hashlib

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_sources(tmp_path) -> None:
    for rel in AXIS_A_EXPECTED_SOURCES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {rel}\nvalue = 1\n", encoding="utf-8")


def _canonical_proof() -> dict:
    """The real shipped Axis A proof shape, with no source binding."""
    return {
        "allowed_families": list(AXIS_A_ALLOWED_FAMILIES),
        "branch_name": "phase17a/producer-fabric-scale",
        "build_descriptors_per_second": 4960.9,
        "build_index_time_seconds": 2.0158,
        "builder_jobs_delta": 0,
        "descriptors_per_family": dict(DESCRIPTORS_PER_FAMILY),
        "descriptors_per_hex_cell": dict(DESCRIPTORS_PER_HEX_CELL),
        "families_total": 6,
        "finished_at_utc": "2026-05-22T12:20:45Z",
        "hex_cells": list(AXIS_A_HEX_CELLS),
        "hex_cells_total": 8,
        "hot_path_cache_stats": {
            "artifact_cache_size_after_lookup": 1000,
            "buffered_flushed_total": 0,
            "buffered_pending_signals": 0,
            "cold_hits_warmed": 1000,
            "misses": 0,
            "warm_hits": 1000,
            "warm_index_size_after_lookup": 1000,
        },
        "is_synthetic_scale": True,
        "lookup_benchmark_shape": "hot_path_cache_attached_warm_pass",
        "lookup_by_source": {"auto_promoted_solver": 1000},
        "lookup_capability_hits_total": 1000,
        "lookup_cold_after_attach": {
            "by_source": {"auto_promoted_solver": 1000},
            "lookup_capability_hits_total": 1000,
            "lookup_fifo_fallback_total": 0,
            "lookup_mean_ms": 2.4893,
            "lookup_miss_total": 0,
            "lookup_p50_ms": 1.9783,
            "lookup_p95_ms": 4.3457,
            "lookup_p99_ms": 6.633,
        },
        "lookup_fifo_fallback_total": 0,
        "lookup_mean_ms": 0.0143,
        "lookup_miss_total": 0,
        "lookup_p50_ms": 0.0149,
        "lookup_p95_ms": 0.0167,
        "lookup_p99_ms": 0.0214,
        "lookup_pass_count": 1000,
        "no_allowlist_widening": True,
        "no_provider_credentials_required": True,
        "no_runtime_network_required": True,
        "not_canonical_corpus": True,
        "phase": "phase17a_solver_scale",
        "production_hot_path_cache_attached": True,
        "provider_jobs_delta": 0,
        "schema_version": 1,
        "started_at_utc": "2026-05-22T12:20:41Z",
        "synthetic_solver_descriptors_total": 10000,
    }


def _clean_report(tmp_path, **overrides) -> dict:
    """Honest real-shape proof augmented with the source binding."""
    report = _canonical_proof()
    report.update(
        {
            "source_commit": COMMIT,
            "generated_at": "2026-08-24T09:00:00Z",
            "source_files": list(AXIS_A_EXPECTED_SOURCES),
            "source_hashes": {
                rel: _lf_sha256(tmp_path / rel)
                for rel in AXIS_A_EXPECTED_SOURCES
            },
        }
    )
    report.update(overrides)
    return report


def _write_report(tmp_path, report):
    report_path = tmp_path / "axis_a_proof.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def _evaluate(tmp_path, report, commit: str = COMMIT):
    return evaluate_axis_a_attestation(
        _write_report(tmp_path, report), tmp_path, commit
    )


def _cold(**overrides) -> dict:
    cold = _canonical_proof()["lookup_cold_after_attach"]
    cold.update(overrides)
    return cold


def test_honest_augmented_report_passes(tmp_path) -> None:
    _write_sources(tmp_path)

    assert _evaluate(tmp_path, _clean_report(tmp_path)) == []


def test_canonical_proof_is_unbound(tmp_path) -> None:
    # The shipped artifact carries no commit, timestamp or inventory.
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _canonical_proof())

    assert "axis_a_source_commit_missing" in blockers
    assert "axis_a_generated_at_invalid" in blockers
    assert "axis_a_sources_unbound" in blockers


# --- the reproduced fail-open ------------------------------------------


@pytest.mark.parametrize("poison", NOT_FINITE)
def test_warm_p99_non_finite_blocks(tmp_path, poison) -> None:
    # The collector accepted string "NaN" here: float("NaN") parses and
    # every `nan > ceiling` comparison is False, so the floor vanished.
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, lookup_p99_ms=poison)
    )

    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize("poison", NOT_FINITE)
def test_cold_p99_non_finite_blocks(tmp_path, poison) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path,
        _clean_report(
            tmp_path, lookup_cold_after_attach=_cold(lookup_p99_ms=poison)
        ),
    )

    assert "axis_a_metrics_incoherent" in blockers


def test_string_nan_both_p99_never_passes(tmp_path) -> None:
    # The exact lead-reproduced shape: both p99 values as string NaN.
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path,
        _clean_report(
            tmp_path,
            lookup_p99_ms="NaN",
            lookup_cold_after_attach=_cold(lookup_p99_ms="NaN"),
        ),
    )

    assert blockers != []
    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "key",
    [
        "lookup_mean_ms",
        "lookup_p50_ms",
        "lookup_p95_ms",
        "build_descriptors_per_second",
        "build_index_time_seconds",
    ],
)
@pytest.mark.parametrize("poison", NOT_FINITE)
def test_other_metrics_non_finite_block(tmp_path, key, poison) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **{key: poison}))

    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "key", ["lookup_mean_ms", "lookup_p50_ms", "lookup_p95_ms"]
)
def test_negative_latency_blocks(tmp_path, key) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **{key: -0.1}))

    assert "axis_a_metrics_incoherent" in blockers


# --- floors -------------------------------------------------------------


def test_warm_p99_above_ceiling_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, lookup_p99_ms=1.5))

    assert "axis_a_below_floor" in blockers


def test_cold_p99_above_ceiling_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path,
        _clean_report(
            tmp_path, lookup_cold_after_attach=_cold(lookup_p99_ms=50.5)
        ),
    )

    assert "axis_a_below_floor" in blockers


@pytest.mark.parametrize("warm,cold", [(1.0, 50.0), (0.9999, 49.9999)])
def test_p99_exactly_at_ceiling_passes(tmp_path, warm, cold) -> None:
    _write_sources(tmp_path)
    report = _clean_report(
        tmp_path,
        lookup_p99_ms=warm,
        lookup_cold_after_attach=_cold(lookup_p99_ms=cold),
    )

    assert _evaluate(tmp_path, report) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"lookup_p50_ms": 0.9, "lookup_p95_ms": 0.5},
        {"lookup_p95_ms": 0.9, "lookup_p99_ms": 0.5},
        {"lookup_p50_ms": 0.02, "lookup_p95_ms": 0.0167},
    ],
    ids=["p50-over-p95", "p95-over-p99", "p50-over-p95-narrow"],
)
def test_inverted_warm_percentiles_block(tmp_path, overrides) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **overrides))

    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "cold_override",
    [
        {"lookup_p50_ms": 5.0, "lookup_p95_ms": 4.3457},
        {"lookup_p95_ms": 7.0, "lookup_p99_ms": 6.633},
    ],
    ids=["p50-over-p95", "p95-over-p99"],
)
def test_inverted_cold_percentiles_block(tmp_path, cold_override) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path,
        _clean_report(
            tmp_path, lookup_cold_after_attach=_cold(**cold_override)
        ),
    )

    assert "axis_a_metrics_incoherent" in blockers


# --- cross-field coherence ---------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"lookup_capability_hits_total": 999},
        {"lookup_fifo_fallback_total": 1},
        {"lookup_miss_total": 1},
        {"provider_jobs_delta": 1},
        {"builder_jobs_delta": 1},
        {"lookup_by_source": {"auto_promoted_solver": 999}},
        {"lookup_by_source": {"manual": 1000}},
        {"lookup_by_source": {"auto_promoted_solver": 500, "manual": 500}},
        {"lookup_capability_hits_total": True},
        {"lookup_miss_total": "0"},
    ],
    ids=[
        "hits-drift",
        "fallback",
        "miss",
        "provider-delta",
        "builder-delta",
        "by-source-count",
        "by-source-name",
        "by-source-extra",
        "hits-bool",
        "miss-string",
    ],
)
def test_cross_field_incoherence_blocks(tmp_path, overrides) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **overrides))

    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "stats_override",
    [
        {"warm_hits": 999},
        {"cold_hits_warmed": 999},
        {"misses": 1},
        {"buffered_flushed_total": 1},
        {"buffered_pending_signals": 1},
        {"artifact_cache_size_after_lookup": 999},
        {"warm_index_size_after_lookup": 999},
        {"warm_hits": True},
    ],
    ids=[
        "warm-hits",
        "cold-hits",
        "misses",
        "buffered-flushed",
        "buffered-pending",
        "artifact-size",
        "warm-index",
        "warm-hits-bool",
    ],
)
def test_cache_stat_drift_blocks(tmp_path, stats_override) -> None:
    _write_sources(tmp_path)
    stats = _canonical_proof()["hot_path_cache_stats"]
    stats.update(stats_override)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, hot_path_cache_stats=stats)
    )

    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "cold_override",
    [
        {"lookup_capability_hits_total": 999},
        {"lookup_fifo_fallback_total": 1},
        {"lookup_miss_total": 1},
        {"by_source": {"auto_promoted_solver": 999}},
    ],
    ids=["cold-hits", "cold-fallback", "cold-miss", "cold-by-source"],
)
def test_cold_block_drift_blocks(tmp_path, cold_override) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path,
        _clean_report(tmp_path, lookup_cold_after_attach=_cold(**cold_override)),
    )

    assert "axis_a_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "value", [None, [], "cold", 5, {"lookup_p99_ms": 6.633}]
)
def test_cold_block_shape_drift_blocks(tmp_path, value) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, lookup_cold_after_attach=value)
    )

    assert "axis_a_metrics_incoherent" in blockers


# --- identity, corpus and distribution ---------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"phase": "phase17b_solver_scale"},
        {"schema_version": 2},
        {"schema_version": "1"},
        {"schema_version": True},
        {"is_synthetic_scale": False},
        {"is_synthetic_scale": 1},
        {"not_canonical_corpus": False},
        {"not_canonical_corpus": "true"},
        {"families_total": 5},
        {"hex_cells_total": 7},
        {"synthetic_solver_descriptors_total": 9999},
        {"lookup_pass_count": 999},
    ],
    ids=[
        "phase",
        "schema-int",
        "schema-string",
        "schema-bool",
        "synthetic-false",
        "synthetic-truthy",
        "noncanonical-false",
        "noncanonical-string",
        "families-total",
        "cells-total",
        "descriptors",
        "lookups",
    ],
)
def test_identity_drift_blocks(tmp_path, overrides) -> None:
    # What was measured: phase, schema and the synthetic corpus claims.
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **overrides))

    assert "axis_a_corpus_mismatch" in blockers


@pytest.mark.parametrize(
    "overrides",
    [
        {"lookup_benchmark_shape": "warm_pass"},
        {"lookup_benchmark_shape": None},
        {"production_hot_path_cache_attached": 1},
        {"production_hot_path_cache_attached": False},
        {"no_provider_credentials_required": None},
        {"no_runtime_network_required": 0},
        {"no_allowlist_widening": "yes"},
    ],
    ids=[
        "shape",
        "shape-none",
        "attached-truthy",
        "attached-false",
        "credentials-none",
        "network-zero",
        "allowlist-string",
    ],
)
def test_benchmark_and_runtime_flag_drift_blocks(tmp_path, overrides) -> None:
    # How it was run: benchmark shape and disclaimed runtime authority.
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **overrides))

    assert "axis_a_metrics_incoherent" in blockers


def test_family_set_drift_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    families = list(AXIS_A_ALLOWED_FAMILIES)[:-1] + ["freeform_reasoning"]
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, allowed_families=families)
    )

    assert "axis_a_corpus_mismatch" in blockers


def test_cell_set_drift_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    cells = list(AXIS_A_HEX_CELLS)[:-1] + ["weather"]
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, hex_cells=cells))

    assert "axis_a_corpus_mismatch" in blockers


def test_duplicate_family_entry_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    families = list(AXIS_A_ALLOWED_FAMILIES)
    families[1] = families[0]
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, allowed_families=families)
    )

    assert "axis_a_corpus_mismatch" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda dist: dist.__setitem__("lookup_table", 1668),
        lambda dist: dist.__setitem__("lookup_table", -1),
        lambda dist: dist.__setitem__("lookup_table", 1667.0),
        lambda dist: dist.pop("lookup_table"),
        lambda dist: dist.__setitem__("freeform_reasoning", 0),
        # Sum-preserving swap: only the pinned per-key values catch it.
        lambda dist: dist.update(
            {"bounded_interpolation": 1667, "interval_bucket_classifier": 1666}
        ),
    ],
    ids=["sum", "negative", "float", "missing", "extra", "swap"],
)
def test_family_distribution_drift_blocks(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    dist = dict(DESCRIPTORS_PER_FAMILY)
    mutate(dist)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, descriptors_per_family=dist)
    )

    assert "axis_a_corpus_mismatch" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda dist: dist.__setitem__("energy", 1253),
        lambda dist: dist.pop("energy"),
        lambda dist: dist.__setitem__("weather", 0),
        # Sum-preserving swap between two cells.
        lambda dist: dist.update({"energy": 1254, "general": 1252}),
    ],
    ids=["sum", "missing", "extra", "swap"],
)
def test_cell_distribution_drift_blocks(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    dist = dict(DESCRIPTORS_PER_HEX_CELL)
    mutate(dist)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, descriptors_per_hex_cell=dist)
    )

    assert "axis_a_corpus_mismatch" in blockers


@pytest.mark.parametrize("value", [None, [], "map", 5])
def test_distribution_shape_drift_blocks(tmp_path, value) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, descriptors_per_family=value)
    )

    assert "axis_a_corpus_mismatch" in blockers


# --- commit and timestamp ----------------------------------------------


@pytest.mark.parametrize(
    "commit",
    [
        "",
        "not-a-commit",
        "D204299440AF5B1C2D3E4F5A6B7C8D9E0F1A2B3C",
        "d204299440af5b1c2d3e4f5a6b7c8d9e0f1a2b3",
        "d204299440af5b1c2d3e4f5a6b7c8d9e0f1a2b3cc",
        None,
        123,
    ],
    ids=["empty", "garbage", "upper", "short", "long", "none", "int"],
)
def test_invalid_expected_commit(tmp_path, commit) -> None:
    _write_sources(tmp_path)

    assert _evaluate(tmp_path, _clean_report(tmp_path), commit) == [
        "expected_commit_invalid"
    ]


@pytest.mark.parametrize(
    "value",
    ["0" * 40, 123, ["d" * 40], "D204299440AF5B1C2D3E4F5A6B7C8D9E0F1A2B3C"],
    ids=["other", "int", "list", "upper"],
)
def test_source_commit_mismatch(tmp_path, value) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, source_commit=value))

    assert "axis_a_source_commit_mismatch" in blockers


def test_source_commit_missing(tmp_path) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    report.pop("source_commit")
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_source_commit_missing" in blockers


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-24T09:00:00",
        "2026-08-24T09:00:00+02:00",
        "2026-08-24",
        "not-a-time",
        "",
        None,
        1756022400,
    ],
    ids=["naive", "offset", "date", "garbage", "empty", "none", "epoch"],
)
def test_generated_at_invalid(tmp_path, value) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, generated_at=value))

    assert "axis_a_generated_at_invalid" in blockers


@pytest.mark.parametrize(
    "value", ["2026-08-24T09:00:00Z", "2026-08-24T09:00:00+00:00"]
)
def test_generated_at_utc_zero_accepted(tmp_path, value) -> None:
    _write_sources(tmp_path)

    assert _evaluate(tmp_path, _clean_report(tmp_path, generated_at=value)) == []


# --- source inventory confinement --------------------------------------


@pytest.mark.parametrize(
    "entry",
    [
        "C:/evil/run_solver_scale_proof.py",
        "/etc/passwd",
        "../outside.py",
        "tools/../tools/run_solver_scale_proof.py",
        "./tools/run_solver_scale_proof.py",
        "tools//run_solver_scale_proof.py",
        "",
        None,
        5,
    ],
    ids=[
        "absolute-win",
        "absolute-posix",
        "traversal",
        "embedded-dotdot",
        "leading-dot",
        "empty-part",
        "empty",
        "none",
        "int",
    ],
)
def test_unconfined_source_entry_blocks(tmp_path, entry) -> None:
    _write_sources(tmp_path)
    files = list(AXIS_A_EXPECTED_SOURCES)
    files[0] = entry
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, source_files=files))

    assert "axis_a_sources_unbound" in blockers


def test_case_duplicate_entry_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    files = list(AXIS_A_EXPECTED_SOURCES)
    files[1] = files[0].upper()
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, source_files=files))

    assert "axis_a_sources_unbound" in blockers


def test_missing_source_entry_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    files = list(AXIS_A_EXPECTED_SOURCES)[:-1]
    report = _clean_report(tmp_path, source_files=files)
    report["source_hashes"] = {
        rel: report["source_hashes"][rel] for rel in files
    }
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_sources_unbound" in blockers


def test_extra_source_entry_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    extra = "tools/extra_input.py"
    (tmp_path / extra).write_text("# extra\n", encoding="utf-8")
    files = list(AXIS_A_EXPECTED_SOURCES) + [extra]
    report = _clean_report(tmp_path, source_files=files)
    report["source_hashes"][extra] = _lf_sha256(tmp_path / extra)
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_sources_unbound" in blockers


def test_absent_file_on_disk_blocks(tmp_path) -> None:
    # Hash the honest tree first, then remove the file it attested.
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    (tmp_path / AXIS_A_EXPECTED_SOURCES[0]).unlink()
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_sources_unbound" in blockers


@pytest.mark.parametrize("value", [None, "list", 5, {}])
def test_source_files_shape_drift_blocks(tmp_path, value) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, source_files=value))

    assert "axis_a_sources_unbound" in blockers


@pytest.mark.parametrize("value", [None, [], "hashes", 5])
def test_source_hashes_shape_drift_blocks(tmp_path, value) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, source_hashes=value))

    assert "axis_a_sources_unbound" in blockers


def test_hash_key_drift_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    report["source_hashes"].pop(AXIS_A_EXPECTED_SOURCES[0])
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_sources_unbound" in blockers


@pytest.mark.parametrize("value", ["sha256:" + "0" * 64, "", 5, None])
def test_hash_mismatch_blocks(tmp_path, value) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    report["source_hashes"][AXIS_A_EXPECTED_SOURCES[0]] = value
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_source_hash_mismatch" in blockers


def test_content_drift_after_hashing_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    target = tmp_path / AXIS_A_EXPECTED_SOURCES[0]
    target.write_text("# tampered\nvalue = 2\n", encoding="utf-8")
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_source_hash_mismatch" in blockers


def test_crlf_is_normalized_before_hashing(tmp_path) -> None:
    # LF normalization keeps a checkout-converted file honest.
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    target = tmp_path / AXIS_A_EXPECTED_SOURCES[0]
    target.write_bytes(
        target.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8")
    )

    assert _evaluate(tmp_path, report) == []


def test_symlinked_source_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    target = tmp_path / AXIS_A_EXPECTED_SOURCES[0]
    outside = tmp_path.parent / "outside_solver_proof.py"
    outside.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted here")
    blockers = _evaluate(tmp_path, _clean_report(tmp_path))

    assert "axis_a_sources_unbound" in blockers


def test_hardlink_alias_blocks(tmp_path) -> None:
    # Two inventory entries resolving to one inode is a padded count.
    _write_sources(tmp_path)
    first = tmp_path / AXIS_A_EXPECTED_SOURCES[0]
    second = tmp_path / AXIS_A_EXPECTED_SOURCES[1]
    second.unlink()
    try:
        os.link(first, second)
    except (OSError, NotImplementedError, AttributeError):
        pytest.skip("hardlink creation is not permitted here")
    report = _clean_report(tmp_path)
    blockers = _evaluate(tmp_path, report)

    assert "axis_a_sources_unbound" in blockers


def test_symlinked_source_root_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    alias = tmp_path.parent / (tmp_path.name + "_alias")
    try:
        alias.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not permitted here")
    blockers = evaluate_axis_a_attestation(
        _write_report(tmp_path, report), alias, COMMIT
    )

    assert "axis_a_sources_unbound" in blockers


# --- unreadable and hostile inputs -------------------------------------


def test_missing_report_is_unreadable(tmp_path) -> None:
    _write_sources(tmp_path)

    assert evaluate_axis_a_attestation(
        tmp_path / "absent.json", tmp_path, COMMIT
    ) == ["axis_a_report_unreadable"]


@pytest.mark.parametrize(
    "payload", ["[]", "null", '"proof"', "5", "{", "not json"]
)
def test_non_object_report_is_unreadable(tmp_path, payload) -> None:
    _write_sources(tmp_path)
    report_path = tmp_path / "axis_a_proof.json"
    report_path.write_text(payload, encoding="utf-8")

    assert evaluate_axis_a_attestation(report_path, tmp_path, COMMIT) == [
        "axis_a_report_unreadable"
    ]


def test_directory_report_is_unreadable(tmp_path) -> None:
    _write_sources(tmp_path)
    directory = tmp_path / "proof_dir"
    directory.mkdir()

    assert evaluate_axis_a_attestation(directory, tmp_path, COMMIT) == [
        "axis_a_report_unreadable"
    ]


def test_deeply_nested_json_never_crashes(tmp_path) -> None:
    _write_sources(tmp_path)
    depth = 20000
    payload = "[" * depth + "]" * depth
    report_path = tmp_path / "axis_a_proof.json"
    report_path.write_text(payload, encoding="utf-8")

    assert evaluate_axis_a_attestation(report_path, tmp_path, COMMIT) == [
        "axis_a_report_unreadable"
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"hot_path_cache_stats": {"warm_hits": {"nested": {"deep": 1}}}},
        {"lookup_by_source": {"auto_promoted_solver": {"count": 1000}}},
        {"descriptors_per_family": {"lookup_table": [1667]}},
        {"lookup_cold_after_attach": {"by_source": [1000]}},
        {"source_files": [{"path": "tools/run_solver_scale_proof.py"}]},
        {"source_hashes": {"tools/run_solver_scale_proof.py": {"sha": 1}}},
    ],
    ids=[
        "stats",
        "by-source",
        "distribution",
        "cold",
        "files",
        "hashes",
    ],
)
def test_hostile_nested_types_fold_into_blockers(tmp_path, overrides) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **overrides))

    assert blockers != []
    assert all(isinstance(blocker, str) for blocker in blockers)


def test_blockers_are_path_free(tmp_path) -> None:
    # A blocker must never leak a filesystem path into release output.
    _write_sources(tmp_path)
    report = _clean_report(tmp_path, phase="wrong", lookup_p99_ms="NaN")
    (tmp_path / AXIS_A_EXPECTED_SOURCES[0]).unlink()
    blockers = _evaluate(tmp_path, report)

    assert blockers
    for blocker in blockers:
        assert str(tmp_path) not in blocker
        assert "/" not in blocker and "\\" not in blocker
        assert blocker.startswith(("axis_a_", "expected_commit_"))
