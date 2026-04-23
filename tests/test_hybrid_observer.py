"""Tests for HybridObserver — candidate-mode MAGMA trace."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from waggledance.core.reasoning.hybrid_observer import HybridObserver, HybridCandidateTrace


class FakeHit:
    def __init__(self, solver_id, score, cell=None, text=""):
        self.metadata = {"canonical_solver_id": solver_id}
        self.score = score
        self.cell_id = cell
        self.text = text


class FakeTraceResult:
    def __init__(self, hits):
        self.hits = hits


class FakeHybridService:
    """Stand-in HybridRetrievalService for tests."""
    def __init__(self, enabled=True, mode="candidate", min_score=0.60, hits=None):
        self.enabled = enabled
        self.mode = mode
        self.min_score = min_score
        self._hits = hits or []

    async def retrieve(self, query, intent="chat", k=5):
        return FakeTraceResult(self._hits)


HEATING_COST = {
    "primary_value": {"name": "daily_cost", "type": "number", "unit": "EUR"},
    "comparable_fields": [{"name": "daily_cost", "unit": "EUR"}],
    "output_mode": "numeric",
}

SPECS = {"heating_cost": HEATING_COST}


def test_observer_skips_when_hybrid_disabled(tmp_path):
    svc = FakeHybridService(enabled=False)
    obs = HybridObserver(svc, SPECS, trace_file=tmp_path / "trace.jsonl")
    result = asyncio.run(obs.record_candidate("query", {"layer": "retrieval"}))
    assert result is None


def test_observer_records_agreement_when_both_choose_solver(tmp_path):
    hits = [FakeHit("heating_cost", 0.85, cell="thermal")]
    svc = FakeHybridService(hits=hits)
    trace_file = tmp_path / "trace.jsonl"
    obs = HybridObserver(svc, SPECS, trace_file=trace_file)

    # Keyword also chose model_based (heating_cost solver)
    keyword = {"layer": "model_based", "confidence": 0.9, "reason": "capsule_match"}
    trace = asyncio.run(obs.record_candidate("what is heating cost", keyword))

    assert trace is not None
    assert trace.hybrid_top_solver == "heating_cost"
    assert trace.hybrid_top_score == 0.85
    assert trace.passed_threshold is True
    assert trace.passed_question_frame is True
    assert trace.hybrid_rejected_off_domain is False
    assert trace.would_agree is True

    # Verify trace file written
    lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["hybrid_top_solver"] == "heating_cost"


def test_observer_flags_hybrid_uniquely_confident(tmp_path):
    """Hybrid found a solver but keyword said retrieval/llm — hybrid saves the day."""
    hits = [FakeHit("heating_cost", 0.75, cell="thermal")]
    svc = FakeHybridService(hits=hits)
    obs = HybridObserver(svc, SPECS, trace_file=tmp_path / "trace.jsonl")

    keyword = {"layer": "retrieval", "confidence": 0.3, "reason": "keyword_fallback"}
    trace = asyncio.run(obs.record_candidate("what is heating cost", keyword))

    assert trace.hybrid_uniquely_confident is True
    assert trace.would_agree is False


def test_observer_rejects_off_domain_query(tmp_path):
    """Hybrid found a solver but question_frame says wrong type → off-domain."""
    hits = [FakeHit("heating_cost", 0.75, cell="thermal")]
    svc = FakeHybridService(hits=hits)
    obs = HybridObserver(svc, SPECS, trace_file=tmp_path / "trace.jsonl")

    # Diagnosis query — heating_cost is numeric, not diagnosis
    keyword = {"layer": "llm_reasoning", "confidence": 0.5, "reason": "llm_fallback"}
    trace = asyncio.run(obs.record_candidate("miksi lämpöpumppu meluaa", keyword))

    assert trace.hybrid_top_solver == "heating_cost"
    assert trace.passed_threshold is True
    # question_frame.parse("miksi ...") → diagnosis
    # heating_cost.output_mode = "numeric" → doesn't match diagnosis
    assert trace.passed_question_frame is False
    assert trace.hybrid_rejected_off_domain is True


def test_observer_below_threshold(tmp_path):
    """Hybrid found something but below min_score → not confident."""
    hits = [FakeHit("heating_cost", 0.50, cell="thermal")]  # below 0.60
    svc = FakeHybridService(min_score=0.60, hits=hits)
    obs = HybridObserver(svc, SPECS, trace_file=tmp_path / "trace.jsonl")

    keyword = {"layer": "retrieval", "confidence": 0.5, "reason": "retrieval"}
    trace = asyncio.run(obs.record_candidate("random query", keyword))

    assert trace.hybrid_top_solver == "heating_cost"
    assert trace.passed_threshold is False
    assert trace.hybrid_uniquely_confident is False


def test_observer_appends_multiple_traces(tmp_path):
    """JSONL append-only behavior per v3 §3."""
    svc = FakeHybridService(hits=[FakeHit("heating_cost", 0.8)])
    trace_file = tmp_path / "trace.jsonl"
    obs = HybridObserver(svc, SPECS, trace_file=trace_file)

    for i in range(3):
        asyncio.run(obs.record_candidate(f"query {i}", {"layer": "retrieval"}))

    lines = trace_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 3


def test_observer_stats(tmp_path):
    svc = FakeHybridService(hits=[FakeHit("heating_cost", 0.8)])
    obs = HybridObserver(svc, SPECS, trace_file=tmp_path / "trace.jsonl")

    # Record 2 queries that would be confident
    asyncio.run(obs.record_candidate("heating", {"layer": "retrieval"}))
    asyncio.run(obs.record_candidate("what is heating cost", {"layer": "retrieval"}))

    stats = obs.stats()
    assert stats["observed"] == 2
    assert stats["hybrid_confident"] == 2
    assert stats["hybrid_mode"] == "candidate"


def test_observer_survives_retrieve_exception(tmp_path):
    class BrokenService:
        enabled = True
        mode = "candidate"
        min_score = 0.60
        async def retrieve(self, *args, **kwargs):
            raise ValueError("faiss broken")

    obs = HybridObserver(BrokenService(), SPECS, trace_file=tmp_path / "trace.jsonl")
    trace = asyncio.run(obs.record_candidate("q", {"layer": "retrieval"}))

    # Should not raise — error captured in trace
    assert trace is not None
    assert "hybrid_err" in trace.keyword_reason
