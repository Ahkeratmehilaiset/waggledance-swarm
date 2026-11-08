import hashlib
import hmac
import json

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_shared_content_projection as fixtures


def binding(name="SyntheticNameForTest"):
    raw = json.dumps({"schema": "wd.mind-identifier-policy.v1", "identifiers": [name]}).encode()
    key = b"synthetic-key-for-test-0123456789"
    return raw, key, hmac.new(key, raw, hashlib.sha256).hexdigest()


def arguments(rows):
    raw, pin = fixtures.pinned()
    return dict(rows=rows, policy_bytes=raw, policy_sha256=pin, roster=fixtures.ROSTER)


@pytest.mark.parametrize("value", [None, (b"bad", b"x" * 32, "0" * 64), (b"bad",)])
def test_invalid_required_binding_refuses_before_any_rows(monkeypatch, value):
    monkeypatch.setattr(cp, "_eligible", lambda *a: pytest.fail("processed row before validation"))
    with pytest.raises(cp.ContentRefused, match="^identifier_policy_refused$"):
        cp.build_content_projection(enabled=True, require_identifier_policy=True,
                                    identifier_policy_binding=value, **arguments(fixtures.rows_of(fixtures.row())))


def test_projection_filters_names_and_validator_binds_exact_policy(monkeypatch):
    data = binding()
    monkeypatch.setattr(cp, "_local_identifiers", lambda: pytest.fail("environment fallback"))
    rows = fixtures.rows_of(fixtures.row("work SyntheticNameForTest", minute=0),
                            fixtures.row("ordinary work", minute=1))
    args = arguments(rows)
    projection = cp.build_content_projection(enabled=True, require_identifier_policy=True,
                                             identifier_policy_binding=data, **args)
    assert projection["accepted"] == 1 and projection["refused"] == {"identifier": 1}
    assert "SyntheticNameForTest" not in json.dumps(projection)
    assert cp.validate_content_projection(projection, require_identifier_policy=True,
                                          identifier_policy_binding=data, **args) is None
    with pytest.raises(cp.ContentRefused):
        cp.validate_content_projection(projection, require_identifier_policy=True,
                                       identifier_policy_binding=binding("OtherSyntheticName"), **args)


def test_default_off_does_not_require_binding():
    assert cp.build_content_projection() == {"status": "OFF"}


def test_binding_is_loaded_once_even_for_multiple_rows(monkeypatch):
    from sandbox.mind.step2 import content_identifier_policy as ip
    original = ip.load_policy
    calls = []

    def count(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(ip, "load_policy", count)
    data = binding()
    rows = fixtures.rows_of(fixtures.row("one useful task", minute=0),
                            fixtures.row("two useful tasks", minute=1))
    cp.build_content_projection(enabled=True, require_identifier_policy=True,
                                identifier_policy_binding=data, **arguments(rows))
    assert calls == [1]


def test_broken_binding_refuses_even_when_no_rows():
    with pytest.raises(cp.ContentRefused, match="^identifier_policy_refused$"):
        cp.build_content_projection(enabled=True, require_identifier_policy=True,
                                    identifier_policy_binding=None, **arguments({}))
