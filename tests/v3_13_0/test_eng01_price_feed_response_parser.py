# SPDX-License-Identifier: BUSL-1.1
"""Tests for the provider-neutral ENG-01 price-feed response parser."""
from __future__ import annotations

import json

import pytest

from waggledance.core.v3_13_0.eng01_price_feed_adapter import (
    PRICE_UNIT_EUR_PER_MWH,
    build_eng01_price_feed,
)
from waggledance.core.v3_13_0.eng01_price_feed_response_parser import (
    Eng01PriceFeedResponseParserError,
    parse_price_feed_response,
)
from waggledance.core.v3_13_0.eng01_spot_electricity import (
    OK,
    recommend_top_3_cheapest_hours,
)


def _flat_rows() -> list[dict]:
    return [
        {"hour_utc": "2026-01-16T00:00:00Z", "price": 100.0},
        {"hour_utc": "2026-01-16T01:00:00Z", "price": 75.0},
        {"hour_utc": "2026-01-16T02:00:00Z", "price": 125.0},
    ]


def test_parses_flat_json_array_with_default_keys() -> None:
    rows = parse_price_feed_response(json.dumps(_flat_rows()).encode("utf-8"))

    assert rows == _flat_rows()


def test_parses_nested_json_via_rows_path() -> None:
    body = {
        "meta": {"provider": "operator_selected"},
        "data": {"prices": _flat_rows()},
    }

    rows = parse_price_feed_response(
        json.dumps(body),
        rows_path=("data", "prices"),
    )

    assert rows == _flat_rows()


def test_parses_custom_hour_and_price_keys() -> None:
    body = {
        "prices": [
            {"timestamp": "2026-01-16T00:00:00Z", "eur_mwh": 100.0},
            {"timestamp": "2026-01-16T01:00:00Z", "eur_mwh": 75.0},
        ],
    }

    rows = parse_price_feed_response(
        json.dumps(body),
        rows_path=("prices",),
        hour_key="timestamp",
        price_key="eur_mwh",
    )

    assert rows == [
        {"timestamp": "2026-01-16T00:00:00Z", "eur_mwh": 100.0},
        {"timestamp": "2026-01-16T01:00:00Z", "eur_mwh": 75.0},
    ]


def test_parser_rows_compose_with_adapter_and_solver() -> None:
    rows = parse_price_feed_response(json.dumps(_flat_rows()))
    feed = build_eng01_price_feed(
        rows,
        fetched_at_utc="2026-01-15T20:00:00Z",
        horizon_start_utc="2026-01-16T00:00:00Z",
        horizon_hours=3,
        price_unit=PRICE_UNIT_EUR_PER_MWH,
    )

    result = recommend_top_3_cheapest_hours(feed)

    assert result.result_marker == OK
    assert result.to_payload()["top_3_cheapest_hours_utc"] == [
        {"hour_utc": "2026-01-16T01:00:00Z", "price_eur_per_kwh": 0.075,
         "rank": 1},
        {"hour_utc": "2026-01-16T00:00:00Z", "price_eur_per_kwh": 0.1,
         "rank": 2},
        {"hour_utc": "2026-01-16T02:00:00Z", "price_eur_per_kwh": 0.125,
         "rank": 3},
    ]


@pytest.mark.parametrize("body", ["", "   ", b""])
def test_refuses_empty_body(body: bytes | str) -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="empty response body"):
        parse_price_feed_response(body)


@pytest.mark.parametrize("body", ["{not-json", b"\xff"])
def test_refuses_malformed_json_body(body: bytes | str) -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="malformed response body"):
        parse_price_feed_response(body)


def test_refuses_unsupported_content_type() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="unsupported content_type"):
        parse_price_feed_response("[]", content_type="text/csv")


def test_accepts_json_content_type_with_charset() -> None:
    rows = parse_price_feed_response(
        json.dumps(_flat_rows()),
        content_type="application/json; charset=utf-8",
    )

    assert rows == _flat_rows()


def test_refuses_rows_path_with_non_object_root() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="rows_path requires object root"):
        parse_price_feed_response("[]", rows_path=("data",))


def test_refuses_non_list_rows_target() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="rows_path target is not a list"):
        parse_price_feed_response(
            json.dumps({"data": {"prices": {"not": "a-list"}}}),
            rows_path=("data", "prices"),
        )


def test_refuses_empty_rows_list() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="rows list is empty"):
        parse_price_feed_response("[]")


def test_refuses_row_missing_hour_key() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="row 0 missing key hour_utc"):
        parse_price_feed_response(json.dumps([{"price": 100.0}]))


def test_refuses_row_missing_price_key() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="row 0 missing key price"):
        parse_price_feed_response(
            json.dumps([{"hour_utc": "2026-01-16T00:00:00Z"}])
        )


def test_refuses_row_with_bool_as_price() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="row 0 field price has wrong type"):
        parse_price_feed_response(
            json.dumps([{
                "hour_utc": "2026-01-16T00:00:00Z",
                "price": True,
            }])
        )


def test_refuses_row_with_non_str_hour_value() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="row 0 field hour_utc has wrong type"):
        parse_price_feed_response(json.dumps([{"hour_utc": 1, "price": 100.0}]))


def test_refuses_row_that_is_not_object() -> None:
    with pytest.raises(Eng01PriceFeedResponseParserError,
                       match="row 0 is not an object"):
        parse_price_feed_response(json.dumps([["2026-01-16T00:00:00Z", 100.0]]))
