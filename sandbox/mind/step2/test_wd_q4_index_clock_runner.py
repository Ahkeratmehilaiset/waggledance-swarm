"""Synthetic caller-time runner integration; no live data, scheduler or real model."""
from dataclasses import asdict
from datetime import datetime, timezone, tzinfo
import hashlib
import importlib
import json
import os
import traceback

import pytest

faiss = pytest.importorskip("faiss", reason="real FAISS required for integration")
pytestmark = pytest.mark.skipif(os.name != "nt", reason="private checkpoint requires a Windows fixed volume")

from sandbox.mind.step2 import prefix_private_state, shared_index_checkpoint as checkpoint
from sandbox.mind.step2.test_public_catchup_producer_integration_new import _read_pointer, _request
from sandbox.mind.step2.test_shared_index_builder import public_event
from sandbox.mind.step2.test_wd_q4_prefix_build_child import backend, enc, model_dir, plan, sha  # noqa: F401


def _case(tmp_path, model_dir):
    rows = [public_event(ts_utc="2026-10-31T11:58:00Z"),
            public_event(ts_utc="2026-10-31T12:00:59Z", agent="fable-5"),
            public_event(ts_utc="2026-10-31T11:57:00Z", to="fable-5", message="synthetic-private")]
    request, fixture = _request(tmp_path, rows)
    document = plan(model_dir, asdict(request.pins), schema="wd.q4-prefix-build-plan.v3", max_windows=8, fenced=True)
    raw = enc(document)
    args = dict(plan_bytes=raw, expected_plan_sha256=sha(raw), config=request.config,
                start_prefix=request.start_prefix, root=request.root, private_state_root=request.private_state_root,
                trusted_model_root=model_dir)
    return request, fixture, args


def test_default_off_and_untrusted_or_missing_clock_cannot_dispatch(monkeypatch):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    assert runner.run() == {"status": "OFF"}
    for time_value in (None, "2026-10-31T12:01:00Z", datetime(2026, 10, 31, 12, 1), True):
        with pytest.raises(runner.IndexRunnerRefused):
            runner.run(enabled=True, caller_cutoff_utc=time_value)


def _assert_runner_frame_sanitized(error):
    assert error.__context__ is None and error.__cause__ is None
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)
              if frame.f_code.co_filename.endswith("wd_q4_index_clock_runner.py")]
    assert frames
    for frame in frames:
        assert "sensitive-synthetic-plan-marker" not in repr(frame.f_locals)
        assert "sensitive-synthetic-root-marker" not in repr(frame.f_locals)


def test_preflight_refusal_clears_runner_frame_and_exception_context():
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    with pytest.raises(runner.IndexRunnerRefused) as caught:
        runner.run(enabled=True, caller_cutoff_utc="invalid",
                   plan_bytes=b"sensitive-synthetic-plan-marker", root="sensitive-synthetic-root-marker")
    assert str(caught.value) == "index_request_refused"
    _assert_runner_frame_sanitized(caught.value)


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_preflight_clock_exception_clears_runner_frame(failure):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    class BrokenClock(tzinfo):
        def utcoffset(self, dt):
            raise failure("synthetic clock secret")
    value = datetime(2026, 10, 31, 12, 1, tzinfo=BrokenClock())
    try:
        runner.run(enabled=True, caller_cutoff_utc=value,
                   plan_bytes=b"sensitive-synthetic-plan-marker", root="sensitive-synthetic-root-marker")
    except BaseException as error:
        expected = runner.IndexRunnerRefused if failure is RuntimeError else failure
        assert type(error) is expected
        assert "synthetic clock secret" not in str(error)
        if failure is SystemExit:
            assert error.code == 1
        _assert_runner_frame_sanitized(error)
    else:
        pytest.fail("preflight did not refuse")


@pytest.mark.parametrize("code,expected", [(None, None), (0, 0), (37, 37), ("synthetic secret", 1), (True, 1)])
def test_preflight_exit_code_keeps_only_safe_integer_or_none(code, expected):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    class ExitClock(tzinfo):
        def utcoffset(self, dt):
            raise SystemExit(code)
    with pytest.raises(SystemExit) as caught:
        runner.run(enabled=True, caller_cutoff_utc=datetime(2026, 10, 31, 12, 1, tzinfo=ExitClock()),
                   plan_bytes=b"sensitive-synthetic-plan-marker", root="sensitive-synthetic-root-marker")
    assert caught.value.code == expected and type(caught.value.code) is type(expected)
    _assert_runner_frame_sanitized(caught.value)


