"""Tests for the P2 S1b Phase 2b ChatService -> chat-served emitter wiring.

Covers: the served_pending denominator is recorded at served return points when
enabled (raw query/response never persisted), the feature is fully DORMANT when
disabled (default), and emission never breaks serving (fail-open).
"""

import asyncio
import os

from unittest.mock import MagicMock

from waggledance.application.dto.chat_dto import ChatRequest
from waggledance.application.services.chat_service import ChatService
from waggledance.core.magma import chat_served_ledger as L
from waggledance.core.orchestration.routing_policy import select_route


def _config(out_dir, *, enabled=True):
    config = MagicMock()
    settings = {
        "chat_served_receipts.enabled": enabled,
        "chat_served_receipts.out_dir": out_dir,
        "swarm.enabled": False,
        "advanced_learning.micro_model_enabled": False,
    }
    config.get.side_effect = lambda key, default=None: settings.get(key, default)
    config.get_profile.return_value = "COTTAGE"
    config.get_hardware_tier.return_value = "standard"
    return config


def _svc(config, mock_orchestrator, mock_memory_service, mock_hot_cache):
    return ChatService(
        orchestrator=mock_orchestrator, memory_service=mock_memory_service,
        hot_cache=mock_hot_cache, routing_policy_fn=select_route, config=config,
    )


def _pending_ids(out_dir):
    entries, _ = L.read_entries(os.path.join(out_dir, "ledger.jsonl"))
    return {e["served_id"] for e in entries if e["entry_type"] == "served_pending"}


async def _drain(svc):
    emitter = svc._chat_served_emitter
    if emitter is not None and emitter._tasks:
        await asyncio.gather(*list(emitter._tasks))


def test_hotcache_served_records_pending_no_raw(
    tmp_path, mock_orchestrator, mock_memory_service, mock_hot_cache
):
    out_dir = str(tmp_path / "csr")
    svc = _svc(_config(out_dir), mock_orchestrator, mock_memory_service, mock_hot_cache)
    mock_hot_cache.get.return_value = "cheapest hours 02-05"
    raw = "when is power cheapest SECRET-TOKEN-xyz"

    async def _run():
        result = await svc.handle(ChatRequest(query=raw, profile="HOME"))
        assert result.source == "hotcache"
        await _drain(svc)

    asyncio.run(_run())
    assert len(_pending_ids(out_dir)) == 1                      # the served denominator was recorded
    raw_bytes = open(os.path.join(out_dir, "ledger.jsonl"), "rb").read()
    assert b"SECRET-TOKEN-xyz" not in raw_bytes                 # raw query never persisted


def test_final_llm_path_records_pending(
    tmp_path, mock_orchestrator, mock_memory_service, mock_hot_cache
):
    out_dir = str(tmp_path / "csr")
    svc = _svc(_config(out_dir), mock_orchestrator, mock_memory_service, mock_hot_cache)
    mock_hot_cache.get.return_value = None                      # cache miss -> orchestrator/final path

    async def _run():
        await svc.handle(ChatRequest(query="how to treat varroa?", profile="HOME"))
        await _drain(svc)

    asyncio.run(_run())
    assert len(_pending_ids(out_dir)) == 1                      # the final served point is wired too


def test_disabled_default_is_dormant(
    tmp_path, mock_orchestrator, mock_memory_service, mock_hot_cache
):
    out_dir = str(tmp_path / "csr")
    svc = _svc(_config(out_dir, enabled=False), mock_orchestrator, mock_memory_service, mock_hot_cache)
    mock_hot_cache.get.return_value = "answer"

    async def _run():
        await svc.handle(ChatRequest(query="q"))

    asyncio.run(_run())
    assert svc._chat_served_emitter is None                     # emitter never built
    assert not os.path.exists(out_dir)                          # nothing written -> fully dormant


def test_emit_failure_never_breaks_serving(
    tmp_path, mock_orchestrator, mock_memory_service, mock_hot_cache
):
    out_dir = str(tmp_path / "csr")
    svc = _svc(_config(out_dir), mock_orchestrator, mock_memory_service, mock_hot_cache)
    # force the emit path to blow up: serving must be unaffected (fail-OPEN)
    svc._chat_served_emitter_or_none = MagicMock(side_effect=RuntimeError("boom"))
    mock_hot_cache.get.return_value = "cached answer"

    async def _run():
        result = await svc.handle(ChatRequest(query="q", profile="HOME"))
        assert result.response == "cached answer" and result.source == "hotcache"

    asyncio.run(_run())                                         # no exception escaped -> fail-open
