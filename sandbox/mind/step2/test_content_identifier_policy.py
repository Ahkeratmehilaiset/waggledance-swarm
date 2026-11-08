import hashlib
import hmac
import json

import pytest

from sandbox.mind.step2 import content_identifier_policy as policy


KEY = b"x" * 32


def bound(names, key=KEY):
    raw = json.dumps({"schema": "wd.mind-identifier-policy.v1", "identifiers": names}).encode()
    return raw, hmac.new(key, raw, hashlib.sha256).hexdigest()


def test_valid_policy_rejects_matching_names_without_exposing_them(monkeypatch):
    for name in ("USERNAME", "USER", "COMPUTERNAME", "HOSTNAME", "USERPROFILE", "HOME"):
        monkeypatch.delenv(name, raising=False)
    raw, tag = bound(["SyntheticNameForTest", "TestHostForExample"])
    result = policy.load_policy(raw, KEY, tag)
    assert result.contains("working on SYNTHETICNAMEFORTEST")
    assert not result.contains("ordinary task")
    assert "SyntheticNameForTest" not in repr(result)
    with pytest.raises(AttributeError):
        result._IdentifierPolicy__values = ()


def test_tampering_and_key_rotation_refuse_without_echo():
    raw, tag = bound(["SyntheticNameForTest"])
    for data, key, expected in [(raw + b" ", KEY, tag), (raw, b"y" * 32, tag),
                                (raw, KEY, "0" * 64)]:
        with pytest.raises(policy.PolicyRefused, match="^policy_refused$") as exc:
            policy.load_policy(data, key, expected)
        assert "SyntheticNameForTest" not in str(exc.value)
    rotated_raw, rotated_tag = bound(["SyntheticNameForTest"], b"y" * 32)
    assert policy.load_policy(rotated_raw, b"y" * 32, rotated_tag).contains("SyntheticNameForTest")


@pytest.mark.parametrize("names", [[], [""], ["ab"], ["SyntheticName", "syntheticname"], [None], ["bad\u0001name"]])
def test_invalid_identifier_lists_refuse(names):
    raw, tag = bound(names)
    with pytest.raises(policy.PolicyRefused, match="^policy_refused$"):
        policy.load_policy(raw, KEY, tag)


@pytest.mark.parametrize("key,tag", [(b"", "0" * 64), (b"x" * 31, "0" * 64), (KEY, "bad")])
def test_invalid_binding_refuses(key, tag):
    raw, _ = bound(["SyntheticName"])
    with pytest.raises(policy.PolicyRefused, match="^policy_refused$"):
        policy.load_policy(raw, key, tag)


def test_duplicate_json_keys_refuse_and_never_echo():
    raw = b'{"schema":"wd.mind-identifier-policy.v1","identifiers":["SyntheticName"],"identifiers":["OtherName"]}'
    tag = hmac.new(KEY, raw, hashlib.sha256).hexdigest()
    with pytest.raises(policy.PolicyRefused, match="^policy_refused$") as exc:
        policy.load_policy(raw, KEY, tag)
    assert "SyntheticName" not in str(exc.value) and "OtherName" not in str(exc.value)


@pytest.mark.parametrize("raw", [b'{"identifiers":["SyntheticPrivateName"],',
                                  b'\xffSyntheticPrivateName',
                                  b'{"schema":"wd.mind-identifier-policy.v1","identifiers":["SyntheticPrivateName",""]}'])
def test_refusal_does_not_expose_context_or_own_frame_secrets(raw):
    key = b"synthetic-private-key-0123456789abcdef"
    tag = hmac.new(key, raw, hashlib.sha256).hexdigest()
    with pytest.raises(policy.PolicyRefused) as caught:
        policy.load_policy(raw, key, tag)
    error = caught.value
    assert error.__context__ is None and error.__cause__ is None
    tb = error.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == policy.__name__:
            values = list(tb.tb_frame.f_locals.values())
            assert key not in values and raw not in values
            assert all("SyntheticPrivateName" not in repr(value) for value in values)
        tb = tb.tb_next


def test_contains_refusal_does_not_keep_policy_or_input_in_own_frames():
    raw, tag = bound(["SyntheticPrivateName"])
    obj = policy.load_policy(raw, KEY, tag)
    bad_input = b"synthetic-private-input"
    with pytest.raises(policy.PolicyRefused) as caught:
        obj.contains(bad_input)
    assert caught.value.__context__ is None
    tb = caught.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == policy.__name__:
            values = list(tb.tb_frame.f_locals.values())
            assert bad_input not in values and obj not in values
        tb = tb.tb_next


def test_format_and_whitespace_normalized_on_policy_and_input():
    raw, tag = bound(["Synthetic\u200b Name", "Other\n Example"])
    obj = policy.load_policy(raw, KEY, tag)
    assert obj.contains("synthetic  \nname")
    assert obj.contains("OTHER\u200d EXAMPLE")


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_interrupt_clears_loader_own_frame(monkeypatch, exception):
    raw, tag = bound(["SyntheticPrivateName"])
    key = KEY
    def interrupt(*args, **kwargs):
        raise exception()
    monkeypatch.setattr(policy.json, "loads", interrupt)
    with pytest.raises(exception) as caught:
        policy.load_policy(raw, key, tag)
    tb = caught.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == policy.__name__:
            values = list(tb.tb_frame.f_locals.values())
            assert key not in values and raw not in values
        tb = tb.tb_next


def test_interrupt_clears_contains_own_frame(monkeypatch):
    raw, tag = bound(["SyntheticPrivateName"])
    obj = policy.load_policy(raw, KEY, tag)
    value = "SyntheticPrivateInput"
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(policy.unicodedata, "normalize", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        obj.contains(value)
    tb = caught.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == policy.__name__:
            values = list(tb.tb_frame.f_locals.values())
            assert value not in values and obj not in values
        tb = tb.tb_next
