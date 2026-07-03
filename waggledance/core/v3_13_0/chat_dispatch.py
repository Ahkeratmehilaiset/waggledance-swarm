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
from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.evaluation_result import build_evaluation_result
from waggledance.core.magma.receipt import build_magma_receipt
from waggledance.core.v3_13_0.solver_registry import (
    SolverManifest,
    SolverRegistryError,
    load_solver_registry,
    resolve_solver_entrypoint,
)


REFUSAL_MARKER = "V3_13_SOLVER_INPUT_REFUSED"
SOURCE = "v3_13_0_solver_registry"
RECEIPT_PAYLOAD_VERSION = "magma.v313_solver_dispatch_payload.v0"
MAX_PAYLOAD_BYTES = 65_536

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
    return _run_recognized_v313_solver_chat_request(request)


def run_v313_solver(solver_ref: str, payload_text: str) -> str:
    """Run a registered v3.13 solver by ref over an already-extracted payload.

    The non-chat entry point (e.g. the ``POST /api/solvers/{case_id}`` route):
    the caller supplies the solver ref and the payload as a JSON text string,
    and gets back the SAME fail-closed JSON response the chat path produces -
    identical size / parse / resolve / write-intent / call / receipt /
    totality handling, so there is exactly one dispatch orchestration. It does
    not parse natural language, open URLs, read files, or write state.
    """
    request = V313SolverChatRequest(
        solver_ref=str(solver_ref).strip().upper(),
        payload_text=payload_text,
    )
    return _run_recognized_v313_solver_chat_request(request)


def refuse_v313_solver(
    solver_ref: str,
    reason: str,
    *,
    payload_digest: str,
) -> str:
    """Build the same receipted refusal envelope used by solver dispatch."""

    return _json_response(_refusal(
        str(solver_ref).strip().upper(),
        reason,
        payload_digest=payload_digest,
    ))


def _run_recognized_v313_solver_chat_request(
    request: V313SolverChatRequest,
) -> str:
    raw_payload_digest = sha256_digest({"payload_text": request.payload_text})
    payload_digest = raw_payload_digest
    solver: SolverManifest | None = None

    try:
        return _run_recognized_v313_solver_chat_request_inner(
            request,
            raw_payload_digest=raw_payload_digest,
            payload_digest=payload_digest,
            solver=solver,
        )
    except Exception:  # noqa: BLE001 - recognized requests must never fall through.
        return _json_response(_refusal(
            request.solver_ref,
            "dispatch_internal_error",
            payload_digest=payload_digest,
            solver=solver,
        ))


