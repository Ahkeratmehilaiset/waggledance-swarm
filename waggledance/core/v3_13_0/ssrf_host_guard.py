# SPDX-License-Identifier: BUSL-1.1
# SPDX-FileCopyrightText: Jani Korpi / Ahkerat Mehilaiset / JKH Service
"""Shared SSRF host-validation guard for outbound operator-selected fetches."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from ipaddress import ip_address


HOST_REFUSED_LOCAL = "URL_LOCAL_HOST_REFUSED"
HOST_REFUSED_NOT_ALLOWLISTED = "URL_HOST_NOT_ALLOWLISTED"
HOST_REFUSED_PRIVATE = "URL_PRIVATE_HOST_REFUSED"


def normalize_host(host: str) -> str:
    """Return the canonical host string used for exact allowlist checks."""
    return host.strip().lower().strip("[]").rstrip(".")


def normalize_allowed_hosts(raw_hosts: Iterable[str]) -> frozenset[str]:
    """Validate and canonicalize an exact-host allowlist."""
    if (
        not isinstance(raw_hosts, Iterable)
        or isinstance(raw_hosts, (str, bytes, Mapping))
    ):
        raise ValueError("ALLOWLIST_HOSTS_REFUSED")
    normalized: set[str] = set()
    for index, host in enumerate(raw_hosts):
        if not isinstance(host, str) or not host.strip():
            raise ValueError(f"ALLOWLIST_HOSTS_REFUSED_{index}")
        clean = normalize_host(host)
        if "://" in clean or "/" in clean or "?" in clean or "@" in clean:
            raise ValueError(f"ALLOWLIST_HOSTS_REFUSED_{index}")
        normalized.add(clean)
    return frozenset(normalized)


def classify_request_host(
    hostname: str,
    *,
    allowed_hosts: Iterable[str] = (),
    require_allowlist: bool = True,
) -> str | None:
    """Return a refusal code for an outbound host, or ``None`` when allowed."""
    normalized = normalize_host(hostname)
    allowed = normalize_allowed_hosts(allowed_hosts)
    if not normalized:
        return HOST_REFUSED_LOCAL
    if normalized in allowed:
        return None
    try:
        parsed_ip = ip_address(normalized)
    except ValueError:
        if _is_localhost_name(normalized):
            return HOST_REFUSED_LOCAL
        if require_allowlist:
            return HOST_REFUSED_NOT_ALLOWLISTED
        return None
    if parsed_ip.is_loopback or parsed_ip.is_link_local or parsed_ip.is_unspecified:
        return HOST_REFUSED_LOCAL
    if parsed_ip.is_private:
        return HOST_REFUSED_PRIVATE
    if require_allowlist:
        return HOST_REFUSED_NOT_ALLOWLISTED
    return None


def _is_localhost_name(normalized: str) -> bool:
    return normalized == "localhost" or normalized.endswith(".localhost")


__all__ = [
    "HOST_REFUSED_LOCAL",
    "HOST_REFUSED_NOT_ALLOWLISTED",
    "HOST_REFUSED_PRIVATE",
    "classify_request_host",
    "normalize_allowed_hosts",
    "normalize_host",
]
