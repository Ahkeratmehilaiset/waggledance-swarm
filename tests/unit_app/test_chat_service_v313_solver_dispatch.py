# SPDX-License-Identifier: BUSL-1.1
"""Tests for v3.13 registry-backed solver dispatch from chat."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from waggledance.application.dto.chat_dto import ChatRequest
from waggledance.application.services.chat_service import ChatService
from waggledance.core.orchestration.routing_policy import (
    extract_features,
    select_route,
)
from waggledance.core.v3_13_0.air01_digheran_adapter import (
    CO_PPM,
    OBSERVATION_SCHEMA_VERSION,
    PM25_UG_M3,
)
from waggledance.core.v3_13_0.eng01_price_feed_adapter import (
    build_eng01_price_feed,
)


ROOT = Path(__file__).resolve().parents[2]


def _read_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _eng01_payload() -> dict:
    sample = _read_json("examples/eng01/offline_prices_sample.json")
    return build_eng01_price_feed(
        sample["rows"],
        fetched_at_utc=sample["fetched_at_utc"],
        horizon_start_utc=sample["horizon_start_utc"],
        horizon_hours=sample["horizon_hours"],
        feed_source=sample["feed_source"],
        price_unit=sample["price_unit"],
        stale_threshold_hours=sample["stale_threshold_hours"],
    )


def _air01_payload() -> dict:
    return {
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "source_url": "http://192.168.1.44/api/air/current",
        "device_id": "digheran-cottage-1",
        "observed_at_utc": "2026-05-15T18:00:00Z",
        "fetched_at_utc": "2026-05-15T18:02:00Z",
        "readings": [
            {"metric": PM25_UG_M3, "value": 12.0, "unit": "ug/m3"},
            {"metric": CO_PPM, "value": 0.5, "unit": "ppm"},
        ],
    }


def _solver_request(case_id: str, payload: dict) -> str:
    return json.dumps({"case_id": case_id, "payload": payload}, sort_keys=True)


@pytest.mark.parametrize(
    ("case_id", "payload"),
    [
        (
            "ENG-01__spot_electricity_monitor__home",
            _eng01_payload(),
        ),
        (
            "ENG-06__cottage_fireplace_advisor__cottage",
            _read_json("examples/eng06/burn_log_sample.json"),
        ),
        (
            "AIR-01__indoor_air_quality_advisor__cottage",
            _air01_payload(),
        ),
        (
            "PDF-01__invoice_field_extractor__home",
            _read_json("examples/pdf01/invoice_text_sample.json"),
        ),
        (
            "ACCT-01__unpaid_bill_reconciler__home",
            _read_json("examples/acct01/unpaid_bill_reconciliation_sample.json"),
        ),
        (
            "EMAIL-01__inbox_priority_classifier__home",
            _read_json("examples/email01/inbox_priority_sample.json"),
        ),
        (
            "EMAIL-02__vendor_email_indexer__home",
            _read_json("examples/email02/vendor_email_index_sample.json"),
        ),
        (
            "FIN-10__cottage_bookkeeping_separator__cottage",
            _read_json("examples/fin10/receipts_sample.json"),
        ),
    ],
)
def test_try_solver_dispatches_all_v313_registry_entries(
    case_id: str,
    payload: dict,
) -> None:
    raw = ChatService._try_solver(_solver_request(case_id, payload), "v3_13_solver")

    assert raw is not None
    response = json.loads(raw)
    assert response["case_id"] == case_id
    assert response["solver_name"]
    assert response["result"]["case_id"] == case_id
    assert response["result"]["result_marker"] != "SOLVER_REQUEST_REFUSED"
    assert response["result"]["write_intent"] == "none"


def test_explicit_v313_solver_json_classifies_as_v313_solver() -> None:
    query = _solver_request(
        "FIN-10__cottage_bookkeeping_separator__cottage",
        _read_json("examples/fin10/receipts_sample.json"),
    )

    features = extract_features(
        query,
        hot_cache_hit=False,
        memory_score=0.0,
        matched_keywords=[],
        profile="HOME",
    )

    assert features.solver_intent == "v3_13_solver"


def test_chat_handle_runs_v313_solver_without_llm(
    mock_orchestrator,
    mock_memory_service,
    mock_hot_cache,
    mock_config,
) -> None:
    svc = ChatService(
        orchestrator=mock_orchestrator,
        memory_service=mock_memory_service,
        hot_cache=mock_hot_cache,
        routing_policy_fn=select_route,
        config=mock_config,
    )
    query = _solver_request(
        "FIN-10__cottage_bookkeeping_separator__cottage",
        _read_json("examples/fin10/receipts_sample.json"),
    )

    async def _run() -> None:
        result = await svc.handle(ChatRequest(query=query, profile="HOME"))
        response = json.loads(result.response)

        assert result.source == "solver"
        assert result.confidence == 0.95
        assert response["case_id"] == "FIN-10__cottage_bookkeeping_separator__cottage"
        assert response["result"]["summary"]["total_receipts"] == 10
        assert result.route_stage_trace[3]["route_type"] == "solver"
        assert result.route_stage_trace[4]["intent"] == "v3_13_solver"
        assert result.route_stage_trace[4]["answered"] is True
        mock_orchestrator.handle_task.assert_not_called()

    asyncio.run(_run())


def test_unknown_v313_case_id_fails_closed() -> None:
    raw = ChatService._try_solver(
        _solver_request("BAD-01__missing_solver__home", {}),
        "v3_13_solver",
    )

    assert raw is not None
    response = json.loads(raw)
    assert response["result"]["result_marker"] == "SOLVER_REQUEST_REFUSED"
    assert response["result"]["refusal_reason"] == "SolverRegistryError"
