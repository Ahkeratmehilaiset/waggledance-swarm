"""Chat dispatch coverage for registered v3.13.0 deterministic solvers."""

from __future__ import annotations

import asyncio
import json

import pytest

from waggledance.application.dto.chat_dto import ChatRequest
from waggledance.application.services.chat_service import ChatService
from waggledance.core.orchestration.routing_policy import select_route


def _payloads() -> dict[str, dict[str, object]]:
    return {
        "ENG-01": {
            "fetched_at_utc": "2026-01-15T20:00:00Z",
            "horizon_start_utc": "2026-01-16T00:00:00Z",
            "horizon_hours": 3,
            "prices_eur_per_kwh": [
                {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.15},
                {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.09},
                {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.12},
            ],
            "stale_threshold_hours": 12,
        },
        "ENG-06": {
            "horizon_start_utc": "2026-01-01T00:00:00Z",
            "horizon_end_utc": "2026-01-02T00:00:00Z",
            "burn_log": [
                {
                    "day_utc": "2026-01-01T00:00:00Z",
                    "fire_event_count": 1,
                    "peak_chimney_temp_c": 150.0,
                    "average_chimney_temp_c": 85.0,
                },
                {
                    "day_utc": "2026-01-02T00:00:00Z",
                    "fire_event_count": 0,
                    "peak_chimney_temp_c": 40.0,
                    "average_chimney_temp_c": 24.0,
                },
            ],
        },
        "AIR-01": {
            "schema_version": "air01.observation.v1",
            "observed_at_utc": "2026-01-16T10:00:00Z",
            "fetched_at_utc": "2026-01-16T10:05:00Z",
            "source_url": "https://sensors.example.invalid/air",
            "device_id": "air-sensor-1",
            "readings": [
                {"metric": "pm25_ug_m3", "value": 12.0, "unit": "ug/m3"},
                {"metric": "co_ppm", "value": 1.0, "unit": "ppm"},
            ],
        },
        "PDF-01": {
            "documents": [
                {
                    "document_id": "invoice-001",
                    "text": (
                        "Saaja: Example Telco Oy\n"
                        "Laskun numero 12604312657\n"
                        "Päiväys 15.01.2026\n"
                        "Eräpäivä 29.01.2026\n"
                        "Maksettava yhteensä EUR 65,00\n"
                        "Viitenumero 1234 5678 9012"
                    ),
                }
            ]
        },
        "ACCT-01": {
            "as_of_date": "2026-05-16",
            "invoices": [
                {
                    "invoice_id": "inv-001",
                    "vendor": "Example Telco",
                    "invoice_number": "T-1001",
                    "due_date": "2026-05-10",
                    "amount_eur": 65.0,
                    "reference_number": "1234 5678 9012",
                    "status": "open",
                }
            ],
            "bank_transactions": [
                {
                    "transaction_id": "tx-001",
                    "booked_date": "2026-05-09",
                    "amount_eur": -65.0,
                    "reference_number": "123456789012",
                }
            ],
        },
        "EMAIL-01": {
            "as_of_date": "2026-05-16",
            "watchlist": [
                {"watch_id": "core_project", "terms": ["project alpha"], "domains": []}
            ],
            "priority_keywords": ["urgent", "reply needed"],
            "noise_keywords": ["newsletter"],
            "messages": [
                {
                    "message_id": "mail-001",
                    "thread_id": "thread-001",
                    "date": "2026-05-16",
                    "from": "Lead <lead@core.example>",
                    "subject": "Urgent project alpha reply needed",
                    "snippet": "Please check the release blocker",
                }
            ],
        },
        "EMAIL-02": {
            "as_of_date": "2026-05-16",
            "vendors": [
                {
                    "vendor_id": "helen",
                    "display_name": "Helen",
                    "domains": ["helen.fi"],
                    "name_signals": ["helen"],
                    "billing_keywords": ["lasku", "invoice"],
                }
            ],
            "messages": [
                {
                    "message_id": "msg-001",
                    "thread_id": "thr-001",
                    "date": "2026-05-01",
                    "from": "Helen Billing <billing@helen.fi>",
                    "subject": "Helen lasku May",
                    "snippet": "Due date 2026-05-14",
                }
            ],
        },
        "FIN-10": {
            "cottage_signals": ["cottage road", "lake supply"],
            "home_signals": ["home street", "urban grocery"],
            "receipts": [
                {
                    "receipt_id": "r-001",
                    "vendor_name": "Lake Supply",
                    "address": "12 Cottage Road",
                    "description": "Repair materials",
                }
            ],
        },
    }


@pytest.mark.parametrize("solver_name,payload", _payloads().items())
def test_chat_dispatch_runs_registered_v313_solver(
    solver_name: str,
    payload: dict[str, object],
    mock_orchestrator,
    mock_memory_service,
    mock_hot_cache,
    mock_config,
) -> None:
    async def _run() -> None:
        svc = ChatService(
            orchestrator=mock_orchestrator,
            memory_service=mock_memory_service,
            hot_cache=mock_hot_cache,
            routing_policy_fn=select_route,
            config=mock_config,
        )

        query = (
            f"Run deterministic solver {solver_name} with payload "
            f"{json.dumps(payload, sort_keys=True)}"
        )
        result = await svc.handle(ChatRequest(query=query))
        response = json.loads(result.response)

        assert result.source == "solver"
        assert response["solver"] == solver_name
        assert response["source"] == "v3_13_0_solver_registry"
        assert response["write_intent"] == "none"
        assert response["result"]["result_marker"]
        assert result.route_stage_trace[2]["stage"] == "memory_context"
        assert result.route_stage_trace[3]["route_type"] == "solver"
        assert result.route_stage_trace[3]["solver_intent"] == "v3_13_0_solver"
        assert result.route_stage_trace[4] == {
            "stage": "deterministic_solver",
            "intent": "v3_13_0_solver",
            "answered": True,
        }
        mock_orchestrator.handle_task.assert_not_called()

    asyncio.run(_run())


def test_chat_dispatch_refuses_malformed_v313_payload(
    mock_orchestrator,
    mock_memory_service,
    mock_hot_cache,
    mock_config,
) -> None:
    async def _run() -> None:
        svc = ChatService(
            orchestrator=mock_orchestrator,
            memory_service=mock_memory_service,
            hot_cache=mock_hot_cache,
            routing_policy_fn=select_route,
            config=mock_config,
        )

        result = await svc.handle(ChatRequest(
            query="Run deterministic solver FIN-10 with payload {not-json}",
        ))
        response = json.loads(result.response)

        assert result.source == "solver"
        assert response["result_marker"] == "V3_13_SOLVER_INPUT_REFUSED"
        assert response["solver"] == "FIN-10"
        assert mock_orchestrator.handle_task.call_count == 0

    asyncio.run(_run())
