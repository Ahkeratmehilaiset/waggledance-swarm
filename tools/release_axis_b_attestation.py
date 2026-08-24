#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed source and metric-coherence binding for Axis B evidence.

Pure helper: no network, no benchmark re-run, no writes. It answers one
question about a stored Axis B hex-eval report - does it attest a
coherent, commit-bound evaluation over the exact oracle corpus?

The canonical artifact this hardens against carries no source commit,
generation timestamp, or input hashes, and the collector's status check
accepts string "NaN" quality and per-file scores as a pass (NaN
comparison semantics make every floor check silently false). Every
failure shape maps to a stable, path-free blocker; an empty list is
returned only for a clean, coherent, exact-commit report.
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

AXIS_B_SCHEMA_VERSION = "waggledance.axis_b_hex_eval.v1"
AXIS_B_TARGET_VERSION = "v3.12.0"
AXIS_B_BENCHMARK_ID = "v3.12-axis-b-hex-aligned-eval"
AXIS_B_CELLS = (
    "bee_ops",
    "environment",
    "home_comfort",
    "hub",
    "logistics",
    "production",
    "safety_security",
)
AXIS_B_FILES = 7
AXIS_B_TOTAL_POSITIVE = 105
AXIS_B_TOTAL_NEGATIVE = 35
AXIS_B_ROW_POS_TOTAL = 15
AXIS_B_ROW_NEG_TOTAL = 5
AXIS_B_ORACLE_DIR = "tests/oracle_hex"
AXIS_B_THRESHOLDS = {
    "quality_floor": 0.74,
    "mismatched_baseline_quality": 0.5,
    "minimum_baseline_delta": 0.2,
    "per_cell_quality_floor": 0.6,
}
AXIS_B_EXPECTED_SOURCES = ("configs/hex_cells.yaml",) + tuple(
    f"tests/oracle_hex/{cell}.yaml" for cell in AXIS_B_CELLS
)
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ABSOLUTE_PATTERN = re.compile(r"^([A-Za-z]:|/|\\\\)")
_SCORE_DECIMALS = 4


def _append_once(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


def _is_strict_finite_number(value: object) -> bool:
    if type(value) is int:
        return True
    if type(value) is float:
        return math.isfinite(value)
    return False


def _is_strict_count(value: object) -> bool:
    return type(value) is int and value >= 0


def _rounded_ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, _SCORE_DECIMALS)


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


def _corpus_matches(corpus: object) -> bool:
    if not isinstance(corpus, dict):
        return False
    cells = corpus.get("cells")
    if not isinstance(cells, list) or sorted(
        cell for cell in cells if isinstance(cell, str)
    ) != sorted(AXIS_B_CELLS) or len(cells) != len(AXIS_B_CELLS):
        return False
    if corpus.get("files") != AXIS_B_FILES or type(
        corpus.get("files")
    ) is not int:
        return False
    if corpus.get("total_positive") != AXIS_B_TOTAL_POSITIVE or type(
        corpus.get("total_positive")
    ) is not int:
        return False
    if corpus.get("total_negative") != AXIS_B_TOTAL_NEGATIVE or type(
        corpus.get("total_negative")
    ) is not int:
        return False
    oracle_dir = corpus.get("oracle_dir")
    if not isinstance(oracle_dir, str) or oracle_dir.replace(
        "\\", "/"
    ) != AXIS_B_ORACLE_DIR:
        return False
    return True


def _thresholds_match(thresholds: object) -> bool:
    if not isinstance(thresholds, dict):
        return False
    if set(thresholds.keys()) != set(AXIS_B_THRESHOLDS.keys()):
        return False
    for key, expected in AXIS_B_THRESHOLDS.items():
        value = thresholds.get(key)
        if not _is_strict_finite_number(value) or type(value) is bool:
            return False
        if float(value) != expected:
            return False
    return True


