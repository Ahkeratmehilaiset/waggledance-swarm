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


@pytest.mark.parametrize("names", [[], [""], ["ab"], ["SyntheticName", "syntheticname"], [None], ["bad\nname"]])
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
