# SPDX-License-Identifier: BUSL-1.1
"""Sanitized evidence that a chat request was served during a claim window."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
import re
from typing import Any

from waggledance.core.magma.canonical import sha256_digest


SCHEMA_VERSION = "waggledance.chat_served_claim_window_evidence.v1"
KNOWN_PUBLIC_LANGUAGES = frozenset({"en", "fi"})
KNOWN_PUBLIC_PROFILES = frozenset({"COTTAGE", "FACTORY", "GADGET", "HOME", "TEST"})
_SAFE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def build_chat_served_claim_window_evidence(
    *,
    query: str,
    language: str,
    profile: str,
    source: str,
    confidence: float,
    latency_ms: float,
    cached: bool,
    round_table: bool,
    route_stage_trace: Sequence[Mapping[str, Any]] | None,
    served_at_utc: datetime | None = None,
) -> dict[str, Any]:
    """Build a path-free, payload-free single-request served evidence record.

    The raw query is reduced to digest + length. Route traces keep only safe
    stage labels, so callers can count served windows without exporting payloads.
    """

    stages = _route_stage_names(route_stage_trace or [])
    served_at = _iso(served_at_utc or datetime.now(timezone.utc))
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_kind": "chat_served_claim_window",
        "runtime_path": "ChatService.handle",
        "window": {
            "kind": "single_chat_request",
            "query_count": 1,
            "served_count": 1,
            "served_ratio": 1.0,
        },
        "served": True,
        "served_at_utc": served_at,
        "source": _safe_source(source),
        "confidence": round(float(confidence), 4),
        "confidence_bucket": _confidence_bucket(confidence),
        "latency_ms": round(float(latency_ms), 2),
        "query_digest": sha256_digest({"query": str(query)}),
        "query_length": len(str(query)),
        "language": language if language in KNOWN_PUBLIC_LANGUAGES else "custom",
        "profile": profile if profile in KNOWN_PUBLIC_PROFILES else "custom",
        "cached": bool(cached),
        "round_table": bool(round_table),
        "route_stage_names": stages,
        "route_depth": len(stages),
        "raw_query_recorded": False,
        "payloads_returned": False,
        "paths_returned": False,
        "runtime_authority_changed": False,
        "default_runtime_receipt_emission_changed": False,
    }


def evidence_digest(evidence: Mapping[str, Any]) -> str:
    """Return a canonical digest for a served evidence record."""

    return sha256_digest(dict(evidence))


def _route_stage_names(
    route_stage_trace: Sequence[Mapping[str, Any]],
) -> list[str]:
    stages: list[str] = []
    for item in route_stage_trace:
        if not isinstance(item, Mapping):
            raise ValueError("route_stage_trace entries must be mappings")
        stage = str(item.get("stage") or "")
        if not _SAFE_TOKEN_RE.fullmatch(stage):
            raise ValueError("route_stage_trace stage is not a safe label")
        stages.append(stage)
    return stages


def _safe_source(source: str) -> str:
    value = str(source or "unknown").strip().lower()
    value = re.sub(r"[^a-z0-9_]+", "_", value).strip("_")
    if not value or not _SAFE_TOKEN_RE.fullmatch(value):
        return "unknown"
    return value


def _confidence_bucket(confidence: float) -> str:
    value = float(confidence)
    if value >= 0.8:
        return "high"
    if value >= 0.6:
        return "medium"
    return "low"


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
