"""Tests for the F1-004 Ollama probe in ``start_runtime``.

Covers the post-gate rewrite from
``C:/WaggleDance_ReleasePolishRun/20260409_054702`` that:

- Promoted ``_check_ollama`` from ``bool`` to ``(ok, reason)``.
- Made the timeout configurable via ``WAGGLE_OLLAMA_PROBE_TIMEOUT``.
- Bumped the default from 3 s to 5 s (warm local probe is ~15 ms,
  so this is 300x headroom; the boost matters for WAN deployments).
- Normalised Windows ``URLError`` reasons into short operator-
  friendly strings ("timed out", "connection refused").

Evidence gathered at gate time:

- warm local Ollama /api/tags: avg 15 ms (10-probe window)
- connection refused (port closed): ~2.04 s via WinError 10061
- unroutable IP timeout: ~3.01 s (at the OLD default boundary)

These tests lock in the new contract without needing a live
Ollama host.
"""

from __future__ import annotations

import importlib
import urllib.error

import pytest

MOD = "waggledance.adapters.cli.start_runtime"


@pytest.fixture
def srt(monkeypatch):
    """Fresh import + env isolation."""
    monkeypatch.delenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", raising=False)
    import waggledance.adapters.cli.start_runtime as m
    return importlib.reload(m)


# ---------------------------------------------------------------------------
# _ollama_probe_timeout
# ---------------------------------------------------------------------------


class TestOllamaProbeTimeout:
    def test_default_is_five_seconds(self, srt, monkeypatch):
        monkeypatch.delenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", raising=False)
        assert srt._ollama_probe_timeout() == 5.0

    def test_env_var_overrides(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "10")
        assert srt._ollama_probe_timeout() == 10.0

    def test_env_var_accepts_float(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "7.5")
        assert srt._ollama_probe_timeout() == 7.5

    def test_blank_env_var_uses_default(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "")
        assert srt._ollama_probe_timeout() == 5.0

    def test_whitespace_env_var_uses_default(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "   ")
        assert srt._ollama_probe_timeout() == 5.0

    def test_garbage_env_var_uses_default(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "not-a-number")
        assert srt._ollama_probe_timeout() == 5.0

    def test_zero_is_rejected(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "0")
        assert srt._ollama_probe_timeout() == 5.0

    def test_negative_is_rejected(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "-1")
        assert srt._ollama_probe_timeout() == 5.0

    def test_absurdly_large_is_rejected(self, srt, monkeypatch):
        # Guard against someone setting "3600" and masking a real
        # Ollama outage for an hour.
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "3600")
        assert srt._ollama_probe_timeout() == 5.0

    def test_sixty_exactly_accepted(self, srt, monkeypatch):
        # Boundary: 60.0 is the last accepted value; 61 is not.
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "60")
        assert srt._ollama_probe_timeout() == 60.0

    def test_above_sixty_rejected(self, srt, monkeypatch):
        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "60.1")
        assert srt._ollama_probe_timeout() == 5.0


# ---------------------------------------------------------------------------
# _check_ollama
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status


class TestCheckOllamaOK:
    def test_ok_path(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            assert url.endswith("/api/tags")
            return _FakeResp(200)
        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is True
        assert reason == ""

    def test_ok_path_returns_tuple_shape(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            return _FakeResp(200)
        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        result = srt._check_ollama("http://localhost:11434")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], bool)
        assert isinstance(result[1], str)

    def test_ok_path_uses_custom_timeout(self, srt, monkeypatch):
        captured = {}

        def fake_urlopen(url, timeout):
            captured["timeout"] = timeout
            return _FakeResp(200)

        monkeypatch.setenv("WAGGLE_OLLAMA_PROBE_TIMEOUT", "9")
        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        srt._check_ollama("http://localhost:11434")
        assert captured["timeout"] == 9.0


class TestCheckOllamaFailure:
    def test_timed_out_is_normalised(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            raise urllib.error.URLError("urlopen error timed out")

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert reason == "timed out"

    def test_connection_refused_winerror_is_normalised(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            raise urllib.error.URLError(
                "[WinError 10061] No connection could be made"
            )

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert reason == "connection refused"

    def test_connection_refused_posix_is_normalised(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert reason == "connection refused"

    def test_http_non_200_reports_status(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            return _FakeResp(503)

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert reason == "HTTP 503"

    def test_http_error_reports_status(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            raise urllib.error.HTTPError(
                url, 502, "Bad Gateway", {}, None
            )

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert reason == "HTTP 502"

    def test_unknown_url_error_reason_truncated(self, srt, monkeypatch):
        very_long = "x" * 200

        def fake_urlopen(url, timeout):
            raise urllib.error.URLError(very_long)

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert len(reason) <= 80  # truncation

    def test_unexpected_exception_is_handled(self, srt, monkeypatch):
        def fake_urlopen(url, timeout):
            raise RuntimeError("something weird happened in the stack")

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        ok, reason = srt._check_ollama("http://localhost:11434")
        assert ok is False
        assert "RuntimeError" in reason


# ---------------------------------------------------------------------------
# Regression guard — contract shape
# ---------------------------------------------------------------------------


class TestContractShape:
    def test_never_returns_plain_bool(self, srt, monkeypatch):
        """The pre-fix version returned ``bool``. Any caller that was
        updated for the tuple shape MUST NOT regress to bool."""
        def fake_urlopen(url, timeout):
            return _FakeResp(200)

        monkeypatch.setattr(
            "urllib.request.urlopen", fake_urlopen, raising=True
        )
        result = srt._check_ollama("http://localhost:11434")
        assert not isinstance(result, bool)
        assert isinstance(result, tuple)

    def test_default_timeout_header_room_5x_over_worst_warm_case(self, srt):
        """Warm probe worst case observed at gate time was ~35 ms.
        The 5 s default gives ~142x headroom. Lock the default in."""
        assert srt._ollama_probe_timeout() >= 5.0
        assert srt._ollama_probe_timeout() <= 60.0
