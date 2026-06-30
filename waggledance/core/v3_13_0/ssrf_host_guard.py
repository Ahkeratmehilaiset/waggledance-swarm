# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Shared SSRF host-validation guard for outbound operator-selected fetches.

Both the AIR-01 sensor transport and the ENG-01 price-feed transport fetch an
operator/automation-supplied URL. Refusing a literal private/loopback IP is easy,
but a DNS hostname needs the same guard: a single-label name ("router"), a
private/local-use suffix ("air.internal", "host.lan"), or a cloud metadata name
("metadata.google.internal") can resolve to an internal address. Centralizing the
check here avoids copy-paste divergence between transports (one fixed, one not --
the #1443 / #1451 class of bug).

The guard returns a refusal CODE (or None) rather than raising, so each transport
keeps raising its own typed error. Residual: pinning the resolved IP to defend
DNS-rebinding of a public name onto a private address is a separate, deeper
transport hardening and is out of scope here.
"""
from __future__ import annotations

from ipaddress import ip_address
from typing import Iterable


# Private / local-use DNS suffixes (RFC 6762 .local, RFC 8375 .home.arpa, common
# LAN/enterprise conventions). A name ending in one of these -- or a single-label
# name with no dot -- is treated as local-use and must be explicitly allowlisted.
LOCAL_USE_SUFFIXES = (
    ".local",
    ".internal",
    ".intranet",
    ".lan",
    ".home",
    ".home.arpa",
    ".localdomain",
    ".corp",
    ".private",
)

HOST_REFUSED_LOCAL = "URL_LOCAL_HOST_REFUSED"
HOST_REFUSED_PRIVATE = "URL_PRIVATE_HOST_REFUSED"
# Deny-by-default refusal: the host is not in the operator-configured allowlist.
# Used by transports whose legitimate hosts are a known fixed set (sensor /
# price-feed), so a public FQDN that resolves to a private IP cannot be reached.
HOST_NOT_ALLOWLISTED = "URL_HOST_NOT_ALLOWLISTED"


def normalize_host(host: str) -> str:
    return host.strip().lower().strip("[]")


def is_local_use_hostname(hostname: str) -> bool:
    """True for a single-label name or a private/local-use DNS suffix."""
    name = hostname.strip(".")
    if not name:
        return True
    if "." not in name:
        return True  # single-label hostname (e.g. "router") -> not a public FQDN
    return any(
        hostname == suffix.lstrip(".") or hostname.endswith(suffix)
        for suffix in LOCAL_USE_SUFFIXES
    )


def classify_request_host(
    hostname: str,
    *,
    allowed_private_hosts: Iterable[str] = (),
    require_allowlist: bool = False,
) -> str | None:
    """Classify an outbound request host.

    Returns a refusal code (HOST_NOT_ALLOWLISTED / HOST_REFUSED_LOCAL /
    HOST_REFUSED_PRIVATE) or None if the host is allowed.

    With ``require_allowlist=True`` (deny-by-default) ANY host -- including a
    normal public FQDN -- that is not in ``allowed_private_hosts`` is refused.
    This closes the resolve-to-private / DNS-rebinding vector (a public name that
    resolves to an internal IP) without runtime resolution, for transports whose
    legitimate hosts are a known fixed set. With the default ``require_allowlist=
    False`` the allowlist is an override: loopback / link-local / unspecified /
    private IPs and local-use DNS names are refused unless allowlisted, but a
    public FQDN / public IP is allowed.
    """
    normalized = normalize_host(hostname)
    allowed = {normalize_host(h) for h in allowed_private_hosts}
    if not normalized:
        return HOST_REFUSED_LOCAL
    if normalized in allowed:
        return None
    if require_allowlist:
        # deny-by-default: not explicitly allowlisted -> refuse (public included)
        return HOST_NOT_ALLOWLISTED
    if normalized in {"localhost", "localhost."} or normalized.endswith(
        ".localhost"
    ):
        return HOST_REFUSED_LOCAL
    try:
        parsed_ip = ip_address(normalized)
    except ValueError:
        if is_local_use_hostname(normalized):
            return HOST_REFUSED_LOCAL
        return None
    if parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_unspecified:
        return HOST_REFUSED_LOCAL
    if parsed_ip.is_private:
        return HOST_REFUSED_PRIVATE
    return None
