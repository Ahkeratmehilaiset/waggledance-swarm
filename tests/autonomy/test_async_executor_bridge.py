"""Tests for async-aware executor bridge in runtime.py."""
import asyncio
import pytest
from waggledance.core.autonomy.runtime import _make_adapter_executor, _run_maybe_async
from waggledance.core.domain.autonomy import Action


class _SyncAdapter:
    def execute(self, query=""):
        return {"answer": f"sync:{query}", "executed": True}


class _AsyncAdapter:
    async def execute(self, query=""):
        await asyncio.sleep(0)
        return {"answer": f"async:{query}", "executed": True}


class _ErrorAdapter:
    async def execute(self, query=""):
        raise ValueError("boom")


def test_bridge_handles_sync_execute():
    executor = _make_adapter_executor(_SyncAdapter())
    action = Action(capability_id="test", payload={"query": "hello"})
    result = executor(action)
    assert result["answer"] == "sync:hello"
    assert result["executed"] is True


def test_bridge_handles_async_execute():
    executor = _make_adapter_executor(_AsyncAdapter())
    action = Action(capability_id="test", payload={"query": "world"})
    result = executor(action)
    assert result["answer"] == "async:world"
    assert result["executed"] is True


def test_bridge_propagates_errors():
    executor = _make_adapter_executor(_ErrorAdapter())
    action = Action(capability_id="test", payload={"query": "fail"})
    with pytest.raises(ValueError, match="boom"):
        executor(action)


def test_run_maybe_async_with_plain_value():
    assert _run_maybe_async(42) == 42
    assert _run_maybe_async({"ok": True}) == {"ok": True}