def _row_coherent(row: object, cell: str) -> bool:
    if not isinstance(row, dict):
        return False
    if row.get("cell") != cell or row.get("file") != f"{cell}.yaml":
        return False
    counts = {
        key: row.get(key)
        for key in ("pos_correct", "pos_total", "neg_correct", "neg_total")
    }
    if not all(_is_strict_count(value) for value in counts.values()):
        return False
    if counts["pos_total"] != AXIS_B_ROW_POS_TOTAL:
        return False
    if counts["neg_total"] != AXIS_B_ROW_NEG_TOTAL:
        return False
    if counts["pos_correct"] > counts["pos_total"]:
        return False
    if counts["neg_correct"] > counts["neg_total"]:
        return False
    scores = {
        key: row.get(key)
        for key in ("pos_score", "neg_score", "file_score")
    }
    if not all(
        _is_strict_finite_number(value) and type(value) is not bool
        for value in scores.values()
    ):
        return False
    expected_pos = _rounded_ratio(counts["pos_correct"], counts["pos_total"])
    expected_neg = _rounded_ratio(counts["neg_correct"], counts["neg_total"])
    # file_score is rounded ONCE from the raw count ratios, mirroring
    # the producer; averaging the already-rounded pos/neg scores would
    # false-block truthful rows (e.g. pos_correct=10: producer 0.8333,
    # double-rounded 0.8334).
    expected_file = round(
        (
            counts["pos_correct"] / counts["pos_total"]
            + counts["neg_correct"] / counts["neg_total"]
        )
        / 2,
        _SCORE_DECIMALS,
    )
    if float(scores["pos_score"]) != expected_pos:
        return False
    if float(scores["neg_score"]) != expected_neg:
        return False
    if float(scores["file_score"]) != expected_file:
        return False
    return True


