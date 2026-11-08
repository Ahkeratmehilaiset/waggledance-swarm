"""Synthetic identifier-loader boundary tests; no real identifiers or keys."""
import hashlib
import hmac
import importlib
import inspect
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


@pytest.mark.parametrize("location", ["hmac", "normalize"])
@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_real_policy_interrupt_clears_own_frames_by_value(monkeypatch, location, interrupt):
    mod = loader()
    raw, key, tag = valid()
    original = policy.hmac.new if location == "hmac" else policy.unicodedata.normalize
    expected_caller = "load_policy" if location == "hmac" else "_normalize"

    def injected(*args, **kwargs):
        if inspect.currentframe().f_back.f_code.co_name == expected_caller:
            raise interrupt()
        return original(*args, **kwargs)

    if location == "hmac":
        monkeypatch.setattr(policy.hmac, "new", injected)
    else:
        monkeypatch.setattr(policy.unicodedata, "normalize", injected)
    with pytest.raises(interrupt) as caught:
        mod.load_identifier_policy(raw, key, tag)
    current = caught.value.__traceback__
    seen = set()
    saw_external_injected_frame = False
    foreign_args_contain_input = False
    while current:
        frame = current.tb_frame
        module = frame.f_globals.get("__name__")
        if module in (mod.__name__, policy.__name__):
            seen.add(module)
            for item in frame.f_locals.values():
                if isinstance(item, (bytes, str)) and any(item == secret for secret in (raw, key, tag, NAME)):
                    pytest.fail("synthetic input remains in an owned frame by value")
                if isinstance(item, (list, tuple, dict)) and NAME in repr(item):
                    pytest.fail("synthetic identifier remains in an owned container")
        elif frame.f_code is injected.__code__:
            saw_external_injected_frame = True
            foreign_args = frame.f_locals.get("args", ())
            foreign_args_contain_input = any(item == expected for item in foreign_args
                                             for expected in (raw, key, tag, NAME))
        current = current.tb_next
    assert seen == {mod.__name__, policy.__name__}
    assert saw_external_injected_frame  # foreign frames remain outside this boundary
    assert foreign_args_contain_input  # a deliberately injected foreign frame can retain input
