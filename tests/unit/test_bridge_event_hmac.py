# SPDX-License-Identifier: Apache-2.0
"""Bridge-event HMAC core (Phase A): sign + log-only verify primitives."""
from __future__ import annotations

from pathlib import Path

import pytest

from waggledance.core.bridge_event_hmac import (
    BRIDGE_EVENT_HMAC_SCHEME,
    SIG_INVALID,
    SIG_UNSIGNED,
    SIG_UNVERIFIABLE,
    SIG_VALID,
    BridgeEventHmacError,
    canonical_signing_bytes,
    generate_agent_key,
    key_id_for,
    load_agent_key,
    sign_event_fields,
    verify_event_signature,
)

KEY = b"k" * 32
OTHER_KEY = b"x" * 32

FIELDS = dict(
    agent="claude-rco-1",
    ts_utc="2026-06-10T06:00:00Z",
    event_type="decision",
    status="rco_pass",
    task_id="task/x",
    message="RCO_PASS PR #1 at exact head abc",
)


def _event(hmac_obj=None, **overrides):
    event = {
        "agent": FIELDS["agent"],
        "ts_utc": FIELDS["ts_utc"],
        "type": FIELDS["event_type"],
        "status": FIELDS["status"],
        "task_id": FIELDS["task_id"],
        "message": FIELDS["message"],
        "payload": {"hmac": hmac_obj} if hmac_obj else {},
    }
    event.update(overrides)
    return event


def test_sign_verify_roundtrip_is_valid():
    hmac_obj = sign_event_fields(key=KEY, **FIELDS)
    assert hmac_obj["scheme"] == BRIDGE_EVENT_HMAC_SCHEME
    assert hmac_obj["sig"].startswith("hmac-sha256:")
    assert hmac_obj["key_id"] == key_id_for(KEY)

    verdict = verify_event_signature(_event(hmac_obj), lambda _a: KEY)
    assert verdict["status"] == SIG_VALID
    assert verdict["enforcement_applied"] is False


@pytest.mark.parametrize(
    "tamper",
    [
        {"agent": "codex-lead-1"},
        {"ts_utc": "2026-06-10T06:00:01Z"},
        {"type": "message"},
        {"status": "rco_block"},
        {"task_id": "task/y"},
        {"message": "RCO_PASS PR #1 at exact head FORGED"},
    ],
)
def test_tampering_any_bound_field_invalidates(tamper):
    hmac_obj = sign_event_fields(key=KEY, **FIELDS)
    verdict = verify_event_signature(
        _event(hmac_obj, **tamper), lambda _a: KEY
    )
    assert verdict["status"] == SIG_INVALID


def test_wrong_key_is_invalid_and_unsigned_unverifiable_classify():
    hmac_obj = sign_event_fields(key=KEY, **FIELDS)
    assert verify_event_signature(
        _event(hmac_obj), lambda _a: OTHER_KEY
    )["status"] == SIG_INVALID
    assert verify_event_signature(
        _event(None), lambda _a: KEY
    )["status"] == SIG_UNSIGNED
    assert verify_event_signature(
        _event(hmac_obj), lambda _a: None
    )["status"] == SIG_UNVERIFIABLE


def test_canonical_bytes_are_injection_safe_and_deterministic():
    first = canonical_signing_bytes(
        agent="a", ts_utc="t", event_type="x", status="s",
        task_id="id", message="m",
    )
    assert first == canonical_signing_bytes(
        agent="a", ts_utc="t", event_type="x", status="s",
        task_id="id", message="m",
    )
    # crafted separators in one field cannot impersonate another split
    crafted = canonical_signing_bytes(
        agent="a", ts_utc="t", event_type="x", status='s","task_id":"id',
        task_id="", message="m",
    )
    assert crafted != first


def test_short_key_and_non_string_inputs_fail_closed():
    with pytest.raises(BridgeEventHmacError, match="key"):
        sign_event_fields(key=b"short", **FIELDS)
    with pytest.raises(BridgeEventHmacError, match="agent"):
        sign_event_fields(key=KEY, **{**FIELDS, "agent": "  "})


def test_key_files_load_and_generate(tmp_path: Path):
    path = generate_agent_key("codex-lead-1", tmp_path)
    assert path.exists()
    key = load_agent_key("codex-lead-1", tmp_path)
    assert isinstance(key, bytes) and len(key) == 32
    # never overwrites an existing key
    with pytest.raises(BridgeEventHmacError, match="refusing"):
        generate_agent_key("codex-lead-1", tmp_path)
    # missing / malformed / short keys load as None
    assert load_agent_key("nobody", tmp_path) is None
    (tmp_path / "bad.key").write_text("not-hex", encoding="utf-8")
    assert load_agent_key("bad", tmp_path) is None
    (tmp_path / "tiny.key").write_text("aa" * 8, encoding="utf-8")
    assert load_agent_key("tiny", tmp_path) is None
    assert load_agent_key("codex-lead-1", None) is None  # no dir, no env


def test_key_id_is_stable_and_not_the_key():
    kid = key_id_for(KEY)
    assert kid == key_id_for(KEY)
    assert kid.startswith("k:") and len(kid) == 18
    assert KEY.hex() not in kid
