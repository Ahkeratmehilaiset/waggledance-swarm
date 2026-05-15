# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""ENG-01 operator advisory card renderer.

Pure presentation mapping from the ENG-01 solver/CLI payload to a
SituationRoom-ready card shape. This module does not fetch data, write state,
control devices, or change the advisory risk class.
"""
from __future__ import annotations

from typing import Any, Mapping


CARD_SCHEMA_VERSION = "eng01_advisory_card.v1"
CASE_ID = "ENG-01__spot_electricity_monitor__home"
RISK_CLASS = "informational"
OK = "OK"


class Eng01AdvisoryCardError(ValueError):
    """Invalid ENG-01 payload for operator-card rendering."""


def render_eng01_advisory_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render an ENG-01 result payload as an operator-visible card.

    The renderer preserves the fail-closed result marker. It never converts a
    refusal into advice and never emits an action that would control external
    systems.
    """
    if not isinstance(payload, Mapping):
        raise Eng01AdvisoryCardError("payload must be an object")

    result_marker = _required_str(payload, "result_marker")
    if result_marker == OK:
        return _render_ok_card(payload, result_marker)
    return _render_refusal_card(payload, result_marker)


def _render_ok_card(
    payload: Mapping[str, Any],
    result_marker: str,
) -> dict[str, Any]:
    top_hours = _parse_top_hours(payload)
    if not top_hours:
        raise Eng01AdvisoryCardError(
            "OK payload must contain top_3_cheapest_hours_utc"
        )
    labels = [
        f"{item['hour_utc']} ({item['price_eur_per_kwh']:.3f} EUR/kWh)"
        for item in top_hours
    ]
    return {
        **_base_card(payload, result_marker, "ok"),
        "title": "ENG-01 cheapest electricity hours",
        "summary": "Cheapest UTC hours: " + ", ".join(labels),
        "top_hours": top_hours,
        "metrics": _metrics(payload),
        "operator_guidance": [
            "Use this as advisory timing input only.",
            "Do not automate chargers, boilers, or relays from this card.",
            "Re-check the source feed if the advisory is older than the TTL.",
        ],
    }


def _render_refusal_card(
    payload: Mapping[str, Any],
    result_marker: str,
) -> dict[str, Any]:
    refusal_reason = _optional_str(payload, "refusal_reason")
    summary = f"ENG-01 advice refused: {result_marker}"
    if refusal_reason:
        summary = f"{summary} ({refusal_reason})"
    details: dict[str, Any] = {}
    for key in ("missing_hours_utc", "duplicate_hours_utc"):
        value = payload.get(key)
        if isinstance(value, list):
            details[key] = [str(item) for item in value]
    return {
        **_base_card(payload, result_marker, "refused"),
        "title": "ENG-01 advisory refused",
        "summary": summary,
        "refusal_reason": refusal_reason or result_marker,
        "refusal_details": details,
        "operator_guidance": [
            "Do not act on this advisory.",
            "Inspect the price feed shape, freshness, and hourly coverage.",
            "Re-run after fixing the feed or mapping configuration.",
        ],
    }


def _base_card(
    payload: Mapping[str, Any],
    result_marker: str,
    status: str,
) -> dict[str, Any]:
    return {
        "schema_version": CARD_SCHEMA_VERSION,
        "case_id": CASE_ID,
        "risk_class": RISK_CLASS,
        "write_intent": "none",
        "status": status,
        "result_marker": result_marker,
        "feed_source": _optional_str(payload, "feed_source"),
        "horizon_start_utc": _optional_str(payload, "horizon_start_utc"),
        "horizon_hours": _optional_int(payload, "horizon_hours"),
        "recommendation_freshness_ttl_minutes": _optional_int(
            payload,
            "recommendation_freshness_ttl_minutes",
        ),
    }


def _metrics(payload: Mapping[str, Any]) -> dict[str, float]:
    metrics = {}
    for key in (
        "horizon_24h_min_price_eur_per_kwh",
        "horizon_24h_max_price_eur_per_kwh",
        "horizon_24h_avg_price_eur_per_kwh",
        "expected_savings_eur_per_kwh_vs_peak",
    ):
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise Eng01AdvisoryCardError(f"{key} must be numeric")
        metrics[key] = round(float(value), 3)
    return metrics


def _parse_top_hours(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("top_3_cheapest_hours_utc")
    if not isinstance(raw, list):
        raise Eng01AdvisoryCardError(
            "top_3_cheapest_hours_utc must be a list"
        )
    parsed = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise Eng01AdvisoryCardError(
                f"top_3_cheapest_hours_utc[{index}] must be an object"
            )
        hour_utc = item.get("hour_utc")
        if not isinstance(hour_utc, str) or not hour_utc.endswith("Z"):
            raise Eng01AdvisoryCardError(
                f"top_3_cheapest_hours_utc[{index}].hour_utc must be UTC Z"
            )
        price = item.get("price_eur_per_kwh")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            raise Eng01AdvisoryCardError(
                f"top_3_cheapest_hours_utc[{index}].price_eur_per_kwh "
                "must be numeric"
            )
        rank = item.get("rank")
        if isinstance(rank, bool) or not isinstance(rank, int) or rank <= 0:
            raise Eng01AdvisoryCardError(
                f"top_3_cheapest_hours_utc[{index}].rank must be positive"
            )
        parsed.append({
            "rank": rank,
            "hour_utc": hour_utc,
            "price_eur_per_kwh": round(float(price), 3),
        })
    return parsed


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Eng01AdvisoryCardError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise Eng01AdvisoryCardError(f"{key} must be a string")
    return value.strip() or None


def _optional_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Eng01AdvisoryCardError(f"{key} must be a positive integer")
    return value


__all__ = [
    "CARD_SCHEMA_VERSION",
    "CASE_ID",
    "Eng01AdvisoryCardError",
    "RISK_CLASS",
    "render_eng01_advisory_card",
]
