# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""ENG-06 operator advisory card renderer.

Pure presentation mapping from the ENG-06 fireplace solver/CLI payload to a
SituationRoom-ready card shape. This module does not fetch data, open SQLite,
write state, control devices, or change the advisory risk class.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


CARD_SCHEMA_VERSION = "eng06_advisory_card.v1"
CASE_ID = "ENG-06__cottage_fireplace_advisor__cottage"
RISK_CLASS = "informational"
OK = "OK"


class Eng06AdvisoryCardError(ValueError):
    """Invalid ENG-06 payload for operator-card rendering."""


def render_eng06_advisory_card(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Render an ENG-06 result payload as an operator-visible card.

    The renderer preserves the fail-closed result marker. It never converts a
    refusal into advice and never emits an action that would control external
    systems.
    """
    if not isinstance(payload, Mapping):
        raise Eng06AdvisoryCardError("payload must be an object")

    result_marker = _required_str(payload, "result_marker")
    if result_marker == OK:
        return _render_ok_card(payload, result_marker)
    return _render_refusal_card(payload, result_marker)


def _render_ok_card(
    payload: Mapping[str, Any],
    result_marker: str,
) -> dict[str, Any]:
    metrics = _ok_metrics(payload)
    first_day = _required_str(payload, "burn_log_first_day_utc")
    last_day = _required_str(payload, "burn_log_last_day_utc")
    return {
        **_base_card(payload, result_marker, "ok"),
        "title": "ENG-06 fireplace burn-log summary",
        "summary": (
            f"{metrics['fire_event_count_30d']} fire events across "
            f"{metrics['days_with_fire']} fire days; average chimney "
            f"{metrics['average_chimney_temp_c']:.1f} C, peak "
            f"{metrics['peak_chimney_temp_c']:.1f} C."
        ),
        "burn_log_window": {
            "first_day_utc": first_day,
            "last_day_utc": last_day,
        },
        "metrics": metrics,
        "operator_guidance": [
            "Use this as informational fireplace context only.",
            "Do not automate dampers, fans, heaters, or alarms from this card.",
            "Inspect burn-log coverage and freshness before acting.",
        ],
    }


def _render_refusal_card(
    payload: Mapping[str, Any],
    result_marker: str,
) -> dict[str, Any]:
    refusal_reason = _optional_str(payload, "refusal_reason")
    summary = f"ENG-06 advice refused: {result_marker}"
    if refusal_reason:
        summary = f"{summary} ({refusal_reason})"
    details: dict[str, Any] = {}
    for key in (
        "missing_days_utc",
        "duplicate_days_utc",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            details[key] = [str(item) for item in value]
    for key in ("burn_log_first_day_utc", "burn_log_last_day_utc"):
        value = _optional_str(payload, key)
        if value is not None:
            details[key] = value
    return {
        **_base_card(payload, result_marker, "refused"),
        "title": "ENG-06 advisory refused",
        "summary": summary,
        "refusal_reason": refusal_reason or result_marker,
        "refusal_details": details,
        "operator_guidance": [
            "Do not act on this advisory.",
            "Inspect burn-log shape, freshness, ordering, and daily coverage.",
            "Re-run after fixing the burn-log export or mapping configuration.",
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
        "recommendation_freshness_ttl_minutes": _optional_int(
            payload,
            "recommendation_freshness_ttl_minutes",
        ),
    }


def _ok_metrics(payload: Mapping[str, Any]) -> dict[str, float | int]:
    return {
        "fire_event_count_30d": _required_non_negative_int(
            payload,
            "fire_event_count_30d",
        ),
        "days_with_fire": _required_non_negative_int(
            payload,
            "days_with_fire",
        ),
        "average_chimney_temp_c": _required_number(
            payload,
            "average_chimney_temp_c",
        ),
        "peak_chimney_temp_c": _required_number(
            payload,
            "peak_chimney_temp_c",
        ),
    }


def _required_str(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Eng06AdvisoryCardError(f"{key} must be a non-empty string")
    return value.strip()


def _optional_str(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise Eng06AdvisoryCardError(f"{key} must be a string")
    return value.strip() or None


def _optional_int(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise Eng06AdvisoryCardError(f"{key} must be a positive integer")
    return value


def _required_non_negative_int(
    payload: Mapping[str, Any],
    key: str,
) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Eng06AdvisoryCardError(
            f"{key} must be a non-negative integer"
        )
    return value


def _required_number(payload: Mapping[str, Any], key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Eng06AdvisoryCardError(f"{key} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise Eng06AdvisoryCardError(f"{key} must be finite")
    return round(parsed, 1)


__all__ = [
    "CARD_SCHEMA_VERSION",
    "CASE_ID",
    "Eng06AdvisoryCardError",
    "RISK_CLASS",
    "render_eng06_advisory_card",
]
