"""Synthetic caller-output contract; this is not a live production witness.

The operator's proposed policy requests answer text with an honest age label.
These tests exercise only caller formatting, not the independently required
projection screening, pinned snapshot rechecks, launcher, or activation.
"""

import json

import pytest

from tools import wd_q4_lane_caller_cli as cli
from tools import wd_q4_lane_query
from tests.tools.test_wd_q4_content_lane_query import content, lane


TOKEN = "synthetic-owner-token-never-output"
TEXT = "Synthetic build lane work and its reviewed outcome."
LANE = "codex-lead-1"
IDENTITY = "a" * 64
DESCRIPTOR = "b" * 64


def result_for(binding, text=TEXT):
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": LANE, "status": "ANSWER", "reason": "",
              "result_count": 1, "results": [{"stored_text": text, "ref_sha256": "c" * 64,
                                                "doc_id_sha256": "d" * 64}],
              "binding": binding, "trust": dict(cli._TRUST)}
    return {"schema": "wd.q4-lane-caller-result.v1", "status": "VALIDATED", "lane": LANE,
            "lane_binding": "DECLARED_CONSISTENT", "lane_authentication": "UNKNOWN",
            "cleanup": "chain_confirmed", "inner_status": "ANSWER", "result_count": 1,
            "answer": answer, "answer_sha256": cli._sha(cli._canon(answer)), "identity_sha256": IDENTITY,
            "descriptor_sha256": DESCRIPTOR, "call_id": "e" * 32}


def emit(binding, *, text=TEXT, expected=None):
    result = result_for(binding, text)
    return cli._output(result, LANE, IDENTITY, DESCRIPTOR, binding if expected is None else expected, 65536, TOKEN)


def test_content_binding_carries_text_and_explicit_unknown_index_age():
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    raw = emit(binding)
    assert TOKEN not in raw
    result = json.loads(raw)
    assert result["schema"] == "wd.q4-lane-content-delivery.v1"
    assert result["freshness"] == "UNKNOWN"
    assert result["index_age"] == {"status": "UNKNOWN", "seconds": None}
    assert result["answer"]["results"][0]["stored_text"] == TEXT
    assert result["answer"]["results"][0]["ref_sha256"] == "c" * 64


def test_pinned_synthetic_content_route_reaches_delivered_answer(lane):
    # The real content lane query re-derives approved synthetic source inputs;
    # transport, encoder and data are synthetic, so this is not a live witness.
    answer = wd_q4_lane_query.query_public(**lane["call"])
    assert answer["status"] == "ANSWER" and answer["results"]
    binding = answer["binding"]
    result = result_for(binding)
    result.update(lane=answer["lane"], answer=answer, inner_status=answer["status"],
                  result_count=answer["result_count"], answer_sha256=cli._sha(cli._canon(answer)))
    raw = cli._output(result, answer["lane"], IDENTITY, DESCRIPTOR, binding, 65536, TOKEN)
    delivered = json.loads(raw)
    assert delivered["schema"] == "wd.q4-lane-content-delivery.v1"
    assert delivered["freshness"] == "UNKNOWN"
    assert delivered["answer"]["results"] == answer["results"]


@pytest.mark.parametrize("keys", [cli._BINDING_KEYS, cli._CARD_BINDING_KEYS, cli._STAGED_BINDING_KEYS],
                         ids=["legacy", "card", "staged"])
def test_noncontent_binding_does_not_gain_content_release(keys):
    binding = {key: "2" * 64 for key in keys}
    raw = emit(binding)
    assert json.loads(raw)["status"] == "CONTENT_WITHHELD"
    assert TEXT not in raw


def test_token_in_content_is_refused():
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    with pytest.raises(ValueError):
        emit(binding, text=TOKEN)


def test_expanded_delivery_envelope_cannot_exceed_output_bound():
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    result = result_for(binding)
    original_limit = len(cli._canon(result)) + 1
    assert len(emit(binding).encode("ascii")) > original_limit
    with pytest.raises(ValueError):
        cli._output(result, LANE, IDENTITY, DESCRIPTOR, binding, original_limit, TOKEN)


def test_degraded_content_does_not_silently_deliver_text():
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    result = result_for(binding)
    result["inner_status"] = "DEGRADED"
    result["answer"]["status"] = "DEGRADED"
    result["answer_sha256"] = cli._sha(cli._canon(result["answer"]))
    raw = cli._output(result, LANE, IDENTITY, DESCRIPTOR, binding, 65536, TOKEN)
    assert json.loads(raw)["status"] == "CONTENT_WITHHELD"
    assert TEXT not in raw


def test_absent_content_remains_empty_and_is_not_an_answer():
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    result = result_for(binding)
    result.update(inner_status="ABSTAIN", result_count=0)
    result["answer"].update(status="ABSTAIN", result_count=0, results=[])
    result["answer_sha256"] = cli._sha(cli._canon(result["answer"]))
    raw = cli._output(result, LANE, IDENTITY, DESCRIPTOR, binding, 65536, TOKEN)
    assert json.loads(raw)["inner_status"] == "ABSTAIN"
    assert TEXT not in raw


@pytest.mark.parametrize("key", sorted(cli._CONTENT_BINDING_KEYS))
def test_mismatched_content_binding_is_refused(key):
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    expected = dict(binding, **{key: "3" * 64})
    with pytest.raises(ValueError):
        emit(binding, expected=expected)


@pytest.mark.parametrize("kind", ["missing", "extra", "nonhex"])
def test_malformed_content_binding_is_refused(kind):
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    if kind == "missing":
        binding.pop("memory_sha256")
    elif kind == "extra":
        binding["unexpected_sha256"] = "2" * 64
    else:
        binding["memory_sha256"] = "z" * 64
    with pytest.raises(ValueError):
        emit(binding)


@pytest.mark.parametrize("field", ["result_lane", "answer_lane", "identity", "descriptor", "answer_hash",
                                    "freshness", "publisher", "cleanup"])
def test_changed_identity_trust_or_hash_is_refused(field):
    binding = {key: "2" * 64 for key in cli._CONTENT_BINDING_KEYS}
    result = result_for(binding)
    if field == "result_lane":
        result["lane"] = "fable-5"
    elif field == "answer_lane":
        result["answer"]["lane"] = "fable-5"
    elif field == "identity":
        result["identity_sha256"] = "f" * 64
    elif field == "descriptor":
        result["descriptor_sha256"] = "f" * 64
    elif field == "answer_hash":
        result["answer_sha256"] = "f" * 64
    elif field == "freshness":
        result["answer"]["trust"]["freshness"] = "VERIFIED"
    elif field == "publisher":
        result["answer"]["trust"]["publisher_authentication"] = "VERIFIED"
    elif field == "cleanup":
        result["cleanup"] = "UNKNOWN"
    if field in ("answer_lane", "freshness", "publisher"):
        result["answer_sha256"] = cli._sha(cli._canon(result["answer"]))
    with pytest.raises(ValueError):
        cli._output(result, LANE, IDENTITY, DESCRIPTOR, binding, 65536, TOKEN)
