"""Synthetic in-memory agent answer seam: never a live agent or snapshot."""
import copy
import hashlib
import json

import pytest

from sandbox.mind.step2 import shared_projection as projection
from tools import wd_q4_lane_query as lane_query


def sha(data):
    return hashlib.sha256(data).hexdigest()


def answer_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"


@pytest.fixture
def data():
    lane = "codex-lead-1"
    lanes = (lane, "claude-rco-1", "claude-rco-2", "fable-5", "codex-tools-1")
    roster = {name: f"{index:08x}-abcd-4ef0-8abc-000000000001" for index, name in enumerate(lanes, 1)}
    task = "synthetic-task"
    event = {"ts_utc": "2026-01-01T00:00:00Z", "agent": lane, "agent_uuid": roster[lane],
             "type": "message", "status": "milestone", "task_id": task}
    doc = projection.project_events([event], approved_tasks=frozenset({task}), roster=roster,
                                    exclude_review_signal=True)
    raw = json.dumps(doc, sort_keys=True, indent=1).encode("utf-8")
    policy = json.dumps({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": [task]}).encode()
    roster_raw = json.dumps({"schema": "wd.q4-roster-snapshot.v1",
                             "lanes": [{"agent": name, "agent_uuid": roster[name]} for name in lanes[:-1]],
                             "tools_supervisor": {"agent": lanes[-1], "agent_uuid": roster[lanes[-1]]}}).encode()
    binding = {key: "a" * 64 for key in lane_query.BINDING_KEYS}
    binding.update(projection_sha256=sha(raw), policy_sha256=sha(policy), roster_sha256=sha(roster_raw))
    record = doc["records"][0]
    ref = record["provenance"]["event_ref"]
    item = {"ref_sha256": sha(ref.encode()), "doc_id_sha256": sha(record["record_id"].encode()),
            "stored_text": record["stored_text"]}
    answer = {"schema": "wd.q4-lane-answer.v1", "lane": lane, "status": "ANSWER", "reason": "",
              "result_count": 1, "results": [item], "binding": binding, "trust": dict(lane_query._TRUST)}
    values = {"lane": lane, "query_bytes": b"synthetic-private-query", "projection_bytes": raw,
              "policy_bytes": policy, "roster_bytes": roster_raw, "expected_binding": binding,
              "query_utc": "2026-01-01T00:01:02.000000Z", "current_utc": "2026-01-01T00:01:10.000000Z",
              "max_age_seconds": 8}
    return values, answer


def invoke(**kwargs):
    from tools.wd_q4_agent_answer import query_agent_answer
    return query_agent_answer(**kwargs)


def test_default_off_has_no_effect_and_does_not_touch_inputs():
    class Opaque:
        def __getattribute__(self, key):
            raise AssertionError("inspected")
    assert invoke(lane=Opaque(), query_bytes=Opaque(), projection_bytes=Opaque(), policy_bytes=Opaque(),
                  roster_bytes=Opaque(), expected_binding=Opaque(), query_utc=Opaque(), current_utc=Opaque(),
                  max_age_seconds=Opaque(), transport=Opaque()) == {"status": "OFF"}


def test_exact_answer_and_time_are_bound_and_transport_is_called_once(data):
    values, answer = data
    calls = []
    def transport(*, query_bytes, query_utc):
        calls.append((query_bytes, query_utc))
        return {"status": "OK", "query_utc": query_utc, "output": answer_bytes(answer)}
    result = invoke(**values, transport=transport, enabled=True)
    assert calls == [(values["query_bytes"], values["query_utc"])]
    assert result == {"status": "OK", "answer": answer, "absence_proven": False,
                      "freshness": "UNKNOWN", "caller_authentication": "UNKNOWN"}
    assert b"synthetic-private-query" not in json.dumps(result).encode()


def test_abstain_does_not_prove_absence(data):
    values, answer = data
    answer.update(status="ABSTAIN", result_count=0, results=[])
    result = invoke(**values, transport=lambda **kw: {"status": "OK", "query_utc": kw["query_utc"],
                    "output": answer_bytes(answer)}, enabled=True)
    assert result["answer"]["status"] == "ABSTAIN" and result["absence_proven"] is False


@pytest.mark.parametrize("fault", ["bad-projection", "bad-policy", "bad-roster", "bad-binding", "stale", "future",
                                  "bad-lane", "bad-query", "bad-transport", "not-bool"])
def test_preflight_refuses_without_call(data, fault):
    values, _ = data
    values = copy.deepcopy(values)
    calls = []
    def transport(**kwargs): calls.append(kwargs)
    if fault == "bad-projection": values["projection_bytes"] += b" "
    if fault == "bad-policy": values["policy_bytes"] += b" "
    if fault == "bad-roster": values["roster_bytes"] += b" "
    if fault == "bad-binding": values["expected_binding"]["projection_sha256"] = "0" * 64
    if fault == "stale": values["max_age_seconds"] = 7
    if fault == "future": values["current_utc"] = "2026-01-01T00:01:01.000000Z"
    if fault == "bad-lane": values["lane"] = "unknown"
    if fault == "bad-query": values["query_bytes"] = b""
    with pytest.raises(ValueError, match="^agent_answer_refused$") as caught:
        invoke(**values, transport=None if fault == "bad-transport" else transport,
               enabled=1 if fault == "not-bool" else True)
    assert calls == [] and caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("fault", ["unknown", "exception", "timeout", "malformed", "wrong-time", "refused", "extra", "bad-output",
                                  "wrong-lane", "wrong-binding", "forged-text", "bad-frame"])
def test_rejects_transport_and_answer_errors_without_retry_or_query_leak(data, fault):
    values, answer = data
    answer = copy.deepcopy(answer)
    if fault == "wrong-lane": answer["lane"] = "fable-5"
    if fault == "wrong-binding": answer["binding"]["pointer_sha256"] = "0" * 64
    if fault == "forged-text": answer["results"][0]["stored_text"] = "forged text"
    calls = []
    def transport(**kwargs):
        calls.append(1)
        if fault == "exception": raise RuntimeError("synthetic-private-query")
        if fault == "malformed": return None
        result = {"status": "UNKNOWN" if fault == "unknown" else "TIMEOUT" if fault == "timeout"
                  else "REFUSED" if fault == "refused" else "OK",
                  "query_utc": "2026-01-01T00:00:00.000000Z" if fault == "wrong-time" else kwargs["query_utc"],
                  "output": None if fault == "refused" else answer_bytes(answer)}
        if fault == "extra": result["extra"] = "synthetic-private-query"
        if fault == "bad-output": result["output"] = b"not-json\n"
        if fault == "bad-frame": result["output"] = answer_bytes(answer).rstrip(b"\n")
        return result
    reason = "agent_answer_unknown" if fault in ("unknown", "exception", "timeout", "malformed", "wrong-time", "extra") else "agent_answer_refused"
    with pytest.raises(ValueError, match="^" + reason + "$") as caught:
        invoke(**values, transport=transport, enabled=True)
    assert len(calls) == 1 and caught.value.__cause__ is None and caught.value.__context__ is None
    assert "synthetic-private-query" not in repr(caught.value)
    trace = caught.value.__traceback__
    while trace is not None and trace.tb_frame.f_code.co_name != "query_agent_answer":
        trace = trace.tb_next
    assert trace is not None and all(value is not values["query_bytes"] for value in trace.tb_frame.f_locals.values())


@pytest.mark.parametrize("fault", ("missing-time", "missing-output", "extra", "wrong-time", "wrong-output"))
def test_only_complete_bound_refusal_is_a_refusal(data, fault):
    values, _ = data
    def transport(**kwargs):
        receipt = {"status": "REFUSED", "query_utc": kwargs["query_utc"], "output": None}
        if fault == "missing-time": del receipt["query_utc"]
        if fault == "missing-output": del receipt["output"]
        if fault == "extra": receipt["extra"] = "unexpected"
        if fault == "wrong-time": receipt["query_utc"] = "2026-01-01T00:00:00.000000Z"
        if fault == "wrong-output": receipt["output"] = b""
        return receipt
    with pytest.raises(ValueError, match="^agent_answer_unknown$"):
        invoke(**values, transport=transport, enabled=True)


@pytest.mark.parametrize("fault", ("missing-time", "missing-output", "wrong-type"))
def test_incomplete_ok_receipt_cannot_establish_cleanup(data, fault):
    values, answer = data
    def transport(**kwargs):
        receipt = {"status": "OK", "query_utc": kwargs["query_utc"], "output": answer_bytes(answer)}
        if fault == "missing-time": del receipt["query_utc"]
        if fault == "missing-output": del receipt["output"]
        if fault == "wrong-type": receipt["output"] = "not-bytes"
        return receipt
    with pytest.raises(ValueError, match="^agent_answer_unknown$"):
        invoke(**values, transport=transport, enabled=True)


class SyntheticCancellation(BaseException):
    pass


@pytest.mark.parametrize("error", (RuntimeError, KeyboardInterrupt, GeneratorExit, SyntheticCancellation))
def test_untrusted_colliding_receipt_keys_are_unknown_without_invoking_equality(data, error):
    values, answer = data
    comparisons = []
    class CollidingKey:
        def __hash__(self):
            return hash("status")
        def __eq__(self, other):
            comparisons.append(True)
            raise error("synthetic-private-query")
    def transport(**kwargs):
        return {CollidingKey(): "OK", "query_utc": kwargs["query_utc"], "output": answer_bytes(answer)}
    with pytest.raises(ValueError, match="^agent_answer_unknown$") as caught:
        invoke(**values, transport=transport, enabled=True)
    assert comparisons == []
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert "synthetic-private-query" not in repr(caught.value)


@pytest.mark.parametrize("interruption", (KeyboardInterrupt, SystemExit, GeneratorExit))
def test_interruption_remains_an_interruption_with_sanitized_unknown(data, interruption):
    values, _ = data
    def transport(**kwargs):
        raise interruption("synthetic-private-query")
    with pytest.raises(interruption, match="^agent_answer_unknown$") as caught:
        invoke(**values, transport=transport, enabled=True)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_other_baseexception_stays_non_exception_and_does_not_leak_query(data):
    values, _ = data
    class Cancellation(BaseException):
        pass
    def transport(**kwargs):
        raise Cancellation("synthetic-private-query")
    with pytest.raises(BaseException, match="^agent_answer_unknown$") as caught:
        invoke(**values, transport=transport, enabled=True)
    assert not isinstance(caught.value, Exception)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_callback_cannot_mutate_validated_binding_through_the_callers_dict(data):
    values, answer = data
    original = copy.deepcopy(answer)
    def transport(**kwargs):
        values["expected_binding"]["pointer_sha256"] = "0" * 64
        return {"status": "OK", "query_utc": kwargs["query_utc"], "output": answer_bytes(original)}
    result = invoke(**values, transport=transport, enabled=True)
    assert result["answer"] == original
