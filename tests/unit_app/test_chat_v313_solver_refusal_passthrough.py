# SPDX-License-Identifier: BUSL-1.1
"""Per-solver refusal passthrough coverage for v3.13 chat dispatch.

Locks the deterministic-first boundary end-to-end for every registered
solver: a solver-level refusal must surface AS a refusal through
``run_v313_solver_chat_request`` — with the solver's own ``result_marker``
verbatim where the solver returns a refusal payload, or the dispatcher's
typed ``solver_refused:<ErrorType>`` mapping where the solver raises — and
always with a MAGMA receipt and never a silent fall-through (``None`` would
send the query to the LLM as if the solver did not exist).

Complements tests/unit_app/test_chat_v313_solver_dispatch.py, which covers
the dispatcher-level refusals (malformed/unknown/oversized/exception classes)
generically; this file pins the behavior per registered solver with
domain-shaped payloads (sprint plan item: per-solver refusal passthrough).
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.v3_13_0.chat_dispatch import (
    REFUSAL_MARKER,
    run_v313_solver_chat_request,
)


def _dispatch(solver: str, payload: dict) -> dict:
    query = f"solver {solver} payload {json.dumps(payload)}"
    out = run_v313_solver_chat_request(query)
    assert out is not None, (
        f"{solver}: recognized command fell through to None (silent LLM path)"
    )
    return json.loads(out)


# Solvers whose entrypoints RETURN a refusal payload for these domain-shaped
# inputs: the solver's own marker must propagate verbatim.
MARKER_CASES = [
    pytest.param(
        "ENG-06",
        {
            "burn_log": [{
                "day_utc": "2026-01-01T00:00:00Z",
                "fire_event_count": 0,
                "peak_chimney_temp_c": 20.0,
                "average_chimney_temp_c": 18.0,
            }],
            "horizon_start_utc": "2026-01-01T00:00:00Z",
            "horizon_end_utc": "2026-01-01T00:00:00Z",
        },
        "NO_FIRES_IN_HORIZON_REFUSED",
        id="eng06-no-fires",
    ),
    pytest.param(
        "AIR-01",
        {"bogus": True},
        "INVALID_OBSERVATION_REFUSED",
        id="air01-invalid-observation",
    ),
    pytest.param(
        "ENG-01",
        {
            "rows": [{"hour_utc": "2026-01-16T00:00:00Z", "price": 1.0}],
            "fetched_at_utc": "2026-01-10T00:00:00Z",
            "horizon_start_utc": "2026-01-16T00:00:00Z",
            "horizon_hours": 1,
        },
        "STALE_DATA_REFUSED",
        id="eng01-stale-feed",
    ),
]


@pytest.mark.parametrize(("solver", "payload", "expected_marker"), MARKER_CASES)
def test_solver_refusal_marker_propagates_verbatim(
    solver: str, payload: dict, expected_marker: str
) -> None:
    response = _dispatch(solver, payload)

    assert response["result_marker"] == expected_marker
    # Verbatim: the top-level marker is the solver payload's own marker.
    assert response["result"]["result_marker"] == expected_marker
    assert response["source"] == "v3_13_0_solver_registry"
    assert "magma_receipt" in response


# Solvers whose entrypoints RAISE a typed domain error on refusable input:
# the dispatcher must map it to its fail-closed refusal (never escape,
# never silently fall through), recording the error type.
EXCEPTION_CASES = [
    pytest.param("PDF-01", {"documents": [{"source_name": "x.pdf", "text": "gibberish"}]},
                 "Pdf01InvoiceFieldExtractorError", id="pdf01"),
    pytest.param("ACCT-01", {"bills": [], "transactions": []},
                 "Acct01UnpaidBillReconcilerError", id="acct01"),
    pytest.param("EMAIL-01", {"messages": []},
                 "Email01InboxPriorityClassifierError", id="email01"),
    pytest.param("EMAIL-02", {"messages": []},
                 "Email02VendorEmailIndexerError", id="email02"),
    pytest.param("FIN-10", {"receipts": []},
                 "Fin10ReceiptClassifierError", id="fin10"),
]


@pytest.mark.parametrize(("solver", "payload", "error_type"), EXCEPTION_CASES)
def test_solver_typed_error_maps_to_failclosed_refusal(
    solver: str, payload: dict, error_type: str
) -> None:
    response = _dispatch(solver, payload)

    assert response["result_marker"] == REFUSAL_MARKER
    assert response["refusal_reason"] == f"solver_refused:{error_type}"
    assert response["source"] == "v3_13_0_solver_registry"
    assert "magma_receipt" in response


def test_every_registered_solver_is_covered() -> None:
    # Completeness guard: if a 9th solver is registered, this file must grow.
    from waggledance.core.v3_13_0.solver_registry import load_solver_registry

    registered = {s.name for s in load_solver_registry()}
    covered = {c.values[0] for c in MARKER_CASES + EXCEPTION_CASES}
    assert registered == covered, (
        f"uncovered solvers: {registered - covered}; stale cases: {covered - registered}"
    )
