# SPDX-License-Identifier: BUSL-1.1
"""Read-only Alertmanager feed for low-risk autogrowth alerts."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from waggledance.adapters.http.magma_handoff_metrics_alert_feed import (
    MagmaHandoffMetricsAlertFeedError,
    MagmaHandoffMetricsAlertFeedHttpResponse,
    MagmaHandoffMetricsAlertFeedTransport,
    MagmaHandoffMetricsAlertmanagerFeed,
    UnavailableMagmaHandoffMetricsAlertFeed,
)


DEFAULT_USER_AGENT = "waggledance-autogrowth-alert-feed/3.8"

AutogrowthAlertFeedError = MagmaHandoffMetricsAlertFeedError
AutogrowthAlertFeedHttpResponse = MagmaHandoffMetricsAlertFeedHttpResponse
AutogrowthAlertFeedTransport = MagmaHandoffMetricsAlertFeedTransport


class UnavailableAutogrowthAlertFeed(UnavailableMagmaHandoffMetricsAlertFeed):
    """Provider object that makes config errors visible as feed unavailable."""

    def snapshot(self) -> dict[str, Any]:
        raise AutogrowthAlertFeedError("AUTOGROWTH_ALERT_FEED_UNAVAILABLE")

    def provider_health(self) -> dict[str, Any]:
        health = super().provider_health()
        health["last_failure_reason"] = "AUTOGROWTH_ALERT_FEED_UNAVAILABLE"
        return health


class AutogrowthAlertmanagerFeed(MagmaHandoffMetricsAlertmanagerFeed):
    """Fetch low-risk autogrowth alert state from operator Alertmanager."""

    def __init__(
        self,
        *,
        alertmanager_base_url: str | None = None,
        timeout_seconds: float = 3.0,
        max_response_bytes: int = 1_000_000,
        cache_ttl_seconds: float = 30.0,
        failure_backoff_seconds: float = 30.0,
        allowed_private_hosts=(),  # noqa: ANN001
        headers: Mapping[str, str] | None = None,
        transport: AutogrowthAlertFeedTransport | None = None,
        monotonic=None,  # noqa: ANN001
        utc_now=None,  # noqa: ANN001
    ) -> None:
        merged_headers = {"User-Agent": DEFAULT_USER_AGENT}
        if headers:
            merged_headers.update(headers)
        super().__init__(
            alertmanager_base_url=alertmanager_base_url,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
            cache_ttl_seconds=cache_ttl_seconds,
            failure_backoff_seconds=failure_backoff_seconds,
            allowed_private_hosts=allowed_private_hosts,
            headers=merged_headers,
            transport=transport,
            monotonic=monotonic,
            utc_now=utc_now,
        )

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        transport: AutogrowthAlertFeedTransport | None = None,
    ) -> "AutogrowthAlertmanagerFeed":
        if not isinstance(config, Mapping):
            raise AutogrowthAlertFeedError("CONFIG_REFUSED")
        return cls(
            alertmanager_base_url=(
                config.get("alertmanager_base_url")
                or config.get("alertmanager_url")
            ),
            timeout_seconds=config.get("timeout_s", 3.0),
            max_response_bytes=config.get("max_response_bytes", 1_000_000),
            cache_ttl_seconds=config.get(
                "cache_ttl_s",
                config.get("cache_ttl_seconds", 30.0),
            ),
            failure_backoff_seconds=config.get(
                "failure_backoff_s",
                config.get("failure_backoff_seconds", 30.0),
            ),
            allowed_private_hosts=config.get("allowed_private_hosts", ()),
            headers=config.get("headers"),
            transport=transport,
        )


__all__ = [
    "AutogrowthAlertFeedError",
    "AutogrowthAlertFeedHttpResponse",
    "AutogrowthAlertFeedTransport",
    "AutogrowthAlertmanagerFeed",
    "UnavailableAutogrowthAlertFeed",
]
