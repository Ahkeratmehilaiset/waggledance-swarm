"""A startup own-task situation may name only the lane the adapter has declared consistent; anything else refuses
before the chain. Every other situation passes through exactly as before."""

import pytest

from tests.tools import test_wd_q4_lane_caller_adapter as base

STARTUP = "%s startup read: own current task, last bound reply, operator directive of the day"
PROJECTION_LANES = ("codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5")


def lane_kw(data, lane):
    """Consistent inputs, descriptor and identity for ``lane`` (the tools lane lives in the supervisor entry)."""
    if lane == base.TOOLS:
        return base.tools_lane(data), base.TOOLS_CWD
    inputs = dict(data["inputs"])
    descriptor = base.descriptor_for(inputs, lanes=base.ALL_LANES)
    cwd = base.WORKTREES[lane] + r"\work"
    identity = base.identity_for(descriptor, agent=lane, worktree=base.WORKTREES[lane], cwd=cwd)
    return dict(inputs=inputs, descriptor_bytes=descriptor, expected_descriptor_sha256=base.sha(descriptor),
                identity_bytes=identity, expected_identity_sha256=base.sha(identity)), cwd


@pytest.fixture
def data():
    return base.data.__wrapped__()


@pytest.fixture
def chain(monkeypatch):
    return base.chain.__wrapped__(monkeypatch)


@pytest.mark.parametrize("lane", base.ALL_LANES)
def test_each_identity_may_ask_its_own_startup_read(data, chain, monkeypatch, lane):
    calls, state = chain
    kw, cwd = lane_kw(data, lane)
    monkeypatch.setattr(base.adapter(), "_process_cwd", lambda: cwd, raising=False)
    state["outcome"] = base.answer_for(lane, kw["descriptor_bytes"])
    result = base.call(data, situation=STARTUP % lane, **kw)
    assert result["status"] == "VALIDATED" and result["lane"] == lane and len(calls) == 1
    assert calls[0]["lane"] == lane and calls[0]["situation"] == STARTUP % lane


@pytest.mark.parametrize("lane", base.ALL_LANES)
@pytest.mark.parametrize("named", base.ALL_LANES + ("mallory", "x"))
def test_a_startup_read_naming_another_lane_refuses_before_the_chain(data, chain, monkeypatch, lane, named):
    if named == lane:
        pytest.skip("own lane: covered by the positive case")
    calls, state = chain
    kw, cwd = lane_kw(data, lane)
    monkeypatch.setattr(base.adapter(), "_process_cwd", lambda: cwd, raising=False)
    state["outcome"] = base.answer_for(lane, kw["descriptor_bytes"])
    with pytest.raises(ValueError, match="^caller_refused$"):
        base.call(data, situation=STARTUP % named, **kw)
    assert calls == []
    assert base.adapter()._QUARANTINED is False


@pytest.mark.parametrize("situation", [
    "claude-rco-1 status today",
    "operator directive of the day",
    "startup read: own current task",
    "Codex-Lead-1 startup read: own current task",
    "please run claude-rco-1 startup read: own current task",
    base.CANARY,
])
def test_non_startup_situations_pass_through_unchanged(data, chain, situation):
    calls, state = chain
    state["outcome"] = base.answer_for(base.LANE, data["descriptor"])
    result = base.call(data, situation=situation)
    assert result["status"] == "VALIDATED" and len(calls) == 1 and calls[0]["situation"] == situation


def test_the_adapter_and_q4_recognize_the_same_startup_shape():
    pytest.importorskip("numpy")
    pytest.importorskip("faiss")
    from sandbox.mind.step2 import q4
    assert base.adapter()._STARTUP_OWN_TASK.pattern == q4.STARTUP_OWN_TASK.pattern
    assert base.adapter()._STARTUP_OWN_TASK.flags == q4.STARTUP_OWN_TASK.flags
