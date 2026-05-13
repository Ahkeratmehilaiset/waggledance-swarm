# SPDX-License-Identifier: BUSL-1.1
"""Tests for BehaviorCapture v1.

Covers acceptance criteria from behavior_capture_spec.md:
* Capture of a synthetic baseline (no operator data)
* PII detection routes payload to operator review
* Opaque sensitive_class refuses capture
* MFA AuthMode recorded without material
* Stdin payload requires opt-in + consent token
* Pipeline linkage via pipeline_id + parent_capture_id
* Retention windows by sensitive_class
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

import pytest

from waggledance.core.v3_13_0.behavior_capture import (
    BehaviorCapture,
    CaptureRefused,
    CapturedBehaviorRecord,
    SensitiveClass,
    ToolDescriptorCaptureView,
    ToolInvocation,
    is_payload_retained,
    retention_window_days,
)


# --------------------------------------------------------------------------
# Fake subprocess + helpers
# --------------------------------------------------------------------------


@dataclass
class _FakeCompleted:
    returncode: int
    stdout: bytes
    stderr: bytes


def _fake_runner(*, stdout: bytes = b"", stderr: bytes = b"",
                  returncode: int = 0):
    def runner(*, args: list, cwd: str, input: Optional[bytes]):
        assert isinstance(args, list)
        return _FakeCompleted(returncode=returncode, stdout=stdout,
                               stderr=stderr)
    return runner


def _emit_collector(collector: list):
    def emit(envelope: dict) -> str:
        envelope_id = f"evt_{len(collector):04d}"
        envelope["__id"] = envelope_id
        collector.append(envelope)
        return envelope_id
    return emit


def _artifact_persister(collector: dict):
    def persist(kind: str, content: bytes) -> str:
        uri = f"artifact://test/{kind}_{len(collector):04d}"
        collector[uri] = content
        return uri
    return persist


def _no_pii(_payload: bytes) -> list[str]:
    return []


def _pii_finds(*categories):
    def scanner(_payload: bytes) -> list[str]:
        return list(categories)
    return scanner


def _make_capture(*, tool: ToolDescriptorCaptureView,
                   scope_allows: bool = True,
                   subprocess_stdout: bytes = b"OK\n",
                   subprocess_stderr: bytes = b"",
                   subprocess_returncode: int = 0,
                   pii_scan=_no_pii,
                   consent_token_valid: bool = True,
                   events: list = None,
                   artifacts: dict = None):
    events = events if events is not None else []
    artifacts = artifacts if artifacts is not None else {}
    return BehaviorCapture(
        fetch_tool_descriptor=lambda _tid: tool,
        operator_scope_policy_check=lambda _tid: scope_allows,
        pii_scan=pii_scan,
        persist_artifact=_artifact_persister(artifacts),
        emit_magma_event=_emit_collector(events),
        consent_token_validator=lambda _tid, _tok: consent_token_valid,
        subprocess_runner=_fake_runner(stdout=subprocess_stdout,
                                         stderr=subprocess_stderr,
                                         returncode=subprocess_returncode),
    )


def _basic_invocation(**overrides) -> ToolInvocation:
    base = dict(
        tool_descriptor_id="tool_synth_baseline",
        invocation_args=["python", "demo.py", "--mode=test"],
        invocation_env_keys=["WD_PROFILE", "WD_LOG_LEVEL"],
        cwd="/tmp/test",
        input_state_refs=["state:input_a"],
        output_state_refs=["state:output_a"],
    )
    base.update(overrides)
    return ToolInvocation(**base)


def _capture_supporting_tool(*, sensitive_class: str = "internal",
                              capture_stdin: bool = False,
                              auth_mode: Optional[str] = None) -> ToolDescriptorCaptureView:
    return ToolDescriptorCaptureView(
        tool_descriptor_id="tool_synth_baseline",
        capture_supported=True,
        capture_stdin=capture_stdin,
        capture_payloads=False,
        sensitive_class=sensitive_class,
        auth_mode=auth_mode,
    )


# --------------------------------------------------------------------------
# Retention helpers
# --------------------------------------------------------------------------


class TestRetention:

    def test_public_class_retains_30_days(self):
        assert retention_window_days("public") == 30
        assert is_payload_retained("public") is True

    def test_internal_class_retains_30_days(self):
        assert retention_window_days("internal") == 30
        assert is_payload_retained("internal") is True

    def test_restricted_class_retains_7_days(self):
        assert retention_window_days("restricted") == 7
        assert is_payload_retained("restricted") is True

    def test_secret_class_is_hash_only(self):
        assert retention_window_days("secret") is None
        assert is_payload_retained("secret") is False

    def test_opaque_class_is_hash_only(self):
        assert retention_window_days("opaque") is None
        assert is_payload_retained("opaque") is False


# --------------------------------------------------------------------------
# Capture refusal paths
# --------------------------------------------------------------------------


class TestCaptureRefusal:

    def test_unknown_tool_descriptor_refused(self):
        capture = BehaviorCapture(
            fetch_tool_descriptor=lambda _tid: None,
            operator_scope_policy_check=lambda _tid: True,
            pii_scan=_no_pii,
            persist_artifact=_artifact_persister({}),
            emit_magma_event=_emit_collector([]),
            consent_token_validator=lambda _tid, _tok: True,
            subprocess_runner=_fake_runner(),
        )
        with pytest.raises(CaptureRefused, match="unknown tool descriptor"):
            capture.capture(_basic_invocation())

    def test_capture_supported_false_refused(self):
        tool = ToolDescriptorCaptureView(
            tool_descriptor_id="tool_synth_baseline",
            capture_supported=False,
            capture_stdin=False,
            capture_payloads=False,
            sensitive_class="internal",
            auth_mode=None,
        )
        capture = _make_capture(tool=tool)
        with pytest.raises(CaptureRefused,
                            match="capture_supported is False"):
            capture.capture(_basic_invocation())

    def test_opaque_sensitive_class_refused(self):
        tool = _capture_supporting_tool(sensitive_class="opaque")
        capture = _make_capture(tool=tool)
        with pytest.raises(CaptureRefused, match="opaque"):
            capture.capture(_basic_invocation())

    def test_scope_policy_denied_refused(self):
        tool = _capture_supporting_tool()
        capture = _make_capture(tool=tool, scope_allows=False)
        with pytest.raises(CaptureRefused,
                            match="operator scope policy denied"):
            capture.capture(_basic_invocation())


# --------------------------------------------------------------------------
# Happy path -- synthetic baseline, no operator data
# --------------------------------------------------------------------------


class TestSyntheticBaselineHappyPath:

    def test_capture_synthetic_baseline_emits_record_and_event(self):
        tool = _capture_supporting_tool()
        events = []
        artifacts = {}
        capture = _make_capture(tool=tool, events=events, artifacts=artifacts,
                                  subprocess_stdout=b"hello world\n")
        record = capture.capture(_basic_invocation())

        assert isinstance(record, CapturedBehaviorRecord)
        assert record.exit_code == 0
        assert record.sensitive_class == "internal"
        assert record.operator_review_status == "approved"
        assert record.pii_scan_hits == []
        assert record.invocation_env_keys == ["WD_PROFILE", "WD_LOG_LEVEL"]
        assert record.stdout_artifact_uri.startswith("artifact://test/stdout_")
        assert record.stderr_artifact_uri.startswith("artifact://test/stderr_")
        # Event emitted
        assert len(events) == 1
        evt = events[0]
        assert evt["event_type"] == "behavior.captured"
        assert evt["capture_id"] == record.capture_id
        assert evt["operator_review_status"] == "approved"

    def test_env_keys_recorded_not_values(self):
        """Spec: invocation_env_keys are env var NAMES only, never values."""
        tool = _capture_supporting_tool()
        capture = _make_capture(tool=tool)
        record = capture.capture(_basic_invocation(
            invocation_env_keys=["WD_PROFILE", "API_KEY"]
        ))
        # Record only carries names; values never enter the capture surface
        assert record.invocation_env_keys == ["WD_PROFILE", "API_KEY"]
        # Sanity: no value-shaped field on the record
        assert not hasattr(record, "invocation_env_values")


# --------------------------------------------------------------------------
# PII detection
# --------------------------------------------------------------------------


class TestPiiDetection:

    def test_pii_hit_routes_to_pending_review(self):
        tool = _capture_supporting_tool()
        capture = _make_capture(
            tool=tool,
            subprocess_stdout=b"<synthetic fake personal data fixture>",
            pii_scan=_pii_finds("synthetic_email_like"),
        )
        record = capture.capture(_basic_invocation())
        assert record.operator_review_status == "pending"
        assert record.pii_scan_hits == ["synthetic_email_like",
                                          "synthetic_email_like"]
        # Hits are CATEGORIES, not raw matches -- never contain the raw
        # PII string
        for hit in record.pii_scan_hits:
            assert "fake personal data" not in hit
            assert "@" not in hit

    def test_classification_summary_does_not_contain_payload(self):
        tool = _capture_supporting_tool()
        payload = b"SECRET_TOKEN_DO_NOT_LEAK"
        capture = _make_capture(
            tool=tool,
            subprocess_stdout=payload,
            pii_scan=_pii_finds("synthetic_secret_like"),
        )
        record = capture.capture(_basic_invocation())
        assert "SECRET_TOKEN_DO_NOT_LEAK" not in record.classification_summary


# --------------------------------------------------------------------------
# MFA / AuthMode recording -- material never enters capture surface
# --------------------------------------------------------------------------


class TestMfaAuthMode:

    def test_auth_mode_recorded_without_material(self):
        tool = _capture_supporting_tool(auth_mode="session_cookie_mfa")
        capture = _make_capture(tool=tool)
        record = capture.capture(_basic_invocation())
        assert record.auth_mode == "session_cookie_mfa"
        # No credential / token fields on the record
        for field_name in ("token", "cookie", "credential", "password",
                            "mfa_secret"):
            assert not hasattr(record, field_name)


# --------------------------------------------------------------------------
# Stdin capture -- opt-in + consent token
# --------------------------------------------------------------------------


class TestStdinCapture:

    def test_stdin_hash_recorded_always(self):
        tool = _capture_supporting_tool(capture_stdin=False)
        capture = _make_capture(tool=tool)
        invocation = _basic_invocation(stdin_payload=b"hello world")
        record = capture.capture(invocation)
        # Hash present, payload artifact absent
        assert record.stdin_hash_sha256 is not None
        assert len(record.stdin_hash_sha256) == 64
        assert record.stdin_artifact_uri is None

    def test_stdin_payload_requires_opt_in_and_token(self):
        # capture_stdin=False -> never capture payload even with token
        tool = _capture_supporting_tool(capture_stdin=False)
        capture = _make_capture(tool=tool, consent_token_valid=True)
        invocation = _basic_invocation(stdin_payload=b"hello world")
        record = capture.capture(invocation, stdin_consent_token="t1")
        assert record.stdin_artifact_uri is None

        # capture_stdin=True + token absent -> hash only
        tool_optin = _capture_supporting_tool(capture_stdin=True)
        capture2 = _make_capture(tool=tool_optin, consent_token_valid=True)
        record2 = capture2.capture(_basic_invocation(
            stdin_payload=b"hello world"
        ))
        assert record2.stdin_artifact_uri is None

        # capture_stdin=True + valid token -> payload persisted
        capture3 = _make_capture(tool=tool_optin, consent_token_valid=True)
        record3 = capture3.capture(
            _basic_invocation(stdin_payload=b"hello world"),
            stdin_consent_token="t1",
        )
        assert record3.stdin_artifact_uri is not None
        assert record3.stdin_artifact_uri.startswith("artifact://test/stdin_")

    def test_stdin_invalid_token_refuses_payload_capture(self):
        tool = _capture_supporting_tool(capture_stdin=True)
        capture = _make_capture(tool=tool, consent_token_valid=False)
        record = capture.capture(
            _basic_invocation(stdin_payload=b"hello"),
            stdin_consent_token="bad",
        )
        assert record.stdin_hash_sha256 is not None
        assert record.stdin_artifact_uri is None


# --------------------------------------------------------------------------
# Pipeline linkage (per spec edit E2)
# --------------------------------------------------------------------------


class TestPipelineLinkage:

    def test_pipeline_id_and_parent_id_threaded_through(self):
        tool = _capture_supporting_tool()
        capture = _make_capture(tool=tool)
        first = capture.capture(_basic_invocation(pipeline_id="pipe-1"))
        second = capture.capture(_basic_invocation(
            pipeline_id="pipe-1",
            parent_capture_id=first.capture_id,
        ))
        assert first.pipeline_id == "pipe-1"
        assert first.parent_capture_id is None
        assert second.pipeline_id == "pipe-1"
        assert second.parent_capture_id == first.capture_id


# --------------------------------------------------------------------------
# Subprocess safety
# --------------------------------------------------------------------------


class TestSubprocessSafety:

    def test_args_list_required(self):
        from waggledance.core.v3_13_0.behavior_capture import (
            _default_subprocess_runner,
        )
        with pytest.raises(ValueError, match="args=list"):
            _default_subprocess_runner(
                args="ls -la", cwd="/tmp", input=None  # type: ignore[arg-type]
            )
