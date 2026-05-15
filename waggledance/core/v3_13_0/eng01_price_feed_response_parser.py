# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Provider-neutral ENG-01 price-feed HTTP response parser.

This module does not fetch live data, open files, or handle credentials. It
only parses an already-fetched JSON response body into hourly price rows for
``eng01_price_feed_adapter.build_eng01_price_feed``.
"""
from __future__ import annotations

import json
from typing import Any


SUPPORTED_CONTENT_TYPES = frozenset({
    "application/json",
    "application/ld+json",
})


class Eng01PriceFeedResponseParserError(ValueError):
    """Invalid caller input for ENG-01 response parsing."""


def parse_price_feed_response(
    body: bytes | str,
    *,
    content_type: str = "application/json",
    rows_path: tuple[str, ...] = (),
    hour_key: str = "hour_utc",
    price_key: str = "price",
) -> list[dict[str, Any]]:
    """Parse an already-fetched JSON response body into adapter rows.

    The parser intentionally validates only response shape and scalar row
    fields. Timestamp parsing, hour alignment, unit conversion, monotonicity,
    freshness, and duplicate detection stay downstream in the adapter/solver.
    """
    _check_content_type(content_type)
    parsed = _decode_json_body(body)
    rows = _select_rows(parsed, rows_path)
    return _validate_rows(rows, hour_key=hour_key, price_key=price_key)


def _check_content_type(content_type: str) -> None:
    if not isinstance(content_type, str):
        raise Eng01PriceFeedResponseParserError("unsupported content_type")
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized not in SUPPORTED_CONTENT_TYPES:
        raise Eng01PriceFeedResponseParserError("unsupported content_type")


def _decode_json_body(body: bytes | str) -> Any:
    if isinstance(body, bytes):
        try:
            raw = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise Eng01PriceFeedResponseParserError(
                "malformed response body"
            ) from exc
    elif isinstance(body, str):
        raw = body
    else:
        raise Eng01PriceFeedResponseParserError("malformed response body")

    if not raw.strip():
        raise Eng01PriceFeedResponseParserError("empty response body")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise Eng01PriceFeedResponseParserError(
            "malformed response body"
        ) from exc


def _select_rows(parsed: Any, rows_path: tuple[str, ...]) -> Any:
    if not isinstance(rows_path, tuple):
        raise Eng01PriceFeedResponseParserError("rows_path must be a tuple")
    if not rows_path:
        rows = parsed
    else:
        if not isinstance(parsed, dict):
            raise Eng01PriceFeedResponseParserError(
                "rows_path requires object root"
            )
        cursor = parsed
        for key in rows_path:
            if not isinstance(key, str) or not key:
                raise Eng01PriceFeedResponseParserError(
                    "rows_path entries must be non-empty strings"
                )
            if not isinstance(cursor, dict) or key not in cursor:
                raise Eng01PriceFeedResponseParserError(
                    "rows_path target is not a list"
                )
            cursor = cursor[key]
        rows = cursor

    if not isinstance(rows, list):
        raise Eng01PriceFeedResponseParserError(
            "rows_path target is not a list"
        )
    if not rows:
        raise Eng01PriceFeedResponseParserError("rows list is empty")
    return rows


def _validate_rows(
    rows: list[Any],
    *,
    hour_key: str,
    price_key: str,
) -> list[dict[str, Any]]:
    _check_key(hour_key, "hour_key")
    _check_key(price_key, "price_key")

    normalized = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise Eng01PriceFeedResponseParserError(
                f"row {index} is not an object"
            )
        if hour_key not in row:
            raise Eng01PriceFeedResponseParserError(
                f"row {index} missing key {hour_key}"
            )
        if price_key not in row:
            raise Eng01PriceFeedResponseParserError(
                f"row {index} missing key {price_key}"
            )
        hour_value = row[hour_key]
        if not isinstance(hour_value, str):
            raise Eng01PriceFeedResponseParserError(
                f"row {index} field {hour_key} has wrong type"
            )
        price_value = row[price_key]
        if isinstance(price_value, bool) or not isinstance(
            price_value, (int, float)
        ):
            raise Eng01PriceFeedResponseParserError(
                f"row {index} field {price_key} has wrong type"
            )
        normalized.append({hour_key: hour_value, price_key: price_value})
    return normalized


def _check_key(value: str, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise Eng01PriceFeedResponseParserError(
            f"{label} must be a non-empty string"
        )


__all__ = [
    "Eng01PriceFeedResponseParserError",
    "SUPPORTED_CONTENT_TYPES",
    "parse_price_feed_response",
]
