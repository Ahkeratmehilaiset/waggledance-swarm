"""Post-gate release-polish tests: startup ergonomics.

Covers:
- F1-003: ``start_waggledance.py`` now echoes ``--stub`` and ``--port``
  alongside the preset banner so operators can tell which mode the
  process is running in at a glance.
- F1-005: ``_setup_windows_utf8()`` no longer shells out to
  ``cmd.exe`` via ``os.system``. It now uses ``subprocess.run`` with
  an explicit ``chcp.com`` argv.

Both were on the DEFERRED list of the Release Polish Run
20260409_054702 and are closed post-gate.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# F1-003 — start_waggledance.py stub/port echo
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = REPO_ROOT / "start_waggledance.py"


def _run_start_script(*argv: str) -> subprocess.CompletedProcess:
    """Run ``start_waggledance.py`` in a subprocess with an argv that
    exercises the banner path and then immediately fails before the
    runtime can actually boot.

    We rely on a deliberately-invalid preset to stop the script after
    the banner is printed but before ``start_runtime.main`` is called
    (an invalid preset hits ``sys.exit(1)``). For the "no preset"
    branch we patch ``start_runtime.main`` to raise.
    """
    return subprocess.run(
        [sys.executable, str(START_SCRIPT), *argv],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )


def test_start_script_echoes_port_and_stub_with_valid_preset():
    # Use a valid preset but force an early exit by intercepting
    # runtime_main via a tiny shim: we run in a subprocess with an env
    # var that the shim can detect. Simplest stable approach: call
    # with a known-bad preset; argparse itself rejects it BEFORE the
    # banner, so we cannot test preset+echo together via subprocess
    # without mocking. Instead, import the module and call main() with
    # runtime_main patched.
    import runpy

    with mock.patch(
        "waggledance.adapters.cli.start_runtime.main"
    ) as runtime_main, mock.patch("sys.argv", [
        "start_waggledance.py",
        "--preset=raspberry-pi-iot",
        "--stub",
        "--port",
        "9123",
    ]):
        runtime_main.return_value = None
        # Capture stdout.
        import io
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            runpy.run_path(str(START_SCRIPT), run_name="__main__")
        output = buf.getvalue()

    assert "preset: raspberry-pi-iot" in output
    assert "CLI args" in output
    assert "port=9123" in output
    assert "stub=on" in output
    runtime_main.assert_called_once()
    # The child call must have forwarded both --stub and --port.
    forwarded_argv = runtime_main.call_args[0][0] or []
    assert "--stub" in forwarded_argv
    assert "--port" in forwarded_argv
    assert "9123" in forwarded_argv


def test_start_script_echoes_port_and_stub_without_preset():
    import runpy
    import io

    with mock.patch(
        "waggledance.adapters.cli.start_runtime.main"
    ) as runtime_main, mock.patch("sys.argv", [
        "start_waggledance.py",
    ]):
        runtime_main.return_value = None
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            runpy.run_path(str(START_SCRIPT), run_name="__main__")
        output = buf.getvalue()

    # Default case — port 8000, stub off.
    assert "CLI args" in output
    assert "port=8000" in output
    assert "stub=off" in output
    # With no overrides, the runtime must be invoked with None (not
    # an empty list) to preserve the documented default path.
    runtime_main.assert_called_once_with(None)


def test_start_script_does_not_leak_api_key_into_banner():
    """Regression guard for the P3 sentinel: the banner must never
    include the WAGGLE_API_KEY env var even if one happens to be set
    in the parent shell. ``start_waggledance.py`` itself does not
    touch the env var; this test makes that guarantee explicit."""
    import runpy
    import io

    sentinel = "test-sentinel-api-key-DO-NOT-LEAK-9999"
    env_patch = {"WAGGLE_API_KEY": sentinel}
    with mock.patch(
        "waggledance.adapters.cli.start_runtime.main"
    ) as runtime_main, mock.patch("sys.argv", [
        "start_waggledance.py",
        "--stub",
    ]), mock.patch.dict("os.environ", env_patch, clear=False):
        runtime_main.return_value = None
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            runpy.run_path(str(START_SCRIPT), run_name="__main__")
        output = buf.getvalue()

    assert sentinel not in output


# ---------------------------------------------------------------------------
# F1-005 — _setup_windows_utf8 no longer uses os.system
# ---------------------------------------------------------------------------

def _reload_start_runtime():
    import waggledance.adapters.cli.start_runtime as mod
    return importlib.reload(mod)


def test_setup_windows_utf8_is_noop_on_non_windows(monkeypatch):
    mod = _reload_start_runtime()
    monkeypatch.setattr("sys.platform", "linux")
    # Should return immediately without touching env vars or the
    # console. We verify by making sure subprocess.run is never called.
    called = {"n": 0}

    def fake_run(*a, **k):
        called["n"] += 1
        return subprocess.CompletedProcess(a, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    mod._setup_windows_utf8()
    assert called["n"] == 0


def test_setup_windows_utf8_uses_subprocess_not_os_system(monkeypatch):
    """The whole point of F1-005 — no more ``os.system`` shell-out."""
    mod = _reload_start_runtime()
    monkeypatch.setattr("sys.platform", "win32")

    os_system_calls: list[str] = []
    subprocess_calls: list[list[str]] = []

    import os as _os
    monkeypatch.setattr(_os, "system", lambda c: os_system_calls.append(c) or 0)

    def fake_run(argv, **kwargs):
        subprocess_calls.append(list(argv) if isinstance(argv, (list, tuple)) else [argv])
        # Reject shell=True — the whole point is to not spawn cmd.exe.
        assert kwargs.get("shell", False) is False, (
            "subprocess.run must be called with shell=False to avoid "
            "spawning cmd.exe"
        )
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    mod._setup_windows_utf8()

    assert os_system_calls == [], (
        f"os.system should never be called, got: {os_system_calls}"
    )
    assert len(subprocess_calls) >= 1
    # First call is the chcp.com invocation.
    first = subprocess_calls[0]
    assert first[0].lower().startswith("chcp")
    assert "65001" in first


def test_setup_windows_utf8_survives_chcp_missing(monkeypatch):
    """If ``chcp.com`` is missing (extremely minimal Windows install),
    the function must not raise — env var + stdout reconfigure are
    enough for Python-side UTF-8 output."""
    mod = _reload_start_runtime()
    monkeypatch.setattr("sys.platform", "win32")

    def fake_run(*a, **k):
        raise FileNotFoundError("chcp.com not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Must not raise.
    mod._setup_windows_utf8()
    # Env vars should still be set.
    import os as _os
    assert _os.environ.get("PYTHONUTF8") == "1"
    assert _os.environ.get("PYTHONIOENCODING") == "utf-8"


def test_setup_windows_utf8_sets_python_env_vars(monkeypatch):
    mod = _reload_start_runtime()
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""),
    )
    import os as _os
    # Clear state so we can assert the function sets them.
    for k in ("PYTHONUTF8", "PYTHONIOENCODING", "PYTHONUNBUFFERED"):
        _os.environ.pop(k, None)
    mod._setup_windows_utf8()
    assert _os.environ["PYTHONUTF8"] == "1"
    assert _os.environ["PYTHONIOENCODING"] == "utf-8"
    assert _os.environ["PYTHONUNBUFFERED"] == "1"
