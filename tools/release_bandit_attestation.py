#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fail-closed source binding for release Bandit evidence.

Pure helper: no network, no Bandit invocation, no artifact writes. It
answers one question about a stored Bandit JSON report - does it attest
a clean scan of *exactly the current source tree at an exact commit*?

The canonical artifact this hardens against currently has no source
commit at all, covers fewer files than the present tree, and the
collector's clean-check accepts even a totals-only forged JSON. Every
failure shape maps to a stable, path-free blocker; an empty list is
returned only for a clean, complete, exact-commit report.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BANDIT_SOURCE_BASES = ("waggledance", "core")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _is_strict_zero_int(value: object) -> bool:
    # bool is an int subclass; a forged True/False total must not count.
    return type(value) is int and value == 0


def _normalized_py_path(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized.endswith(".py"):
        return None
    if "__pycache__" in normalized.split("/"):
        return None
    return normalized


def _current_source_py_paths(source_root: Path) -> set[str]:
    paths: set[str] = set()
    for base in BANDIT_SOURCE_BASES:
        base_dir = source_root / base
        if not base_dir.is_dir():
            continue
        for path in base_dir.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            paths.add(path.relative_to(source_root).as_posix())
    return paths


def _generated_at_valid(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    try:
        parsed.astimezone(dt.UTC)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def evaluate_bandit_source_attestation(
    report_path: Path | str,
    source_root: Path | str,
    expected_commit: str,
) -> list[str]:
    """Return stable blockers binding a Bandit report to current source.

    Empty list only when the report is a readable JSON object whose
    HIGH/MEDIUM totals are strict-int zero, whose per-file metrics cover
    exactly the current ``waggledance`` + ``core`` ``.py`` tree (ignoring
    ``__pycache__``), whose ``source_commit`` equals the 40-lowercase-hex
    ``expected_commit``, and whose ``generated_at`` parses as a valid
    timezone-aware UTC timestamp. All blockers are path-free; malformed
    nested types fold into blockers, never exceptions.
    """
    blockers: list[str] = []

    if not isinstance(expected_commit, str) or not _COMMIT_PATTERN.match(
        expected_commit
    ):
        return ["expected_commit_invalid"]

    try:
        loaded = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ["bandit_report_unreadable"]
    if not isinstance(loaded, dict):
        return ["bandit_report_not_object"]

    metrics = loaded.get("metrics")
    if not isinstance(metrics, dict):
        blockers.append("bandit_severity_totals_not_clean")
        blockers.append("bandit_report_totals_only_unbound")
    else:
        totals = metrics.get("_totals")
        if not isinstance(totals, dict) or not (
            _is_strict_zero_int(totals.get("SEVERITY.HIGH"))
            and _is_strict_zero_int(totals.get("SEVERITY.MEDIUM"))
        ):
            blockers.append("bandit_severity_totals_not_clean")

        scanned: set[str] = set()
        for key in metrics:
            if key == "_totals":
                continue
            normalized = _normalized_py_path(key)
            if normalized is not None:
                scanned.add(normalized)
        if not scanned:
            blockers.append("bandit_report_totals_only_unbound")
        elif scanned != _current_source_py_paths(Path(source_root)):
            blockers.append("bandit_scanned_paths_stale")

    source_commit = loaded.get("source_commit")
    if source_commit is None:
        blockers.append("bandit_source_commit_missing")
    elif (
        not isinstance(source_commit, str)
        or source_commit != expected_commit
    ):
        blockers.append("bandit_source_commit_mismatch")

    if not _generated_at_valid(loaded.get("generated_at")):
        blockers.append("bandit_generated_at_invalid")

    return blockers
