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

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.v3_13_0.chat_dispatch import (
    MAX_PAYLOAD_BYTES,
    REFUSAL_MARKER,
    refuse_v313_solver,
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
    body, too_large, content_length = await _read_bounded_body(request)
    if too_large:
        result = json.loads(refuse_v313_solver(
            case_id,
            "payload_too_large",
            payload_digest=_body_digest(
                body,
                truncated=True,
                content_length=content_length,
            ),
        ))
        return JSONResponse(result, status_code=_status_for(result))
    try:
        payload_text = body.decode("utf-8") if body else ""
    except UnicodeDecodeError:
        result = json.loads(refuse_v313_solver(
            case_id,
            "payload_json_invalid",
            payload_digest=_body_digest(body),
        ))
        return JSONResponse(result, status_code=_status_for(result))
    result_text = run_v313_solver(case_id, payload_text)
    result = json.loads(result_text)
    return JSONResponse(result, status_code=_status_for(result))


async def _read_bounded_body(request: Request) -> tuple[bytes, bool, int | None]:
    """Read at most one byte past the solver payload limit."""

    content_length = _content_length(request)
    if content_length is not None and content_length > MAX_PAYLOAD_BYTES:
        return b"", True, content_length

    chunks: list[bytes] = []
    total = 0
    for_chunk_limit = MAX_PAYLOAD_BYTES + 1
    async for chunk in request.stream():
        if not chunk:
            continue
        remaining = for_chunk_limit - total
        if remaining <= 0:
            return b"".join(chunks), True, content_length
        chunks.append(chunk[:remaining])
        total += min(len(chunk), remaining)
        if len(chunk) > remaining or total > MAX_PAYLOAD_BYTES:
            return b"".join(chunks), True, content_length
    return b"".join(chunks), False, content_length


def _content_length(request: Request) -> int | None:
    raw = request.headers.get("content-length")
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def _body_digest(
    body: bytes,
    *,
    truncated: bool = False,
    content_length: int | None = None,
) -> str:
    return sha256_digest({
        "payload_bytes_hex": body.hex(),
        "payload_truncated": truncated,
        "content_length": content_length,
        "max_payload_bytes": MAX_PAYLOAD_BYTES,
    })


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
