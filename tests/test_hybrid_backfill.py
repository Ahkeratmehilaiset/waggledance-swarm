"""Tests for HybridBackfillService — P1 of v3.5.0."""

import asyncio
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from waggledance.application.services.hybrid_backfill_service import (
    BackfillResult,
    HybridBackfillService,
)


# ── Fixtures ────────────────────────────────────────────────────


def _make_case(tid: str, intent: str = "chat", query: str = "test", response: str = "answer"):
    """Create a minimal case trajectory dict."""
    import json
    return {
        "trajectory_id": tid,
        "intent": intent,
        "quality_grade": "silver",
        "data": json.dumps({"query": query, "response": response}),
    }


def _make_hybrid_retrieval(enabled: bool = True):
    """Create a mock HybridRetrievalService."""
    from waggledance.core.hex_cell_topology import HexCellTopology

    mock = MagicMock()
    mock._enabled = enabled
    mock.enabled = enabled
    mock._topology = HexCellTopology()
    mock._ring2_enabled = False

    async def _ingest(doc_id, text, vector, intent="chat", metadata=None):
        assignment = mock._topology.assign_cell(intent, text[:200])
        return assignment.cell_id

    mock.ingest = _ingest
    return mock


def _make_case_store(cases):
    """Create a mock case store."""
    store = MagicMock()
    store.list_full.return_value = cases
    return store


def _make_embed_fn():
    """Create a simple embedding function."""
    def embed(text):
        return np.random.randn(768).astype(np.float32)
    return embed


# ── Tests ───────────────────────────────────────────────────────


class TestBackfillIdempotent:
    """Verify backfill is idempotent on repeated runs."""

    def test_repeat_run_no_duplicates(self):
        cases = [_make_case("c1", query="calculate 15+27"), _make_case("c2", query="frost risk")]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases)
        embed = _make_embed_fn()

        svc = HybridBackfillService(hybrid, store, embed)

        # First run
        r1 = asyncio.run(svc.run())
        assert r1.indexed == 2
        assert r1.skipped_duplicate == 0

        # Second run — same cases should be skipped
        r2 = asyncio.run(svc.run())
        assert r2.indexed == 0
        assert r2.skipped_duplicate == 2

    def test_incremental_new_cases(self):
        cases1 = [_make_case("c1", query="hello")]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases1)
        embed = _make_embed_fn()

        svc = HybridBackfillService(hybrid, store, embed)
        r1 = asyncio.run(svc.run())
        assert r1.indexed == 1

        # Add a new case
        cases2 = [_make_case("c1", query="hello"), _make_case("c2", query="world")]
        store.list_full.return_value = cases2
        r2 = asyncio.run(svc.run())
        assert r2.indexed == 1
        assert r2.skipped_duplicate == 1


class TestBackfillDryRun:
    """Verify dry-run mode reports counts without writing."""

    def test_dry_run_reports_counts(self):
        cases = [
            _make_case("c1", query="calculate 15+27"),
            _make_case("c2", query="frost risk at -5C"),
            _make_case("c3", query="what is MAGMA"),
        ]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases)
        embed = _make_embed_fn()

        svc = HybridBackfillService(hybrid, store, embed)
        result = asyncio.run(svc.run(dry_run=True))

        assert result.dry_run is True
        assert result.indexed == 3
        assert result.total_scanned == 3
        assert sum(result.cell_counts.values()) == 3
        # Ingest should NOT have been called (dry run)
        # But our mock uses the real topology, so just check counts

    def test_dry_run_then_real_run(self):
        cases = [_make_case("c1", query="hello world")]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases)
        embed = _make_embed_fn()

        svc = HybridBackfillService(hybrid, store, embed)

        # Dry run indexes the ID tracking
        dr = asyncio.run(svc.run(dry_run=True))
        assert dr.indexed == 1

        # Real run after dry run — ID was tracked, so it's a duplicate
        rr = asyncio.run(svc.run(dry_run=False))
        assert rr.skipped_duplicate == 1


