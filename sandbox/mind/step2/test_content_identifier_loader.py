"""Synthetic identifier-loader boundary tests; no real identifiers or keys."""
import hashlib
import hmac
import importlib
import json

import pytest

from sandbox.mind.step2 import content_identifier_policy as policy

NAME = "SyntheticPrivateName"
KEY = b"synthetic-test-key-not-real-0123456789"


def loader():
    return importlib.import_module("sandbox.mind.step2.content_identifier_loader")


def valid():
    raw = json.dumps({"schema": "wd.mind-identifier-policy.v1", "identifiers": [NAME]}).encode()
    return raw, KEY, hmac.new(KEY, raw, hashlib.sha256).hexdigest()


def assert_own_frames_clear(error, module, values):
    current = error.__traceback__
    while current:
        if current.tb_frame.f_globals.get("__name__") == module.__name__:
            locals_ = current.tb_frame.f_locals
            for value in values:
                assert all(item is not value for item in locals_.values())
        current = current.tb_next


def test_loads_only_explicit_synthetic_bytes_and_normalizes():
    mod = loader()
    raw, key, tag = valid()
    result = mod.load_identifier_policy(raw, key, tag)
    assert result.contains("synthetic\u200bprivatename")
    assert not result.contains("ordinary task")
    assert NAME not in repr(result)


@pytest.mark.parametrize("bad", ["data", "key", "tag"])
def test_refusal_has_fixed_message_no_context_and_clears_own_frames(bad):
    mod = loader()
    raw, key, tag = valid()
    if bad == "data": raw += b" "
    if bad == "key": key = b"wrong-synthetic-key-not-real-0123456"
    if bad == "tag": tag = "0" * 64
    with pytest.raises(mod.IdentifierLoaderRefused, match="^identifier_loader_refused$") as caught:
        mod.load_identifier_policy(raw, key, tag)
    error = caught.value
    assert error.__context__ is None and error.__cause__ is None
    assert_own_frames_clear(error, mod, (raw, key, tag))


def test_foreign_exception_is_replaced_without_context(monkeypatch):
    mod = loader()
    raw, key, tag = valid()
    def fail(*args):
        raise ValueError("synthetic-private-foreign-error")
    monkeypatch.setattr(policy, "load_policy", fail)
    with pytest.raises(mod.IdentifierLoaderRefused) as caught:
        mod.load_identifier_policy(raw, key, tag)
    assert str(caught.value) == "identifier_loader_refused"
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert_own_frames_clear(caught.value, mod, (raw, key, tag))


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_interrupt_propagates_and_clears_own_frames(monkeypatch, interrupt):
    mod = loader()
    raw, key, tag = valid()
    def fail(*args):
        raise interrupt()
    monkeypatch.setattr(policy, "load_policy", fail)
    with pytest.raises(interrupt) as caught:
        mod.load_identifier_policy(raw, key, tag)
    assert_own_frames_clear(caught.value, mod, (raw, key, tag))
