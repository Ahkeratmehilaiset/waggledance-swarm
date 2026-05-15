# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Digheran-shaped AIR-01 air-quality observation adapter.

This adapter normalizes already-fetched JSON into the provider-neutral
observation shape consumed by :mod:`air01_air_quality_advisor`. It does not
open sockets, scan networks, read credentials, write state, or call an LLM.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from typing import Any, Mapping


CASE_ID = "AIR-01__indoor_air_quality_advisor__cottage"
OBSERVATION_SCHEMA_VERSION = "air01.observation.v1"

PM25_UG_M3 = "pm25_ug_m3"
PM10_UG_M3 = "pm10_ug_m3"
CO_PPM = "co_ppm"
RADON_BQ_M3 = "radon_bq_m3"

_SUPPORTED_CONTENT_TYPES = frozenset({"application/json", "application/ld+json"})
_METRIC_ALIASES: tuple[tuple[str, tuple[str, ...], str, tuple[str, ...]], ...] = (
    (PM25_UG_M3, ("pm25", "pm2_5", "pm25_ug", "pm25_ug_m3"), "ug/m3",
     ("ug/m3", "ug/m^3", "microgram/m3", "micrograms/m3")),
    (PM10_UG_M3, ("pm10", "pm10_ug", "pm10_ug_m3"), "ug/m3",
     ("ug/m3", "ug/m^3", "microgram/m3", "micrograms/m3")),
    (CO_PPM, ("co", "co_ppm", "carbon_monoxide"), "ppm", ("ppm",)),
    (RADON_BQ_M3, ("radon", "radon_bq", "radon_bq_m3"), "Bq/m3",
     ("bq/m3", "bq/m^3")),
)
_SECRET_MARKERS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "passwd",
    "password",
    "secret",
    "token",
    "x-api-key",
    "api_key",
)


class Air01DigheranAdapterError(ValueError):
    """Invalid caller input for AIR-01 observation adaptation."""


def parse_digheran_air_quality_response(
    body: bytes,
    *,
    content_type: str,
    source_url: str,
    fetched_at_utc: str | None = None,
) -> dict[str, Any]:
    """Parse a Digheran-shaped JSON response into AIR-01 observation shape."""
    _validate_content_type(content_type)
    if not isinstance(body, bytes):
        raise Air01DigheranAdapterError("response body must be bytes")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Air01DigheranAdapterError("response body must be UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise Air01DigheranAdapterError("response JSON must be an object")
    return normalize_digheran_air_quality_payload(
        payload,
        source_url=source_url,
        fetched_at_utc=fetched_at_utc,
    )


def normalize_digheran_air_quality_payload(
    payload: Mapping[str, Any],
    *,
    source_url: str,
    fetched_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build a provider-neutral AIR-01 observation from source-shaped JSON."""
    if not isinstance(payload, Mapping):
        raise Air01DigheranAdapterError("payload must be an object")
    safe_source_url = _safe_source_ref(source_url, "source_url")
    observed_at = _parse_utc_instant(
        _first_present_string(payload, ("timestamp_utc", "timestamp", "time")),
        "timestamp_utc",
    )
    fetched_at = (
        _parse_utc_instant(fetched_at_utc, "fetched_at_utc")
        if fetched_at_utc is not None
        else observed_at
    )
    readings_source = payload.get("readings")
    if not isinstance(readings_source, Mapping):
        raise Air01DigheranAdapterError("payload.readings must be an object")

    readings: list[dict[str, Any]] = []
    for metric, aliases, canonical_unit, allowed_units in _METRIC_ALIASES:
        item = _read_metric(
            readings_source,
            aliases=aliases,
            canonical_metric=metric,
            canonical_unit=canonical_unit,
            allowed_units=allowed_units,
        )
        if item is not None:
            readings.append(item)
    if not readings:
        raise Air01DigheranAdapterError("payload.readings has no known metrics")

    return {
        "case_id": CASE_ID,
        "schema_version": OBSERVATION_SCHEMA_VERSION,
        "observation_source": "operator_allowlisted_digheran_http",
        "source_url": safe_source_url,
        "device_id": _device_id(payload.get("device")),
        "observed_at_utc": observed_at,
        "fetched_at_utc": fetched_at,
        "readings": readings,
    }


def _read_metric(
    source: Mapping[str, Any],
    *,
    aliases: tuple[str, ...],
    canonical_metric: str,
    canonical_unit: str,
    allowed_units: tuple[str, ...],
) -> dict[str, Any] | None:
    for alias in aliases:
        if alias in source:
            raw = source[alias]
            if isinstance(raw, Mapping):
                value = raw.get("value")
                unit = raw.get("unit", canonical_unit)
            else:
                value = raw
                unit = canonical_unit
            normalized_unit = _normalize_unit(unit, allowed_units, alias)
            return {
                "metric": canonical_metric,
                "value": _non_negative_finite_number(value, alias),
                "unit": canonical_unit,
                "source_key": alias,
                "source_unit": normalized_unit,
            }
    return None


def _validate_content_type(content_type: str) -> None:
    if not isinstance(content_type, str):
        raise Air01DigheranAdapterError("content_type must be a string")
    normalized = content_type.split(";", 1)[0].strip().lower()
    if normalized not in _SUPPORTED_CONTENT_TYPES:
        raise Air01DigheranAdapterError("content_type must be JSON")


def _normalize_unit(raw: Any, allowed_units: tuple[str, ...], label: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise Air01DigheranAdapterError(f"{label}.unit must be a string")
    normalized = raw.strip().lower()
    if normalized not in allowed_units:
        raise Air01DigheranAdapterError(f"{label}.unit is unsupported")
    return normalized


def _non_negative_finite_number(raw: Any, label: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise Air01DigheranAdapterError(f"{label}.value must be numeric")
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise Air01DigheranAdapterError(
            f"{label}.value must be non-negative finite"
        )
    return round(value, 3)


def _first_present_string(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise Air01DigheranAdapterError("payload must include timestamp_utc")


def _parse_utc_instant(value: str | None, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or not value.endswith("Z"):
        raise Air01DigheranAdapterError(f"{label} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise Air01DigheranAdapterError(
            f"{label} must be an ISO-8601 UTC timestamp"
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise Air01DigheranAdapterError(f"{label} must be UTC")
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def _device_id(raw_device: Any) -> str:
    if not isinstance(raw_device, Mapping):
        return "unknown_digheran_device"
    for key in ("id", "serial", "name"):
        value = raw_device.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_source_ref(value, f"device.{key}")
    return "unknown_digheran_device"


def _safe_source_ref(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Air01DigheranAdapterError(f"{label} must be a non-empty string")
    normalized = value.strip()
    lowered = normalized.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise Air01DigheranAdapterError(f"{label} must not contain secrets")
    return normalized


__all__ = [
    "Air01DigheranAdapterError",
    "CASE_ID",
    "CO_PPM",
    "OBSERVATION_SCHEMA_VERSION",
    "PM10_UG_M3",
    "PM25_UG_M3",
    "RADON_BQ_M3",
    "normalize_digheran_air_quality_payload",
    "parse_digheran_air_quality_response",
]