def test_preflight_systemexit_subclass_cannot_execute_code_property(monkeypatch):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    entered = []
    monkeypatch.setattr(child, "build_prefix_generation", lambda **kw: entered.append(True))

    class HostileExit(SystemExit):
        def __getattribute__(self, name):
            if name == "code":
                raise RuntimeError("sensitive-synthetic-plan-marker")
            return super().__getattribute__(name)

    class ExitClock(tzinfo):
        def utcoffset(self, dt):
            raise HostileExit("secret")

    with pytest.raises(SystemExit) as caught:
        runner.run(enabled=True, caller_cutoff_utc=datetime(2026, 10, 31, 12, 1, tzinfo=ExitClock()),
                   plan_bytes=b"sensitive-synthetic-plan-marker", root="sensitive-synthetic-root-marker")
    assert type(caught.value) is SystemExit
    assert caught.value.code == 1
    assert not entered
    _assert_runner_frame_sanitized(caught.value)


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt, SystemExit])
def test_child_exception_is_unknown_and_clears_runner_frame(monkeypatch, failure):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    from sandbox.mind.step2 import wd_q4_prefix_build_child as child
    def fail(**kwargs):
        raise failure("synthetic child secret")
    monkeypatch.setattr(child, "build_prefix_generation", fail)
    with pytest.raises(runner.IndexRunnerOutcomeUnknown) as caught:
        runner.run(enabled=True, caller_cutoff_utc=datetime(2026, 10, 31, 12, 1, tzinfo=timezone.utc),
                   plan_bytes=b"sensitive-synthetic-plan-marker", root="sensitive-synthetic-root-marker")
    assert str(caught.value) == "index_outcome_unknown"
    _assert_runner_frame_sanitized(caught.value)


@pytest.mark.parametrize("minute,expected_count", [(1, 1), (2, 2)])
def test_real_fenced_producer_uses_exact_caller_cutoff(tmp_path, model_dir, backend, minute, expected_count):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    request, fixture, args = _case(tmp_path, model_dir)
    supplied = datetime(2026, 10, 31, 12, minute, tzinfo=timezone.utc)
    result = runner.run(enabled=True, caller_cutoff_utc=supplied, **args)
    assert result["clock"] == "CALLER_UNVERIFIED" and result["cutoff_utc"] == supplied.isoformat()
    child = result["child"]
    assert child["status"] == "PUBLISHED_GENESIS" and child["freshness"] == "UNKNOWN"
    assert child["coverage"] == ("partial_ready_prefix" if minute == 1 else "caught_up_to_final_snapshot_not_history")
    _, mark = _read_pointer(request, fixture, child["pointer_sha256"])
    raw = open(request.config.source.source, "rb").read()
    assert mark.cursor.offset == (len(raw.splitlines(keepends=True)[0]) if minute == 1 else len(raw))
    assert mark.sha256 == hashlib.sha256(raw[:mark.cursor.offset]).hexdigest()
    with prefix_private_state.locked_state(request.private_state_root, request.root, enabled=True) as state:
        assert state.load(child["pointer_sha256"]) == mark
    projection = json.loads((request.root / "gen-a" / "projection.json").read_bytes())
    assert len(projection["records"]) == expected_count and "synthetic-private" not in repr(projection)
    assert all(item["provenance"]["ref_kind"] == "sha256_of_projected_metadata" for item in projection["records"])


def test_unknown_after_real_pointer_commit_keeps_fence_and_does_not_retry(tmp_path, model_dir, backend, monkeypatch):
    runner = importlib.import_module("tools.wd_q4_index_clock_runner")
    request, _, args = _case(tmp_path, model_dir)
    original = checkpoint.commit_prefix_genesis
    calls = []
    def after_commit(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        raise OSError("synthetic ambiguous commit")
    monkeypatch.setattr(checkpoint, "commit_prefix_genesis", after_commit)
    with pytest.raises(runner.IndexRunnerOutcomeUnknown) as exc:
        runner.run(enabled=True, caller_cutoff_utc=datetime(2026, 10, 31, 12, 1, tzinfo=timezone.utc), **args)
    assert str(exc.value) == "index_outcome_unknown" and exc.value.__context__ is None
    assert len(calls) == 1
    assert (request.root / checkpoint.POINTER).is_file() and (request.root / checkpoint.FENCE).is_file()
