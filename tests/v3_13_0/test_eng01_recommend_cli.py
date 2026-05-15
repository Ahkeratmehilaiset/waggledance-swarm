# SPDX-License-Identifier: BUSL-1.1
"""Tests for the offline ENG-01 recommendation CLI."""
from __future__ import annotations

import io
import json
from pathlib import Path

from waggledance.adapters.cli.eng01_recommend import main, run_from_payload


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "eng01" / "offline_prices_sample.json"


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
