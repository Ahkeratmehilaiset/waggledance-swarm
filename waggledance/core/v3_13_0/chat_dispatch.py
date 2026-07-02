# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Explicit chat dispatch for registered v3.13.0 deterministic solvers.

This module intentionally accepts only an explicit operator-style command:

    solver ENG-01 payload {"...": "..."}

It does not infer payloads from natural language, open URLs, read local files,
write state, or invoke CLI helpers. The caller supplies already-fetched,
sanitized payload JSON; this dispatcher only selects a registry entry and calls
its pure deterministic entrypoint.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping

from waggledance.core.v3_13_0.solver_registry import (
    SolverManifest,
    SolverRegistryError,
    load_solver_registry,
    resolve_solver_entrypoint,
)


REFUSAL_MARKER = "V3_13_SOLVER_INPUT_REFUSED"
SOURCE = "v3_13_0_solver_registry"

_REQUEST_RE = re.compile(
    r"\bsolver\s+(?P<solver>[A-Za-z0-9_-]{2,32})\b"
    r".*?\bpayload\b\s*(?P<payload>\{.*\})\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class V313SolverChatRequest:
    """Parsed explicit v3.13.0 solver chat request."""

    solver_ref: str
    payload_text: str


def detect_v313_solver_chat_request(query: str) -> bool:
    """Return True iff the query asks for explicit v3.13.0 solver dispatch."""

    return parse_v313_solver_chat_request(query) is not None


def parse_v313_solver_chat_request(query: str) -> V313SolverChatRequest | None:
    """Parse the explicit solver/payload command form, or return None."""

    match = _REQUEST_RE.search(query)
    if match is None:
        return None
    return V313SolverChatRequest(
        solver_ref=match.group("solver").strip().upper(),
        payload_text=match.group("payload").strip(),
    )


def run_v313_solver_chat_request(query: str) -> str | None:
    """Run an explicit registered solver request and return JSON text.

    ``None`` means the query is not in the explicit v3.13.0 command form.
    All recognized but invalid requests return a deterministic refusal payload.
    """

    request = parse_v313_solver_chat_request(query)
    if request is None:
        return None

    try:
        payload = json.loads(request.payload_text)
    except json.JSONDecodeError:
        return _json_response(_refusal(request.solver_ref, "payload_json_invalid"))
    if not isinstance(payload, Mapping):
        return _json_response(_refusal(request.solver_ref, "payload_must_be_object"))

    try:
        solver = _resolve_solver(request.solver_ref)
    except SolverRegistryError:
        return _json_response(_refusal(request.solver_ref, "unknown_solver"))

    if solver.write_intent != "none":
        return _json_response(_refusal(solver.name, "write_intent_not_allowed"))

    try:
        result = _call_solver(solver, payload)
        result_payload = _to_payload(result)
    except Exception as exc:  # noqa: BLE001 - refusal keeps chat fail-closed.
        return _json_response(
            _refusal(solver.name, f"solver_refused:{type(exc).__name__}")
        )

    return _json_response({
        "source": SOURCE,
        "solver": solver.name,
        "case_id": solver.case_id,
        "write_intent": solver.write_intent,
        "risk_class": solver.risk_class,
        "result": result_payload,
    })


def _resolve_solver(solver_ref: str) -> SolverManifest:
    solvers = load_solver_registry()
    for solver in solvers:
        if solver.name.upper() == solver_ref or solver.case_id.upper() == solver_ref:
            return solver
    raise SolverRegistryError(f"unknown solver: {solver_ref}")


def _call_solver(solver: SolverManifest, payload: Mapping[str, Any]) -> Any:
    entrypoint = resolve_solver_entrypoint(solver)
    if solver.name == "ENG-06":
        burn_log = payload.get("burn_log")
        return entrypoint(
            burn_log,
            horizon_start_utc=str(payload.get("horizon_start_utc", "")),
            horizon_end_utc=str(payload.get("horizon_end_utc", "")),
            stale_threshold_hours=payload.get("stale_threshold_hours"),
        )
    return entrypoint(payload)


def _to_payload(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_payload") and callable(result.to_payload):
        payload = result.to_payload()
    elif isinstance(result, Mapping):
        payload = dict(result)
    else:
        payload = {"value": result}
    if not isinstance(payload, dict):
        return {"value": payload}
    return payload


def _refusal(solver_ref: str, reason: str) -> dict[str, Any]:
    return {
        "source": SOURCE,
        "solver": solver_ref,
        "write_intent": "none",
        "result_marker": REFUSAL_MARKER,
        "refusal_reason": reason,
    }


def _json_response(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "REFUSAL_MARKER",
    "V313SolverChatRequest",
    "detect_v313_solver_chat_request",
    "parse_v313_solver_chat_request",
    "run_v313_solver_chat_request",
]
