import hashlib
import hmac
import json

import pytest

from sandbox.mind.step2 import shared_content_projection as cp


def binding():
    raw = json.dumps({"schema": "wd.mind-identifier-policy.v1",
                      "identifiers": ["SyntheticNameForTest"]}).encode()
    key = b"synthetic-key-for-test-0123456789"
    return raw, key, hmac.new(key, raw, hashlib.sha256).hexdigest()


def test_explicit_binding_does_not_use_environment(monkeypatch):
    data = binding()
    for key in ("USERNAME", "USER", "COMPUTERNAME", "HOSTNAME", "USERPROFILE", "HOME"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cp, "_local_identifiers", lambda: (_ for _ in ()).throw(AssertionError("fallback")))
    assert cp.classify("work SyntheticNameForTest", require_identifier_policy=True,
                       identifier_policy_binding=data) == ("DROP", "identifier")
    assert cp.classify("work example", require_identifier_policy=True,
                       identifier_policy_binding=data) == ("KEEP", "work example")
    monkeypatch.setenv("USERNAME", "AnotherSyntheticName")
    assert cp.classify("work SyntheticNameForTest", require_identifier_policy=True,
                       identifier_policy_binding=data) == ("DROP", "identifier")
    assert cp.classify("work example", require_identifier_policy=True,
                       identifier_policy_binding=data) == ("KEEP", "work example")


@pytest.mark.parametrize("mutation", ["missing", "tag", "key", "shape", "bytes"])
def test_explicit_binding_missing_or_tampered_refuses_without_echo(mutation, capsys):
    raw, key, tag = binding()
    values = {"missing": None, "tag": (raw, key, "0" * 64), "key": (raw, b"y" * 32, tag),
              "shape": (raw, key), "bytes": (raw + b" ", key, tag)}
    verdict = cp.classify("work example", require_identifier_policy=True,
                          identifier_policy_binding=values[mutation])
    assert verdict == ("DROP", "unknown")
    captured = capsys.readouterr()
    assert "SyntheticNameForTest" not in captured.out + captured.err


def test_binding_without_explicit_opt_in_refuses():
    assert cp.classify("work example", identifier_policy_binding=binding()) == ("DROP", "unknown")


def test_interrupt_clears_classifier_binding_locals(monkeypatch):
    from sandbox.mind.step2 import content_identifier_policy as ip
    data = binding()
    message = "synthetic private test message"

    def interrupt(*args):
        raise KeyboardInterrupt()

    monkeypatch.setattr(ip, "load_policy", interrupt)
    with pytest.raises(KeyboardInterrupt) as caught:
        cp.classify(message, require_identifier_policy=True, identifier_policy_binding=data)
    tb = caught.value.__traceback__
    while tb is not None:
        if tb.tb_frame.f_code.co_name == "classify" and tb.tb_frame.f_globals.get("__name__") == cp.__name__:
            values = list(tb.tb_frame.f_locals.values())
            assert data not in values and message not in values
        tb = tb.tb_next
