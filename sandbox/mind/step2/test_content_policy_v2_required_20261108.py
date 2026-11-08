"""Synthetic production route must require exact-source policy before source reads."""
import json

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import test_content_exact_source_allowlist_20261108 as f
from sandbox.mind.step2 import test_shared_content_projection as fixture


def legacy_tuple(tmp_path):
    _, _, _, messages, decision, strict = f.route_fixture(tmp_path)
    legacy, pin = fixture.pinned()
    changed = json.loads(decision)
    changed["message_policy_sha256"] = pin
    return dict(messages, policy_bytes=legacy, policy_sha256=pin), f.raw(changed), strict


@pytest.mark.parametrize("entry", ["build", "rederive"])
def test_legacy_policy_refuses_before_source_read_or_publish(tmp_path, monkeypatch, entry):
    messages, decision, strict = legacy_tuple(tmp_path)
    calls = []
    def forbidden(*args, **kwargs):
        calls.append("source-or-publish")
        raise AssertionError("unexpected source read or publish")
    monkeypatch.setattr(cm, "_read_prefix_rows", forbidden)
    monkeypatch.setattr(cm, "_build", forbidden)
    with pytest.raises(cm.ContentMemoryRefused, match="^exact_source_policy_required$"):
        if entry == "build":
            cm.build_route_content_memory(enabled=True, messages=messages, documents=None,
                                          decision_bytes=decision, decision_sha256=f.sha(decision), **strict)
        else:
            projection = b"{}"
            cm.validate_content_snapshot(enabled=True, projection_bytes=projection,
                                         projection_sha256=f.sha(projection), messages=messages, documents=None,
                                         decision_bytes=decision, decision_sha256=f.sha(decision), **strict)
    assert calls == []


def test_exact_policy_rederivation_and_old_pin_refusal(tmp_path):
    allowed, _, _, messages, decision, strict = f.route_fixture(tmp_path)
    projection, _ = cm._route_projection(messages, None, decision, f.sha(decision), **strict)
    assert {row["provenance"]["source_event_ref"] for row in projection["messages"]["records"]} == {f.event_id(allowed)}
    projection_bytes = cm._serialized(projection)
    result = cm.validate_content_snapshot(enabled=True, projection_bytes=projection_bytes,
                                          projection_sha256=f.sha(projection_bytes), messages=messages,
                                          documents=None, decision_bytes=decision,
                                          decision_sha256=f.sha(decision), **strict)
    assert result["status"] == "MATCH"
    legacy, legacy_pin = fixture.pinned()
    with pytest.raises(cm.ContentMemoryRefused, match="^decision_mismatch$"):
        cm.validate_content_snapshot(enabled=True, projection_bytes=projection_bytes,
                                     projection_sha256=f.sha(projection_bytes),
                                     messages=dict(messages, policy_bytes=legacy, policy_sha256=legacy_pin),
                                     documents=None, decision_bytes=decision, decision_sha256=f.sha(decision), **strict)