def _run_recognized_v313_solver_chat_request_inner(
    request: V313SolverChatRequest,
    *,
    raw_payload_digest: str,
    payload_digest: str,
    solver: SolverManifest | None,
) -> str:
    if len(request.payload_text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return _json_response(_refusal(
            request.solver_ref,
            "payload_too_large",
            payload_digest=raw_payload_digest,
        ))

    try:
        payload = json.loads(request.payload_text)
    except json.JSONDecodeError:
        return _json_response(_refusal(
            request.solver_ref,
            "payload_json_invalid",
            payload_digest=raw_payload_digest,
        ))
    if not isinstance(payload, Mapping):
        return _json_response(_refusal(
            request.solver_ref,
            "payload_must_be_object",
            payload_digest=raw_payload_digest,
        ))
    try:
        payload_digest = sha256_digest(payload)
    except (TypeError, ValueError):
        return _json_response(_refusal(
            request.solver_ref,
            "payload_json_invalid",
            payload_digest=raw_payload_digest,
        ))

    try:
        solver = _resolve_solver(request.solver_ref)
    except SolverRegistryError:
        return _json_response(_refusal(
            request.solver_ref,
            "unknown_solver",
            payload_digest=payload_digest,
        ))

    if solver.write_intent != "none":
        return _json_response(_refusal(
            solver.name,
            "write_intent_not_allowed",
            payload_digest=payload_digest,
            solver=solver,
        ))

    try:
        result = _call_solver(solver, payload)
        result_payload = _to_payload(result)
    except Exception as exc:  # noqa: BLE001 - refusal keeps chat fail-closed.
        return _json_response(_refusal(
            solver.name,
            f"solver_refused:{type(exc).__name__}",
            payload_digest=payload_digest,
            solver=solver,
        ))

    try:
        response = {
            "source": SOURCE,
            "solver": solver.name,
            "case_id": solver.case_id,
            "write_intent": solver.write_intent,
            "risk_class": solver.risk_class,
            "result_marker": str(result_payload.get("result_marker", "")),
            "result": result_payload,
        }
        response["magma_receipt"] = _build_dispatch_receipt_bundle(
            solver_ref=request.solver_ref,
            solver=solver,
            payload_digest=payload_digest,
            result_payload=result_payload,
            verdict="pass",
            reason_codes=["v313_solver_dispatch_pass"],
        )
        return _json_response(response)
    except Exception:  # noqa: BLE001 - success-path residue must fail closed.
        return _json_response(_refusal(
            solver.name,
            "dispatch_internal_error",
            payload_digest=payload_digest,
            solver=solver,
        ))


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


def _refusal(
    solver_ref: str,
    reason: str,
    *,
    payload_digest: str,
    solver: SolverManifest | None = None,
) -> dict[str, Any]:
    response = {
        "source": SOURCE,
        "solver": solver.name if solver is not None else solver_ref,
        "write_intent": "none",
        "result_marker": REFUSAL_MARKER,
        "refusal_reason": reason,
    }
    if solver is not None:
        response["case_id"] = solver.case_id
        response["risk_class"] = solver.risk_class
    response["magma_receipt"] = _build_dispatch_receipt_bundle(
        solver_ref=solver_ref,
        solver=solver,
        payload_digest=payload_digest,
        result_payload=response,
        verdict="refuse",
        reason_codes=[_reason_code(reason)],
    )
    return response


def _build_dispatch_receipt_bundle(
    *,
    solver_ref: str,
    solver: SolverManifest | None,
    payload_digest: str,
    result_payload: Mapping[str, Any],
    verdict: str,
    reason_codes: list[str],
) -> dict[str, Any]:
    solver_name = solver.name if solver is not None else solver_ref
    case_id = (
        solver.case_id
        if solver is not None
        else f"case:v313_solver_dispatch:{_safe_ref(solver_ref)}"
    )
    risk_class = solver.risk_class if solver is not None else "informational"
    result_marker = str(result_payload.get("result_marker", ""))
    summary = {
        "payload_version": RECEIPT_PAYLOAD_VERSION,
        "source": SOURCE,
        "solver_ref": solver_ref,
        "solver": solver_name,
        "case_id": case_id,
        "risk_class": risk_class,
        "write_intent": solver.write_intent if solver is not None else "none",
        "entry_module": solver.module if solver is not None else None,
        "entry_function": solver.entry_function if solver is not None else None,
        "transport_modules_used": [],
        "network_access": "not_permitted",
        "payload_digest": payload_digest,
        "result_digest": sha256_digest(result_payload),
        "result_marker": result_marker,
        "verdict": verdict,
    }
    policy_version = "policy:v313_chat_dispatch:v0"
    charter_version = "charter:runtime_truth:v1"
    threshold_version = "threshold:v313_chat_dispatch:v0"
    actual_gate = "allow" if verdict == "pass" else "refuse"
    evaluation = build_evaluation_result(
        case_id=case_id,
        subject_type="solver",
        target_payload=summary,
        risk_class=risk_class,
        expected_gate="allow",
        actual_gate=actual_gate,
        verifier_path=[
            "v313_chat_dispatch",
            "registry_manifest",
            "write_intent_none",
            "transport_modules_not_used",
        ],
        solver_selection=[case_id],
        policy_version=policy_version,
        charter_version=charter_version,
        domain_threshold_version=threshold_version,
        verdict=verdict,
        reason_codes=reason_codes,
        confidence_score=1.0 if verdict == "pass" else 0.95,
    )
    receipt = build_magma_receipt(
        event_id=(
            f"magma:v313_solver_dispatch:{_safe_ref(solver_name)}:"
            f"{payload_digest.removeprefix('sha256:')[:12]}"
        ),
        ts_utc=_utc_now(),
        risk_class=risk_class,
        payload=summary,
        evaluation_result=evaluation,
        policy_digest=sha256_digest({"policy_version": policy_version}),
        charter_digest=sha256_digest({"charter_version": charter_version}),
        rco_decision_digest=sha256_digest({
            "actual_gate": actual_gate,
            "reason_codes": reason_codes,
            "transport_modules_used": [],
            "write_intent": summary["write_intent"],
        }),
        world_snapshot_digest=sha256_digest({
            "source": "operator_supplied_chat_payload",
            "network_access": "not_permitted",
        }),
        solver_contract_digest=sha256_digest({
            "case_id": case_id,
            "entry_module": summary["entry_module"],
            "entry_function": summary["entry_function"],
            "write_intent": summary["write_intent"],
            "transport_modules_used": [],
        }),
    )
    return {
        "summary": summary,
        "evaluation_result": evaluation,
        "receipt": receipt,
    }


def _reason_code(reason: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9:_-]+", "_", reason).strip("_")
    return f"v313_solver_dispatch_{sanitized or 'refused'}"


def _safe_ref(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9:_-]+", "_", value).strip("_")
    if len(sanitized) < 3:
        sanitized = f"ref_{sanitized}"
    if not sanitized[0].isalpha():
        sanitized = f"ref_{sanitized}"
    return sanitized[:80]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_response(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, allow_nan=False, sort_keys=True, separators=(",", ":"))


__all__ = [
    "REFUSAL_MARKER",
    "MAX_PAYLOAD_BYTES",
    "RECEIPT_PAYLOAD_VERSION",
    "V313SolverChatRequest",
    "detect_v313_solver_chat_request",
    "parse_v313_solver_chat_request",
    "refuse_v313_solver",
    "run_v313_solver",
    "run_v313_solver_chat_request",
]
