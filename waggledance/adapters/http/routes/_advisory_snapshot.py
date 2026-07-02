# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Shared advisory snapshot loader for the advisory routes and dashboard.

Single source of truth for the ``data/<case>/latest_advisory.json`` snapshot
validation semantics that were previously copy-pasted across
``eng01_advisory.py``, ``air01_advisory.py``, ``eng06_advisory.py`` and
``advisory_dashboard.py``. The NaN/Infinity-500 bug wave (#1470 lead finding,
#1468/#1472 twins) existed precisely because the copies drifted; one loader
closes the class (same lesson as the shared SSRF host guard).

Every failure mode degrades to a safe ``result_marker`` payload, never an
exception: missing / empty / oversized / unreadable / parse-fail / non-object
/ missing-marker / non-finite numbers (Python's ``json.loads`` accepts NaN
and Infinity constants, and ``1e999`` overflows to inf, which strict JSON
encoders like Starlette's ``JSONResponse`` refuse with a 500).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ADVISORY_MAX_BYTES = 1_000_000

NO_ADVISORY_YET = "NO_ADVISORY_YET"
SNAPSHOT_REFUSED = "SNAPSHOT_REFUSED"


def load_snapshot(path: Path) -> dict[str, Any]:
    """Load one advisory snapshot, fail-closed on every malformation."""
    try:
        if not path.exists() or not path.is_file():
            return no_advisory("missing")
        size = path.stat().st_size
        if size == 0:
            return no_advisory("empty")
        if size > ADVISORY_MAX_BYTES:
            return refused("size_exceeded")
        raw = path.read_bytes()
    except OSError:
        return refused("read_failed")

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return refused("parse_failed")
    if not isinstance(parsed, dict):
        return refused("not_object")
    marker = parsed.get("result_marker")
    if not isinstance(marker, str) or not marker.strip():
        return refused("missing_result_marker")
    # Fail-closed serializability guard: reject non-finite / unserializable
    # content before it can crash a strict JSON encoder downstream.
    try:
        json.dumps(parsed, allow_nan=False)
    except (ValueError, RecursionError):
        return refused("non_finite_number")
    return parsed


def no_advisory(reason: str) -> dict[str, Any]:
    return {"result_marker": NO_ADVISORY_YET, "reason": reason}


def refused(reason: str) -> dict[str, Any]:
    return {"result_marker": SNAPSHOT_REFUSED, "reason": reason}


__all__ = [
    "ADVISORY_MAX_BYTES",
    "NO_ADVISORY_YET",
    "SNAPSHOT_REFUSED",
    "load_snapshot",
    "no_advisory",
    "refused",
]