def evaluate_axis_b_attestation(
    report_path: Path | str,
    source_root: Path | str,
    expected_commit: str,
) -> list[str]:
    """Return stable blockers binding an Axis B report to source truth.

    Empty list only when the report is a readable JSON object with the
    exact schema/target/benchmark, literal ``pass`` result and exactly
    empty blockers; ``source_commit`` equals the 40-lowercase-hex
    ``expected_commit``; ``generated_at`` is explicit UTC-offset-zero;
    the source inventory is exactly ``configs/hex_cells.yaml`` plus the
    seven oracle cell yamls, unique, confined (no absolute paths,
    traversal, aliases, symlinks, or reparse components) with
    LF-normalized sha256 digests that recompute; the corpus and
    threshold constants match exactly; every metric is a strict finite
    numeric (bool, string, NaN, and infinity rejected) with counts as
    bounded nonnegative ints; per-cell rows are exactly one per cell
    with recomputed scores; aggregates and quality recompute exactly
    from the rows; negative routing is perfect; and quality clears
    every floor. All blockers are path-free; hostile nested types fold
    into blockers, never exceptions.
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
    ):
        return ["axis_b_report_unreadable"]
    if not isinstance(loaded, dict):
        return ["axis_b_report_unreadable"]

    blockers: list[str] = []

    if not (
        loaded.get("schema_version") == AXIS_B_SCHEMA_VERSION
        and loaded.get("target_version") == AXIS_B_TARGET_VERSION
        and loaded.get("benchmark_id") == AXIS_B_BENCHMARK_ID
        and loaded.get("result") == "pass"
        and loaded.get("blockers") == []
    ):
        _append_once(blockers, "axis_b_not_pass")

    source_commit = loaded.get("source_commit")
    if source_commit is None:
        _append_once(blockers, "axis_b_source_commit_missing")
    elif (
        not isinstance(source_commit, str)
        or source_commit != expected_commit
    ):
        _append_once(blockers, "axis_b_source_commit_mismatch")

    if _parse_utc_zero(loaded.get("generated_at")) is None:
        _append_once(blockers, "axis_b_generated_at_invalid")

    if not _corpus_matches(loaded.get("corpus")) or not _thresholds_match(
        loaded.get("thresholds")
    ):
        _append_once(blockers, "axis_b_corpus_mismatch")

    source_files = loaded.get("source_files")
    source_hashes = loaded.get("source_hashes")
    source_root_path = Path(source_root)
    bound_files: list[tuple[str, Path]] = []
    sources_bound = True
    try:
        if source_root_path.is_symlink() or _is_reparse_point(
            source_root_path
        ):
            sources_bound = False
    except (OSError, RuntimeError):
        sources_bound = False
    if (
        not isinstance(source_files, list)
        or not isinstance(source_hashes, dict)
    ):
        sources_bound = False
    else:
        seen: set[str] = set()
        seen_identities: set = set()
        for entry in source_files if sources_bound else []:
            normalized = _normalized_rel_path(entry)
            if normalized is None or normalized.casefold() in seen:
                sources_bound = False
                break
            seen.add(normalized.casefold())
            candidate = source_root_path / normalized
            try:
                if candidate.is_symlink() or not candidate.is_file():
                    sources_bound = False
                    break
                component_link = _is_reparse_point(candidate)
                resolved_root = source_root_path.resolve()
                if not component_link:
                    for parent in candidate.parents:
                        if (
                            parent == source_root_path
                            or parent == resolved_root
                        ):
                            break
                        if parent.is_symlink() or _is_reparse_point(
                            parent
                        ):
                            component_link = True
                            break
                if component_link:
                    sources_bound = False
                    break
                resolved = candidate.resolve()
                if not resolved.is_relative_to(resolved_root):
                    sources_bound = False
                    break
                candidate_stat = os.stat(candidate)
                if candidate_stat.st_ino:
                    identity = (
                        candidate_stat.st_dev,
                        candidate_stat.st_ino,
                    )
                else:
                    identity = str(resolved).casefold()
                if identity in seen_identities:
                    sources_bound = False
                    break
                seen_identities.add(identity)
            except (OSError, RuntimeError):
                sources_bound = False
                break
            bound_files.append((entry, candidate))
        if sources_bound:
            normalized_set = {
                _normalized_rel_path(entry) for entry, _ in bound_files
            }
            if normalized_set != set(AXIS_B_EXPECTED_SOURCES):
                sources_bound = False
        if sources_bound and set(source_hashes.keys()) != {
            entry for entry, _ in bound_files
        }:
            sources_bound = False
    if not sources_bound:
        _append_once(blockers, "axis_b_sources_unbound")

    if sources_bound:
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
            _append_once(blockers, "axis_b_source_hash_mismatch")

    per_file = loaded.get("per_file")
    rows_coherent = isinstance(per_file, list) and len(per_file) == len(
        AXIS_B_CELLS
    )
    rows_by_cell: dict[str, dict] = {}
    if rows_coherent:
        for row in per_file:
            cell = row.get("cell") if isinstance(row, dict) else None
            if not isinstance(cell, str) or cell in rows_by_cell:
                rows_coherent = False
                break
            rows_by_cell[cell] = row
        if rows_coherent and set(rows_by_cell) != set(AXIS_B_CELLS):
            rows_coherent = False
    if rows_coherent:
        for cell in AXIS_B_CELLS:
            if not _row_coherent(rows_by_cell[cell], cell):
                rows_coherent = False
                break

    aggregates_coherent = rows_coherent
    quality_value: float | None = None
    if rows_coherent:
        micro_pos = sum(
            rows_by_cell[cell]["pos_correct"] for cell in AXIS_B_CELLS
        )
        micro_pos_total = sum(
            rows_by_cell[cell]["pos_total"] for cell in AXIS_B_CELLS
        )
        micro_neg = sum(
            rows_by_cell[cell]["neg_correct"] for cell in AXIS_B_CELLS
        )
        micro_neg_total = sum(
            rows_by_cell[cell]["neg_total"] for cell in AXIS_B_CELLS
        )
        for key, expected in (
            ("micro_pos", micro_pos),
            ("micro_pos_total", micro_pos_total),
            ("micro_neg", micro_neg),
            ("micro_neg_total", micro_neg_total),
        ):
            value = loaded.get(key)
            if type(value) is not int or value != expected:
                aggregates_coherent = False
        # Quality mirrors the producer exactly: the rounded mean of the
        # already-reported (and revalidated) per-row file_score values,
        # NOT a symmetric micro-ratio formula - the two rounding paths
        # genuinely diverge on heterogeneous rows.
        expected_quality = round(
            sum(
                float(rows_by_cell[cell]["file_score"])
                for cell in AXIS_B_CELLS
            )
            / len(AXIS_B_CELLS),
            _SCORE_DECIMALS,
        )
        reported_quality = loaded.get("quality")
        if (
            not _is_strict_finite_number(reported_quality)
            or type(reported_quality) is bool
            or float(reported_quality) != expected_quality
        ):
            aggregates_coherent = False
        else:
            quality_value = expected_quality
        # Perfect negative routing is part of the coherence contract.
        if micro_neg != micro_neg_total or any(
            rows_by_cell[cell]["neg_correct"]
            != rows_by_cell[cell]["neg_total"]
            for cell in AXIS_B_CELLS
        ):
            aggregates_coherent = False
    if not rows_coherent or not aggregates_coherent:
        _append_once(blockers, "axis_b_metrics_incoherent")

    if quality_value is not None and rows_coherent:
        floors_ok = (
            quality_value >= AXIS_B_THRESHOLDS["quality_floor"]
            and quality_value
            > AXIS_B_THRESHOLDS["mismatched_baseline_quality"]
            + AXIS_B_THRESHOLDS["minimum_baseline_delta"]
            and all(
                float(rows_by_cell[cell]["file_score"])
                >= AXIS_B_THRESHOLDS["per_cell_quality_floor"]
                for cell in AXIS_B_CELLS
            )
        )
        if not floors_ok:
            _append_once(blockers, "axis_b_quality_below_floor")

    return blockers
