# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""HTTP execution surface for deterministic v3.13.0 first-slice solvers."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from waggledance.core.v3_13_0.solver_registry import (
    SolverManifest,
    SolverRegistryError,
    get_solver_manifest,
    iter_solver_manifests,
    resolve_solver_entrypoint,
)


SOLVER_EXECUTION_REFUSED = "SOLVER_EXECUTION_REFUSED"
SOLVER_REGISTRY_REFUSED = "SOLVER_REGISTRY_REFUSED"

router = APIRouter(prefix="/api/solvers", tags=["solvers"])


@router.get("")
def list_solvers() -> JSONResponse:
    """Return the deterministic solver catalog exposed by the registry."""
    try:
        solvers = iter_solver_manifests()
    except SolverRegistryError as exc:
        raise HTTPException(
            status_code=500,
            detail=_registry_refusal(str(exc)),
        ) from exc
    return JSONResponse({
        "schema_version": 1,
        "solver_count": len(solvers),
        "solvers": [solver.to_mapping() for solver in solvers],
    })


@router.get("/{case_id}")
def get_solver(case_id: str) -> JSONResponse:
    """Return one deterministic solver manifest."""
    solver = _load_solver_or_404(case_id)
    return JSONResponse(solver.to_mapping())


@router.post("/{case_id}")
def execute_solver(
    case_id: str,
    request_body: Any = Body(...),
) -> JSONResponse:
    """Execute a registry-declared deterministic solver with caller JSON.

    Accepted body forms:
    - ``{"input": <payload>, "parameters": {...}}``
    - ``{"payload": <payload>, "parameters": {...}}``
    - a direct object payload when no keyword parameters are needed
    """
    solver = _load_solver_or_404(case_id)
    payload, parameters = _extract_call_parts(request_body)
    entrypoint = _resolve_entrypoint(solver)
    try:
        result = entrypoint(payload, **parameters)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "result_marker": SOLVER_EXECUTION_REFUSED,
                "case_id": solver.case_id,
                "reason": str(exc),
            },
        ) from exc
    result_payload = _result_to_payload(result)
    return JSONResponse({
        "case_id": solver.case_id,
        "name": solver.name,
        "human_name": solver.human_name,
        "risk_class": solver.risk_class,
        "write_intent": solver.write_intent,
        "result_marker": (
            result_payload.get("result_marker")
            if isinstance(result_payload, dict)
            else None
        ),
        "result": result_payload,
    })


def _load_solver_or_404(case_id: str) -> SolverManifest:
    try:
        return get_solver_manifest(case_id)
    except SolverRegistryError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "result_marker": SOLVER_REGISTRY_REFUSED,
                "reason": str(exc),
            },
        ) from exc


def _resolve_entrypoint(solver: SolverManifest):
    try:
        return resolve_solver_entrypoint(solver)
    except SolverRegistryError as exc:
        raise HTTPException(
            status_code=500,
            detail=_registry_refusal(str(exc), solver.case_id),
        ) from exc


def _extract_call_parts(request_body: Any) -> tuple[Any, dict[str, Any]]:
    if isinstance(request_body, Mapping):
        has_input = "input" in request_body
        has_payload = "payload" in request_body
        if has_input and has_payload:
            raise HTTPException(
                status_code=422,
                detail=_execution_refusal("ambiguous_input_payload"),
            )
        if has_input or has_payload:
            payload = request_body["input"] if has_input else request_body["payload"]
            raw_parameters = request_body.get("parameters", {})
        else:
            payload = request_body
            raw_parameters = {}
    else:
        payload = request_body
        raw_parameters = {}

    if not isinstance(raw_parameters, Mapping):
        raise HTTPException(
            status_code=422,
            detail=_execution_refusal("parameters_must_be_object"),
        )
    parameters: dict[str, Any] = {}
    for key, value in raw_parameters.items():
        if not isinstance(key, str):
            raise HTTPException(
                status_code=422,
                detail=_execution_refusal("parameter_keys_must_be_strings"),
            )
        parameters[key] = value
    return payload, parameters


def _result_to_payload(result: Any) -> Any:
    to_payload = getattr(result, "to_payload", None)
    if callable(to_payload):
        return jsonable_encoder(to_payload())
    return jsonable_encoder(result)


def _execution_refusal(reason: str) -> dict[str, str]:
    return {
        "result_marker": SOLVER_EXECUTION_REFUSED,
        "reason": reason,
    }


def _registry_refusal(
    reason: str,
    case_id: str | None = None,
) -> dict[str, str]:
    payload = {
        "result_marker": SOLVER_REGISTRY_REFUSED,
        "reason": reason,
    }
    if case_id is not None:
        payload["case_id"] = case_id
    return payload


__all__ = [
    "SOLVER_EXECUTION_REFUSED",
    "SOLVER_REGISTRY_REFUSED",
    "execute_solver",
    "get_solver",
    "list_solvers",
    "router",
]
