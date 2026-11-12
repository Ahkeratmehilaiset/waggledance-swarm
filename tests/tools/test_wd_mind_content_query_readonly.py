"""Synthetic RED contract for the messages-only content verification slice.

This first slice proves source re-derivation and result resolution. It does not
claim an implemented semantic query, encoder parity, native snapshot loading,
live pins, or an approved runtime. Those remain required before task completion.
Historical shapes are from exact 30fc2dbcf6df1c8e8716064d45b5e2dd0105fef3,
shared_content_projection.py:429-439,493-495. The v1 fixture below has no
meta.to or identifier-policy tag and has review_signal_excluded=True. It is
comparison evidence, not current policy and not executed historical parity.
The CURRENT contract explicitly uses v2 combined/message/decision schemas:
review discussion is admitted under Lead's bound 2026-11-12 21:31Z contract;
RCO rows and sensitive content remain excluded. V1 is never silently upgraded.
Every source, identity and key is fake. HMAC consistency is not authorization.

Proposed extraction API, confined to one new reader module:
* module_sha256s(): current extraction-role code pins, never old approvals;
* rederive_message_projection(**verification_inputs): read-only source check;
* validate_content_snapshot(projection_bytes, projection_sha256, **inputs):
  exact re-derivation MATCH in the explicitly versioned current schema;
* resolve_content_results(results, query_utc, **snapshot_inputs): verify first,
  then resolve exact visible projected pairs, rescreen, and disclose only the
  projected hash pair (canonical_event=False) plus a separately typed source
  canonical-event-JSON hash, verified against the anchored prefix. The latter
  is neither an authenticated sender nor the hash of a raw JSONL line.
The explicit inputs below add pinned task-list and identifier-policy bindings
to the historical messages-only verifier. They are synthetic caller pins, not
authorization inferred from hashes. No live path is selected by these tests.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import hmac
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "wd_mind_content_query_readonly.py"
SCHEMA = "wd.mind-content-memory-projection.v2"
MESSAGE_SCHEMA = "wd.bridge-mind-content-projection.v2"
DECISION_SCHEMA = "wd.mind-content-route-decision.v2"
REQUIRED_RELEASE_TASK_LIST_SHA256 = "40e04e7002050b66085f8eff5723feb73e5f62a26910fe73405ab59e5ee5b765"
ROUTE_SCOPE = "wd.q4-shared-projected-events.v1"
TASKS = [f"synthetic-approved-task-{number}" for number in range(8)]
LANES = ("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2")
BUILD_LANES = LANES[:3]
ROSTER = {lane: f"00000000-0000-4000-8000-{number:012d}"
          for number, lane in enumerate(LANES, 1)}
TIME = "2026-01-01T00:00:00.000000Z"
AVAILABLE = "2026-01-01T00:01:00.000000Z"
QUERY_TIME = "2026-01-01T00:02:00Z"
REVIEW_TEXT = "RCO review found a bounded parser issue and the fix is ready"


def _raw(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _serialized(value):
    # The historical projection pin is over this exact pretty byte form.
    return json.dumps(value, sort_keys=True, indent=1).encode("utf-8")


def _row(message=REVIEW_TEXT, **changes):
    result = {"agent": BUILD_LANES[0], "agent_uuid": ROSTER[BUILD_LANES[0]],
              "type": "message", "status": "in_progress", "task_id": TASKS[0],
              "ts_utc": TIME, "to": BUILD_LANES[1], "message": message}
    result.update(changes)
    return result


@pytest.fixture(scope="module")
def reader():
    # An absent module is asserted by _case during each test, so the first RED
    # is a normal failing assertion, not a collection/setup import failure.
    if not SOURCE.is_file():
        return None
    spec = importlib.util.spec_from_file_location("mind_content_readonly_contract", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _synthetic_code_dependency(reader, tmp_path, monkeypatch, relative, data, digest=None):
    """Exercise the real byte/hash guard against test-owned files only."""
    assert reader is not None
    root = tmp_path / "synthetic-code"
    source = root / "tools" / "synthetic-reader.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# synthetic verifier source\n")
    dependency = root / relative
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_bytes(data)
    monkeypatch.setattr(reader, "__file__", str(source))
    monkeypatch.setattr(reader, "_REQUIRED_CODE", {relative: _sha(data) if digest is None else digest})
    return source, dependency


@pytest.mark.parametrize("relative,data", [
    pytest.param("tools/__init__.py", b"", id="empty-tools-package"),
    pytest.param("waggledance/core/__init__.py", b"", id="empty-core-package"),
    pytest.param("synthetic-dependency.py", b"# dependency\n", id="nonempty-module"),
    pytest.param("synthetic-dependency.py", b"x" * (2 * 1024 * 1024), id="exact-size-boundary"),
])
def test_pinned_dependency_bytes_accept_empty_or_boundary_with_matching_hash(
        reader, tmp_path, monkeypatch, relative, data):
    _synthetic_code_dependency(reader, tmp_path, monkeypatch, relative, data)
    reader._check_code()


@pytest.mark.parametrize("data,digest", [
    pytest.param(b"", _sha(b"wrong"), id="empty-wrong-pin"),
    pytest.param(b"# dependency\n", _sha(b""), id="nonempty-empty-pin"),
    pytest.param(b"x" * (2 * 1024 * 1024), _sha(b"wrong"), id="boundary-wrong-pin"),
])
def test_pinned_dependency_bytes_reject_wrong_digest(reader, tmp_path, monkeypatch, data, digest):
    _synthetic_code_dependency(reader, tmp_path, monkeypatch, "synthetic-dependency.py", data, digest)
    with pytest.raises(ValueError, match="^dependency_changed$"):
        reader._check_code()


def test_pinned_dependency_bytes_detect_tampering_after_same_fixture_success(reader, tmp_path, monkeypatch):
    _, dependency = _synthetic_code_dependency(
        reader, tmp_path, monkeypatch, "synthetic-dependency.py", b"# original\n")
    reader._check_code()
    dependency.write_bytes(b"# changed\n")
    with pytest.raises(ValueError, match="^dependency_changed$"):
        reader._check_code()


def test_pinned_dependency_bytes_reject_over_budget_even_with_correct_digest(reader, tmp_path, monkeypatch):
    _synthetic_code_dependency(reader, tmp_path, monkeypatch, "synthetic-dependency.py",
                               b"x" * (2 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="^code_over_budget$"):
        reader._check_code()


def test_pinned_dependency_bytes_reject_missing_file_after_same_fixture_success(reader, tmp_path, monkeypatch):
    _, dependency = _synthetic_code_dependency(
        reader, tmp_path, monkeypatch, "synthetic-dependency.py", b"# original\n")
    reader._check_code()
    dependency.unlink()
    with pytest.raises(FileNotFoundError):
        reader._check_code()


def test_verifier_self_source_must_remain_nonempty(reader, tmp_path, monkeypatch):
    source, _ = _synthetic_code_dependency(
        reader, tmp_path, monkeypatch, "synthetic-dependency.py", b"# dependency\n")
    assert reader.module_sha256s()["content_module_sha256"] == _sha(source.read_bytes())
    source.write_bytes(b"")
    with pytest.raises(reader.ContentQueryRefused):
        reader.module_sha256s()


def test_control_json_pin_must_remain_nonempty(reader):
    assert reader is not None
    with pytest.raises(ValueError, match="^invalid_pin$"):
        reader._pinned(b"", _sha(b""))


def _case(reader, tmp_path, rows=None):
    assert reader is not None, "messages-only source verifier is not implemented"
    from waggledance.core import bridge_log_reader as bridge

    rows = [_row()] if rows is None else rows
    prefix = tmp_path / "synthetic-events.jsonl"
    generation = tmp_path / "synthetic-generation.json"
    source_bytes = b"".join(_raw(row) + b"\n" for row in rows)
    prefix.write_bytes(source_bytes)
    generation.write_bytes(_raw({"generation": "synthetic-g1"}))
    with bridge._open_log(prefix) as handle:
        identity = bridge._file_identity(handle)
    cursor = bridge.BridgeCursor(len(source_bytes), identity, "synthetic-g1")
    task_bytes = _raw(TASKS)
    policy_bytes = _raw({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": TASKS})
    roster_bytes = _raw({"schema": "wd.q4-roster-snapshot.v1",
        "lanes": [{"agent": lane, "agent_uuid": ROSTER[lane]}
                  for lane in sorted(LANES) if lane != "codex-tools-1"],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": ROSTER["codex-tools-1"]},
    })
    message_policy = _raw({
        "schema": "wd.mind-content-policy.v1", "lanes": list(BUILD_LANES),
        "types": ["message", "done"], "approved_tasks": TASKS,
        "removed_source_refs": [], "max_message_chars": 4096, "max_chunk_chars": 300,
    })
    identifier_bytes = _raw({"schema": "wd.mind-identifier-policy.v1",
                             "identifiers": ["Synthetic Private Operator"]})
    identifier_key = b"synthetic-fixture-key-not-a-credential-0000"
    identifier_tag = hmac.new(identifier_key, identifier_bytes, hashlib.sha256).hexdigest()
    anchored = {"sha256": _sha(source_bytes), "offset": cursor.offset, "generation": cursor.generation}
    decision = dict(reader.module_sha256s(), schema=DECISION_SCHEMA,
                    decision="enable_content_route", projection_schema=SCHEMA, scope=ROUTE_SCOPE,
                    nonreview_policy_sha256=_sha(policy_bytes), roster_sha256=_sha(roster_bytes),
                    message_policy_sha256=_sha(message_policy), doc_policy_sha256=None,
                    source_prefix=anchored, message_projection_schema=MESSAGE_SCHEMA,
                    review_signal_excluded=False, approved_tasks_sha256=_sha(task_bytes),
                    identifier_policy_hmac=identifier_tag)
    decision_bytes = _raw(decision)
    return {
        "enabled": True,
        "messages": {"source_path": str(prefix), "generation_path": str(generation),
                     "end_cursor": cursor, "policy_bytes": message_policy,
                     "policy_sha256": _sha(message_policy)},
        "documents": None,
        "decision_bytes": decision_bytes, "decision_sha256": _sha(decision_bytes),
        "policy_bytes": policy_bytes, "policy_sha256": _sha(policy_bytes),
        "roster_bytes": roster_bytes, "roster_sha256": _sha(roster_bytes),
        "approved_tasks_bytes": task_bytes, "approved_tasks_sha256": _sha(task_bytes),
        "identifier_policy_binding": (identifier_bytes, identifier_key, identifier_tag),
    }


def _golden_projection(case, row=None):
    # Independent one-chunk fixture: no expected record is generated by the
    # implementation being tested. ASCII text here needs no content escaping.
    row = _row() if row is None else row
    source_ref = _sha(_raw(row))
    chunk_ref = _sha(_raw({"text": row["message"], "source": source_ref, "chunk": 0}))
    record = {
        "record_id": "content:" + chunk_ref, "kind": "event", "time_utc": TIME,
        "available_utc": AVAILABLE, "stored_text": row["message"], "summary": row["message"],
        "meta": {"agent": row["agent"], "type": row["type"], "task_id": row["task_id"],
                 "text_encoding": "wd.content-text.v1"},
        "provenance": {"source_kind": "bridge_message", "event_ref": chunk_ref,
                       "ref_kind": "sha256_of_content_chunk_and_source", "canonical_event": False,
                       "source_event_ref": source_ref, "chunk_index": 0, "chunk_count": 1,
                       "tier": {"source_type": "bridge_event"}, "available_utc": AVAILABLE},
    }
    messages = {"schema": MESSAGE_SCHEMA, "authority": "none",
                "scope": "shared_content_projection_not_gate",
                "policy_sha256": case["messages"]["policy_sha256"],
                "text_encoding": "wd.content-text.v1", "accepted": 1, "refused": {},
                "records": [record], "authorship": "declared_roster_consistent",
                "review_signal_excluded": False,
                "identifier_policy_hmac": case["identifier_policy_binding"][2]}
    return {"schema": SCHEMA, "authority": "none", "scope": "wd.mind-content-events.v1",
            "messages": messages, "documents": None}


def _historical_v1_projection(case, row):
    """One neutral synthetic record in exact 30fc's published v1 key shape.

    Kept independent of _golden_projection so a new-format change cannot
    silently alter the historical expected record. No private policy existed
    in this v1 shape. The test never treats its absence as current permission.
    """
    source_ref = _sha(_raw(row))
    chunk_ref = _sha(_raw({"text": row["message"], "source": source_ref, "chunk": 0}))
    record = {
        "record_id": "content:" + chunk_ref, "kind": "event", "time_utc": TIME,
        "available_utc": AVAILABLE, "stored_text": row["message"], "summary": row["message"],
        "meta": {"agent": row["agent"], "type": row["type"], "task_id": row["task_id"],
                 "text_encoding": "wd.content-text.v1"},
        "provenance": {"source_kind": "bridge_message", "event_ref": chunk_ref,
                       "ref_kind": "sha256_of_content_chunk_and_source", "canonical_event": False,
                       "source_event_ref": source_ref, "chunk_index": 0, "chunk_count": 1,
                       "tier": {"source_type": "bridge_event"}, "available_utc": AVAILABLE},
    }
    messages = {
        "schema": "wd.bridge-mind-content-projection.v1", "authority": "none",
        "scope": "shared_content_projection_not_gate", "policy_sha256": case["messages"]["policy_sha256"],
        "text_encoding": "wd.content-text.v1", "accepted": 1, "refused": {}, "records": [record],
        "authorship": "declared_roster_consistent", "review_signal_excluded": True,
    }
    return {"schema": "wd.mind-content-memory-projection.v1", "authority": "none",
            "scope": "wd.mind-content-events.v1", "messages": messages, "documents": None}


def _snapshot(case, projection=None):
    projection = _golden_projection(case) if projection is None else projection
    data = _serialized(projection)
    return dict(case, projection_bytes=data, projection_sha256=_sha(data))


def _edit_decision(case, **changes):
    decision = json.loads(case["decision_bytes"])
    decision.update(changes)
    case["decision_bytes"] = _raw(decision)
    case["decision_sha256"] = _sha(case["decision_bytes"])


def _derive(reader, case):
    return reader.rederive_message_projection(**case)


def _check(reader, snapshot):
    return reader.validate_content_snapshot(**snapshot)


def _refused(reader, operation):
    with pytest.raises(reader.ContentQueryRefused) as caught:
        operation()
    # Nothing from source messages, paths, query or private policy is echoed.
    assert str(caught.value) == "content_query_refused"


def test_allowed_build_lane_review_discussion_matches_independent_projection(reader, tmp_path):
    case = _case(reader, tmp_path)
    assert _derive(reader, case) == _golden_projection(case)
    report = _check(reader, _snapshot(case))
    assert report["status"] == "MATCH"
    assert report["projection_sha256"] == _snapshot(case)["projection_sha256"]
    assert report["decision_sha256"] == case["decision_sha256"]
    assert report["doc_commit"] is None


def test_real_historical_v1_shape_does_not_silently_enable_current_policy(reader, tmp_path):
    row = _row("Network retry path completed")
    case = _case(reader, tmp_path, [row])
    legacy = _historical_v1_projection(case, row)
    # Exact sets read from 30fc, not the later d108 module. New current tags or
    # recipient metadata cannot be smuggled under this historical schema name.
    assert set(legacy["messages"]) == {
        "schema", "authority", "scope", "policy_sha256", "text_encoding", "accepted",
        "refused", "records", "authorship", "review_signal_excluded",
    }
    assert set(legacy["messages"]["records"][0]["meta"]) == {
        "agent", "type", "task_id", "text_encoding",
    }
    _refused(reader, lambda: _check(reader, _snapshot(case, legacy)))


@pytest.mark.parametrize("fault", ["combined", "messages", "review_flag", "identifier_tag", "recipient"])
def test_current_format_cannot_mix_v1_names_or_unbound_metadata(reader, tmp_path, fault):
    case = _case(reader, tmp_path)
    projection = _golden_projection(case)
    if fault == "combined":
        projection["schema"] = "wd.mind-content-memory-projection.v1"
    elif fault == "messages":
        projection["messages"]["schema"] = "wd.bridge-mind-content-projection.v1"
    elif fault == "review_flag":
        projection["messages"]["review_signal_excluded"] = True
    elif fault == "identifier_tag":
        projection["messages"].pop("identifier_policy_hmac")
    else:
        projection["messages"]["records"][0]["meta"]["to"] = "codex-tools-1"
    _refused(reader, lambda: _check(reader, _snapshot(case, projection)))


@pytest.mark.parametrize("fault", ["schema", "projection_schema", "message_projection_schema",
                                   "review_signal_excluded", "approved_tasks_sha256", "identifier_policy_hmac"])
def test_current_decision_binds_explicit_format_and_operator_policy(reader, tmp_path, fault):
    case = _case(reader, tmp_path)
    other = {
        "schema": "wd.mind-content-route-decision.v1",
        "projection_schema": "wd.mind-content-memory-projection.v1",
        "message_projection_schema": "wd.bridge-mind-content-projection.v1",
        "review_signal_excluded": True, "approved_tasks_sha256": "f" * 64,
        "identifier_policy_hmac": "f" * 64,
    }
    _edit_decision(case, **{fault: other[fault]})
    _refused(reader, lambda: _check(reader, _snapshot(case)))


@pytest.mark.parametrize("lane", BUILD_LANES)
def test_each_build_lane_may_discuss_review_without_exact_source_allowlist(reader, tmp_path, lane):
    row = _row(agent=lane, agent_uuid=ROSTER[lane])
    projection = _derive(reader, _case(reader, tmp_path, [row]))
    assert projection["messages"]["accepted"] == 1
    assert projection["messages"]["records"][0]["stored_text"] == REVIEW_TEXT


@pytest.mark.parametrize("change", [
    {"agent": lane, "agent_uuid": ROSTER[lane]} for lane in LANES[3:]
] + [
    {"to": "claude-rco-1"}, {"to": ["codex-tools-1", "claude-rco-2"]},
    {"payload": {"cc": ["claude-rco-1"]}},
    {"payload": {"expected_responders": {"claude-rco-2": {}}}},
])
def test_rco_authorship_and_nested_recipient_forms_are_excluded(reader, tmp_path, change):
    projection = _derive(reader, _case(reader, tmp_path, [_row(**change)]))
    assert projection["messages"]["records"] == []
    assert projection["messages"]["accepted"] == 0


@pytest.mark.parametrize("message", [
    "sealed synthetic case", "GOLD synthetic case", "heldout synthetic material",
    "judge synthetic material", "sk-syntheticcredentialvalue123456",
    "Authorization: Bearer syntheticcredentialvalue123456",
    "Synthetic Private Operator approved this action", "contact fixture@example.invalid",
    "password=syntheticfixturevalue", "-----BEGIN PRIVATE KEY-----",
])
def test_sensitive_classes_never_become_content_records(reader, tmp_path, message):
    projection = _derive(reader, _case(reader, tmp_path, [_row(message)]))
    assert projection["messages"]["records"] == []
    assert message not in json.dumps(projection)


@pytest.mark.parametrize("change", [
    {"task_id": "synthetic-outside-approved-eight"},
    {"agent_uuid": "00000000-0000-4000-8000-999999999999"},
    {"type": "rco_pass"},
])
def test_unapproved_task_identity_and_event_type_cannot_be_admitted(reader, tmp_path, change):
    projection = _derive(reader, _case(reader, tmp_path, [_row(**change)]))
    assert projection["messages"]["records"] == []


@pytest.mark.parametrize("field", [
    "policy_bytes", "policy_sha256", "roster_bytes", "roster_sha256",
    "decision_bytes", "decision_sha256", "approved_tasks_bytes", "approved_tasks_sha256",
    "identifier_policy_binding",
])
def test_missing_required_pin_refuses_before_any_source_read(reader, tmp_path, monkeypatch, field):
    snapshot = _snapshot(_case(reader, tmp_path))
    snapshot[field] = None
    from tools import wd_mind_content_prefix_readonly as prefix

    def no_read(*args, **kwargs):
        pytest.fail("invalid pin reached source I/O")
    monkeypatch.setattr(prefix, "read_prefix_rows", no_read)
    _refused(reader, lambda: _check(reader, snapshot))


@pytest.mark.parametrize("field", [
    "policy_sha256", "roster_sha256", "decision_sha256", "approved_tasks_sha256",
    "projection_sha256",
])
def test_wrong_digest_refuses_before_source_read(reader, tmp_path, monkeypatch, field):
    snapshot = _snapshot(_case(reader, tmp_path))
    snapshot[field] = "0" * 64
    from tools import wd_mind_content_prefix_readonly as prefix

    def no_read(*args, **kwargs):
        pytest.fail("wrong digest reached source I/O")
    monkeypatch.setattr(prefix, "read_prefix_rows", no_read)
    _refused(reader, lambda: _check(reader, snapshot))


@pytest.mark.parametrize("field", [
    "content_module_sha256", "message_module_sha256", "doc_module_sha256",
    "nonreview_policy_sha256", "roster_sha256", "message_policy_sha256",
])
def test_rehashed_decision_cannot_change_code_or_policy_binding(reader, tmp_path, field):
    case = _case(reader, tmp_path)
    _edit_decision(case, **{field: "0" * 64})
    _refused(reader, lambda: _check(reader, _snapshot(case)))


@pytest.mark.parametrize("fault", ["hash", "generation", "offset", "identity", "source_bytes"])
def test_source_prefix_cursor_generation_and_identity_are_bound(reader, tmp_path, fault):
    case = _case(reader, tmp_path)
    messages = case["messages"]
    if fault == "source_bytes":
        path = Path(messages["source_path"])
        data = path.read_bytes()
        changed = data.replace(b"bounded parser", b"changed parser", 1)
        assert changed != data and len(changed) == len(data)
        path.write_bytes(changed)
    elif fault == "identity":
        messages["end_cursor"] = dataclasses.replace(messages["end_cursor"], file_identity="synthetic-wrong")
    else:
        prefix = json.loads(case["decision_bytes"])["source_prefix"]
        field = {"hash": "sha256", "generation": "generation", "offset": "offset"}[fault]
        prefix[field] = {"hash": "0" * 64, "generation": "synthetic-other", "offset": prefix["offset"] - 1}[fault]
        _edit_decision(case, source_prefix=prefix)
    _refused(reader, lambda: _check(reader, _snapshot(case)))


def test_append_beyond_the_anchored_prefix_is_not_new_content(reader, tmp_path):
    case = _case(reader, tmp_path)
    with Path(case["messages"]["source_path"]).open("ab") as handle:
        handle.write(_raw(_row("later append must remain outside this snapshot")) + b"\n")
    assert _check(reader, _snapshot(case))["status"] == "MATCH"


@pytest.mark.parametrize("fault", ["text", "source_ref", "record_id", "extra_record", "documents"])
def test_self_consistent_rehashed_projection_is_not_source_proof(reader, tmp_path, fault):
    case = _case(reader, tmp_path)
    projection = _golden_projection(case)
    record = projection["messages"]["records"][0]
    if fault == "text":
        record["stored_text"] = record["summary"] = "forged but rehashed content"
    elif fault == "source_ref":
        record["provenance"]["source_event_ref"] = "f" * 64
    elif fault == "record_id":
        record["record_id"] = "content:" + "f" * 64
    elif fault == "extra_record":
        projection["messages"]["records"].append(copy.deepcopy(record))
        projection["messages"]["accepted"] = 2
    else:
        projection["documents"] = {"records": []}
    _refused(reader, lambda: _check(reader, _snapshot(case, projection)))


def test_document_source_is_closed_even_when_the_decision_is_rehashed(reader, tmp_path):
    case = _case(reader, tmp_path)
    _edit_decision(case, doc_policy_sha256="a" * 64)
    case["documents"] = {"repo_root": "synthetic-document-root"}
    _refused(reader, lambda: _check(reader, _snapshot(case)))


def test_bad_identifier_policy_binding_refuses_without_echo(reader, tmp_path):
    case = _case(reader, tmp_path)
    raw, key, _ = case["identifier_policy_binding"]
    case["identifier_policy_binding"] = (raw, key, "0" * 64)
    _refused(reader, lambda: _check(reader, _snapshot(case)))


def test_rekeying_identifier_policy_does_not_transfer_the_pinned_decision(reader, tmp_path):
    case = _case(reader, tmp_path)
    raw = _raw({"schema": "wd.mind-identifier-policy.v1", "identifiers": ["Other Synthetic Person"]})
    key = b"another-synthetic-fixture-key-not-a-credential-0000"
    tag = hmac.new(key, raw, hashlib.sha256).hexdigest()
    case["identifier_policy_binding"] = (raw, key, tag)
    # All three self-supplied policy bytes/key/tag agree, but the unchanged
    # pinned decision does not bind this policy. This proves consistency only.
    _refused(reader, lambda: _check(reader, _snapshot(case)))


def test_eight_task_policy_cannot_be_widened_by_rehashing_both_policies(reader, tmp_path):
    case = _case(reader, tmp_path)
    policy = json.loads(case["policy_bytes"])
    policy["approved_tasks"].append("synthetic-outside-approved-eight")
    case["policy_bytes"] = _raw(policy)
    case["policy_sha256"] = _sha(case["policy_bytes"])
    message_policy = json.loads(case["messages"]["policy_bytes"])
    message_policy["approved_tasks"] = policy["approved_tasks"]
    case["messages"]["policy_bytes"] = _raw(message_policy)
    case["messages"]["policy_sha256"] = _sha(case["messages"]["policy_bytes"])
    _edit_decision(case, nonreview_policy_sha256=case["policy_sha256"],
                   message_policy_sha256=case["messages"]["policy_sha256"])
    _refused(reader, lambda: _check(reader, _snapshot(case)))


@pytest.mark.parametrize("fault", [
    "raw_fleet", "wrong_schema", "missing_lanes", "missing_supervisor",
    "missing_rco1", "missing_rco2", "missing_lead", "missing_fable", "missing_tools",
    "unknown_lane", "sixth_lane", "duplicate_agent", "duplicate_uuid", "swapped_supervisor",
    "extra_top_field", "extra_entry_field", "extra_supervisor_field", "entry_missing_uuid",
    "uppercase_uuid", "role_type", "role_token", "role_length", "lane_order",
    "duplicate_json_key", "noncanonical_bytes",
])
def test_only_canonical_complete_five_lane_roster_snapshots_are_accepted(reader, tmp_path, monkeypatch, fault):
    case = _case(reader, tmp_path)
    roster = json.loads(case["roster_bytes"])
    lanes = roster["lanes"]
    if fault == "raw_fleet":
        roster.pop("schema")
    elif fault == "wrong_schema":
        roster["schema"] = "wd-fleet.v1"
    elif fault == "missing_lanes":
        roster.pop("lanes")
    elif fault == "missing_supervisor":
        roster.pop("tools_supervisor")
    elif fault.startswith("missing_"):
        lane = {"missing_rco1": "claude-rco-1", "missing_rco2": "claude-rco-2",
                "missing_lead": "codex-lead-1", "missing_fable": "fable-5",
                "missing_tools": "codex-tools-1"}[fault]
        if lane == "codex-tools-1":
            roster["tools_supervisor"] = {}
        else:
            roster["lanes"] = [entry for entry in lanes if entry["agent"] != lane]
    elif fault == "unknown_lane":
        lanes[0]["agent"] = "synthetic-unknown-lane"
    elif fault == "sixth_lane":
        lanes.append({"agent": "synthetic-unknown-lane", "agent_uuid": "00000000-0000-4000-8000-999999999999"})
    elif fault == "duplicate_agent":
        lanes.append(copy.deepcopy(lanes[0]))
    elif fault == "duplicate_uuid":
        lanes[0]["agent_uuid"] = lanes[1]["agent_uuid"]
    elif fault == "swapped_supervisor":
        lead = next(number for number, entry in enumerate(lanes) if entry["agent"] == "codex-lead-1")
        lanes[lead], roster["tools_supervisor"] = roster["tools_supervisor"], lanes[lead]
        lanes.sort(key=lambda entry: entry["agent"])
    elif fault == "extra_top_field":
        roster["model"] = "synthetic-extra"
    elif fault == "extra_entry_field":
        lanes[0]["worktree"] = "synthetic-extra"
    elif fault == "extra_supervisor_field":
        roster["tools_supervisor"]["prompt"] = "synthetic-extra"
    elif fault == "entry_missing_uuid":
        lanes[0].pop("agent_uuid")
    elif fault == "uppercase_uuid":
        lanes[0]["agent_uuid"] = "A0000000-0000-4000-8000-000000000001"
    elif fault == "role_type":
        lanes[0]["role"] = 1
    elif fault == "role_token":
        lanes[0]["role"] = "not_a_role"
    elif fault == "role_length":
        lanes[0]["role"] = "x" * 41
    elif fault == "lane_order":
        lanes.reverse()
    raw = _raw(roster)
    if fault == "duplicate_json_key":
        raw = b'{"schema":"synthetic-other",' + raw[1:]
    elif fault == "noncanonical_bytes":
        raw = json.dumps(roster, sort_keys=True, indent=1).encode()
    case["roster_bytes"], case["roster_sha256"] = raw, _sha(raw)
    _edit_decision(case, roster_sha256=case["roster_sha256"])
    def no_read(*args, **kwargs):
        pytest.fail("noncanonical roster reached source prefix I/O")
    # Monitor the verification-to-source seam, rather than monkeypatching a
    # dependency entrypoint whose wrong origin is itself a refusal condition.
    monkeypatch.setattr(reader, "_projection", no_read)
    _refused(reader, lambda: _check(reader, _snapshot(case)))


def test_canonical_roster_allows_only_constrained_optional_roles(reader, tmp_path):
    case = _case(reader, tmp_path)
    roster = json.loads(case["roster_bytes"])
    for entry in roster["lanes"]:
        entry["role"] = "synthetic-role"
    roster["tools_supervisor"]["role"] = "tools-tests"
    case["roster_bytes"] = _raw(roster)
    case["roster_sha256"] = _sha(case["roster_bytes"])
    _edit_decision(case, roster_sha256=case["roster_sha256"])
    assert _check(reader, _snapshot(case))["status"] == "MATCH"


@pytest.mark.parametrize("fault", ["prefix_origin", "prefix_function", "bridge_origin"])
def test_preloaded_or_shadowed_dependency_is_refused_before_it_executes(reader, tmp_path, monkeypatch, fault):
    # A fake import can supply valid-looking rows while the disk hashes remain
    # correct. No hostile code/native/model call occurs: the only side effect
    # would be appending to this local synthetic list, which must stay empty.
    case = _case(reader, tmp_path)
    from tools import wd_mind_content_prefix_readonly as prefix
    from waggledance.core import bridge_log_reader as bridge
    import tools
    import waggledance.core

    called = []
    if fault.startswith("prefix"):
        fake = ModuleType("tools.wd_mind_content_prefix_readonly")
        fake.__file__ = (str(tmp_path / "synthetic-shadow-prefix.py") if fault == "prefix_origin" else prefix.__file__)
        fake.__spec__ = prefix.__spec__

        def read_rows(*args, **kwargs):
            called.append("untrusted_prefix_function")
            return {_sha(_raw(_row())): _row()}
        fake.read_prefix_rows = read_rows
        monkeypatch.setitem(sys.modules, fake.__name__, fake)
        monkeypatch.setattr(tools, "wd_mind_content_prefix_readonly", fake)
    else:
        fake = ModuleType("waggledance.core.bridge_log_reader")
        fake.__file__ = str(tmp_path / "synthetic-shadow-bridge.py")
        fake.__spec__ = bridge.__spec__
        fake.BridgeCursor = bridge.BridgeCursor

        def cursor_check(*args, **kwargs):
            called.append("untrusted_bridge_function")
            return None
        fake._validate_cursor = cursor_check
        monkeypatch.setitem(sys.modules, fake.__name__, fake)
        monkeypatch.setattr(waggledance.core, "bridge_log_reader", fake)
    _refused(reader, lambda: _check(reader, _snapshot(case)))
    assert called == []


def test_coherently_repinned_other_eight_tasks_never_claims_operator_release_authority(reader, tmp_path):
    # Low-level source consistency is not the missing production caller guard.
    # A future deployment/query caller must independently enforce the fixed
    # operator list SHA; this verifier reports the obligation and UNKNOWN.
    case = _case(reader, tmp_path)
    different = [TASKS[0]] + [f"synthetic-other-approved-task-{number}" for number in range(7)]
    case["approved_tasks_bytes"] = _raw(different)
    case["approved_tasks_sha256"] = _sha(case["approved_tasks_bytes"])
    assert case["approved_tasks_sha256"] != REQUIRED_RELEASE_TASK_LIST_SHA256
    policy = json.loads(case["policy_bytes"])
    policy["approved_tasks"] = different
    case["policy_bytes"] = _raw(policy)
    case["policy_sha256"] = _sha(case["policy_bytes"])
    message_policy = json.loads(case["messages"]["policy_bytes"])
    message_policy["approved_tasks"] = different
    case["messages"]["policy_bytes"] = _raw(message_policy)
    case["messages"]["policy_sha256"] = _sha(case["messages"]["policy_bytes"])
    _edit_decision(case, approved_tasks_sha256=case["approved_tasks_sha256"],
                   nonreview_policy_sha256=case["policy_sha256"],
                   message_policy_sha256=case["messages"]["policy_sha256"])
    report = _check(reader, _snapshot(case))
    assert report["status"] == "MATCH"
    assert report.get("operator_scope_authorization") == "UNKNOWN"
    assert report.get("required_release_task_list_sha256") == REQUIRED_RELEASE_TASK_LIST_SHA256


def _hit(projection):
    record = projection["messages"]["records"][0]
    return {"ref_sha256": _sha(record["provenance"]["event_ref"].encode()),
            "doc_id_sha256": _sha(record["record_id"].encode()),
            "ref_kind": "sha256_of_projected_metadata", "canonical_event": False}


def test_resolved_result_verifies_source_event_json_ref_without_claiming_sender_auth(reader, tmp_path):
    case = _case(reader, tmp_path)
    hit = _hit(_golden_projection(case))
    out = reader.resolve_content_results([hit], QUERY_TIME, **_snapshot(case))
    assert out == [dict(hit, stored_text=REVIEW_TEXT, source_reference={
        "sha256": _sha(_raw(_row())), "kind": "sha256_of_canonical_event_json",
        "verified": "MATCH_WITHIN_PREFIX", "sender_authentication": "UNKNOWN",
    })]
    assert out[0]["canonical_event"] is False  # labels the projected hash pair
    source_bytes = Path(case["messages"]["source_path"]).read_bytes()
    assert out[0]["source_reference"]["sha256"] != _sha(source_bytes)  # JSONL includes LF


@pytest.mark.parametrize("fault", ["ref", "pair", "canonical", "kind", "time", "excluded", "source_sidecar"])
def test_forged_excluded_and_time_invisible_result_is_refused(reader, tmp_path, fault):
    case = _case(reader, tmp_path)
    hit = _hit(_golden_projection(case))
    query_time = QUERY_TIME
    if fault == "ref":
        hit["ref_sha256"] = "f" * 64
    elif fault == "pair":
        hit["doc_id_sha256"] = "f" * 64
    elif fault == "canonical":
        hit["canonical_event"] = True
    elif fault == "kind":
        hit["ref_kind"] = "canonical_event_json_sha256"
    elif fault == "time":
        query_time = "2026-01-01T00:01:01Z"  # strict one-second guard band
    elif fault == "source_sidecar":
        hit["source_reference"] = {"sha256": "f" * 64}  # never trust backend-supplied provenance
    else:
        # Source now really is an excluded recipient, with the source/decision
        # pins honestly updated. A formerly valid hit still must not resolve.
        case = _case(reader, tmp_path, [_row(to="claude-rco-2")])
    _refused(reader, lambda: reader.resolve_content_results([hit], query_time, **_snapshot(case)))


def test_resolver_revalidates_source_after_match(reader, tmp_path):
    case = _case(reader, tmp_path)
    snapshot = _snapshot(case)
    assert _check(reader, snapshot)["status"] == "MATCH"
    Path(case["messages"]["generation_path"]).write_bytes(_raw({"generation": "synthetic-g2"}))
    _refused(reader, lambda: reader.resolve_content_results([_hit(_golden_projection(case))], QUERY_TIME,
                                                          **snapshot))


@pytest.mark.parametrize("enabled", [False, None, 1, "true"])
def test_non_true_enable_never_reads_source(reader, tmp_path, monkeypatch, enabled):
    snapshot = _snapshot(_case(reader, tmp_path))
    snapshot["enabled"] = enabled
    from tools import wd_mind_content_prefix_readonly as prefix

    def no_read(*args, **kwargs):
        pytest.fail("disabled verifier reached source I/O")
    monkeypatch.setattr(prefix, "read_prefix_rows", no_read)
    _refused(reader, lambda: _check(reader, snapshot))