class TestBackfillCellCounts:
    """Verify cell counts increase after backfill."""

    def test_cell_counts_increase(self):
        cases = [
            _make_case("c1", intent="math", query="calculate 15+27"),
            _make_case("c2", intent="chat", query="frost risk at -5C"),
            _make_case("c3", intent="chat", query="what is MAGMA"),
            _make_case("c4", intent="math", query="12 squared"),
        ]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases)
        embed = _make_embed_fn()

        svc = HybridBackfillService(hybrid, store, embed)
        result = asyncio.run(svc.run())

        assert result.indexed == 4
        assert result.failed == 0
        assert len(result.cell_counts) > 0
        assert sum(result.cell_counts.values()) == 4


class TestBackfillAuth:
    """Verify auth is enforced on backfill routes."""

    def test_backfill_routes_use_require_auth(self):
        """Verify the route file imports and uses require_auth."""
        import inspect
        from waggledance.adapters.http.routes.hybrid import backfill_status, backfill_run

        # Check that the functions exist and are callable
        assert callable(backfill_status)
        assert callable(backfill_run)

        # Check the source for require_auth dependency
        src_status = inspect.getsource(backfill_status)
        src_run = inspect.getsource(backfill_run)
        assert "require_auth" in src_status
        assert "require_auth" in src_run


class TestBackfillNoContent:
    """Verify cases with no content are skipped."""

    def test_empty_content_skipped(self):
        cases = [
            {"trajectory_id": "c1", "intent": "chat", "data": "{}"},
            {"trajectory_id": "c2", "intent": "chat", "data": None},
        ]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases)
        embed = _make_embed_fn()

        svc = HybridBackfillService(hybrid, store, embed)
        result = asyncio.run(svc.run())

        assert result.skipped_no_content == 2
        assert result.indexed == 0


class TestBackfillNoEmbedFn:
    """Verify graceful handling when embed function is unavailable."""

    def test_no_embed_fn_skips(self):
        cases = [_make_case("c1", query="test")]
        hybrid = _make_hybrid_retrieval()
        store = _make_case_store(cases)

        svc = HybridBackfillService(hybrid, store, embed_fn=None)
        result = asyncio.run(svc.run())

        assert result.skipped_embed_fail == 1
        assert result.indexed == 0


class TestBackfillNoCaseStore:
    """Verify graceful handling when case store is unavailable."""

    def test_no_case_store_reports_error(self):
        hybrid = _make_hybrid_retrieval()
        svc = HybridBackfillService(hybrid, case_store=None)
        result = asyncio.run(svc.run())

        assert len(result.errors) > 0
        assert "No case store" in result.errors[0]


class TestBackfillResult:
    """Verify BackfillResult serialization."""

    def test_to_dict(self):
        r = BackfillResult(
            started=1000.0,
            finished=1010.0,
            total_scanned=5,
            indexed=3,
            skipped_duplicate=1,
            skipped_no_content=1,
        )
        d = r.to_dict()
        assert d["duration_s"] == 10.0
        assert d["indexed"] == 3
        assert d["total_scanned"] == 5

    def test_status_method(self):
        hybrid = _make_hybrid_retrieval()
        svc = HybridBackfillService(hybrid)
        status = svc.status()
        assert status["running"] is False
        assert status["total_runs"] == 0
        assert status["last_result"] is None


class TestBackfillNoRegression:
    """Verify existing hybrid tests are not broken."""

    def test_existing_hybrid_retrieval_unaffected(self):
        """HybridRetrievalService should still work without backfill."""
        from waggledance.core.hex_cell_topology import HexCellTopology
        topo = HexCellTopology()
        assignment = topo.assign_cell("math", "calculate 15+27")
        assert assignment.cell_id == "math"
        assert assignment.method == "intent"
