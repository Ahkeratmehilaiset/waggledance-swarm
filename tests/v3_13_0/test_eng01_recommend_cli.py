# SPDX-License-Identifier: BUSL-1.1
"""Tests for the offline ENG-01 recommendation CLI."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Mapping

from pytest import MonkeyPatch

from waggledance.adapters.cli.eng01_recommend import main, run_from_payload
from waggledance.core.v3_13_0.eng01_price_feed_http_transport import (
    Eng01PriceFeedHttpResponse,
)


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "eng01" / "offline_prices_sample.json"
URL = "https://prices.example.test/day-ahead.json"


def _payload() -> dict:
    return {
        "fetched_at_utc": "2026-01-15T20:00:00Z",
        "horizon_start_utc": "2026-01-16T00:00:00Z",
        "horizon_hours": 3,
        "feed_source": "operator_local_price_file",
        "price_unit": "EUR_per_MWh",
        "rows": [
            {"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0},
            {"hour_utc": "2026-01-16T01:00:00Z", "price": 75.0},
            {"hour_utc": "2026-01-16T02:00:00Z", "price": 125.0},
        ],
    }


def test_run_from_payload_returns_operator_recommendation() -> None:
    result = run_from_payload(_payload())

    assert result["result_marker"] == "OK"
    assert result["feed_source"] == "operator_local_price_file"
    assert result["top_3_cheapest_hours_utc"] == [
        {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.075,
         "rank": 1},
        {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.1,
         "rank": 2},
        {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.125,
         "rank": 3},
    ]


def test_main_prints_compact_json(tmp_path: Path) -> None:
    input_path = tmp_path / "prices.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == "OK"
    assert output["top_3_cheapest_hours_utc"][0]["hour_utc"] == \
        "2026-01-16T01:00:00Z"


def test_main_can_override_price_unit(tmp_path: Path) -> None:
    payload = _payload()
    payload["price_unit"] = "EUR_per_kWh"
    payload["rows"][0]["price"] = 0.100
    payload["rows"][1]["price"] = 0.075
    payload["rows"][2]["price"] = 0.125
    input_path = tmp_path / "prices.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(
        ["--input", str(input_path), "--price-unit", "EUR_per_kWh"],
        stdout=stdout,
    )
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert output["top_3_cheapest_hours_utc"][0]["price_eur_per_kwh"] == 0.075


def test_main_refuses_invalid_input_with_json_error(tmp_path: Path) -> None:
    input_path = tmp_path / "prices.json"
    input_path.write_text(json.dumps({"rows": []}), encoding="utf-8")
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "fetched_at_utc" in output["error"]


def test_main_preserves_bool_horizon_for_fail_closed_adapter_check(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["horizon_hours"] = True
    input_path = tmp_path / "prices.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "horizon_hours must be a positive integer" in output["error"]


def test_example_input_file_runs_through_cli() -> None:
    stdout = io.StringIO()

    exit_code = main(["--input", str(EXAMPLE_INPUT)], stdout=stdout)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert output["result_marker"] == "OK"
    assert output["feed_source"] == \
        "operator_selected_spot_price_public_feed_sample"
    assert output["top_3_cheapest_hours_utc"] == [
        {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.031,
         "rank": 1},
        {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.038,
         "rank": 2},
        {"hour_utc": "2026-01-16T03:00:00Z", "price_eur_per_kwh": 0.042,
         "rank": 3},
    ]


def test_main_can_render_operator_advisory_card() -> None:
    stdout = io.StringIO()

    exit_code = main(
        ["--input", str(EXAMPLE_INPUT), "--render-card"],
        stdout=stdout,
    )
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert output["schema_version"] == "eng01_advisory_card.v1"
    assert output["case_id"] == "ENG-01__spot_electricity_monitor__home"
    assert output["risk_class"] == "informational"
    assert output["write_intent"] == "none"
    assert output["status"] == "ok"
    assert output["top_hours"][0]["hour_utc"] == "2026-01-16T02:00:00Z"


def test_main_writes_output_snapshot_under_data_eng01(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output_path = Path("data/eng01/latest_advisory.json")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "--input", str(EXAMPLE_INPUT),
            "--render-card",
            "--pretty",
            "--output", str(output_path),
        ],
        stdout=stdout,
        stderr=stderr,
    )
    printed = json.loads(stdout.getvalue())
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert written == printed
    assert written["schema_version"] == "eng01_advisory_card.v1"
    assert written["result_marker"] == "OK"
    assert list(output_path.parent.glob(".latest_advisory.json.*.tmp")) == []


def test_main_url_mode_writes_output_snapshot(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output_path = Path("data/eng01/latest_advisory.json")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "--url", URL,
            "--fetched-at-utc", "2026-01-15T20:00:00Z",
            "--horizon-start-utc", "2026-01-16T00:00:00Z",
            "--horizon-hours", "3",
            "--output", str(output_path),
        ],
        stdout=stdout,
        stderr=stderr,
        transport=lambda url, *_: _http_response(url),
    )
    printed = json.loads(stdout.getvalue())
    written = json.loads(output_path.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert written == printed
    assert written["result_marker"] == "OK"
    assert written["feed_source"] == "operator_selected_prices_example_test"


def test_main_refuses_output_outside_data_eng01(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["--input", str(EXAMPLE_INPUT), "--output", "latest.json"],
        stdout=stdout,
        stderr=stderr,
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "--output must be a relative path under data/eng01" in \
        output["error"]


def test_main_refuses_absolute_output_path(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "--input", str(EXAMPLE_INPUT),
            "--output", str(tmp_path / "latest_advisory.json"),
        ],
        stdout=stdout,
        stderr=stderr,
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "--output must be a relative path under data/eng01" in \
        output["error"]


def test_main_refuses_output_path_traversal(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "--input", str(EXAMPLE_INPUT),
            "--output", "data/eng01/../latest_advisory.json",
        ],
        stdout=stdout,
        stderr=stderr,
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "--output must be a relative path under data/eng01" in \
        output["error"]


def test_main_refuses_output_root_without_file_name(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["--input", str(EXAMPLE_INPUT), "--output", "data/eng01"],
        stdout=stdout,
        stderr=stderr,
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "--output must include a file name" in output["error"]


def test_main_refuses_output_directory_target(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    output_path = Path("data/eng01/latest_advisory.json")
    output_path.mkdir(parents=True)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        ["--input", str(EXAMPLE_INPUT), "--output", str(output_path)],
        stdout=stdout,
        stderr=stderr,
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "--output must target a regular file" in output["error"]


def test_main_fetches_url_with_injected_transport() -> None:
    calls: list[tuple[str, Mapping[str, str], float]] = []

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Eng01PriceFeedHttpResponse:
        calls.append((url, headers, timeout_seconds))
        return _http_response(url)

    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "--url", URL,
            "--fetched-at-utc", "2026-01-15T20:00:00Z",
            "--horizon-start-utc", "2026-01-16T00:00:00Z",
            "--horizon-hours", "3",
        ],
        stdout=stdout,
        stderr=stderr,
        transport=transport,
    )
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert calls[0][0] == URL
    assert calls[0][2] == 10.0
    assert output["result_marker"] == "OK"
    assert output["feed_source"] == "operator_selected_prices_example_test"
    assert output["top_3_cheapest_hours_utc"][0] == {
        "hour_utc": "2026-01-16T01:00:00Z",
        "price_eur_per_kwh": 0.075,
        "rank": 1,
    }


def test_main_url_mode_reads_headers_file_and_custom_rows_path(
    tmp_path: Path,
) -> None:
    seen_headers: list[Mapping[str, str]] = []
    headers_path = tmp_path / "headers.json"
    headers_path.write_text(
        json.dumps({"Accept": "application/json", "X-Trace": "eng01"}),
        encoding="utf-8",
    )

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Eng01PriceFeedHttpResponse:
        seen_headers.append(headers)
        body = json.dumps({
            "data": {
                "prices": [
                    {"timestamp": "2026-01-16T00:00:00Z",
                     "eur_mwh": 100.0},
                    {"timestamp": "2026-01-16T01:00:00Z",
                     "eur_mwh": 75.0},
                    {"timestamp": "2026-01-16T02:00:00Z",
                     "eur_mwh": 125.0},
                ],
            },
        }).encode("utf-8")
        return Eng01PriceFeedHttpResponse(
            body=body,
            content_type="application/json",
            status_code=200,
            source_url=url,
        )

    stdout = io.StringIO()

    exit_code = main(
        [
            "--url", URL,
            "--headers-file", str(headers_path),
            "--rows-path", "data,prices",
            "--hour-key", "timestamp",
            "--price-key", "eur_mwh",
            "--fetched-at-utc", "2026-01-15T20:00:00Z",
            "--horizon-start-utc", "2026-01-16T00:00:00Z",
            "--horizon-hours", "3",
            "--feed-source", "operator_selected_custom_shape",
        ],
        stdout=stdout,
        transport=transport,
    )
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert seen_headers[0]["Accept"] == "application/json"
    assert seen_headers[0]["X-Trace"] == "eng01"
    assert output["feed_source"] == "operator_selected_custom_shape"
    assert output["top_3_cheapest_hours_utc"][0]["hour_utc"] == \
        "2026-01-16T01:00:00Z"


def test_main_url_mode_derives_horizon_start_from_first_row() -> None:
    stdout = io.StringIO()

    exit_code = main(
        [
            "--url", URL,
            "--fetched-at-utc", "2026-01-15T20:00:00Z",
            "--horizon-hours", "3",
        ],
        stdout=stdout,
        transport=lambda url, *_: _http_response(url),
    )
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert output["horizon_start_utc"] == "2026-01-16T00:00:00Z"


def test_main_url_mode_refuses_transport_error() -> None:
    stderr = io.StringIO()

    exit_code = main(
        ["--url", "https://127.0.0.1/feed.json"],
        stderr=stderr,
        transport=lambda url, *_: _http_response(url),
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "URL_LOCAL_HOST_REFUSED" in output["error"]


def test_main_headers_file_must_be_json_object(tmp_path: Path) -> None:
    headers_path = tmp_path / "headers.json"
    headers_path.write_text(json.dumps(["Accept"]), encoding="utf-8")
    stderr = io.StringIO()

    exit_code = main(
        ["--url", URL, "--headers-file", str(headers_path)],
        stderr=stderr,
        transport=lambda url, *_: _http_response(url),
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert "headers file must contain a JSON object" in output["error"]


def test_main_url_mode_can_opt_in_to_credential_headers(
    tmp_path: Path,
) -> None:
    seen_headers: list[Mapping[str, str]] = []
    headers_path = tmp_path / "headers.json"
    headers_path.write_text(
        json.dumps({"Authorization": "Bearer test-token"}),
        encoding="utf-8",
    )

    def transport(
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Eng01PriceFeedHttpResponse:
        seen_headers.append(headers)
        return _http_response(url)

    stdout = io.StringIO()

    exit_code = main(
        [
            "--url", URL,
            "--headers-file", str(headers_path),
            "--allow-credential-headers",
            "--fetched-at-utc", "2026-01-15T20:00:00Z",
            "--horizon-start-utc", "2026-01-16T00:00:00Z",
            "--horizon-hours", "3",
        ],
        stdout=stdout,
        transport=transport,
    )

    assert exit_code == 0
    assert seen_headers[0]["Authorization"] == "Bearer test-token"


def test_main_refuses_headers_file_with_input_mode(tmp_path: Path) -> None:
    input_path = tmp_path / "prices.json"
    headers_path = tmp_path / "headers.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    headers_path.write_text(json.dumps({"Accept": "application/json"}),
                            encoding="utf-8")
    stderr = io.StringIO()

    exit_code = main(
        ["--input", str(input_path), "--headers-file", str(headers_path)],
        stderr=stderr,
    )
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert "--headers-file requires --url" in output["error"]


def _http_response(url: str) -> Eng01PriceFeedHttpResponse:
    return Eng01PriceFeedHttpResponse(
        body=json.dumps(_payload()["rows"]).encode("utf-8"),
        content_type="application/json; charset=utf-8",
        status_code=200,
        source_url=url,
    )
