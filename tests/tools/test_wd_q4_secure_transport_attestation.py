"""Synthetic opt-in interpreter file lease; not Q4 or runtime authentication."""
import os
import sys
import traceback
import ast
import inspect
import textwrap

import pytest

from tools import wd_q4_fixed_parent_attestation as parent, wd_q4_secure_transport as transport

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows file lease and transport")


class _LeaseContext:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _call(tmp_path, **kwargs):
    return transport.run_bounded(executable=sys.executable, arguments=("-I", "-B", "-c", "pass"),
                                 cwd=str(tmp_path), stdin_bytes=b"synthetic", timeout_seconds=5,
                                 max_output_bytes=1024, **kwargs)


def test_pinned_lease_held_through_run_and_closed_after(monkeypatch, tmp_path):
    state = {"open": False}
    class Lease(_LeaseContext):
        def close(self):
            assert state["open"]
            state["open"] = False
    def attest(path, digest, *, max_bytes, enabled):
        assert path == sys.executable and digest == "a" * 64 and max_bytes == 100 and enabled is True
        state["open"] = True
        return Lease()
    def run(*args):
        assert state["open"]
        return b"synthetic-result", 0
    monkeypatch.setattr(parent, "attest_file", attest)
    monkeypatch.setattr(transport, "_run", run)
    assert _call(tmp_path, expected_interpreter_sha256="a" * 64, interpreter_max_bytes=100) == (b"synthetic-result", 0)
    assert state["open"] is False


def test_no_pin_preserves_legacy_invocation_and_does_not_attest(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: pytest.fail("legacy unexpectedly attested"))
    monkeypatch.setattr(transport, "_run", lambda *args: (calls.append(args) or (b"ok", 0)))
    assert _call(tmp_path) == (b"ok", 0)
    assert calls[0][2] == {"mode": "python-c", "code": "pass"}
    assert calls[0][3:] == (b"synthetic", 5, 1024)


def test_no_pin_keeps_legacy_run_exception(monkeypatch, tmp_path):
    original = ValueError("synthetic legacy error")
    monkeypatch.setattr(transport, "_run", lambda *a: (_ for _ in ()).throw(original))
    with pytest.raises(ValueError) as caught:
        _call(tmp_path)
    assert caught.value is original


@pytest.mark.parametrize("pins", [
    {"expected_interpreter_sha256": "a" * 64},
    {"interpreter_max_bytes": 100},
])
def test_partial_pin_refuses_before_run_or_attestation(monkeypatch, tmp_path, pins):
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: pytest.fail("partial pin reached attestation"))
    monkeypatch.setattr(transport, "_run", lambda *a: pytest.fail("partial pin reached process"))
    with pytest.raises(transport._Refused):
        _call(tmp_path, **pins)


def test_wrong_pin_refuses_before_run(monkeypatch, tmp_path):
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: (_ for _ in ()).throw(parent.AttestationRefused("bad")))
    monkeypatch.setattr(transport, "_run", lambda *a: pytest.fail("bad pin reached process"))
    with pytest.raises(transport._Refused):
        _call(tmp_path, expected_interpreter_sha256="0" * 64, interpreter_max_bytes=100)


@pytest.mark.parametrize("fault", ["partial_pin", "validate", "open", "attest"])
def test_pinned_preadmission_refusal_drops_query_and_context(monkeypatch, tmp_path, fault):
    secret = b"synthetic-preadmission-private-sentinel"
    pin, limit = "a" * 64, 100
    if fault == "partial_pin":
        limit = None
    elif fault == "validate":
        monkeypatch.setattr(transport, "_validate", lambda *a: (_ for _ in ()).throw(ValueError("synthetic-private")))
    elif fault == "open":
        monkeypatch.setattr(transport, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic-private")), raising=False)
    else:
        monkeypatch.setattr(parent, "attest_file", lambda *a, **k: (_ for _ in ()).throw(parent.AttestationRefused("synthetic-private")))
    monkeypatch.setattr(transport, "_run", lambda *a: pytest.fail("preadmission refusal reached process"))
    with pytest.raises(transport._Refused) as caught:
        transport.run_bounded(executable=sys.executable, arguments=("-I", "-B", "-c", "pass"),
            cwd=str(tmp_path), stdin_bytes=secret, timeout_seconds=5, max_output_bytes=1024,
            expected_interpreter_sha256=pin, interpreter_max_bytes=limit)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "synthetic-private" not in str(caught.value)
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_name == "run_bounded":
            assert all(value is not secret for value in frame.f_locals.values())


@pytest.mark.parametrize("run_fails,close_fails", [(True, False), (False, True), (True, True)])
def test_run_and_cleanup_errors_remain_visible(monkeypatch, tmp_path, run_fails, close_fails):
    state = []
    run_error = ValueError("synthetic-run-error")
    class Lease(_LeaseContext):
        def close(self):
            state.append("closed")
            if close_fails:
                raise parent.AttestationCleanupUnknown(self)
    lease = Lease()
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: lease)
    def run(*args):
        if run_fails:
            raise run_error
        return b"ok", 0
    monkeypatch.setattr(transport, "_run", run)
    expected = parent.AttestationCleanupUnknown if close_fails else transport._Refused
    with pytest.raises(expected) as caught:
        _call(tmp_path, expected_interpreter_sha256="a" * 64, interpreter_max_bytes=100)
    assert state == ["closed"]
    if close_fails:
        assert caught.value.lease is lease
        assert caught.value.__context__ is None
    else:
        assert caught.value.__context__ is None


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_run_failure_never_exposes_query_in_refusal_traceback(monkeypatch, tmp_path, cleanup_fails):
    secret = b"synthetic-private-query-sentinel"
    class Lease(_LeaseContext):
        def close(self):
            if cleanup_fails:
                raise parent.AttestationCleanupUnknown(self)
    lease = Lease()
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: lease)
    def run(*args):
        assert args[3] is secret
        raise ValueError("synthetic-private-query-sentinel")
    monkeypatch.setattr(transport, "_run", run)
    expected = parent.AttestationCleanupUnknown if cleanup_fails else transport._Refused
    with pytest.raises(expected) as caught:
        transport.run_bounded(executable=sys.executable, arguments=("-I", "-B", "-c", "pass"),
            cwd=str(tmp_path), stdin_bytes=secret, timeout_seconds=5, max_output_bytes=1024,
            expected_interpreter_sha256="a" * 64, interpreter_max_bytes=100)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "synthetic-private" not in str(caught.value)
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_name == "run_bounded":
            assert all(value is not secret for value in frame.f_locals.values())


