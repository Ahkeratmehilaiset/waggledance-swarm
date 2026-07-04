# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from waggledance.core.magma.chat_served_claim_window_evidence import (
    SCHEMA_VERSION,
    build_chat_served_claim_window_evidence,
    evidence_digest,
)


FIXED_NOW = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)


def test_build_chat_served_evidence_is_path_and_payload_free() -> None:
    raw_query = "private hive query DO_NOT_LEAK"
    evidence = build_chat_served_claim_window_evidence(
        query=raw_query,
        language="PRIVATE_LANGUAGE_MARKER",
        profile="PRIVATE_PROFILE_MARKER",
        source="llm",
        confidence=0.81234,
        latency_ms=12.345,
        cached=False,
        round_table=False,
        route_stage_trace=[
            {"stage": "language_detection", "detected_language": "custom"},
            {"stage": "hot_cache", "hit": False},
            {"stage": "orchestrator_llm_fallback", "source": "llm"},
        ],
        served_at_utc=FIXED_NOW,
    )

    assert evidence["schema_version"] == SCHEMA_VERSION
    assert evidence["served"] is True
    assert evidence["window"] == {
        "kind": "single_chat_request",
        "query_count": 1,
        "served_count": 1,
        "served_ratio": 1.0,
    }
    assert evidence["query_digest"].startswith("sha256:")
    assert evidence["query_length"] == len(raw_query)
    assert evidence["language"] == "custom"
    assert evidence["profile"] == "custom"
    assert evidence["confidence"] == 0.8123
    assert evidence["confidence_bucket"] == "high"
    assert evidence["latency_ms"] == 12.35
    assert evidence["route_stage_names"] == [
        "language_detection",
        "hot_cache",
        "orchestrator_llm_fallback",
    ]
    assert evidence["route_depth"] == 3
    assert evidence["payloads_returned"] is False
    assert evidence["paths_returned"] is False
    assert evidence["runtime_authority_changed"] is False
    assert evidence["default_runtime_receipt_emission_changed"] is False

    serialized = json.dumps(evidence, sort_keys=True)
    assert raw_query not in serialized
    assert "DO_NOT_LEAK" not in serialized
    assert "PRIVATE_LANGUAGE_MARKER" not in serialized
    assert "PRIVATE_PROFILE_MARKER" not in serialized


def test_evidence_digest_is_canonical() -> None:
    evidence = build_chat_served_claim_window_evidence(
        query="same query",
        language="en",
        profile="HOME",
        source="hotcache",
        confidence=1.0,
        latency_ms=1.0,
        cached=True,
        round_table=False,
        route_stage_trace=[{"stage": "hot_cache"}],
        served_at_utc=FIXED_NOW,
    )

    assert evidence_digest(dict(reversed(list(evidence.items())))) == (
        evidence_digest(evidence)
    )


def test_unsafe_route_stage_label_is_rejected() -> None:
    with pytest.raises(ValueError, match="safe label"):
        build_chat_served_claim_window_evidence(
            query="secret",
            language="en",
            profile="HOME",
            source="llm",
            confidence=0.8,
            latency_ms=1.0,
            cached=False,
            round_table=False,
            route_stage_trace=[{"stage": "raw query: secret"}],
            served_at_utc=FIXED_NOW,
        )
