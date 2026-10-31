"""Synthetic Windows transport contract; no real Mind service or model.

The CreateProcess spy only observes calls in this pytest process. It does not
prove named-pipe peer authentication, ACLs, descendant behavior or readiness
for production. The payload here is synthetic and is never a command argument.
"""

import importlib
import os
import sys
import time

import pytest


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows CreateProcess contract")


@pytest.fixture
def transport():
    # Import here so every test is RED when the implementation is absent.
    return importlib.import_module("tools.wd_q4_secure_transport")


def _run(transport, tmp_path, code, payload=b"", *, timeout=5, output_limit=1024):
    return transport.run_bounded(
        executable=sys.executable,
        arguments=("-I", "-B", "-c", code),
        cwd=str(tmp_path),
        stdin_bytes=payload,
        timeout_seconds=timeout,
        max_output_bytes=output_limit,
    )


def test_synthetic_roundtrip_launches_with_noninheritable_handles(transport, tmp_path, monkeypatch):
    import _winapi

    marker = b"WD_SYNTHETIC_STDIN_ONLY_15d8db0ad52f4b38"
    observations = []
    original = _winapi.CreateProcess

    def traced(*args, **kwargs):
        # CPython's CreateProcess binding uses positional parameters here.
        command_line = args[1]
        inherit_handles = args[4]
        child_env = args[6] if args[6] is not None else os.environ
        marker_text = marker.decode("ascii")
        payload_in_command = marker_text in str(command_line)
        payload_in_environment = any(
            marker_text in str(key) or marker_text in str(value)
            for key, value in child_env.items()
        )
        observations.append((inherit_handles, payload_in_command, payload_in_environment))
        return original(*args, **kwargs)

    monkeypatch.setattr(_winapi, "CreateProcess", traced)
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())", marker)
    assert result == (marker, 0)
    assert observations, "root CreateProcess was not observed in this process"
    assert all(inherit is False or inherit == 0 for inherit, _, _ in observations)
    assert not any(in_args or in_env for _, in_args, in_env in observations)


def test_nonzero_exit_is_returned_with_synthetic_stdout(transport, tmp_path):
    result = _run(transport, tmp_path,
                  "import sys; sys.stdout.buffer.write(b'ok'); sys.exit(7)")
    assert result == (b"ok", 7)


def test_output_above_the_declared_limit_is_refused(transport, tmp_path):
    with pytest.raises(ValueError):
        _run(transport, tmp_path, "import sys; sys.stdout.buffer.write(b'x' * 1025)",
             output_limit=1024)


def test_whole_call_timeout_is_reported_within_a_bounded_interval(transport, tmp_path):
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        _run(transport, tmp_path, "import time; time.sleep(30)", timeout=0.2)
    assert time.monotonic() - started < 10
