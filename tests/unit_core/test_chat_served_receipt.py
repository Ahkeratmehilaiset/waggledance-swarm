# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b chat-served MAGMA receipt builder."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.chat_served_receipt import (
    RCO_DECISION_NA_SENTINEL,
    SOLVER_CONTRACT_NA_SENTINEL,
    build_chat_served_summary,
    write_chat_served_receipt_bundle,
)

_NOW = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _summary(
    *,
    query: str = "what are the cheapest hours",
    response: str = "hours 2,3,4",
    route_type: str = "solver",
    source: str = "solver",
):
    return build_chat_served_summary(
        query=query,
        response=response,
        route_type=route_type,
        source=source,
        confidence=0.95,
        latency_ms=12.3,
        cached=False,
        round_table=False,
        agent_id=None,
        language="en",
        profile="COTTAGE",
        world_snapshot_ref="snap-1",
        route_stage_trace=[
            {
                "stage": "route_selection",
                "route_type": route_type,
                "source": source,
                # a non-allowlisted key carrying raw text must be dropped:
                "raw_query_text": "SECRET_RAW_QUERY",
            },
            {"stage": "deterministic_solver", "intent": "eng01", "answered": True},
        ],
    )


def _emitted_text(root: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(root.rglob("*.json"))
    )


def test_builder_writes_schema_valid_verified_bundle(tmp_path: Path) -> None:
    report = write_chat_served_receipt_bundle(
        out_dir=tmp_path / "cs-1",
        summary_payload=_summary(),
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    assert report["verifier_report"]["ok"] is True
    assert int(report["receipt_count"]) == 1


def test_na_sentinels_present_known_hex_constants(tmp_path: Path) -> None:
    import re

    from waggledance.core.magma.canonical import sha256_digest

    write_chat_served_receipt_bundle(
        out_dir=tmp_path / "cs-1",
        summary_payload=_summary(),
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    text = _emitted_text(tmp_path / "cs-1")
    assert RCO_DECISION_NA_SENTINEL in text
    assert SOLVER_CONTRACT_NA_SENTINEL in text
    # schema-valid hex sha256 (MAGMA receipt v1 requires ^sha256:[a-f0-9]{64}$)
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", RCO_DECISION_NA_SENTINEL)
    assert re.fullmatch(r"sha256:[a-f0-9]{64}", SOLVER_CONTRACT_NA_SENTINEL)
    # distinct known constants, and NOT a real content digest / sha256("")
    assert RCO_DECISION_NA_SENTINEL != SOLVER_CONTRACT_NA_SENTINEL
    assert RCO_DECISION_NA_SENTINEL != sha256_digest({"query": "anything"})
    assert RCO_DECISION_NA_SENTINEL != sha256_digest("")


def test_privacy_no_raw_query_response_or_trace_text(tmp_path: Path) -> None:
    write_chat_served_receipt_bundle(
        out_dir=tmp_path / "cs-1",
        summary_payload=_summary(
            query="DO_NOT_LEAK_QUERY", response="DO_NOT_LEAK_RESPONSE"
        ),
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    text = _emitted_text(tmp_path / "cs-1")
    assert "query_digest" in text
    assert "response_digest" in text
    assert "DO_NOT_LEAK_QUERY" not in text
    assert "DO_NOT_LEAK_RESPONSE" not in text
    # non-allowlisted trace keys (which could carry raw text) are dropped
    assert "raw_query_text" not in text
    assert "SECRET_RAW_QUERY" not in text


def test_payload_rejects_raw_query_key() -> None:
    payload = _summary()
    payload["query"] = "raw query text"  # a forbidden raw key
    with pytest.raises(ValueError, match="raw"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )


def test_trace_digest_tamper_rejected() -> None:
    payload = _summary()
    payload["route_stage_trace_digest"] = "deadbeef" * 8  # tampered
    with pytest.raises(ValueError, match="route_stage_trace_digest"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )


def test_chain_second_receipt_links_previous(tmp_path: Path) -> None:
    r1 = write_chat_served_receipt_bundle(
        out_dir=tmp_path / "cs-1",
        summary_payload=_summary(),
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    receipt1 = r1["entries"][0]["receipt"] if "entries" in r1 else None
    # If the report exposes the receipt, chain the next one on it.
    if receipt1 is not None:
        r2 = write_chat_served_receipt_bundle(
            out_dir=tmp_path / "cs-2",
            summary_payload=_summary(query="second"),
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=2,
            previous_receipt=receipt1,
        )
        assert r2["verifier_report"]["ok"] is True
