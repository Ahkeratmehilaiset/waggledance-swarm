"""Synthetic exact-source admission: no live events or production memory."""
import hashlib
import json
from dataclasses import replace

import pytest

from sandbox.mind.step2 import content_memory as cm
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import test_shared_content_projection as fx
from tools.bridge_compact_view import event_id
from waggledance.core import bridge_log_reader as bridge


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def policy(*allowed, **changes):
    doc = fx.policy_doc(schema="wd.mind-content-policy.v2", approved_source_refs=list(allowed))
    doc.update(changes)
    data = raw(doc)
    return data, sha(data)


def project(events, policy_bytes, pin):
    rows = {event_id(event): event for event in events}
    result = cp.build_content_projection(enabled=True, rows=rows, policy_bytes=policy_bytes,
                                         policy_sha256=pin, roster=fx.ROSTER)
    return rows, result


def test_only_exact_preapproved_refs_reach_projection_and_rederivation():
    allowed = fx.row("The build work continues.", minute=1)
    quoted = fx.row("RCO2 said: the change is safe.", minute=2)
    paraphrased = fx.row("The second checker says the change is good.", minute=3)
    data, pin = policy(event_id(allowed))
    rows, result = project([allowed, quoted, paraphrased], data, pin)
    assert {record["provenance"]["source_event_ref"] for record in result["records"]} == {event_id(allowed)}
    cp.validate_content_projection(result, rows=rows, policy_bytes=data, policy_sha256=pin, roster=fx.ROSTER)
    assert result["refused"] == {}  # unknown sources are silently excluded
    changed, changed_pin = policy(event_id(allowed), event_id(quoted))
    with pytest.raises(cp.ContentRefused):
        cp.validate_content_projection(result, rows=rows, policy_bytes=changed,
                                       policy_sha256=changed_pin, roster=fx.ROSTER)


def test_allowlisting_does_not_override_other_existing_exclusions():
    review = fx.row("Synthetic note.", agent=fx.RCO1, minute=1)
    recipient = fx.row("Synthetic note.", to=fx.RCO2, minute=2)
    secret = fx.row("pass" + "word=synthetic", minute=3)
    removed = fx.row("Synthetic ordinary note.", minute=4)
    data, pin = policy(*(event_id(x) for x in (review, recipient, secret, removed)),
                       removed_source_refs=[event_id(removed)])
    _, result = project([review, recipient, secret, removed], data, pin)
    assert result["records"] == []
    assert result["refused"].get("removed") == 1
    assert result["refused"].get("credential") == 1


def test_allowlist_is_not_proof_of_semantic_review():
    quote = fx.row("RCO2 said: the change is safe.")
    data, pin = policy(event_id(quote))
    _, result = project([quote], data, pin)
    assert result["accepted"] == 1  # callers must independently review every approved source


def test_ref_collision_spoof_and_policy_hash_mismatch_refuse():
    event = fx.row("A synthetic original message.")
    altered = dict(event, message="A different synthetic message.")
    data, pin = policy(event_id(event))
    with pytest.raises(cp.ContentRefused, match="^rows_refused$"):
        cp.build_content_projection(enabled=True, rows={event_id(event): altered}, policy_bytes=data,
                                    policy_sha256=pin, roster=fx.ROSTER)
    with pytest.raises(cp.ContentRefused, match="^policy_refused$"):
        project([event], data, "0" * 64)


@pytest.mark.parametrize("refs", [None, "a" * 64, ["A" * 64], ["a" * 64, "a" * 64], ["a" * 63]])
def test_bad_or_ambiguous_allowlist_refuses(refs):
    data = raw(fx.policy_doc(schema="wd.mind-content-policy.v2", approved_source_refs=refs))
    with pytest.raises(cp.ContentRefused):
        cp.load_content_policy(data, sha(data))


def test_empty_allowlist_is_closed_and_old_v1_remains_separate():
    event = fx.row("Synthetic ordinary note.")
    data, pin = policy()
    _, result = project([event], data, pin)
    assert result["records"] == []
    legacy, legacy_pin = fx.pinned()
    _, legacy_result = project([event], legacy, legacy_pin)
    assert legacy_result["records"]  # caller must explicitly require v2


