# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Read-only ENG-01 advisory snapshot route.

The route never fetches a URL and never runs the solver. It only returns an
operator-written JSON snapshot so dashboard/SituationRoom clients can render
the latest advisory without gaining a new network execution surface.
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


DEFAULT_SNAPSHOT_PATH = Path("data/eng01/latest_advisory.json")

router = APIRouter(prefix="/api/eng01", tags=["eng01-advisory"])


def get_snapshot_path() -> Path:
    """Return the operator-written advisory snapshot path."""
    return DEFAULT_SNAPSHOT_PATH


@router.get("/advisory/latest")
def get_latest_advisory(
    snapshot_path: Path = Depends(get_snapshot_path),
) -> JSONResponse:
    """Return the latest operator-written ENG-01 advisory snapshot."""
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
