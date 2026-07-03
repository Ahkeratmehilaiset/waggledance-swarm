# SPDX-License-Identifier: BUSL-1.1
"""Tests for the P2 S1b chat-served MAGMA receipt builder."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.chat_served_receipt import (
    RCO_DECISION_NA_SENTINEL,
    SOLVER_CONTRACT_NA_SENTINEL,
    build_chat_served_receipt,
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


def test_genesis_bundle_exposes_receipt(tmp_path: Path) -> None:
    r1 = write_chat_served_receipt_bundle(
        out_dir=tmp_path / "cs-1",
        summary_payload=_summary(),
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    assert r1["verifier_report"]["ok"] is True
    assert r1["receipt"]["prev_receipt_hash"] is None  # a persisted bundle is genesis


def test_chain_second_receipt_links_previous() -> None:
    from waggledance.core.magma.canonical import sha256_digest

    _, _, receipt1 = build_chat_served_receipt(
        summary_payload=_summary(), now_utc=_NOW, ordinal=1
    )
    assert receipt1["prev_receipt_hash"] is None  # genesis

    _, _, receipt2 = build_chat_served_receipt(
        summary_payload=_summary(query="second"),
        now_utc=_NOW,
        ordinal=2,
        previous_receipt=receipt1,
    )
    # The chain link is REAL: the second receipt binds the first by its hash
    # (this assertion actually executes now -- the old test gated on a key the
    # writer never returned, so it never ran).
    assert receipt2["prev_receipt_hash"] == sha256_digest(receipt1)
    assert receipt2["prev_receipt_hash"] is not None


# ── adversarial edge inputs (lead + rco-1 #1496 blockers) ──


def test_malformed_query_digest_rejected() -> None:
    payload = _summary()
    payload["query_digest"] = "not-a-sha"  # not a sha256-shaped digest
    with pytest.raises(ValueError, match="query_digest"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )


def test_malformed_response_digest_rejected() -> None:
    payload = _summary()
    # correct prefix/length but non-hex characters
    payload["response_digest"] = "sha256:" + "z" * 64
    with pytest.raises(ValueError, match="response_digest"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )


def test_builder_clamps_out_of_range_confidence() -> None:
    high = build_chat_served_summary(
        query="q", response="r", route_type="solver", source="solver",
        confidence=1.5, latency_ms=1.0, cached=False, round_table=False,
        agent_id=None, language="en", profile="COTTAGE",
        world_snapshot_ref="snap-1", route_stage_trace=[],
    )
    assert high["confidence"] == 1.0
    low = build_chat_served_summary(
        query="q", response="r", route_type="solver", source="solver",
        confidence=-0.1, latency_ms=1.0, cached=False, round_table=False,
        agent_id=None, language="en", profile="COTTAGE",
        world_snapshot_ref="snap-1", route_stage_trace=[],
    )
    assert low["confidence"] == 0.0


def test_out_of_range_confidence_in_payload_rejected() -> None:
    payload = _summary()
    payload["confidence"] = 1.5  # injected out-of-range provenance value
    with pytest.raises(ValueError, match="confidence"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )


def test_nested_object_trace_value_dropped_no_leak(tmp_path: Path) -> None:
    summary = build_chat_served_summary(
        query="q", response="r", route_type="solver", source="solver",
        confidence=0.9, latency_ms=1.0, cached=False, round_table=False,
        agent_id=None, language="en", profile="COTTAGE",
        world_snapshot_ref="snap-1",
        route_stage_trace=[
            {
                "stage": "route_selection",
                # an ALLOWLISTED key whose value is a NESTED object with raw text
                "source": {"nested_raw": "SECRET_NESTED_LEAK"},
            }
        ],
    )
    # the non-scalar value is DROPPED, never str()'d into the payload
    assert "SECRET_NESTED_LEAK" not in json.dumps(summary)
    write_chat_served_receipt_bundle(
        out_dir=tmp_path / "cs-1",
        summary_payload=summary,
        now_utc=_NOW,
        verify_manifest=verify_manifest,
        ordinal=1,
    )
    assert "SECRET_NESTED_LEAK" not in _emitted_text(tmp_path / "cs-1")


@pytest.mark.parametrize(
    "extra_key", ["raw_query_text", "raw_response_text", "prompt", "user_message"]
)
def test_payload_rejects_unexpected_top_level_key(extra_key: str) -> None:
    # A wholesale-copied payload must not smuggle any non-allowlisted top-level
    # key (raw-content or otherwise) into the persisted receipt.
    payload = _summary()
    payload[extra_key] = "SECRET_RAW_SMUGGLED"
    with pytest.raises(ValueError, match="unexpected top-level key"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )


def test_smuggled_raw_key_is_rejected_before_any_bundle_is_written(
    tmp_path: Path,
) -> None:
    # tools' exact repro: raw_query_text previously persisted while
    # verify_manifest reported ok. It must now be rejected before write.
    payload = _summary()
    payload["raw_query_text"] = "SECRET_RAW_SMUGGLED"
    out = tmp_path / "cs-leak"
    with pytest.raises(ValueError, match="unexpected top-level key"):
        write_chat_served_receipt_bundle(
            out_dir=out,
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )
    assert not out.exists()  # nothing was persisted


def test_digest_semantics_rejects_nested_extra_raw_key_before_write(
    tmp_path: Path,
) -> None:
    payload = _summary()
    payload["digest_semantics"] = dict(payload["digest_semantics"])
    payload["digest_semantics"]["raw_leak"] = "SECRET_IN_DIGEST_SEMANTICS"
    out = tmp_path / "cs-digest-semantics-leak"
    with pytest.raises(ValueError, match="digest_semantics"):
        write_chat_served_receipt_bundle(
            out_dir=out,
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )
    assert not out.exists()


def test_digest_semantics_rejects_tampered_known_value() -> None:
    payload = _summary()
    payload["digest_semantics"] = dict(payload["digest_semantics"])
    payload["digest_semantics"]["rco_decision_digest"] = "real:fake_gate"
    with pytest.raises(ValueError, match="digest_semantics"):
        write_chat_served_receipt_bundle(
            out_dir=Path("unused"),
            summary_payload=payload,
            now_utc=_NOW,
            verify_manifest=verify_manifest,
            ordinal=1,
        )
