# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Programmatic solver-execute route: POST /api/solvers/{case_id}.

Runs a registered v3.13.0 deterministic solver directly on a JSON request body
- the REST complement to the chat-dispatch path (#1469). Both share ONE
fail-closed orchestration via ``chat_dispatch.run_v313_solver``; this route
adds no solver, network, or write logic of its own:

- request-time network is impossible: the dispatch core resolves the registry
  entry_function (a pure solver) and never touches a transport, so a URL in
  the body is inert;
- only ``write_intent == "none"`` solvers run; anything else is refused;
- every response is a fail-closed JSON body carrying ``result_marker`` and a
  MAGMA receipt; the endpoint NEVER 5xx's on solver/input error (the dispatch
  core is total). HTTP status is mapped from the deterministic outcome:
  a real run (including a solver's own domain refusal) is 200; an unknown
  ``case_id`` is 404; a malformed/oversized body is 400/413.

The route lives under ``/api/*`` so it carries the same bearer/session auth as
the other protected API routes.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from waggledance.core.v3_13_0.chat_dispatch import (
    REFUSAL_MARKER,
    run_v313_solver,
)


router = APIRouter(prefix="/api", tags=["solvers"])

# Refusal reasons that indicate a client-side input problem -> 4xx.
_BAD_REQUEST_REASONS = frozenset({
    "payload_json_invalid",
    "payload_must_be_object",
})


@router.post("/solvers/{case_id}")
async def run_solver(case_id: str, request: Request) -> JSONResponse:
    """Run the registered solver for ``case_id`` over the JSON request body."""
    body = await request.body()
    try:
        payload_text = body.decode("utf-8") if body else ""
    except UnicodeDecodeError:
        payload_text = ""
    result_text = run_v313_solver(case_id, payload_text)
    result = json.loads(result_text)
    return JSONResponse(result, status_code=_status_for(result))


def _status_for(result: dict[str, Any]) -> int:
    """Map a deterministic dispatch outcome to an HTTP status code."""
    if result.get("result_marker") != REFUSAL_MARKER:
        # A real solver run - including the solver's own domain refusal
        # (e.g. STALE_DATA_REFUSED) - is a successful, deterministic result.
        return 200
    reason = str(result.get("refusal_reason", ""))
    if reason == "unknown_solver":
        return 404
    if reason == "payload_too_large":
        return 413
    if reason in _BAD_REQUEST_REASONS:
        return 400
    # write_intent_not_allowed / solver_refused:* / dispatch_internal_error:
    # the request was well-formed and the solver was reached; the fail-closed
    # refusal is the deterministic answer, not a transport error.
    return 200


__all__ = ["router", "run_solver"]
