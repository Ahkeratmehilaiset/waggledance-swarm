# SPDX-License-Identifier: BUSL-1.1
"""Tests for the shared outbound host SSRF guard."""
from __future__ import annotations

import pytest

from waggledance.core.v3_13_0.ssrf_host_guard import (
    HOST_REFUSED_LOCAL,
    HOST_REFUSED_NOT_ALLOWLISTED,
    HOST_REFUSED_PRIVATE,
    classify_request_host,
    normalize_allowed_hosts,
    normalize_host,
)


def test_normalizes_terminal_dns_root_dot_for_exact_allowlist() -> None:
    assert normalize_host("Sensor.Example.Test.") == "sensor.example.test"
    assert normalize_allowed_hosts(("sensor.example.test.",)) == frozenset({
        "sensor.example.test",
    })
    assert classify_request_host(
        "sensor.example.test.",
        allowed_hosts=("sensor.example.test",),
    ) is None


@pytest.mark.parametrize("host", [
    "sensor.example.test",
    "1.2.3.4",
])
def test_refuses_non_allowlisted_public_hosts_by_default(host: str) -> None:
    assert classify_request_host(host) == HOST_REFUSED_NOT_ALLOWLISTED


@pytest.mark.parametrize("host", [
    "localhost",
    "api.localhost.",
    "127.0.0.1",
    "::1",
    "0.0.0.0",
    "::",
])
def test_refuses_local_hosts_before_public_allowlist_code(host: str) -> None:
    assert classify_request_host(host) == HOST_REFUSED_LOCAL


@pytest.mark.parametrize("host", [
    "10.0.0.1",
    "172.16.0.5",
    "192.168.1.10",
])
def test_refuses_private_ip_hosts_before_public_allowlist_code(host: str) -> None:
    assert classify_request_host(host) == HOST_REFUSED_PRIVATE


def test_legacy_public_mode_still_rejects_local_or_private_hosts() -> None:
    assert classify_request_host(
        "sensor.example.test",
        require_allowlist=False,
    ) is None
    assert classify_request_host(
        "localhost",
        require_allowlist=False,
    ) == HOST_REFUSED_LOCAL
    assert classify_request_host(
        "10.0.0.1",
        require_allowlist=False,
    ) == HOST_REFUSED_PRIVATE
