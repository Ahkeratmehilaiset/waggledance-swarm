# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os

import pytest

from tools.release_axis_b_attestation import (
    AXIS_B_CELLS,
    AXIS_B_EXPECTED_SOURCES,
    evaluate_axis_b_attestation,
)


COMMIT = "d204299440af5b1c2d3e4f5a6b7c8d9e0f1a2b3c"


def _lf_sha256(path) -> str:
    import hashlib

    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_sources(tmp_path) -> None:
    for rel in AXIS_B_EXPECTED_SOURCES:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {rel}\ncontent: 1\n", encoding="utf-8")


def _row(cell: str, pos_correct: int = 6) -> dict:
    pos_score = round(pos_correct / 15, 4)
    # file_score rounds ONCE from raw ratios, mirroring the producer.
    file_score = round((pos_correct / 15 + 1.0) / 2, 4)
    return {
        "cell": cell,
        "file": f"{cell}.yaml",
        "pos_correct": pos_correct,
        "pos_total": 15,
        "pos_score": pos_score,
        "neg_correct": 5,
        "neg_total": 5,
        "neg_score": 1.0,
        "file_score": file_score,
    }


def _clean_report(tmp_path, **overrides) -> dict:
    # pos_correct=12 per cell: micro 84/105 = 0.8; quality
    # round((0.8 + 1.0) / 2, 4) = 0.9 clears every floor; per-cell
    # file_score = round((0.8 + 1.0) / 2, 4) = 0.9 >= 0.6.
    rows = [_row(cell, pos_correct=12) for cell in AXIS_B_CELLS]
    report = {
        "schema_version": "waggledance.axis_b_hex_eval.v1",
        "target_version": "v3.12.0",
        "benchmark_id": "v3.12-axis-b-hex-aligned-eval",
        "result": "pass",
        "blockers": [],
        "quality": 0.9,
        "micro_pos": 84,
        "micro_pos_total": 105,
        "micro_neg": 35,
        "micro_neg_total": 35,
        "corpus": {
            "cells": list(AXIS_B_CELLS),
            "files": 7,
            "total_positive": 105,
            "total_negative": 35,
            "oracle_dir": "tests\\oracle_hex",
        },
        "thresholds": {
            "quality_floor": 0.74,
            "mismatched_baseline_quality": 0.5,
            "minimum_baseline_delta": 0.2,
            "per_cell_quality_floor": 0.6,
        },
        "per_file": rows,
        "source_commit": COMMIT,
        "generated_at": "2026-08-24T09:00:00Z",
        "source_files": list(AXIS_B_EXPECTED_SOURCES),
        "source_hashes": {
            rel: _lf_sha256(tmp_path / rel)
            for rel in AXIS_B_EXPECTED_SOURCES
        },
    }
    report.update(overrides)
    return report


def _write_report(tmp_path, report):
    report_path = tmp_path / "axis_b_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def _evaluate(tmp_path, report):
    return evaluate_axis_b_attestation(
        _write_report(tmp_path, report), tmp_path, COMMIT
    )


def test_real_shape_fixture_passes(tmp_path) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path))

    assert blockers == []


