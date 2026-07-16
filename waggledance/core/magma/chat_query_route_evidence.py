# SPDX-License-Identifier: BUSL-1.1
"""Canonical identities shared by chat route-evidence producers."""

from __future__ import annotations

import unicodedata

from waggledance.core.magma.canonical import sha256_digest

NORMALIZATION_VERSION = "wd.chat_query_normalization.v1"
QUERY_DIGEST_DOMAIN = "wd.chat_query_route_evidence.query_digest.v1"


def canonical_query_digest(query: str) -> str:
    """Return the versioned route-evidence identity for a raw chat query."""
    normalized_query = unicodedata.normalize("NFC", query).strip()
    return sha256_digest(
        {
            "domain": QUERY_DIGEST_DOMAIN,
            "normalization_version": NORMALIZATION_VERSION,
            "normalized_query": normalized_query,
        }
    )


__all__ = [
    "NORMALIZATION_VERSION",
    "QUERY_DIGEST_DOMAIN",
    "canonical_query_digest",
]
