"""Synthetic pinned projection -> visible record map; never reads live data."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def fixture_module():
    path = Path(__file__).with_name("test_wd_q4_lane_query.py")
    spec = importlib.util.spec_from_file_location("synthetic_allowed_record_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(data, **changes):
    from tools.wd_q4_allowed_records import build_allowed_records
    return build_allowed_records(**dict(data, **changes))


@pytest.fixture
def data():
    fixture = fixture_module()
    projection, raw = fixture._projection([fixture._event(), fixture._event(ts_utc="2026-01-01T00:02:00Z")])
    policy, roster = fixture._policy_bytes(), fixture._roster_bytes()
    binding = {key: "a" * 64 for key in fixture.BINDING_KEYS}
    binding.update(projection_sha256=hashlib.sha256(raw).hexdigest(),
                   policy_sha256=hashlib.sha256(policy).hexdigest(),
                   roster_sha256=hashlib.sha256(roster).hexdigest())
    values = dict(projection_bytes=raw, policy_bytes=policy, roster_bytes=roster, expected_binding=binding,
                  query_utc="2026-01-01T00:01:02.000000Z", current_utc="2026-01-01T00:01:10.000000Z",
                  max_age_seconds=8, enabled=True)
    return values, projection


def test_off_by_default(data):
    values, _ = data
    with pytest.raises(ValueError, match="^allowed_records_refused$"):
        call(values, enabled=False)


def test_visible_map_uses_real_projection_identities(data):
    values, projection = data
    fixture = fixture_module()
    records = call(values)
    assert len(records) == 1
    row = projection["records"][0]
    assert records[fixture._digests(row)] == row


def test_visibility_guard_is_strict_at_one_second_boundary(data):
    values, _ = data
    assert call(values, query_utc="2026-01-01T00:01:01.000000Z",
                current_utc="2026-01-01T00:01:01.000000Z") == {}
    assert len(call(values, query_utc="2026-01-01T00:01:02.000000Z",
                    current_utc="2026-01-01T00:01:02.000000Z")) == 1


def test_map_composes_with_actual_lane_answer_and_validator(data):
    from tools import wd_q4_answer_validate, wd_q4_lane_query
    values, _ = data
    records = call(values)
    key, record = next(iter(records.items()))
    row = {"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": record["stored_text"]}
    answer, reason = wd_q4_lane_query._answer("codex-lead-1",
        {"status": "ANSWER", "binding": values["expected_binding"], "results": [row]},
        values["expected_binding"], records, values["query_utc"])
    assert reason is None
    raw = json.dumps(answer, separators=(",", ":")).encode() + b"\n"
    assert wd_q4_answer_validate.validate_answer(answer_bytes=raw, expected_lane="codex-lead-1",
        expected_binding=values["expected_binding"], allowed_records=records, enabled=True) == answer


@pytest.mark.parametrize("fault", ["projection_pin", "policy_pin", "roster_pin", "other_bad_pin",
                                   "missing_binding", "extra_binding", "future", "stale", "bad_query",
                                   "bad_current", "negative_age", "bool_age", "unbounded_age", "policy_big",
                                   "roster_big", "projection_big", "duplicate_record", "malformed_projection"])
def test_untrusted_or_stale_inputs_refuse(data, fault):
    values, projection = data
    values = copy.deepcopy(values)
    if fault.endswith("_pin"):
        key = {"projection_pin": "projection_sha256", "policy_pin": "policy_sha256",
               "roster_pin": "roster_sha256", "other_bad_pin": "pointer_sha256"}[fault]
        values["expected_binding"][key] = "bad"
    elif fault == "missing_binding": del values["expected_binding"]["pointer_sha256"]
    elif fault == "extra_binding": values["expected_binding"]["extra"] = "a" * 64
    elif fault == "future": values["current_utc"] = "2026-01-01T00:01:01.000000Z"
    elif fault == "stale": values["max_age_seconds"] = 7
    elif fault == "bad_query": values["query_utc"] = "2026-01-01T00:01:02Z"
    elif fault == "bad_current": values["current_utc"] = "not-a-time"
    elif fault == "negative_age": values["max_age_seconds"] = -1
    elif fault == "bool_age": values["max_age_seconds"] = True
    elif fault == "unbounded_age": values["max_age_seconds"] = 2 ** 31
    elif fault == "policy_big": values["policy_bytes"] = b"x" * 65537
    elif fault == "roster_big": values["roster_bytes"] = b"x" * (1024 * 1024 + 1)
    elif fault == "projection_big": values["projection_bytes"] = b"x" * (16 * 1024 * 1024 + 1)
    elif fault == "malformed_projection":
        values["projection_bytes"] = b'{"private":"synthetic"}'
        values["expected_binding"]["projection_sha256"] = hashlib.sha256(values["projection_bytes"]).hexdigest()
    elif fault == "duplicate_record":
        projection["records"].append(copy.deepcopy(projection["records"][0]))
        projection["accepted"] += 1
        raw = json.dumps(projection, sort_keys=True, indent=1).encode()
        values["projection_bytes"] = raw
        values["expected_binding"]["projection_sha256"] = hashlib.sha256(raw).hexdigest()
    with pytest.raises(ValueError, match="^allowed_records_refused$") as error:
        call(values)
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_refusal_frame_does_not_retain_raw_inputs(data):
    values, _ = data
    values["projection_bytes"] = b"synthetic-private-marker"
    with pytest.raises(ValueError, match="^allowed_records_refused$") as error:
        call(values)
    trace = error.value.__traceback__
    while trace is not None and trace.tb_frame.f_code.co_name != "build_allowed_records":
        trace = trace.tb_next
    assert trace is not None
    assert "synthetic-private-marker" not in repr(trace.tb_frame.f_locals)
