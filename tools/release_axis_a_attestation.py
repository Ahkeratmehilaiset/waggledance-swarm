#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed source and metric-coherence binding for Axis A evidence.

Pure helper: no network, no benchmark re-run, no writes. It answers one
question about a stored Axis A solver-scale proof - does it attest a
coherent, commit-bound run over the exact synthetic corpus?

The canonical artifact this hardens against carries no source commit,
generation timestamp, or input hashes, and the collector's status check
accepts string "NaN" latencies as a pass: ``float("NaN")`` parses, and
every ``nan > ceiling`` comparison is silently False, so both the warm
and the cold p99 floors evaporate. Every failure shape maps to a stable,
path-free blocker; an empty list is returned only for a clean, coherent,
exact-commit proof.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AXIS_A_PHASE = "phase17a_solver_scale"
AXIS_A_SCHEMA_VERSION = 1
AXIS_A_BENCHMARK_SHAPE = "hot_path_cache_attached_warm_pass"
AXIS_A_LOOKUP_SOURCE = "auto_promoted_solver"
AXIS_A_ALLOWED_FAMILIES = (
    "bounded_interpolation",
    "interval_bucket_classifier",
    "linear_arithmetic",
    "lookup_table",
    "scalar_unit_conversion",
    "threshold_rule",
)
AXIS_A_HEX_CELLS = (
    "energy",
    "general",
    "learning",
    "math",
    "safety",
    "seasonal",
    "system",
    "thermal",
)
AXIS_A_DESCRIPTORS_TOTAL = 10000
AXIS_A_LOOKUPS_TOTAL = 1000
# The generator is deterministic, so the distribution maps are pinned by
# value, not merely by key set and total: a swap that preserves the sum
# is still a different corpus than the one this attestation binds.
AXIS_A_DESCRIPTORS_PER_FAMILY = {
    "bounded_interpolation": 1666,
    "interval_bucket_classifier": 1667,
    "linear_arithmetic": 1666,
    "lookup_table": 1667,
    "scalar_unit_conversion": 1667,
    "threshold_rule": 1667,
}
AXIS_A_DESCRIPTORS_PER_HEX_CELL = {
    "energy": 1252,
    "general": 1254,
    "learning": 1248,
    "math": 1248,
    "safety": 1248,
    "seasonal": 1248,
    "system": 1248,
    "thermal": 1254,
}
AXIS_A_WARM_P99_CEILING = 1.0
AXIS_A_COLD_P99_CEILING = 50.0
AXIS_A_EXPECTED_SOURCES = (
    "tools/run_solver_scale_proof.py",
    "waggledance/core/autonomy_growth/gap_intake.py",
    "waggledance/core/autonomy_growth/hot_path_cache.py",
    "waggledance/core/autonomy_growth/runtime_query_router.py",
    "waggledance/core/autonomy_growth/solver_dispatcher.py",
    "waggledance/core/storage/control_plane.py",
)
# Flags the proof must assert literally; a truthy stand-in never
# satisfies them. They split by what they describe: the corpus flags
# declare what was measured, the runtime flags declare how, so their
# failures carry different blockers.
AXIS_A_CORPUS_TRUE_FLAGS = (
    "is_synthetic_scale",
    "not_canonical_corpus",
)
AXIS_A_RUNTIME_TRUE_FLAGS = (
    "no_allowlist_widening",
    "no_provider_credentials_required",
    "no_runtime_network_required",
    "production_hot_path_cache_attached",
)
AXIS_A_WARM_LATENCIES = (
    "lookup_mean_ms",
    "lookup_p50_ms",
    "lookup_p95_ms",
    "lookup_p99_ms",
)
AXIS_A_COLD_LATENCIES = AXIS_A_WARM_LATENCIES
AXIS_A_CACHE_STAT_ZEROS = (
    "buffered_flushed_total",
    "buffered_pending_signals",
    "misses",
)
AXIS_A_CACHE_STAT_LOOKUP_TOTALS = (
    "artifact_cache_size_after_lookup",
    "cold_hits_warmed",
    "warm_hits",
    "warm_index_size_after_lookup",
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATTERN = re.compile(r"^([A-Za-z]:|/|\\\\)")


def _append_once(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _is_strict_finite_number(value: object) -> bool:
    if type(value) is int:
        # A huge JSON integer (e.g. 10**1000) overflows float(); it must
        # fold into a blocker, never raise, so probe the conversion here
        # where every later float() call is guarded by this check.
        try:
            return math.isfinite(float(value))
        except OverflowError:
            return False
    if type(value) is float:
        return math.isfinite(value)
    return False


def _is_strict_nonnegative_number(value: object) -> bool:
    return _is_strict_finite_number(value) and float(value) >= 0.0


def _is_strict_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _is_exact_count(value: object, expected: int) -> bool:
    return type(value) is int and value == expected


def _parse_utc_zero(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        return None
    return parsed


def _normalized_rel_path(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.replace("\\", "/").strip()
    if _ABSOLUTE_PATTERN.match(normalized):
        return None
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return normalized


def _is_reparse_point(path: Path) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not reparse_flag:
        return False
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return True
    return bool(attributes & reparse_flag)


def _source_digest(path: Path) -> str | None:
    try:
        normalized = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError):
        return None
    digest = hashlib.sha256(normalized.encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def _exact_string_list(value: object, expected: tuple[str, ...]) -> bool:
    if not isinstance(value, list) or len(value) != len(expected):
        return False
    if any(not isinstance(item, str) for item in value):
        return False
    return sorted(value) == sorted(expected)


def _distribution_exact(
    value: object, expected: dict[str, int], total: int
) -> bool:
    """The pinned deterministic map, value by value, summing to total."""
    if not isinstance(value, dict):
        return False
    if set(value.keys()) != set(expected):
        return False
    counts = [value[key] for key in expected]
    if not all(_is_strict_count(count) for count in counts):
        return False
    if any(value[key] != expected[key] for key in expected):
        return False
    return sum(counts) == total


def _by_source_exact(value: object, lookups: int) -> bool:
    if not isinstance(value, dict):
        return False
    if set(value.keys()) != {AXIS_A_LOOKUP_SOURCE}:
        return False
    return _is_exact_count(value.get(AXIS_A_LOOKUP_SOURCE), lookups)


def _latencies_ordered(block: dict, keys: tuple[str, ...]) -> bool:
    """Percentiles must not invert; a truthful run is monotonic."""
    ordered = [
        float(block[key])
        for key in ("lookup_p50_ms", "lookup_p95_ms", "lookup_p99_ms")
        if key in keys
    ]
    return all(
        earlier <= later for earlier, later in zip(ordered, ordered[1:])
    )


def _identity_and_corpus_coherent(loaded: dict) -> bool:
    if loaded.get("phase") != AXIS_A_PHASE:
        return False
    if not _is_exact_count(
        loaded.get("schema_version"), AXIS_A_SCHEMA_VERSION
    ):
        return False
    for flag in AXIS_A_CORPUS_TRUE_FLAGS:
        if loaded.get(flag) is not True:
            return False
    if not _exact_string_list(
        loaded.get("allowed_families"), AXIS_A_ALLOWED_FAMILIES
    ):
        return False
    if not _exact_string_list(loaded.get("hex_cells"), AXIS_A_HEX_CELLS):
        return False
    if not _is_exact_count(
        loaded.get("families_total"), len(AXIS_A_ALLOWED_FAMILIES)
    ):
        return False
    if not _is_exact_count(
        loaded.get("hex_cells_total"), len(AXIS_A_HEX_CELLS)
    ):
        return False
    if not _is_exact_count(
        loaded.get("synthetic_solver_descriptors_total"),
        AXIS_A_DESCRIPTORS_TOTAL,
    ):
        return False
    if not _is_exact_count(
        loaded.get("lookup_pass_count"), AXIS_A_LOOKUPS_TOTAL
    ):
        return False
    if not _distribution_exact(
        loaded.get("descriptors_per_family"),
        AXIS_A_DESCRIPTORS_PER_FAMILY,
        AXIS_A_DESCRIPTORS_TOTAL,
    ):
        return False
    if not _distribution_exact(
        loaded.get("descriptors_per_hex_cell"),
        AXIS_A_DESCRIPTORS_PER_HEX_CELL,
        AXIS_A_DESCRIPTORS_TOTAL,
    ):
        return False
    return True


def _metrics_coherent(loaded: dict) -> bool:
    lookups = AXIS_A_LOOKUPS_TOTAL

    # How the run was executed: the benchmark shape it claims and the
    # runtime authority it disclaims.
    if loaded.get("lookup_benchmark_shape") != AXIS_A_BENCHMARK_SHAPE:
        return False
    for flag in AXIS_A_RUNTIME_TRUE_FLAGS:
        if loaded.get(flag) is not True:
            return False

    for key in AXIS_A_WARM_LATENCIES + (
        "build_descriptors_per_second",
        "build_index_time_seconds",
    ):
        if not _is_strict_nonnegative_number(loaded.get(key)):
            return False
    if not _latencies_ordered(loaded, AXIS_A_WARM_LATENCIES):
        return False

    if not _is_exact_count(loaded.get("lookup_capability_hits_total"), lookups):
        return False
    if not _is_exact_count(loaded.get("lookup_fifo_fallback_total"), 0):
        return False
    if not _is_exact_count(loaded.get("lookup_miss_total"), 0):
        return False
    if not _is_exact_count(loaded.get("provider_jobs_delta"), 0):
        return False
    if not _is_exact_count(loaded.get("builder_jobs_delta"), 0):
        return False
    if not _by_source_exact(loaded.get("lookup_by_source"), lookups):
        return False

    stats = loaded.get("hot_path_cache_stats")
    if not isinstance(stats, dict):
        return False
    for key in AXIS_A_CACHE_STAT_ZEROS:
        if not _is_exact_count(stats.get(key), 0):
            return False
    for key in AXIS_A_CACHE_STAT_LOOKUP_TOTALS:
        if not _is_exact_count(stats.get(key), lookups):
            return False

    cold = loaded.get("lookup_cold_after_attach")
    if not isinstance(cold, dict):
        return False
    for key in AXIS_A_COLD_LATENCIES:
        if not _is_strict_nonnegative_number(cold.get(key)):
            return False
    if not _latencies_ordered(cold, AXIS_A_COLD_LATENCIES):
        return False
    if not _is_exact_count(cold.get("lookup_capability_hits_total"), lookups):
        return False
    if not _is_exact_count(cold.get("lookup_fifo_fallback_total"), 0):
        return False
    if not _is_exact_count(cold.get("lookup_miss_total"), 0):
        return False
    if not _by_source_exact(cold.get("by_source"), lookups):
        return False

    return True


def _sources_bound(
    loaded: dict, source_root: Path
) -> tuple[bool, list[tuple[str, Path]]]:
    """Exact, unique, confined inventory - no alias, link or escape."""
    source_files = loaded.get("source_files")
    source_hashes = loaded.get("source_hashes")
    bound_files: list[tuple[str, Path]] = []
    try:
        if source_root.is_symlink() or _is_reparse_point(source_root):
            return False, []
    except (OSError, RuntimeError):
        return False, []
    if not isinstance(source_files, list) or not isinstance(
        source_hashes, dict
    ):
        return False, []

    seen: set[str] = set()
    seen_identities: set = set()
    for entry in source_files:
        normalized = _normalized_rel_path(entry)
        if normalized is None or normalized.casefold() in seen:
            return False, []
        seen.add(normalized.casefold())
        candidate = source_root / normalized
        try:
            if candidate.is_symlink() or not candidate.is_file():
                return False, []
            component_link = _is_reparse_point(candidate)
            resolved_root = source_root.resolve()
            if not component_link:
                for parent in candidate.parents:
                    if parent == source_root or parent == resolved_root:
                        break
                    if parent.is_symlink() or _is_reparse_point(parent):
                        component_link = True
                        break
            if component_link:
                return False, []
            resolved = candidate.resolve()
            if not resolved.is_relative_to(resolved_root):
                return False, []
            candidate_stat = os.stat(candidate)
            if candidate_stat.st_ino:
                identity = (candidate_stat.st_dev, candidate_stat.st_ino)
            else:
                identity = str(resolved).casefold()
            if identity in seen_identities:
                return False, []
            seen_identities.add(identity)
        except (OSError, RuntimeError):
            return False, []
        bound_files.append((entry, candidate))

    normalized_set = {
        _normalized_rel_path(entry) for entry, _ in bound_files
    }
    if normalized_set != set(AXIS_A_EXPECTED_SOURCES):
        return False, []
    if set(source_hashes.keys()) != {entry for entry, _ in bound_files}:
        return False, []
    return True, bound_files


def evaluate_axis_a_attestation(
    report_path: Path | str,
    source_root: Path | str,
    expected_commit: str,
) -> list[str]:
    """Return stable blockers binding an Axis A proof to source truth.

    Empty list only when the proof is a readable JSON object whose
    ``source_commit`` equals the 40-lowercase-hex ``expected_commit``;
    ``generated_at`` is explicit UTC-offset-zero; the source inventory is
    exactly the six solver-scale inputs, unique, confined (no absolute
    paths, traversal, aliases, symlinks, reparse points, or hardlink
    duplicates) with LF-normalized sha256 digests that recompute; the
    phase, schema, synthetic and noncanonical flags, family and cell
    sets, and the 10000-descriptor / 1000-lookup corpus match exactly
    with coherent distribution maps; every count is a strict nonnegative
    int and every latency, rate and time metric is a strict finite
    nonnegative numeric (bool, string, NaN, and infinity rejected);
    hits equal lookups with zero fallback and misses, by-source is
    exactly the auto-promoted solver, cache stats and job deltas are
    exact, and both the warm and cold p99 clear their ceilings. All
    blockers are path-free; hostile nested types fold into blockers,
    never exceptions.
    """
    if not isinstance(expected_commit, str) or not _COMMIT_PATTERN.match(
        expected_commit
    ):
        return ["expected_commit_invalid"]

    try:
        loaded = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ):
        return ["axis_a_report_unreadable"]
    if not isinstance(loaded, dict):
        return ["axis_a_report_unreadable"]

    blockers: list[str] = []

    source_commit = loaded.get("source_commit")
    if source_commit is None:
        _append_once(blockers, "axis_a_source_commit_missing")
    elif (
        not isinstance(source_commit, str)
        or source_commit != expected_commit
    ):
        _append_once(blockers, "axis_a_source_commit_mismatch")

    if _parse_utc_zero(loaded.get("generated_at")) is None:
        _append_once(blockers, "axis_a_generated_at_invalid")

    try:
        identity_ok = _identity_and_corpus_coherent(loaded)
    except (RecursionError, TypeError, ValueError):
        identity_ok = False
    if not identity_ok:
        _append_once(blockers, "axis_a_corpus_mismatch")

    try:
        sources_bound, bound_files = _sources_bound(
            loaded, Path(source_root)
        )
    except (RecursionError, TypeError, ValueError):
        sources_bound, bound_files = False, []
    if not sources_bound:
        _append_once(blockers, "axis_a_sources_unbound")
    else:
        source_hashes = loaded.get("source_hashes")
        hashes_ok = True
        for entry, candidate in bound_files:
            expected_digest = source_hashes.get(entry)
            actual_digest = _source_digest(candidate)
            if (
                not isinstance(expected_digest, str)
                or actual_digest is None
                or actual_digest != expected_digest
            ):
                hashes_ok = False
        if not hashes_ok:
            _append_once(blockers, "axis_a_source_hash_mismatch")

    try:
        metrics_ok = _metrics_coherent(loaded)
    except (RecursionError, TypeError, ValueError):
        metrics_ok = False
    if not metrics_ok:
        _append_once(blockers, "axis_a_metrics_incoherent")

    if metrics_ok:
        cold = loaded.get("lookup_cold_after_attach")
        # Reached only once both p99 values are strict finite numerics,
        # so the ceilings cannot be dodged by a NaN comparison.
        floors_ok = (
            float(loaded["lookup_p99_ms"]) <= AXIS_A_WARM_P99_CEILING
            and float(cold["lookup_p99_ms"]) <= AXIS_A_COLD_P99_CEILING
        )
        if not floors_ok:
            _append_once(blockers, "axis_a_below_floor")

    return blockers