def test_canonical_missing_binding_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    del report["source_commit"]
    del report["generated_at"]
    del report["source_files"]
    del report["source_hashes"]
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_source_commit_missing" in blockers
    assert "axis_b_generated_at_invalid" in blockers
    assert "axis_b_sources_unbound" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report.update({"quality": "NaN"}),
        lambda report: report.update({"quality": float("nan")}),
        lambda report: report.update({"quality": float("inf")}),
        lambda report: report.update({"quality": True}),
        lambda report: report["per_file"][0].update(
            {"file_score": "NaN"}
        ),
        lambda report: report["per_file"][0].update(
            {"file_score": float("nan")}
        ),
        lambda report: report["per_file"][0].update(
            {"pos_score": "0.8"}
        ),
        lambda report: report["per_file"][0].update(
            {"neg_score": True}
        ),
    ],
    ids=[
        "quality-nan-string",
        "quality-nan-float",
        "quality-inf",
        "quality-bool",
        "row-nan-string",
        "row-nan-float",
        "row-string-score",
        "row-bool-score",
    ],
)
def test_non_finite_or_non_numeric_metrics_block(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    mutate(report)
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["per_file"][0].update({"pos_correct": -1}),
        lambda report: report["per_file"][0].update({"pos_correct": 16}),
        lambda report: report["per_file"][0].update({"pos_correct": True}),
        lambda report: report["per_file"][0].update({"pos_score": 0.9}),
        lambda report: report.update({"micro_pos": 90}),
        lambda report: report.update({"micro_pos_total": True}),
        lambda report: report.update({"quality": 0.91}),
        lambda report: report["per_file"][0].update(
            {"neg_correct": 4, "neg_score": 0.8,
             "file_score": round((0.8 + 0.8) / 2, 4)}
        ),
    ],
    ids=[
        "negative-count",
        "over-total",
        "bool-count",
        "score-mismatch",
        "aggregate-drift",
        "aggregate-bool",
        "quality-drift",
        "imperfect-negative-routing",
    ],
)
def test_incoherent_counts_scores_aggregates_block(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    mutate(report)
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_metrics_incoherent" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["per_file"].pop(),
        lambda report: report["per_file"].__setitem__(
            0, {**report["per_file"][1]}
        ),
        lambda report: report["per_file"][0].update(
            {"file": "wrong.yaml"}
        ),
    ],
    ids=["missing-cell", "duplicate-cell", "file-mismatch"],
)
def test_row_shape_defects_block(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    mutate(report)
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_metrics_incoherent" in blockers


def _double_rounded_file_score(pos_correct: int) -> float:
    """The rejected path: average the already-rounded pos/neg scores."""
    return round((round(pos_correct / 15, 4) + 1.0) / 2, 4)


@pytest.mark.parametrize("pos_correct", [10, 11, 13])
def test_rounding_boundary_rows_pass(tmp_path, pos_correct: int) -> None:
    # Boundary positions above the quality floor where the producer's
    # single rounding of the raw ratios differs from averaging the
    # pre-rounded scores (e.g. 10 => producer 0.8333 vs 0.8334). Each
    # truthful report must survive.
    _write_sources(tmp_path)
    rows = [_row(cell, pos_correct=pos_correct) for cell in AXIS_B_CELLS]
    file_score = rows[0]["file_score"]
    assert file_score != _double_rounded_file_score(pos_correct)
    report = _clean_report(
        tmp_path,
        per_file=rows,
        micro_pos=pos_correct * len(AXIS_B_CELLS),
        quality=file_score,
    )
    blockers = _evaluate(tmp_path, report)

    assert blockers == []


@pytest.mark.parametrize("pos_correct", [2, 5])
def test_rounding_boundary_rows_below_floor_stay_coherent(
    tmp_path, pos_correct: int
) -> None:
    # The same divergence exists below the quality floor. Those reports
    # are blocked on the floor, but must never be called incoherent -
    # that would misattribute a real quality shortfall to metric drift.
    _write_sources(tmp_path)
    rows = [_row(cell, pos_correct=pos_correct) for cell in AXIS_B_CELLS]
    file_score = rows[0]["file_score"]
    assert file_score != _double_rounded_file_score(pos_correct)
    report = _clean_report(
        tmp_path,
        per_file=rows,
        micro_pos=pos_correct * len(AXIS_B_CELLS),
        quality=file_score,
    )
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_metrics_incoherent" not in blockers


def test_quality_mirrors_mean_of_file_scores_not_micro(tmp_path) -> None:
    # Heterogeneous rows where the two rounding paths diverge:
    # 6 rows at pos_correct=10 (file_score 0.8333) + 1 at 12 (0.9).
    # Producer quality = round(mean(reported), 4) = 0.8428, while the
    # symmetric micro formula would give 0.8429.
    _write_sources(tmp_path)
    rows = [
        _row(cell, pos_correct=(12 if index == 6 else 10))
        for index, cell in enumerate(AXIS_B_CELLS)
    ]
    base = dict(
        per_file=rows,
        micro_pos=6 * 10 + 12,
    )

    passing = _clean_report(tmp_path, **base, quality=0.8428)
    assert _evaluate(tmp_path, passing) == []

    micro_style = _clean_report(tmp_path, **base, quality=0.8429)
    blockers = _evaluate(tmp_path, micro_style)
    assert "axis_b_metrics_incoherent" in blockers


def test_quality_below_floor_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    # pos_correct=8 per cell: micro 56/105 -> quality
    # round((0.5333 + 1.0)/2, 4) via exact fractions = 0.7667 >= 0.74
    # but per-cell file_score = round((round(8/15,4)+1.0)/2,4) = 0.7667
    # still above 0.6. Use pos_correct=3: pos 21/105=0.2, quality 0.6
    # below the 0.74 floor while rows stay internally coherent.
    rows = [_row(cell, pos_correct=3) for cell in AXIS_B_CELLS]
    report = _clean_report(
        tmp_path,
        per_file=rows,
        micro_pos=21,
        quality=round((21 / 105 + 1.0) / 2, 4),
    )
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_quality_below_floor" in blockers
    assert "axis_b_metrics_incoherent" not in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report: report["corpus"].update({"files": 6}),
        lambda report: report["corpus"].update({"total_positive": 104}),
        lambda report: report["corpus"]["cells"].pop(),
        lambda report: report["corpus"].update({"oracle_dir": "other"}),
        lambda report: report["thresholds"].update(
            {"quality_floor": 0.7}
        ),
        lambda report: report["thresholds"].update(
            {"quality_floor": True}
        ),
        lambda report: report.pop("thresholds"),
    ],
    ids=[
        "file-count",
        "positive-total",
        "missing-cell",
        "oracle-dir",
        "threshold-drift",
        "threshold-bool",
        "thresholds-missing",
    ],
)
def test_corpus_or_threshold_drift_blocks(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    mutate(report)
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_corpus_mismatch" in blockers


@pytest.mark.parametrize(
    "overrides",
    [
        {"result": "hold"},
        {"blockers": ["quality_below_floor"]},
        {"schema_version": "waggledance.axis_b_hex_eval.v2"},
        {"target_version": "v3.13.0"},
        {"benchmark_id": "other"},
        {"benchmark_id": None},
    ],
    ids=["result", "blockers", "schema", "target", "benchmark", "benchmark-null"],
)
def test_non_pass_or_identity_drift_blocks(tmp_path, overrides) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(tmp_path, _clean_report(tmp_path, **overrides))

    assert "axis_b_not_pass" in blockers


def test_mismatched_commit_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    blockers = _evaluate(
        tmp_path, _clean_report(tmp_path, source_commit="a" * 40)
    )

    assert "axis_b_source_commit_mismatch" in blockers


@pytest.mark.parametrize(
    "bad_commit",
    ["", COMMIT.upper(), COMMIT[:-1], None],
    ids=["empty", "uppercase", "short", "none"],
)
def test_invalid_expected_commit_fails_closed(tmp_path, bad_commit) -> None:
    _write_sources(tmp_path)
    report_path = _write_report(tmp_path, _clean_report(tmp_path))

    blockers = evaluate_axis_b_attestation(
        report_path, tmp_path, bad_commit
    )

    assert blockers == ["expected_commit_invalid"]


@pytest.mark.parametrize(
    "generated_at",
    ["", "2026-08-24T09:00:00", "2026-08-24T12:00:00+03:00", 5, None],
    ids=["empty", "naive", "nonzero-offset", "non-string", "missing"],
)
def test_invalid_generated_at_blocks(tmp_path, generated_at) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    if generated_at is None:
        del report["generated_at"]
    else:
        report["generated_at"] = generated_at
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_generated_at_invalid" in blockers


@pytest.mark.parametrize(
    "mutate",
    [
        lambda report, files: report.update(
            {"source_files": files + [files[0]]}
        ),
        lambda report, files: report.update(
            {"source_files": files + ["CONFIGS/HEX_CELLS.YAML"]}
        ),
        lambda report, files: report.update(
            {"source_files": files[:-1]}
        ),
        lambda report, files: report.update(
            {"source_files": files[:-1] + ["tests/oracle_hex/extra.yaml"]}
        ),
        lambda report, files: report.update(
            {"source_files": files[:-1] + ["C:/evil/hex.yaml"]}
        ),
        lambda report, files: report.update(
            {"source_files": files[:-1] + ["../outside.yaml"]}
        ),
        lambda report, files: report.update(
            {"source_files": "configs"}
        ),
        lambda report, files: report["source_hashes"].pop(files[0]),
    ],
    ids=[
        "duplicate",
        "casefold-alias",
        "missing-entry",
        "unexpected-entry",
        "absolute",
        "traversal",
        "non-list",
        "hash-keyset-drift",
    ],
)
def test_unbound_source_inventories_block(tmp_path, mutate) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    mutate(report, list(AXIS_B_EXPECTED_SOURCES))
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_sources_unbound" in blockers


def test_symlink_source_is_unbound(tmp_path) -> None:
    _write_sources(tmp_path)
    original = tmp_path / "configs" / "hex_cells.yaml"
    alias = tmp_path / "configs" / "hex_cells_real.yaml"
    original.rename(alias)
    try:
        os.symlink(alias, original)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")
    report = _clean_report(tmp_path)
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_sources_unbound" in blockers


def test_hardlink_alias_is_unbound(tmp_path) -> None:
    _write_sources(tmp_path)
    target = tmp_path / "tests" / "oracle_hex" / "bee_ops.yaml"
    extra = tmp_path / "tests" / "oracle_hex" / "hub.yaml"
    extra.unlink()
    try:
        os.link(target, extra)
    except (OSError, NotImplementedError):
        pytest.skip("hardlink creation not permitted on this host")
    report = _clean_report(tmp_path)
    report["source_hashes"]["tests/oracle_hex/hub.yaml"] = _lf_sha256(
        extra
    )
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_sources_unbound" in blockers


def test_hash_drift_blocks(tmp_path) -> None:
    _write_sources(tmp_path)
    report = _clean_report(tmp_path)
    (tmp_path / "configs" / "hex_cells.yaml").write_text(
        "tampered: true\n", encoding="utf-8"
    )
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_source_hash_mismatch" in blockers


def test_hostile_nested_types_never_crash_or_leak(tmp_path) -> None:
    report = {
        "schema_version": ["v1"],
        "result": {"value": "pass"},
        "blockers": "none",
        "quality": {"score": 1},
        "micro_pos": "84",
        "corpus": "seven",
        "thresholds": [0.74],
        "per_file": {"cell": "bee_ops"},
        "source_commit": 12345,
        "generated_at": {"at": "now"},
        "source_files": {"a": 1},
        "source_hashes": ["sha256:x"],
    }
    blockers = _evaluate(tmp_path, report)

    assert "axis_b_not_pass" in blockers
    assert "axis_b_source_commit_mismatch" in blockers
    assert "axis_b_generated_at_invalid" in blockers
    assert "axis_b_corpus_mismatch" in blockers
    assert "axis_b_sources_unbound" in blockers
    assert "axis_b_metrics_incoherent" in blockers
    encoded = json.dumps(blockers)
    assert str(tmp_path) not in encoded


def test_unreadable_and_non_object_fail_closed(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    assert evaluate_axis_b_attestation(missing, tmp_path, COMMIT) == [
        "axis_b_report_unreadable"
    ]

    deep = tmp_path / "deep.json"
    depth = 200_000
    deep.write_text("[" * depth + "]" * depth, encoding="utf-8")
    assert evaluate_axis_b_attestation(deep, tmp_path, COMMIT) == [
        "axis_b_report_unreadable"
    ]

    not_object = tmp_path / "list.json"
    not_object.write_text("[1]", encoding="utf-8")
    assert evaluate_axis_b_attestation(
        not_object, tmp_path, COMMIT
    ) == ["axis_b_report_unreadable"]
