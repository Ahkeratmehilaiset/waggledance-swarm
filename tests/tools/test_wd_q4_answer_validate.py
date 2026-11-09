"""Synthetic public-answer validation; no live source, query, or runtime."""
import copy
import json

import pytest

LANE = "codex-lead-1"
KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
        "public_policy_sha256", "descriptor_sha256", "projection_sha256")
TRUST = {"lane_authentication": "UNKNOWN", "freshness": "UNKNOWN", "publisher_authentication": "UNKNOWN",
         "projection_public_attestation": "UNKNOWN", "transport": "caller_injected", "projection": "caller_pinned"}


def call(raw, binding, records, *, enabled=True):
    from tools.wd_q4_answer_validate import validate_answer
    return validate_answer(answer_bytes=raw, expected_lane=LANE, expected_binding=binding,
                           allowed_records=records, enabled=enabled)


def enc(value):
    return json.dumps(value, separators=(",", ":")).encode() + b"\n"


@pytest.fixture
def data():
    binding = {key: "a" * 64 for key in KEYS}
    item = {"ref_sha256": "b" * 64, "doc_id_sha256": "c" * 64,
            "stored_text": "message milestone codex-lead-1 synthetic-task ci:green"}
    records = {(item["ref_sha256"], item["doc_id_sha256"]): {"stored_text": item["stored_text"]}}
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": LANE, "status": "ANSWER", "reason": "",
              "result_count": 1, "results": [item], "binding": copy.deepcopy(binding), "trust": dict(TRUST)}
    return answer, binding, records


def test_default_off(data):
    answer, binding, records = data
    with pytest.raises(ValueError, match="^answer_validation_refused$"):
        call(enc(answer), binding, records, enabled=False)


def test_exact_authorized_record_membership(data):
    answer, binding, records = data
    assert call(enc(answer), binding, records) == answer


def test_abstain_is_valid_but_absence_is_not_proven(data):
    answer, binding, records = data
    answer.update(status="ABSTAIN", result_count=0, results=[])
    assert call(enc(answer), binding, records)["status"] == "ABSTAIN"
    answer["status"] = "ABSENT"  # Not a status in the actual answer schema.
    with pytest.raises(ValueError, match="^answer_validation_refused$"):
        call(enc(answer), binding, records)


@pytest.mark.parametrize("fault", [
    "missing_record", "wrong_ref", "wrong_doc", "wrong_text", "private_text", "duplicate_result",
    "wrong_lane", "wrong_binding", "missing_binding", "extra_field", "missing_field", "bad_trust",
    "bool_count", "wrong_count", "empty_answer", "abstain_with_result", "refused", "invalid_json",
    "duplicate_json_key", "unframed", "extra_line", "oversize", "bad_map", "disabled_truthy",
])
def test_refuses_tamper_malformed_privacy_and_unknown(data, fault):
    answer, binding, records = data
    item = answer["results"][0]
    if fault == "missing_record": records.clear()
    elif fault == "wrong_ref": item["ref_sha256"] = "d" * 64
    elif fault == "wrong_doc": item["doc_id_sha256"] = "d" * 64
    elif fault == "wrong_text": item["stored_text"] = "forged projected text"
    elif fault == "private_text": item["stored_text"] = "secret@example.test"
    elif fault == "duplicate_result":
        answer["results"].append(copy.deepcopy(item)); answer["result_count"] = 2
    elif fault == "wrong_lane": answer["lane"] = "fable-5"
    elif fault == "wrong_binding": answer["binding"]["pointer_sha256"] = "e" * 64
    elif fault == "missing_binding": del binding["pointer_sha256"]
    elif fault == "extra_field": answer["private"] = "synthetic-private"
    elif fault == "missing_field": del answer["trust"]
    elif fault == "bad_trust": answer["trust"]["freshness"] = "PROVEN"
    elif fault == "bool_count": answer["result_count"] = True
    elif fault == "wrong_count": answer["result_count"] = 2
    elif fault == "empty_answer": answer.update(result_count=0, results=[])
    elif fault == "abstain_with_result": answer["status"] = "ABSTAIN"
    elif fault == "refused": answer["status"] = "REFUSED"
    elif fault == "bad_map": records[("bad", "c" * 64)] = {"stored_text": "not valid"}
    raw = enc(answer)
    if fault == "invalid_json": raw = b"{\n"
    elif fault == "duplicate_json_key": raw = raw.replace(b'"lane":', b'"lane":"bad","lane":', 1)
    elif fault == "unframed": raw = raw[:-1]
    elif fault == "extra_line": raw += b"{}\n"
    elif fault == "oversize": raw = b"x" * 65537
    with pytest.raises(ValueError, match="^answer_validation_refused$") as caught:
        call(raw, binding, records, enabled=1 if fault == "disabled_truthy" else True)
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_existing_projected_answer_and_full_record_map_pass():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).with_name("test_wd_q4_session_outer_synthetic.py")
    spec = importlib.util.spec_from_file_location("synthetic_projected_answer_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    record, answer, reason = fixture._projected_answer("m0-snapshot-ready")
    assert reason is None
    item = answer["results"][0]
    records = {(item["ref_sha256"], item["doc_id_sha256"]): record}
    assert call(enc(answer), fixture.BINDING, records) == answer


@pytest.mark.parametrize("private", ["C:\\Users\\synthetic\\secret", "secret@example.test", "a" * 40])
def test_matching_but_unsafe_text_is_refused(data, private):
    answer, binding, records = data
    item = answer["results"][0]
    item["stored_text"] = private
    records[(item["ref_sha256"], item["doc_id_sha256"])]["stored_text"] = private
    with pytest.raises(ValueError, match="^answer_validation_refused$"):
        call(enc(answer), binding, records)


def test_refusal_frame_does_not_retain_raw_answer_or_record_map(data):
    answer, binding, records = data
    answer["private"] = "synthetic-private-marker"
    raw = enc(answer)
    with pytest.raises(ValueError, match="^answer_validation_refused$") as caught:
        call(raw, binding, records)
    trace = caught.value.__traceback__
    while trace is not None and trace.tb_frame.f_code.co_name != "validate_answer":
        trace = trace.tb_next
    assert trace is not None
    assert "synthetic-private-marker" not in repr(trace.tb_frame.f_locals)
    assert all(value is not records and value is not raw for value in trace.tb_frame.f_locals.values())
