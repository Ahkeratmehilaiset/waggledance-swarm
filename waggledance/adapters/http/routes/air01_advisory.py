# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Read-only AIR-01 indoor air-quality advisory snapshot route.

The route never fetches a URL and never runs the solver. It only returns a
JSON snapshot written by the AIR-01 refresher (or an operator), so
dashboard/SituationRoom clients can render the latest advisory without gaining
a new network execution surface. Mirrors ``routes/eng01_advisory.py``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse


DEFAULT_SNAPSHOT_PATH = Path("data/air01/latest_advisory.json")
ADVISORY_MAX_BYTES = 1_000_000

NO_ADVISORY_YET = "NO_ADVISORY_YET"
SNAPSHOT_REFUSED = "SNAPSHOT_REFUSED"

router = APIRouter(prefix="/api/air01", tags=["air01-advisory"])


def get_snapshot_path() -> Path:
    """Return the advisory snapshot path the refresher writes."""
    return DEFAULT_SNAPSHOT_PATH


@router.get("/advisory/latest")
def get_latest_advisory(
    snapshot_path: Path = Depends(get_snapshot_path),
) -> JSONResponse:
    """Return the latest AIR-01 advisory snapshot."""
    payload = _load_snapshot(snapshot_path)
    return JSONResponse(payload)


def _load_snapshot(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return _no_advisory("missing")
        size = path.stat().st_size
        if size == 0:
            return _no_advisory("empty")
        if size > ADVISORY_MAX_BYTES:
            return _refused("size_exceeded")
        raw = path.read_bytes()
    except OSError:
        return _refused("read_failed")

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _refused("parse_failed")
    if not isinstance(parsed, dict):
        return _refused("not_object")
    marker = parsed.get("result_marker")
    if not isinstance(marker, str) or not marker.strip():
        return _refused("missing_result_marker")
    return parsed


def _no_advisory(reason: str) -> dict[str, Any]:
    return {
        "result_marker": NO_ADVISORY_YET,
        "reason": reason,
    }


def _refused(reason: str) -> dict[str, Any]:
    return {
        "result_marker": SNAPSHOT_REFUSED,
        "reason": reason,
    }


__all__ = [
    "ADVISORY_MAX_BYTES",
    "DEFAULT_SNAPSHOT_PATH",
    "NO_ADVISORY_YET",
    "SNAPSHOT_REFUSED",
    "get_latest_advisory",
    "get_snapshot_path",
    "router",
]
