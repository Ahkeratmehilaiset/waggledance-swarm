# SPDX-License-Identifier: BUSL-1.1
"""Response normalizer — Phase 9 §J.

Normalizes raw provider responses into a uniform ProviderResponse
shape for the distillation pipeline. NEVER directly mutates self/world.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import PROVIDER_PLANE_SCHEMA_VERSION, TRUST_LAYERS


@dataclass(frozen=True)
class ProviderResponse:
    schema_version: int
    response_id: str
    request_id: str
    provider_used: str
    raw_payload: dict
    ts_iso: str
    latency_ms: float
    trust_layer_state: str
    no_direct_mutation: bool

    def __post_init__(self) -> None:
        if self.trust_layer_state not in TRUST_LAYERS:
            raise ValueError(
                f"unknown trust_layer_state: {self.trust_layer_state!r}; "
                f"allowed: {TRUST_LAYERS}"
            )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "response_id": self.response_id,
            "request_id": self.request_id,
            "provider_used": self.provider_used,
            "raw_payload": dict(self.raw_payload),
            "ts_iso": self.ts_iso,
            "latency_ms": self.latency_ms,
            "trust_layer_state": self.trust_layer_state,
            "no_direct_mutation": self.no_direct_mutation,
        }


def compute_response_id(request_id: str, provider_used: str,
                              ts_iso: str) -> str:
    canonical = json.dumps({
        "request_id": request_id, "provider_used": provider_used,
        "ts_iso": ts_iso,
    }, sort_keys=True, separators=(",", ":"))
    return "resp_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:10]


def normalize(*,
                  request_id: str,
                  provider_used: str,
                  raw_payload: dict,
                  ts_iso: str,
                  latency_ms: float = 0.0,
                  ) -> ProviderResponse:
    return ProviderResponse(
        schema_version=PROVIDER_PLANE_SCHEMA_VERSION,
        response_id=compute_response_id(request_id, provider_used, ts_iso),
        request_id=request_id, provider_used=provider_used,
        raw_payload=dict(raw_payload), ts_iso=ts_iso,
        latency_ms=latency_ms,
        trust_layer_state="raw_quarantine",   # ALWAYS starts here
        no_direct_mutation=True,
    )
