"""Validate legacy producer timeout before preparing a snapshot or building."""

import hashlib
from types import SimpleNamespace
import sys

import pytest

from sandbox.mind.step2 import shared_producer as source
from sandbox.mind.step2 import shared_public_producer as public


def request(timeout):
    pin = hashlib.sha256(b"").hexdigest()
    config = source.SourceConfig(None, None, None, b"", pin, b"", pin, b"", pin, 1, 1)
    return public.PublicPublishRequest(
        config=source.PublicSourceConfig(config, b"", pin), pins=public.CallerPins(pin, pin, pin, pin),
        root="synthetic-root", generation="synthetic", encode=lambda _: None,
        identity={}, dim=1, timeout=timeout,
    )


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf"),
                                    30.00001, 31, 10**400, 0, -0.1, True, False])
def test_invalid_timeout_refused_before_prepare_or_build(monkeypatch, timeout):
    calls = []
    monkeypatch.setattr(public.producer, "prepare_public_delta", lambda **_: calls.append("prepare"))
    monkeypatch.setitem(sys.modules, "sandbox.mind.step2.shared_index_builder",
                        SimpleNamespace(build_memory=lambda *a, **k: calls.append("build")))
    with pytest.raises(public.PublicProducerRefused, match="^request_invalid$"):
        public.publish_public_generation(enabled=True, request=request(timeout))
    assert calls == []


@pytest.mark.parametrize("timeout", [0.000001, 0.2, 1, 30, 30.0])
def test_valid_timeout_bounds_pass_request_validation(timeout):
    public._validate_request(request(timeout))
