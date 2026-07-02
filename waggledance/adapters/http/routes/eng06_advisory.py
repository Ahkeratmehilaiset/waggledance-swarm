# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Read-only ENG-06 fireplace-safety advisory snapshot route.

The route never reads a burn log and never runs the solver. It only returns a
JSON snapshot written by the ENG-06 refresher (or an operator), so
dashboard/SituationRoom clients can render the latest fireplace advisory card
without gaining a new execution surface. Mirrors ``routes/eng01_advisory.py``
and ``routes/air01_advisory.py``.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from waggledance.adapters.http.routes._advisory_snapshot import (
    ADVISORY_MAX_BYTES,
    NO_ADVISORY_YET,
    SNAPSHOT_REFUSED,
    load_snapshot as _load_snapshot,
)


DEFAULT_SNAPSHOT_PATH = Path("data/eng06/latest_advisory.json")

router = APIRouter(prefix="/api/eng06", tags=["eng06-advisory"])


def get_snapshot_path() -> Path:
    """Return the advisory snapshot path the refresher writes."""
    return DEFAULT_SNAPSHOT_PATH


@router.get("/advisory/latest")
def get_latest_advisory(
    snapshot_path: Path = Depends(get_snapshot_path),
) -> JSONResponse:
    """Return the latest ENG-06 advisory snapshot."""
    payload = _load_snapshot(snapshot_path)
    return JSONResponse(payload)



__all__ = [
    "ADVISORY_MAX_BYTES",
    "DEFAULT_SNAPSHOT_PATH",
    "NO_ADVISORY_YET",
    "SNAPSHOT_REFUSED",
    "get_latest_advisory",
    "get_snapshot_path",
    "router",
]
