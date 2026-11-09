"""Synthetic cutoff validation at the pre-I/O boundary."""
import datetime as dt
import hashlib
import importlib

import pytest


class ThrowingTimezone(dt.tzinfo):
    def utcoffset(self, value):
        raise RuntimeError("synthetic-private-timezone-message")


def _request(monkeypatch):
    child = importlib.import_module("sandbox.mind.step2.wd_q4_prefix_build_child")
    plan = b"synthetic-private-plan"
    monkeypatch.setattr(child, "_parse", lambda _: {"schema": child.PLAN_FENCED_CATCHUP_SCHEMA, "pins": {}})
    monkeypatch.setattr(child, "_plan_shape", lambda _: True)
    monkeypatch.setattr(child, "_request_shape", lambda *_: True)
    args = {"enabled": True, "plan_bytes": plan, "expected_plan_sha256": hashlib.sha256(plan).hexdigest(),
            "config": "synthetic-private-config", "start_prefix": "synthetic-private-prefix",
            "root": "synthetic-private-root", "private_state_root": "synthetic-private-state",
            "trusted_model_root": "synthetic-private-model"}
    return child, args


def test_throwing_timezone_refuses_cleanly_before_pins_or_io(monkeypatch):
    child, args = _request(monkeypatch)
    monkeypatch.setattr(child, "_pins_bound", lambda *_: pytest.fail("unexpected pin/model/producer access"))
    cutoff = dt.datetime(2026, 1, 1, tzinfo=ThrowingTimezone())
    with pytest.raises(child.BuildChildRefused) as caught:
        child.build_prefix_generation(cutoff_utc=cutoff, **args)
    assert str(caught.value) == "request_refused"
    assert caught.value.__context__ is None
    tb = caught.value.__traceback__
    while tb and tb.tb_frame.f_code.co_name != "build_prefix_generation":
        tb = tb.tb_next
    assert tb is not None
    for name in ("plan_bytes", "expected_plan_sha256", "config", "start_prefix", "root", "private_state_root",
                 "trusted_model_root", "cutoff_utc", "plan"):
        assert tb.tb_frame.f_locals.get(name) is None


@pytest.mark.parametrize("cutoff", [None, dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)])
def test_legacy_and_valid_utc_still_reach_pin_validation(monkeypatch, cutoff):
    child, args = _request(monkeypatch)
    called = []
    monkeypatch.setattr(child, "_pins_bound", lambda *value: called.append(value) or False)
    with pytest.raises(child.BuildChildRefused, match="^pins_refused$"):
        child.build_prefix_generation(cutoff_utc=cutoff, **args)
    assert len(called) == 1
