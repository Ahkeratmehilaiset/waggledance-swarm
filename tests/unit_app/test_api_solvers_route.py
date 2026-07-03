# SPDX-License-Identifier: BUSL-1.1
"""Tests for the programmatic solver-execute route POST /api/solvers/{case_id}."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest import mock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from waggledance.adapters.http.routes import solvers as solvers_route
from waggledance.adapters.http.routes.solvers import router
from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container as RuntimeContainer
from waggledance.core.v3_13_0.chat_dispatch import MAX_PAYLOAD_BYTES, REFUSAL_MARKER
from tools.verify_magma_receipt import verify_manifest


def _client(container: object | None = None) -> TestClient:
    app = FastAPI()
    if container is not None:
        app.state.container = container
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


def _post(
    case_id: str,
    payload,
    *,
    container: object | None = None,
) -> tuple[int, dict]:
    body = payload if isinstance(payload, (str, bytes)) else json.dumps(payload)
    resp = _client(container=container).post(f"/api/solvers/{case_id}", content=body)
    return resp.status_code, resp.json()


def _container_with_solver_receipts(
    receipt_root: Path,
    **runtime_receipts: object,
) -> RuntimeContainer:
    config = {
        "enabled": True,
        "out_dir": str(receipt_root),
    }
    config.update(runtime_receipts)
    return RuntimeContainer(
        settings=WaggleSettings(
            profile="TEST",
            _extras={"runtime_receipts": config},
        ),
        stub=True,
    )


def test_runs_registered_solver_and_returns_marker_and_receipt() -> None:
    # AIR-01 with a malformed observation is a deterministic solver refusal
    # (marker preserved), which is a successful run -> 200.
    status, body = _post("AIR-01", {"bogus": True})
    assert status == 200
    assert body["case_id"].startswith("AIR-01")
    assert body["result_marker"] == "INVALID_OBSERVATION_REFUSED"
    assert body["source"] == "v3_13_0_solver_registry"
    assert "magma_receipt" in body
    assert "magma_receipt_sink" not in body


def test_case_id_is_case_insensitive() -> None:
    status, body = _post("air-01", {"bogus": True})
    assert status == 200
    assert body["result_marker"] == "INVALID_OBSERVATION_REFUSED"


def test_unknown_solver_is_404() -> None:
    status, body = _post("NOPE-99", {})
    assert status == 404
    assert body["result_marker"] == "V3_13_SOLVER_INPUT_REFUSED"
    assert body["refusal_reason"] == "unknown_solver"


def test_malformed_json_body_is_400() -> None:
    status, body = _post("AIR-01", "{not-json")
    assert status == 400
    assert body["refusal_reason"] == "payload_json_invalid"
    assert "magma_receipt" in body


def test_invalid_utf8_body_is_400() -> None:
    status, body = _post("AIR-01", b'{"bogus": "\xff"}')
    assert status == 400
    assert body["result_marker"] == "V3_13_SOLVER_INPUT_REFUSED"
    assert body["refusal_reason"] == "payload_json_invalid"
    assert "magma_receipt" in body


def test_non_object_body_is_400() -> None:
    status, body = _post("AIR-01", "[1, 2, 3]")
    assert status == 400
    assert body["refusal_reason"] == "payload_must_be_object"


def test_oversized_body_is_413() -> None:
    big = json.dumps({"x": "a" * (MAX_PAYLOAD_BYTES + 10)})
    status, body = _post("AIR-01", big)
    assert status == 413
    assert body["refusal_reason"] == "payload_too_large"
    assert "magma_receipt" in body


def test_route_streams_without_request_body_buffer(monkeypatch) -> None:
    async def fail_body(_request):
        raise AssertionError("route must not buffer with request.body()")

    monkeypatch.setattr("starlette.requests.Request.body", fail_body)

    status, body = _post("AIR-01", {"bogus": True})

    assert status == 200
    assert body["result_marker"] == "INVALID_OBSERVATION_REFUSED"


def test_oversized_content_length_refuses_before_streaming(monkeypatch) -> None:
    async def fail_stream(_request):
        raise AssertionError("oversized Content-Length should refuse before stream")
        yield b""

    monkeypatch.setattr("starlette.requests.Request.stream", fail_stream)

    big = json.dumps({"x": "a" * (MAX_PAYLOAD_BYTES + 10)})
    status, body = _post("AIR-01", big)

    assert status == 413
    assert body["refusal_reason"] == "payload_too_large"
    assert "magma_receipt" in body


def test_solver_domain_refusal_is_200() -> None:
    # ENG-06 no-fires horizon: the solver ran and returned its own refusal.
    status, body = _post("ENG-06", {
        "burn_log": [{
            "day_utc": "2026-01-01T00:00:00Z", "fire_event_count": 0,
            "peak_chimney_temp_c": 20.0, "average_chimney_temp_c": 18.0,
        }],
        "horizon_start_utc": "2026-01-01T00:00:00Z",
        "horizon_end_utc": "2026-01-01T00:00:00Z",
    })
    assert status == 200
    assert body["result_marker"] == "NO_FIRES_IN_HORIZON_REFUSED"


def test_no_http_transport_reachable_from_body() -> None:
    # A URL in the body must not trigger any network fetch: the dispatch core
    # calls a pure solver entrypoint, never a transport.
    with mock.patch("httpx.Client") as client_cls, \
            mock.patch("httpx.AsyncClient") as aclient_cls:
        status, body = _post("AIR-01", {"url": "http://169.254.169.254/latest"})
        client_cls.assert_not_called()
        aclient_cls.assert_not_called()
    assert status == 200
    receipt = body["magma_receipt"]["summary"]
    assert receipt["network_access"] == "not_permitted"
    assert receipt["transport_modules_used"] == []


def test_enabled_receipt_sink_writes_verified_path_free_bundle(
    tmp_path: Path,
) -> None:
    receipt_root = tmp_path / "solver-receipts"
    container = _container_with_solver_receipts(receipt_root)

    status, body = _post(
        "AIR-01",
        {"bogus": "DO_NOT_LEAK"},
        container=container,
    )

    assert status == 200
    sink = body["magma_receipt_sink"]
    assert sink == {
        "ok": True,
        "receipt_count": 1,
        "verifier_report": {"ok": True, "receipt_count": 1, "errors": []},
        "sink": "configured_local_v313_solver_dispatch_receipts",
        "paths_returned": False,
        "payloads_returned": False,
        "default_runtime_receipt_emission_changed": False,
        "runtime_authority_changed": False,
    }
    assert "out_dir" not in sink
    assert "manifest" not in sink

    receipt_dirs = list((receipt_root / "v313_solver_dispatch").iterdir())
    assert len(receipt_dirs) == 1
    manifest_path = receipt_dirs[0] / "manifest.json"
    assert verify_manifest(manifest_path)["ok"] is True

    emitted_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(receipt_root.rglob("*.json"))
    )
    assert "payload_digest" in emitted_text
    assert "result_digest" in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text


def test_enabled_receipt_sink_covers_malformed_json_refusal(
    tmp_path: Path,
) -> None:
    receipt_root = tmp_path / "solver-receipts"
    container = _container_with_solver_receipts(receipt_root)

    status, body = _post("AIR-01", "{not-json DO_NOT_LEAK", container=container)

    assert status == 400
    assert body["refusal_reason"] == "payload_json_invalid"
    assert body["magma_receipt_sink"]["ok"] is True
    receipt_dirs = list((receipt_root / "v313_solver_dispatch").iterdir())
    assert len(receipt_dirs) == 1
    emitted_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(receipt_root.rglob("*.json"))
    )
    assert "DO_NOT_LEAK" not in emitted_text


@pytest.mark.asyncio
async def test_receipt_sink_runs_outside_event_loop_thread() -> None:
    event_loop_thread = threading.get_ident()

    def sink(_receipt: dict[str, object]) -> dict[str, object]:
        return {"thread_id": threading.get_ident()}

    result = await solvers_route._run_receipt_sink_in_executor(sink, {})

    assert result["thread_id"] != event_loop_thread


def test_enabled_receipt_sink_prunes_old_owned_bundles(
    tmp_path: Path,
) -> None:
    receipt_root = tmp_path / "solver-receipts"
    sink_root = receipt_root / "v313_solver_dispatch"
    manual_dir = sink_root / "manual-keep"
    manual_dir.mkdir(parents=True)
    container = _container_with_solver_receipts(
        receipt_root,
        v313_solver_max_bundles=2,
    )

    for index in range(3):
        status, body = _post("AIR-01", {"bogus": index}, container=container)
        assert status == 200
        assert body["magma_receipt_sink"]["ok"] is True

    owned_dirs = sorted(
        path
        for path in sink_root.iterdir()
        if path.is_dir() and path.name != manual_dir.name
    )
    assert len(owned_dirs) == 2
    assert manual_dir.is_dir()
    assert all((path / "manifest.json").is_file() for path in owned_dirs)


def test_receipt_sink_errors_reject_prefixed_raw_disclosure() -> None:
    class ForgedSinkContainer:
        @staticmethod
        def v313_solver_receipt_sink(_receipt: dict[str, object]) -> dict[str, object]:
            return {
                "ok": True,
                "receipt_count": 1,
                "verifier_report": {
                    "ok": True,
                    "receipt_count": 1,
                    "errors": [
                        "verifier_error:1234567890abcdef",
                        "receipt_sink_error:abcdef1234567890",
                        "verifier_error:C:\\secret\\manifest.json DO_NOT_LEAK",
                        "receipt_sink_error:private payload DO_NOT_LEAK",
                    ],
                },
            }

    status, body = _post(
        "AIR-01",
        {"bogus": "DO_NOT_LEAK"},
        container=ForgedSinkContainer(),
    )

    assert status == 200
    sink = body["magma_receipt_sink"]
    assert sink["paths_returned"] is False
    assert sink["payloads_returned"] is False
    errors = sink["verifier_report"]["errors"]
    assert errors[:2] == [
        "verifier_error:1234567890abcdef",
        "receipt_sink_error:abcdef1234567890",
    ]
    assert all(
        error.startswith(("verifier_error:", "receipt_sink_error:"))
        for error in errors
    )
    assert all(len(error.rsplit(":", 1)[1]) == 16 for error in errors)
    assert all(
        error.rsplit(":", 1)[1] == error.rsplit(":", 1)[1].lower()
        for error in errors
    )
    assert all(
        set(error.rsplit(":", 1)[1]) <= set("0123456789abcdef")
        for error in errors
    )
    serialized = json.dumps(sink)
    assert "C:\\secret\\manifest.json" not in serialized
    assert "private payload" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_receipt_sink_ok_is_rederived_from_verifier_report() -> None:
    class ForgedSinkContainer:
        @staticmethod
        def v313_solver_receipt_sink(_receipt: dict[str, object]) -> dict[str, object]:
            return {
                "ok": True,
                "receipt_count": 1,
                "verifier_report": {
                    "ok": False,
                    "receipt_count": 1,
                    "errors": [],
                },
            }

    status, body = _post(
        "AIR-01",
        {"bogus": True},
        container=ForgedSinkContainer(),
    )

    assert status == 200
    sink = body["magma_receipt_sink"]
    assert sink["ok"] is False
    assert sink["verifier_report"]["ok"] is False
    assert sink["receipt_count"] == 1


def test_every_registered_solver_is_reachable() -> None:
    from waggledance.core.v3_13_0.solver_registry import load_solver_registry

    for solver in load_solver_registry():
        status, body = _post(solver.case_id, {})
        # Empty body reaches the solver (which then validates/refuses); the
        # point is the ROUTE resolves every registered case_id (never 404).
        assert status != 404, f"{solver.case_id} not reachable"
        assert body["source"] == "v3_13_0_solver_registry"


def test_route_registered_in_api_factory_and_auth_gated() -> None:
    from waggledance.adapters.http.api import create_app

    class Settings:
        api_key = "test-key"

    class Container:
        _settings = Settings()

    app = create_app(Container())
    client = TestClient(app, raise_server_exceptions=False)

    # Under /api/* -> requires the bearer token.
    unauth = client.post("/api/solvers/AIR-01", content=json.dumps({"bogus": True}))
    assert unauth.status_code == 401

    ok = client.post(
        "/api/solvers/AIR-01",
        content=json.dumps({"bogus": True}),
        headers={"Authorization": "Bearer test-key"},
    )
    assert ok.status_code == 200
    assert ok.json()["result_marker"] == "INVALID_OBSERVATION_REFUSED"


# --- Exact request-body size-boundary behavior (locks _read_bounded_body) ---
# The oversized tests above use MAX+10 (clearly over). These pin the exact
# off-by-one: a body of EXACTLY MAX passes the size gate, MAX+1 is refused.


def _body_of_size(n: int) -> bytes:
    """Return a JSON object body of EXACTLY ``n`` bytes."""
    base = len(json.dumps({"x": ""}).encode("utf-8"))
    body = json.dumps({"x": "a" * (n - base)}).encode("utf-8")
    assert len(body) == n, f"padding math off: {len(body)} != {n}"
    return body


def test_body_at_exact_max_bytes_is_not_too_large() -> None:
    # A body of EXACTLY MAX_PAYLOAD_BYTES passes the size gate (never 413);
    # it reaches the solver, which refuses on the unrecognized shape -> 200.
    status, body = _post("AIR-01", _body_of_size(MAX_PAYLOAD_BYTES))
    assert status != 413
    assert body.get("refusal_reason") != "payload_too_large"


def test_body_one_over_max_bytes_is_413() -> None:
    # One byte past the limit is refused as payload_too_large, receipted.
    status, body = _post("AIR-01", _body_of_size(MAX_PAYLOAD_BYTES + 1))
    assert status == 413
    assert body["refusal_reason"] == "payload_too_large"
    assert "magma_receipt" in body


def test_stream_cap_refuses_oversized_when_content_length_underreports(
    monkeypatch,
) -> None:
    # Defense against a lying-LOW Content-Length: even when the pre-stream
    # Content-Length check is fooled into seeing a small value, the independent
    # byte-counting stream cap still refuses a MAX+1 body.
    monkeypatch.setattr(
        "waggledance.adapters.http.routes.solvers._content_length",
        lambda _request: 5,
    )
    status, body = _post("AIR-01", _body_of_size(MAX_PAYLOAD_BYTES + 1))
    assert status == 413
    assert body["refusal_reason"] == "payload_too_large"
    assert "magma_receipt" in body


# --- Per-solver refusal passthrough THROUGH THE ROUTE (all 8 registered) ---
# The chat-dispatch path is covered in test_chat_v313_solver_refusal_passthrough
# (#1473). This locks the same deterministic-first boundary at the HTTP execute
# surface POST /api/solvers/{case_id}: every solver's refusal surfaces AS a
# refusal (own marker verbatim, or solver_refused:<ErrorType>) with a MAGMA
# receipt -- never a silent success or a transport error.

_ROUTE_MARKER_CASES = [
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
        "AIR-01", {"bogus": True}, "INVALID_OBSERVATION_REFUSED",
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

_ROUTE_EXCEPTION_CASES = [
    pytest.param(
        "PDF-01",
        {"documents": [{"source_name": "x.pdf", "text": "gibberish"}]},
        "Pdf01InvoiceFieldExtractorError", id="pdf01",
    ),
    pytest.param(
        "ACCT-01", {"bills": [], "transactions": []},
        "Acct01UnpaidBillReconcilerError", id="acct01",
    ),
    pytest.param(
        "EMAIL-01", {"messages": []},
        "Email01InboxPriorityClassifierError", id="email01",
    ),
    pytest.param(
        "EMAIL-02", {"messages": []},
        "Email02VendorEmailIndexerError", id="email02",
    ),
    pytest.param(
        "FIN-10", {"receipts": []},
        "Fin10ReceiptClassifierError", id="fin10",
    ),
]


@pytest.mark.parametrize(
    ("solver", "payload", "expected_marker"), _ROUTE_MARKER_CASES
)
def test_route_solver_marker_refusal_passthrough(
    solver: str, payload: dict, expected_marker: str
) -> None:
    status, body = _post(solver, payload)
    assert status == 200
    assert body["result_marker"] == expected_marker
    assert body["source"] == "v3_13_0_solver_registry"
    assert "magma_receipt" in body


@pytest.mark.parametrize(
    ("solver", "payload", "error_type"), _ROUTE_EXCEPTION_CASES
)
def test_route_solver_typed_error_maps_to_failclosed_refusal(
    solver: str, payload: dict, error_type: str
) -> None:
    status, body = _post(solver, payload)
    assert status == 200
    assert body["result_marker"] == REFUSAL_MARKER
    assert body["refusal_reason"] == f"solver_refused:{error_type}"
    assert "magma_receipt" in body


def test_route_refusal_passthrough_covers_every_registered_solver() -> None:
    # Completeness guard mirroring the chat-dispatch one: a 9th registered
    # solver forces this file to grow so route coverage never silently lags.
    from waggledance.core.v3_13_0.solver_registry import load_solver_registry

    registered = {s.name for s in load_solver_registry()}
    covered = {
        c.values[0] for c in _ROUTE_MARKER_CASES + _ROUTE_EXCEPTION_CASES
    }
    assert registered == covered, (
        f"uncovered via route: {registered - covered}; "
        f"stale cases: {covered - registered}"
    )
