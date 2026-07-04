# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json

from waggledance.core.magma.chat_served_emitter import (
    ChatServedEvidenceEmitter,
    JsonlChatServedEvidenceSink,
)


FIXED_NOW = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)


def _emit_kwargs() -> dict:
    return {
        "query": "private emitted query DO_NOT_LEAK",
        "language": "en",
        "profile": "HOME",
        "source": "llm",
        "confidence": 0.8,
        "latency_ms": 42.0,
        "cached": False,
        "round_table": False,
        "route_stage_trace": [{"stage": "orchestrator_llm_fallback"}],
        "served_at_utc": FIXED_NOW,
    }


def test_emitter_without_sink_reports_default_off_no_payload_returned() -> None:
    report = ChatServedEvidenceEmitter().emit(**_emit_kwargs())

    assert report["emitted"] is False
    assert report["sink_configured"] is False
    assert report["evidence_digest"].startswith("sha256:")
    assert report["paths_returned"] is False
    assert report["payloads_returned"] is False
    assert report["default_chat_served_evidence_emission_changed"] is False
    assert report["runtime_authority_changed"] is False
    assert "DO_NOT_LEAK" not in json.dumps(report, sort_keys=True)


def test_emitter_sends_sanitized_evidence_to_sink() -> None:
    captured: list[dict] = []
    emitter = ChatServedEvidenceEmitter(sink=lambda evidence: captured.append(dict(evidence)))

    report = emitter.emit(**_emit_kwargs())

    assert report["emitted"] is True
    assert report["sink_configured"] is True
    assert len(captured) == 1
    assert captured[0]["query_digest"].startswith("sha256:")
    serialized_payload = json.dumps(captured[0], sort_keys=True)
    assert "private emitted query" not in serialized_payload
    assert "DO_NOT_LEAK" not in serialized_payload
    assert "DO_NOT_LEAK" not in json.dumps(report, sort_keys=True)


def test_emitter_hashes_sink_errors_without_leaking_error_text() -> None:
    def broken_sink(_evidence):
        raise RuntimeError("disk path C:\\private\\served.jsonl DO_NOT_LEAK")

    report = ChatServedEvidenceEmitter(sink=broken_sink).emit(**_emit_kwargs())

    assert report["emitted"] is False
    assert report["sink_configured"] is True
    assert report["error"].startswith("sink_error:")
    serialized = json.dumps(report, sort_keys=True)
    assert "C:\\private" not in serialized
    assert "DO_NOT_LEAK" not in serialized


def test_jsonl_sink_writes_sanitized_record_and_returns_path_free_result(tmp_path):
    out_path = tmp_path / "served" / "evidence.jsonl"
    emitter = ChatServedEvidenceEmitter(sink=JsonlChatServedEvidenceSink(out_path))

    report = emitter.emit(**_emit_kwargs())

    assert report["emitted"] is True
    text = out_path.read_text(encoding="utf-8")
    assert "query_digest" in text
    assert "private emitted query" not in text
    assert "DO_NOT_LEAK" not in text
    assert str(tmp_path) not in json.dumps(report, sort_keys=True)
