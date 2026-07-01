# SPDX-License-Identifier: BUSL-1.1
"""Read-only Prometheus/Alertmanager feed for route-stage latency panels."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from waggledance.core.v3_13_0.secret_markers import (
    contains_secret_marker_substring,
)
from waggledance.core.v3_13_0.ssrf_host_guard import (
    classify_request_host,
    normalize_allowed_hosts,
)

DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 5_000_000
DEFAULT_CACHE_TTL_SECONDS = 30.0
MAX_CACHE_TTL_SECONDS = 300.0
DEFAULT_FAILURE_BACKOFF_SECONDS = 30.0
MAX_FAILURE_BACKOFF_SECONDS = 300.0
DEFAULT_ACCEPT_HEADER = "application/json"
DEFAULT_USER_AGENT = "waggledance-route-stage-latency-feed/3.8"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_SUPPORTED_CONTENT_TYPES = frozenset({"application/json"})
_CREDENTIAL_HEADER_NAMES = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "x-api-key",
    }
)

_PANEL_QUERIES = {
    "route_stage_latency_p95_ms": (
        "histogram_quantile(0.95, sum by (le, stage) "
        "(rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m])))"
    ),
    "route_stage_latency_p99_ms": (
        "histogram_quantile(0.99, sum by (le, stage) "
        "(rate(waggledance_route_stage_request_latency_histogram_ms_bucket[5m])))"
    ),
    "route_stage_request_rate": (
        "sum by (stage) " "(rate(waggledance_route_stage_observations_total[5m]))"
    ),
}


@dataclass(frozen=True)
class RouteStageLatencyFeedHttpResponse:
    body: bytes
    content_type: str
    status_code: int
    source_url: str


class RouteStageLatencyFeedError(ValueError):
    """Invalid operator feed config or failed read-only feed fetch."""


RouteStageLatencyFeedTransport = Callable[
    [str, Mapping[str, str], float, Mapping[str, str]],
    RouteStageLatencyFeedHttpResponse,
]


class UnavailableRouteStageLatencyFeed:
    """Provider object that makes config errors visible as feed unavailable."""

    def snapshot(self) -> dict[str, Any]:
        raise RouteStageLatencyFeedError("ROUTE_STAGE_LATENCY_FEED_UNAVAILABLE")

    def provider_health(self) -> dict[str, Any]:
        return {
            "source": "prometheus_alertmanager_adapter",
            "status": "warning",
            "configured": True,
            "available": False,
            "cache_enabled": False,
            "cache_present": False,
            "cache_stale": False,
            "backoff_active": False,
            "cache_ttl_seconds": 0.0,
            "failure_backoff_seconds": 0.0,
            "timeout_seconds": 0.0,
            "max_response_bytes": 0,
            "last_response_bytes": 0,
            "cache_hit_count": 0,
            "cache_miss_count": 0,
            "fetch_success_count": 0,
            "fetch_failure_count": 0,
            "backoff_skip_count": 0,
            "last_success_at": None,
            "last_failure_at": None,
            "last_failure_reason": "ROUTE_STAGE_LATENCY_FEED_UNAVAILABLE",
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        }


class RouteStageLatencyPrometheusAlertmanagerFeed:
    """Fetch route-stage latency values from operator-owned observability APIs."""

    def __init__(
        self,
        *,
        prometheus_base_url: str | None = None,
        alertmanager_base_url: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        failure_backoff_seconds: float = DEFAULT_FAILURE_BACKOFF_SECONDS,
        allowed_private_hosts: Sequence[str] = (),
        transport: RouteStageLatencyFeedTransport | None = None,
        monotonic: Callable[[], float] | None = None,
        utc_now: Callable[[], datetime] | None = None,
    ) -> None:
        allowed_hosts = _normalize_allowed_hosts(allowed_private_hosts)
        self._prometheus_query_url = _endpoint_url(
            prometheus_base_url,
            "/api/v1/query",
            allowed_private_hosts=allowed_hosts,
        )
        self._alertmanager_alerts_url = _endpoint_url(
            alertmanager_base_url,
            "/api/v2/alerts",
            allowed_private_hosts=allowed_hosts,
        )
        if self._prometheus_query_url is None and self._alertmanager_alerts_url is None:
            raise RouteStageLatencyFeedError("ENDPOINTS_EMPTY")
        self._timeout_seconds = _validate_timeout(timeout_seconds)
        self._max_response_bytes = _validate_size_cap(max_response_bytes)
        self._cache_ttl_seconds = _validate_interval(
            cache_ttl_seconds,
            max_seconds=MAX_CACHE_TTL_SECONDS,
            error_code="CACHE_TTL_OUT_OF_RANGE",
        )
        self._failure_backoff_seconds = _validate_interval(
            failure_backoff_seconds,
            max_seconds=MAX_FAILURE_BACKOFF_SECONDS,
            error_code="BACKOFF_OUT_OF_RANGE",
        )
        self._transport = transport or _httpx_transport
        self._headers = _validate_headers(None)
        self._monotonic = monotonic or time.monotonic
        self._utc_now = utc_now or _utc_now
        self._cached_snapshot: dict[str, Any] | None = None
        self._cache_expires_at = 0.0
        self._backoff_until = 0.0
        self._cache_hit_count = 0
        self._cache_miss_count = 0
        self._fetch_success_count = 0
        self._fetch_failure_count = 0
        self._backoff_skip_count = 0
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._last_failure_reason: str | None = None
        self._last_response_bytes = 0

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        transport: RouteStageLatencyFeedTransport | None = None,
    ) -> "RouteStageLatencyPrometheusAlertmanagerFeed":
        if not isinstance(config, Mapping):
            raise RouteStageLatencyFeedError("CONFIG_REFUSED")
        return cls(
            prometheus_base_url=_string_or_none(
                config.get("prometheus_base_url") or config.get("prometheus_url")
            ),
            alertmanager_base_url=_string_or_none(
                config.get("alertmanager_base_url") or config.get("alertmanager_url")
            ),
            timeout_seconds=config.get("timeout_s", DEFAULT_TIMEOUT_SECONDS),
            max_response_bytes=config.get(
                "max_response_bytes",
                DEFAULT_MAX_RESPONSE_BYTES,
            ),
            cache_ttl_seconds=config.get(
                "cache_ttl_s",
                config.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS),
            ),
            failure_backoff_seconds=config.get(
                "failure_backoff_s",
                config.get(
                    "failure_backoff_seconds",
                    DEFAULT_FAILURE_BACKOFF_SECONDS,
                ),
            ),
            allowed_private_hosts=config.get("allowed_private_hosts", ()),
            transport=transport,
        )

    def snapshot(self) -> dict[str, Any]:
        now = self._monotonic()
        if self._cached_snapshot is not None and now < self._cache_expires_at:
            self._cache_hit_count += 1
            return self._snapshot_with_health(self._cached_snapshot)
        if now < self._backoff_until:
            self._backoff_skip_count += 1
            if self._cached_snapshot is not None:
                return self._snapshot_with_health(self._cached_snapshot)
            raise RouteStageLatencyFeedError("BACKOFF_ACTIVE")

        self._cache_miss_count += 1
        try:
            snapshot = self._read_snapshot()
        except RouteStageLatencyFeedError as exc:
            self._record_failure(str(exc) or "FEED_READ_FAILED")
            if self._cached_snapshot is not None:
                return self._snapshot_with_health(self._cached_snapshot)
            raise

        self._cached_snapshot = snapshot
        self._cache_expires_at = now + self._cache_ttl_seconds
        self._fetch_success_count += 1
        self._last_success_at = snapshot["updated_at"]
        self._last_failure_reason = None
        return self._snapshot_with_health(snapshot)

    def provider_health(self) -> dict[str, Any]:
        now = self._monotonic()
        cache_present = self._cached_snapshot is not None
        cache_stale = cache_present and now >= self._cache_expires_at
        backoff_active = now < self._backoff_until
        available = cache_present or (
            self._fetch_success_count > 0 and self._last_failure_reason is None
        )
        status = "nominal"
        if backoff_active or self._last_failure_reason is not None:
            status = "warning"
        return {
            "source": "prometheus_alertmanager_adapter",
            "status": status,
            "configured": True,
            "available": available,
            "cache_enabled": True,
            "cache_present": cache_present,
            "cache_stale": cache_stale,
            "backoff_active": backoff_active,
            "cache_ttl_seconds": self._cache_ttl_seconds,
            "failure_backoff_seconds": self._failure_backoff_seconds,
            "timeout_seconds": self._timeout_seconds,
            "max_response_bytes": self._max_response_bytes,
            "last_response_bytes": self._last_response_bytes,
            "cache_hit_count": self._cache_hit_count,
            "cache_miss_count": self._cache_miss_count,
            "fetch_success_count": self._fetch_success_count,
            "fetch_failure_count": self._fetch_failure_count,
            "backoff_skip_count": self._backoff_skip_count,
            "last_success_at": self._last_success_at,
            "last_failure_at": self._last_failure_at,
            "last_failure_reason": self._last_failure_reason,
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
        }

    def _record_failure(self, reason: str) -> None:
        self._fetch_failure_count += 1
        self._last_failure_at = _utc_now_rfc3339(self._utc_now)
        self._last_failure_reason = _safe_reason(reason)
        self._backoff_until = self._monotonic() + self._failure_backoff_seconds

    def _snapshot_with_health(self, snapshot: Mapping[str, Any]) -> dict[str, Any]:
        result = {
            "updated_at": snapshot.get("updated_at"),
            "panel_values": list(snapshot.get("panel_values", [])),
            "active_alerts": list(snapshot.get("active_alerts", [])),
        }
        result["provider_health"] = self.provider_health()
        return result

    def _read_snapshot(self) -> dict[str, Any]:
        panel_values: list[dict[str, Any]] = []
        active_alerts: list[dict[str, Any]] = []

        if self._prometheus_query_url is not None:
            for panel_id, query in _PANEL_QUERIES.items():
                payload = self._get_json(
                    self._prometheus_query_url,
                    {"query": query},
                )
                panel_values.extend(_prometheus_panel_values(panel_id, payload))

        if self._alertmanager_alerts_url is not None:
            payload = self._get_json(self._alertmanager_alerts_url, {})
            active_alerts = _alertmanager_active_alerts(payload)

        return {
            "updated_at": _utc_now_rfc3339(self._utc_now),
            "panel_values": panel_values,
            "active_alerts": active_alerts,
        }

    def _get_json(self, url: str, params: Mapping[str, str]) -> Any:
        try:
            response = self._transport(
                url,
                self._headers,
                self._timeout_seconds,
                params,
            )
        except RouteStageLatencyFeedError:
            raise
        except httpx.TimeoutException as exc:
            raise RouteStageLatencyFeedError("NETWORK_TIMEOUT") from exc
        except (
            httpx.ConnectError,
            httpx.DecodingError,
            httpx.HTTPError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise RouteStageLatencyFeedError("NETWORK_REQUEST_FAILED") from exc
        except Exception as exc:
            raise RouteStageLatencyFeedError("NETWORK_REQUEST_FAILED") from exc
        response = _validate_response(response, url, self._max_response_bytes)
        self._last_response_bytes = len(response.body)
        try:
            return json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RouteStageLatencyFeedError("RESPONSE_JSON_REFUSED") from exc


def _httpx_transport(
    url: str,
    headers: Mapping[str, str],
    timeout_seconds: float,
    params: Mapping[str, str],
) -> RouteStageLatencyFeedHttpResponse:
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 3.0))
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.get(url, headers=dict(headers), params=dict(params))
    return RouteStageLatencyFeedHttpResponse(
        body=response.content,
        content_type=response.headers.get("Content-Type", ""),
        status_code=response.status_code,
        source_url=url,
    )


def _endpoint_url(
    base_url: str | None,
    endpoint_path: str,
    *,
    allowed_private_hosts: frozenset[str],
) -> str | None:
    if base_url is None:
        return None
    normalized = _validate_base_url(
        base_url,
        allowed_private_hosts=allowed_private_hosts,
    )
    parsed = urlsplit(normalized)
    path = f"{parsed.path.rstrip('/')}{endpoint_path}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_base_url(
    url: str,
    *,
    allowed_private_hosts: frozenset[str],
) -> str:
    if not isinstance(url, str) or not url.strip():
        raise RouteStageLatencyFeedError("URL_EMPTY")
    normalized = url.strip()
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise RouteStageLatencyFeedError("URL_SCHEME_REFUSED")
    if parsed.username or parsed.password:
        raise RouteStageLatencyFeedError("URL_USERINFO_REFUSED")
    if parsed.query or parsed.fragment:
        raise RouteStageLatencyFeedError("URL_QUERY_REFUSED")
    if contains_secret_marker_substring(normalized):
        raise RouteStageLatencyFeedError("URL_SECRET_REFUSED")
    _validate_host(parsed.hostname or "", allowed_private_hosts)
    return normalized


def _validate_host(hostname: str, allowed_private_hosts: frozenset[str]) -> None:
    code = classify_request_host(hostname, allowed_hosts=allowed_private_hosts)
    if code is not None:
        raise RouteStageLatencyFeedError(code)


def _normalize_allowed_hosts(raw_hosts: Sequence[str]) -> frozenset[str]:
    try:
        return normalize_allowed_hosts(raw_hosts)
    except ValueError as exc:
        raise RouteStageLatencyFeedError(str(exc)) from exc


def _validate_headers(headers: Mapping[str, str] | None) -> Mapping[str, str]:
    normalized: dict[str, str] = {
        "Accept": DEFAULT_ACCEPT_HEADER,
        "User-Agent": DEFAULT_USER_AGENT,
    }
    if headers is None:
        return normalized
    if not isinstance(headers, Mapping):
        raise RouteStageLatencyFeedError("HEADER_TYPE_REFUSED")
    for key, value in headers.items():
        if not isinstance(key, str) or not key.strip():
            raise RouteStageLatencyFeedError("HEADER_TYPE_REFUSED")
        if not isinstance(value, str):
            raise RouteStageLatencyFeedError("HEADER_TYPE_REFUSED")
        clean_key = key.strip()
        clean_value = value.strip()
        lowered_key = clean_key.lower()
        if _has_header_injection(clean_key) or _has_header_injection(clean_value):
            raise RouteStageLatencyFeedError("HEADER_CONTROL_REFUSED")
        if (
            lowered_key in _CREDENTIAL_HEADER_NAMES
            or contains_secret_marker_substring(clean_key)
            or lowered_key.endswith("-token")
            or contains_secret_marker_substring(clean_value)
        ):
            raise RouteStageLatencyFeedError("CREDENTIAL_HEADER_REFUSED")
        normalized[clean_key] = clean_value
    return normalized


def _validate_timeout(timeout_seconds: float) -> float:
    if isinstance(timeout_seconds, bool) or not isinstance(
        timeout_seconds,
        (int, float),
    ):
        raise RouteStageLatencyFeedError("TIMEOUT_OUT_OF_RANGE")
    normalized = float(timeout_seconds)
    if (
        not math.isfinite(normalized)
        or normalized <= 0
        or normalized > MAX_TIMEOUT_SECONDS
    ):
        raise RouteStageLatencyFeedError("TIMEOUT_OUT_OF_RANGE")
    return normalized


def _validate_size_cap(max_response_bytes: int) -> int:
    if isinstance(max_response_bytes, bool) or not isinstance(
        max_response_bytes,
        int,
    ):
        raise RouteStageLatencyFeedError("SIZE_CAP_OUT_OF_RANGE")
    if max_response_bytes <= 0 or max_response_bytes > MAX_RESPONSE_BYTES:
        raise RouteStageLatencyFeedError("SIZE_CAP_OUT_OF_RANGE")
    return max_response_bytes


def _validate_interval(
    seconds: float,
    *,
    max_seconds: float,
    error_code: str,
) -> float:
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        raise RouteStageLatencyFeedError(error_code)
    normalized = float(seconds)
    if not math.isfinite(normalized) or normalized <= 0 or normalized > max_seconds:
        raise RouteStageLatencyFeedError(error_code)
    return normalized


def _validate_response(
    response: RouteStageLatencyFeedHttpResponse,
    expected_url: str,
    max_response_bytes: int,
) -> RouteStageLatencyFeedHttpResponse:
    if not isinstance(response, RouteStageLatencyFeedHttpResponse):
        raise RouteStageLatencyFeedError("RESPONSE_SHAPE_REFUSED")
    if isinstance(response.status_code, bool) or not isinstance(
        response.status_code,
        int,
    ):
        raise RouteStageLatencyFeedError("RESPONSE_STATUS_REFUSED")
    if response.status_code < 200 or response.status_code >= 300:
        raise RouteStageLatencyFeedError(f"HTTP_STATUS_{response.status_code}")
    if not isinstance(response.body, bytes):
        raise RouteStageLatencyFeedError("RESPONSE_BODY_REFUSED")
    if len(response.body) > max_response_bytes:
        raise RouteStageLatencyFeedError("RESPONSE_TOO_LARGE")
    if not isinstance(response.content_type, str):
        raise RouteStageLatencyFeedError("RESPONSE_CONTENT_TYPE_REFUSED")
    normalized_content_type = response.content_type.split(";", 1)[0].strip()
    if normalized_content_type.lower() not in _SUPPORTED_CONTENT_TYPES:
        raise RouteStageLatencyFeedError("RESPONSE_CONTENT_TYPE_REFUSED")
    if response.source_url != expected_url:
        raise RouteStageLatencyFeedError("RESPONSE_SOURCE_URL_REFUSED")
    return response


def _prometheus_panel_values(panel_id: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or payload.get("status") != "success":
        raise RouteStageLatencyFeedError("PROMETHEUS_STATUS_REFUSED")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise RouteStageLatencyFeedError("PROMETHEUS_DATA_REFUSED")
    result = data.get("result")
    if not isinstance(result, list):
        raise RouteStageLatencyFeedError("PROMETHEUS_RESULT_REFUSED")

    values = []
    for item in result:
        if not isinstance(item, Mapping):
            continue
        metric = item.get("metric")
        metric = metric if isinstance(metric, Mapping) else {}
        raw_value = item.get("value")
        if not (
            isinstance(raw_value, Sequence)
            and not isinstance(raw_value, (str, bytes))
            and len(raw_value) >= 2
        ):
            continue
        value = _number_or_none(raw_value[1])
        stage = metric.get("stage")
        if value is None or not isinstance(stage, str):
            continue
        values.append(
            {
                "id": panel_id,
                "stage": stage,
                "value": round(value, 3),
            }
        )
    return values


def _alertmanager_active_alerts(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise RouteStageLatencyFeedError("ALERTMANAGER_RESULT_REFUSED")
    active = []
    for item in payload:
        if not isinstance(item, Mapping):
            continue
        labels = item.get("labels")
        labels = labels if isinstance(labels, Mapping) else {}
        status = item.get("status")
        status = status if isinstance(status, Mapping) else {}
        state = status.get("state", item.get("state", "active"))
        alertname = labels.get("alertname", item.get("id"))
        stage = labels.get("stage", item.get("stage"))
        severity = labels.get("severity", item.get("severity", "warning"))
        if not isinstance(alertname, str) or not isinstance(stage, str):
            continue
        active.append(
            {
                "labels": {
                    "alertname": alertname,
                    "stage": stage,
                    "severity": severity if isinstance(severity, str) else "warning",
                },
                "state": state if isinstance(state, str) else "active",
                "value": _number_or_none(item.get("value")),
            }
        )
    return active


def _number_or_none(value: Any) -> float | None:
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_rfc3339(now: Callable[[], datetime] = _utc_now) -> str:
    return now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_reason(reason: str) -> str:
    if not isinstance(reason, str):
        return "FEED_READ_FAILED"
    clean = reason.strip().upper()
    if not clean or len(clean) > 80 or contains_secret_marker_substring(clean):
        return "FEED_READ_FAILED"
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in clean)


def _has_header_injection(value: str) -> bool:
    return "\r" in value or "\n" in value


__all__ = [
    "DEFAULT_CACHE_TTL_SECONDS",
    "DEFAULT_FAILURE_BACKOFF_SECONDS",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "RouteStageLatencyFeedError",
    "RouteStageLatencyFeedHttpResponse",
    "RouteStageLatencyFeedTransport",
    "RouteStageLatencyPrometheusAlertmanagerFeed",
    "UnavailableRouteStageLatencyFeed",
]