def test_pinned_timeout_keeps_fixed_type_without_original_context(monkeypatch, tmp_path):
    class Lease(_LeaseContext):
        def close(self):
            pass
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: Lease())
    monkeypatch.setattr(transport, "_run", lambda *a: (_ for _ in ()).throw(TimeoutError("synthetic-private")))
    with pytest.raises(TimeoutError, match="^secure_transport_timeout$") as caught:
        _call(tmp_path, expected_interpreter_sha256="a" * 64, interpreter_max_bytes=100)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_cleanup_failure_drops_success_result_from_own_frame(monkeypatch, tmp_path):
    secret = b"synthetic-private-result-sentinel"
    class Lease(_LeaseContext):
        def close(self):
            raise parent.AttestationCleanupUnknown(self)
    lease = Lease()
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: lease)
    monkeypatch.setattr(transport, "_run", lambda *a: (secret, 0))
    with pytest.raises(parent.AttestationCleanupUnknown) as caught:
        _call(tmp_path, expected_interpreter_sha256="a" * 64, interpreter_max_bytes=100)
    assert caught.value.lease is lease and caught.value.__context__ is None
    for frame, _ in traceback.walk_tb(caught.value.__traceback__):
        if frame.f_code.co_name == "run_bounded":
            assert "result" not in frame.f_locals


def test_attested_run_is_inside_the_lease_context():
    """A straight-line close after try/except leaves an interruptible gap."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(transport._run_bounded_impl)))
    contexts = [node for node in ast.walk(tree) if isinstance(node, ast.With)
                and any(isinstance(item.context_expr, ast.Call)
                        and isinstance(item.context_expr.func, ast.Name)
                        and item.context_expr.func.id == "attest_file" for item in node.items)]
    assert any(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id == "_run" for node in ast.walk(context)) for context in contexts)


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_interrupt_runs_lease_cleanup_and_sanitizes(monkeypatch, tmp_path, interrupt, cleanup_fails):
    state = []
    class Lease(_LeaseContext):
        def close(self):
            state.append("close")
            if cleanup_fails:
                raise parent.AttestationCleanupUnknown(self)
    lease = Lease()
    monkeypatch.setattr(parent, "attest_file", lambda *a, **kw: lease)
    monkeypatch.setattr(transport, "_run", lambda *a: (_ for _ in ()).throw(interrupt("synthetic-private")))
    expected = parent.AttestationCleanupUnknown if cleanup_fails else interrupt
    with pytest.raises(expected) as caught:
        _call(tmp_path, expected_interpreter_sha256="a" * 64, interpreter_max_bytes=100)
    assert state == ["close"]
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    if cleanup_fails:
        assert caught.value.lease is lease


def test_real_synthetic_roundtrip_under_lease(tmp_path):
    """One opt-in smoke, only when a separate caller explicitly supplies the pin."""
    digest = os.environ.get("WD_SYNTHETIC_INTERPRETER_SHA256")
    if digest is None:
        pytest.skip("no independently supplied synthetic interpreter pin")
    code = "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"
    result = transport.run_bounded(executable=sys.executable, arguments=("-I", "-B", "-c", code),
        cwd=str(tmp_path), stdin_bytes=b"synthetic-only", timeout_seconds=10, max_output_bytes=1024,
        expected_interpreter_sha256=digest, interpreter_max_bytes=1024 * 1024 * 1024)
    assert result == (b"synthetic-only", 0)
