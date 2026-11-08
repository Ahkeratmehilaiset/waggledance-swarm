"""Synthetic-only recipient alias exclusions before projection."""
import json

import pytest

from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2.test_shared_capture_adapter import NOW, TASKS, row, snapshot, strict_inputs, encoded_pins


def project(monkeypatch, event, strict):
    monkeypatch.setattr(adapter, "_utc_now", lambda: NOW)
    if not strict:
        return adapter.adapt_rows([row(**event)], snapshot(), TASKS, ttl_seconds=120)["projection"]
    policy, fleet, roster = strict_inputs()
    p, ph, r, rh = encoded_pins(policy, fleet)
    return adapter.adapt_rows([row(agent_uuid=roster["codex-lead-1"], **event)], snapshot(), ttl_seconds=120,
                              policy_bytes=p, policy_sha256=ph, roster_bytes=r, roster_sha256=rh)["projection"]


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("event", [
    {"recipients": ["claude-rco-2"]},
    {"payload": {"recipients": ["claude-rco-1"]}},
    {"targets": ["rco-2"]},
    {"payload": {"targets": ["claude-rco-3"]}},
    {"payload": {"request": {"to": "RCO3"}}},
    {"to": "claude-rco-3"},
    {"to": "fable-5", "recipients": ["RCO1"]},
    {"payload": {"recipient": "fable-5", "request": {"target_agents": ["RCO2"]}}},
    {"recipients": None},
    {"payload": {"targets": {"unknown": "fable-5"}}},
    {"payload": {"request": {"to_list": ["fable-5", 1]}}},
])
def test_rco_or_unclassifiable_alias_never_enters_projection(monkeypatch, strict, event):
    result = project(monkeypatch, event, strict)
    assert result["accepted"] == 0
    assert result["records"] == []
    assert "rco" not in json.dumps(result).lower()


@pytest.mark.parametrize("strict", [False, True])
def test_non_rco_control_is_still_projected(monkeypatch, strict):
    result = project(monkeypatch, {"to": "fable-5"}, strict)
    assert result["accepted"] == 1
