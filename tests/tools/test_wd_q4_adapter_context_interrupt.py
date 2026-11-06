import pytest

from tools import wd_q4_guard_completion as completion
from tools import wd_q4_lane_caller_adapter as adapter


@pytest.mark.parametrize(
    "failure",
    [KeyboardInterrupt(), SystemExit(3), BaseExceptionGroup("interrupted", [KeyboardInterrupt()])],
)
def test_interrupt_during_context_validation_clears_caller_frame(monkeypatch, failure):
    def interrupted(_guard, _context):
        raise failure

    monkeypatch.setattr(completion._Guard, "_accept_owner_context", interrupted)
    guard = object.__new__(completion._Guard)
    with pytest.raises(type(failure)) as caught:
        adapter.call_lane_session(
            enabled=True,
            identity_bytes=b"sensitive-identity",
            descriptor_bytes=b"sensitive-descriptor",
            situation="sensitive-query",
            time="sensitive-time",
            inputs={"sensitive": "input"},
            _owner_guard=guard,
            _owner_context=object(),
        )

    trace = caught.value.__traceback__
    frames = []
    while trace is not None:
        if trace.tb_frame.f_code.co_name == "call_lane_session":
            frames.append(trace.tb_frame)
        trace = trace.tb_next
    assert len(frames) == 1
    assert not {"identity_bytes", "descriptor_bytes", "situation", "time", "inputs"}.intersection(frames[0].f_locals)
