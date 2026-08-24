# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

import pytest

from tools.release_bandit_attestation import (
    evaluate_bandit_source_attestation,
)


COMMIT = "d204299440af5b1c2d3e4f5a6b7c8d9e0f1a2b3c"


def _write_source_tree(tmp_path) -> list[str]:
    files = ["waggledance/alpha.py", "waggledance/pkg/beta.py", "core/gamma.py"]
    for rel in files:
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    junk = tmp_path / "waggledance" / "__pycache__" / "alpha.cpython-313.py"
    junk.parent.mkdir(parents=True, exist_ok=True)
    junk.write_text("ignored\n", encoding="utf-8")
    (tmp_path / "waggledance" / "notes.txt").write_text("np\n", encoding="utf-8")
    return files


def _clean_report(files: list[str], commit: str = COMMIT) -> dict:
    metrics: dict = {
        # Mixed separators on purpose: the canonical artifact uses
        # backslash keys on Windows.
        rel.replace("/", "\\") if index % 2 else rel: {
            "SEVERITY.HIGH": 0,
            "SEVERITY.MEDIUM": 0,
        }
        for index, rel in enumerate(files)
    }
    metrics["_totals"] = {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 0}
    return {
        "generated_at": "2026-08-24T08:00:00Z",
        "source_commit": commit,
        "metrics": metrics,
        "results": [],
        "errors": [],
    }


def _write_report(tmp_path, report) -> "Path":
    report_path = tmp_path / "bandit_report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return report_path


def test_clean_complete_exact_commit_report_passes(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report_path = _write_report(tmp_path, _clean_report(files))

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert blockers == []


def test_totals_only_report_is_unbound(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = _clean_report([])
    report["metrics"] = {
        "_totals": {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 0}
    }
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_unbound" in blockers


def test_duplicate_normalized_alias_is_unbound(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    # The same file under both separators collapses to one normalized
    # path: an ambiguous inventory must fail closed.
    report["metrics"]["core/gamma.py"] = {"SEVERITY.HIGH": 0}
    report["metrics"]["core\\gamma.py"] = {"SEVERITY.HIGH": 0}
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_unbound" in blockers


def test_invalid_inventory_key_alongside_clean_set_is_unbound(
    tmp_path,
) -> None:
    # An otherwise exact inventory plus one out-of-scope entry (non-.py
    # here) must fail closed instead of the entry being silently skipped.
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    report["metrics"]["waggledance/notes.txt"] = {"SEVERITY.HIGH": 0}
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_unbound" in blockers


def test_malformed_per_file_metric_value_is_unbound(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    report["metrics"][files[0]] = ["not", "a", "dict"]
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_unbound" in blockers


@pytest.mark.parametrize(
    "per_file",
    [
        {},
        {"SEVERITY.HIGH": 0},
        {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 1},
        {"SEVERITY.HIGH": False, "SEVERITY.MEDIUM": 0},
        {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": "0"},
    ],
    ids=["empty-dict", "missing-medium", "medium-one", "bool-high", "string-medium"],
)
def test_per_file_metrics_without_clean_counts_are_unbound(
    tmp_path, per_file
) -> None:
    # An inventory entry is scan evidence only with strict-int-zero
    # HIGH/MEDIUM counts; anything else (tools' empty-dict probe class)
    # fails closed as unbound.
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    report["metrics"][files[0]] = per_file
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_unbound" in blockers


def test_missing_inventory_is_stale(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report_path = _write_report(tmp_path, _clean_report(files[:-1]))

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_stale" in blockers


def test_extra_scanned_file_is_stale(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report_path = _write_report(
        tmp_path, _clean_report(files + ["waggledance/ghost.py"])
    )

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_scanned_paths_stale" in blockers


def test_missing_source_commit_blocks(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    del report["source_commit"]
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_source_commit_missing" in blockers


def test_mismatched_source_commit_blocks(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files, commit="a" * 40)
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_source_commit_mismatch" in blockers


@pytest.mark.parametrize(
    "bad_commit",
    ["", "ABC123", COMMIT.upper(), COMMIT[:-1], COMMIT + "0", None],
    ids=["empty", "short-mixed", "uppercase", "39-hex", "41-hex", "none"],
)
def test_invalid_expected_commit_fails_closed(tmp_path, bad_commit) -> None:
    files = _write_source_tree(tmp_path)
    report_path = _write_report(tmp_path, _clean_report(files))

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, bad_commit
    )

    assert blockers == ["expected_commit_invalid"]


@pytest.mark.parametrize(
    "totals",
    [
        {"SEVERITY.HIGH": 1, "SEVERITY.MEDIUM": 0},
        {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": 3},
        {"SEVERITY.HIGH": False, "SEVERITY.MEDIUM": 0},
        {"SEVERITY.HIGH": 0, "SEVERITY.MEDIUM": "0"},
        {"SEVERITY.HIGH": 0},
        "not-a-dict",
    ],
    ids=["high", "medium", "bool-high", "string-medium", "missing-medium", "malformed"],
)
def test_unclean_or_malformed_totals_block(tmp_path, totals) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    report["metrics"]["_totals"] = totals
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_high_medium_present" in blockers


@pytest.mark.parametrize(
    "generated_at",
    [
        "",
        "yesterday",
        "2026-08-24T08:00:00",
        "2026-08-24T11:00:00+03:00",
        12345,
        None,
    ],
    ids=["empty", "garbage", "naive", "nonzero-offset", "non-string", "missing"],
)
def test_invalid_generated_at_blocks(tmp_path, generated_at) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files)
    if generated_at is None:
        del report["generated_at"]
    else:
        report["generated_at"] = generated_at
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_generated_at_invalid" in blockers


def test_unreadable_and_non_object_fail_closed(tmp_path) -> None:
    _write_source_tree(tmp_path)
    missing = tmp_path / "missing.json"
    assert evaluate_bandit_source_attestation(missing, tmp_path, COMMIT) == [
        "bandit_report_unreadable"
    ]

    not_object = tmp_path / "list.json"
    not_object.write_text("[1, 2, 3]", encoding="utf-8")
    assert evaluate_bandit_source_attestation(
        not_object, tmp_path, COMMIT
    ) == ["bandit_report_unreadable"]


def test_malformed_nested_types_never_crash(tmp_path) -> None:
    _write_source_tree(tmp_path)
    report = {
        "generated_at": {"nested": True},
        "source_commit": ["not", "a", "string"],
        "metrics": None,
        "results": "garbage",
    }
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    assert "bandit_high_medium_present" in blockers
    assert "bandit_scanned_paths_unbound" in blockers
    assert "bandit_source_commit_mismatch" in blockers
    assert "bandit_generated_at_invalid" in blockers


def test_no_path_or_content_leak_in_blockers(tmp_path) -> None:
    files = _write_source_tree(tmp_path)
    report = _clean_report(files[:-1], commit="b" * 40)
    report_path = _write_report(tmp_path, report)

    blockers = evaluate_bandit_source_attestation(
        report_path, tmp_path, COMMIT
    )

    encoded = json.dumps(blockers)
    assert str(tmp_path) not in encoded
    assert report_path.name not in encoded
    assert "b" * 40 not in encoded