@pytest.mark.parametrize("change", [{"approved_source_refs": None}, {"schema": "wd.mind-content-policy.v3"},
                                   {"unexpected": True}])
def test_v2_schema_never_falls_back_on_invalid_fields(change):
    doc = fx.policy_doc(schema="wd.mind-content-policy.v2", approved_source_refs=[])
    doc.update(change)
    data = raw(doc)
    with pytest.raises(cp.ContentRefused, match="^policy_refused$"):
        cp.load_content_policy(data, sha(data))


def route_fixture(tmp_path):
    allowed = fx.row("The build work continues.", minute=1)
    denied = fx.row("The second checker says the change is good.", minute=2)
    data, pin = policy(event_id(allowed))
    policy_bytes = raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [fx.TASK]})
    roster_bytes = raw({"lanes": [{"agent": agent, "agent_uuid": uuid} for agent, uuid in fx.ROSTER.items()
                                  if agent != fx.TOOLS],
                        "tools_supervisor": {"agent": fx.TOOLS, "agent_uuid": fx.ROSTER[fx.TOOLS]}})
    log, generation = tmp_path / "synthetic-log", tmp_path / "synthetic-generation"
    source = b"".join(raw(event) + b"\n" for event in (allowed, denied))
    log.write_bytes(source)
    generation.write_bytes(raw({"generation": "synthetic-g1"}))
    cursor = bridge.read_bridge_log(str(log), generation_path=str(generation)).candidate_cursor
    strict = {"policy_bytes": policy_bytes, "policy_sha256": sha(policy_bytes),
              "roster_bytes": roster_bytes, "roster_sha256": sha(roster_bytes)}
    decision = dict(cm.module_sha256s(), schema=cm.DECISION_SCHEMA, decision=cm.DECISION_NAME,
                    projection_schema=cm.SCHEMA, scope=cm.ROUTE_SCOPE,
                    nonreview_policy_sha256=strict["policy_sha256"], roster_sha256=strict["roster_sha256"],
                    message_policy_sha256=pin, doc_policy_sha256=None,
                    source_prefix={"sha256": sha(source), "offset": cursor.offset, "generation": cursor.generation})
    decision_bytes = raw(decision)
    messages = {"source_path": str(log), "generation_path": str(generation), "end_cursor": cursor,
                "policy_bytes": data, "policy_sha256": pin}
    return allowed, log, generation, messages, decision_bytes, strict


def test_pinned_decision_prefix_generation_and_rederivation(tmp_path):
    allowed, log, generation, messages, decision, strict = route_fixture(tmp_path)
    projection, _ = cm._route_projection(messages, None, decision, sha(decision), **strict)
    assert {row["provenance"]["source_event_ref"] for row in projection["messages"]["records"]} == {event_id(allowed)}
    with pytest.raises(cm.ContentMemoryRefused):
        cm._route_projection(messages, None, decision, "0" * 64, **strict)
    wrong_cursor = replace(messages["end_cursor"], generation="synthetic-other-generation")
    with pytest.raises(cm.ContentMemoryRefused):
        cm._route_projection(dict(messages, end_cursor=wrong_cursor), None, decision, sha(decision), **strict)
    generation.write_bytes(raw({"generation": "synthetic-g2"}))
    with pytest.raises(cm.ContentMemoryRefused):
        cm._route_projection(messages, None, decision, sha(decision), **strict)
    generation.write_bytes(raw({"generation": "synthetic-g1"}))
    original = log.read_bytes()
    log.write_bytes(original.replace(b"build", b"BUild", 1))
    with pytest.raises(cm.ContentMemoryRefused):
        cm._route_projection(messages, None, decision, sha(decision), **strict)
    log.write_bytes(original)
    changed_policy, changed_pin = policy()
    with pytest.raises(cm.ContentMemoryRefused):
        cm._route_projection(dict(messages, policy_bytes=changed_policy, policy_sha256=changed_pin),
                             None, decision, sha(decision), **strict)
