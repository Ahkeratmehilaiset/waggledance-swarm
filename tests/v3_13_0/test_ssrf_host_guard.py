# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from waggledance.core.v3_13_0.ssrf_host_guard import (
    HOST_NOT_ALLOWLISTED,
    HOST_REFUSED_LOCAL,
    HOST_REFUSED_PRIVATE,
    classify_request_host,
    is_local_use_hostname,
)


@pytest.mark.parametrize("hostname", [
    "metadata.google.internal",   # cloud metadata endpoint
    "air.internal",
    "host.lan",
    "sensor.local",
    "box.home.arpa",
    "thing.corp",
    "router",                     # single-label
    "localhost",
    "host.localhost",
])
def test_local_use_names_refused_without_allowlist(hostname):
    assert classify_request_host(hostname) == HOST_REFUSED_LOCAL


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1",           # loopback
    "169.254.169.254",            # link-local (AWS/GCP metadata)
    "0.0.0.0", "::",              # unspecified
])
def test_loopback_linklocal_unspecified_refused(ip):
    assert classify_request_host(ip) == HOST_REFUSED_LOCAL


@pytest.mark.parametrize("ip", ["192.168.1.10", "10.0.0.5", "172.16.4.4"])
def test_private_ips_refused(ip):
    assert classify_request_host(ip) == HOST_REFUSED_PRIVATE


@pytest.mark.parametrize("hostname", [
    "prices.example.test",
    "api.entsoe.eu",
    "aq.example.com",
    "8.8.8.8",                    # public IP
])
def test_public_hosts_allowed(hostname):
    assert classify_request_host(hostname) is None


def test_allowlist_overrides_refusal():
    assert classify_request_host("air.internal") == HOST_REFUSED_LOCAL
    assert classify_request_host(
        "air.internal", allowed_private_hosts=("air.internal",)
    ) is None
    assert classify_request_host(
        "192.168.1.44", allowed_private_hosts=("192.168.1.44",)
    ) is None


def test_allowlist_is_normalized():
    # case/bracket-insensitive match against the normalized host
    assert classify_request_host(
        "AIR.INTERNAL", allowed_private_hosts=("air.internal",)
    ) is None


def test_empty_host_refused():
    assert classify_request_host("") == HOST_REFUSED_LOCAL


def test_is_local_use_hostname_classification():
    assert is_local_use_hostname("air.internal") is True
    assert is_local_use_hostname("router") is True
    assert is_local_use_hostname("metadata.google.internal") is True
    assert is_local_use_hostname("prices.example.test") is False
    assert is_local_use_hostname("aq.example.com") is False


def test_require_allowlist_denies_public_host_unless_allowlisted():
    # Deny-by-default: even a normal public FQDN is refused without an allowlist
    # entry, and only an exact allowlist match lets it through.
    assert classify_request_host(
        "evil.attacker.com", require_allowlist=True
    ) == HOST_NOT_ALLOWLISTED
    assert classify_request_host(
        "evil.attacker.com",
        allowed_private_hosts=("evil.attacker.com",),
        require_allowlist=True,
    ) is None


def test_trailing_dot_does_not_bypass_local_use_or_allowlist():
    # SSRF regression (tools #1454): a trailing DNS root dot must NOT evade the
    # local-use refusal, and must match the allowlist as the same host.
    assert classify_request_host("air.internal.") == HOST_REFUSED_LOCAL
    assert classify_request_host("metadata.google.internal.") == HOST_REFUSED_LOCAL
    assert classify_request_host(
        "evil.attacker.com.", require_allowlist=True
    ) == HOST_NOT_ALLOWLISTED
    # trailing dot still matches an allowlisted host (no false-negative)
    assert classify_request_host(
        "air.example.test.",
        allowed_private_hosts=("air.example.test",),
        require_allowlist=True,
    ) is None
