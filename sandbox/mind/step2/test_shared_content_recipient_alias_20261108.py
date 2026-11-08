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
    {"target-agents": ["claude-rco-3"]},
    {"payload": {"recipient-list": ["claude-rco-1"]}},
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


def responder(**changes):
    value = {"agent_uuid": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "run_id": "run-synthetic",
             "session_id": "session-synthetic"}
    value.update(changes)
    return value


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("lane", ["fable-5", "codex-tools-1"])
def test_canonical_non_rco_expected_responders_are_kept(monkeypatch, strict, lane):
    expected = {lane: responder()}
    assert project(monkeypatch, {"to": lane, "expected_responders": expected}, strict)["accepted"] == 1


@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("expected", [
    {"claude-rco-3": responder()}, {"fable-5": responder(run_id="rco3")},
    {"fable-5": responder(run_id=123)}, {"fable-5": responder(extra="unknown")},
    {"fable-5": {"agent_uuid": "synthetic"}}, {"fable-5": []}, {},
])
def test_malformed_or_rco_expected_responders_are_excluded(monkeypatch, strict, expected):
    assert project(monkeypatch, {"to": "fable-5", "expected_responders": expected}, strict)["accepted"] == 0


@pytest.mark.parametrize("task", ["x-rco3-work", "rco4", "x-rco123_work", "review"])
def test_all_rco_task_ids_rejected_by_strict_policy(task):
    policy, fleet, _ = strict_inputs()
    policy["approved_tasks"] = [task]
    with pytest.raises(ValueError):
        adapter.strict_pins(*encoded_pins(policy, fleet))


def test_content_projection_preserves_only_safe_bound_request():
    from sandbox.mind.step2.test_shared_content_projection import build, row as content_row, rows_of

    safe = content_row(expected_responders={"fable-5": responder()})
    unsafe = content_row(expected_responders={"claude-rco-3": responder()})
    assert build(rows_of(safe))["accepted"] == 1
    assert build(rows_of(unsafe))["accepted"] == 0
