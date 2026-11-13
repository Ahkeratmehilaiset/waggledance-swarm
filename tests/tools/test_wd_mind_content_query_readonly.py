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


# Synthetic semantic contract addition. The only real policy metadata below is
# the independently frozen public eight-task list; events/identities/assets,
# index data, query and providers are synthetic. This is not runtime authority.
import io
import struct

OPERATOR_TASK_BYTES = b'[\n  "lead-mind-4h-fable-memory-20261108-v1",\n  "lead-mind-4h-integration-tools-20261108-v1",\n  "lead-mind-4h-live-anchor-fable-20261108-v1",\n  "lead-mind-4h-package-20261108",\n  "lead-mind-4h-tools-package-20261108-v1",\n  "lead-mind-current-recall-policy-fable-20261108-v1",\n  "lead-mind-final-head-memory-fable-20261108-v1",\n  "lead-mind-production-20261031"\n]\n'
SEMANTIC_SCHEMA = "wd.q4-content-snapshot-query.v2"
SEMANTIC_DIM = 384
SEMANTIC_TEXTS = (
    "network retry network retry network retry",
    "RCO review discussed network retry adapter",
    "network retry restored transport",
    "network retry restored channel",
)


class _ObservedStream(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.reads = 0

    def read(self, size=-1):
        self.reads += 1
        return super().read(size)


class _SyntheticEncoder:
    def __init__(self, identity):
        self.identity = copy.deepcopy(identity)
        self.calls = []
        self.on_query = None

    def query(self, text):
        self.calls.append("query: " + text[:2000])
        if self.on_query:
            self.on_query()
        return (1.0,) + (0.0,) * (SEMANTIC_DIM - 1)


class _SyntheticFlatIP:
    metric_type = 0  # FAISS METRIC_INNER_PRODUCT

    def __init__(self, vectors):
        self.d = SEMANTIC_DIM
        self.ntotal = len(vectors)
        self._vectors = tuple(tuple(row) for row in vectors)

    def reconstruct_n(self, start, count):
        return self._vectors[start:start + count]


class _SyntheticIndexProvider:
    def __init__(self, identity, indices):
        self.identity = copy.deepcopy(identity)
        self.indices = indices
        self.calls = []
        self.on_load = None

    def load(self, index_bytes):
        self.calls.append(index_bytes)
        assert index_bytes in self.indices, "only the verified synthetic index may be decoded"
        index = _SyntheticFlatIP(self.indices[index_bytes])
        if self.on_load:
            self.on_load(index)
        return index


def _semantic_projection(case, rows):
    projection = _golden_projection(case, rows[0])
    records = [_golden_projection(case, row)["messages"]["records"][0] for row in rows]
    records.sort(key=lambda item: (item["time_utc"], item["provenance"]["source_event_ref"],
                                  item["provenance"]["chunk_index"]))
    projection["messages"].update(accepted=len(records), records=records)
    return projection


def _semantic_cell(records, cell_id, index_bytes, vectors, encoder_identity, projection_sha256):
    ids, texts, provenance = [], [], []
    for record in records:
        for view in ("w", "d"):
            doc_id = record["record_id"] + "#" + view
            ids.append(doc_id)
            texts.append(record["stored_text"])
            provenance.append({"record_id": doc_id, "kind": "event", "time_utc": record["time_utc"],
                "cell_id": cell_id, "provenance": dict(record["provenance"], record=record["record_id"],
                    view=view, ref_kind="sha256_of_projected_metadata", canonical_event=False)})
    vector_bytes = b"".join(struct.pack("<" + "f" * SEMANTIC_DIM, *row) for row in vectors)
    pairs = b"".join(doc_id.encode() + b"\0" + struct.pack("<" + "f" * SEMANTIC_DIM, *row)
                     for doc_id, row in zip(ids, vectors))
    artifacts = {"index": index_bytes, "vectors": vector_bytes,
        "meta": _raw({"dim": SEMANTIC_DIM, "doc_ids": ids, "texts": texts, "metadata": [{} for _ in ids]}),
        "provenance": b"".join(_raw(row) + b"\n" for row in provenance)}
    content = {"cell_id": cell_id, "count": len(ids), "dim": SEMANTIC_DIM,
        "cutoff_utc": AVAILABLE, "encoder": encoder_identity,
        "projection_sha256": projection_sha256, "projection_schema": SCHEMA,
        "files": {name: _sha(raw) for name, raw in artifacts.items()},
        "ids_sha256": _sha("\n".join(ids).encode()), "pairs_sha256": _sha(pairs)}
    artifacts["receipt"] = _raw({"schema": "wd.bridge-mind-hex-cell.v1", "authority": "none",
        "scope": ROUTE_SCOPE, "content": content, "content_sha256": _sha(_raw(content))})
    receipt_sha = _sha(artifacts["receipt"])
    pointer = {"generation": 1, "seq": 1, "receipt_sha256": receipt_sha}
    log = dict(pointer, schema="wd.bridge-mind-hex-cell-log.v1", prev_line_sha256="0" * 64)
    return {"pointer": _raw(pointer), "log": _raw(log) + b"\n", "artifacts": artifacts}, pointer


def _semantic_case(reader, tmp_path, *, count=4):
    tasks = json.loads(OPERATOR_TASK_BYTES)
    assert _sha(OPERATOR_TASK_BYTES) == REQUIRED_RELEASE_TASK_LIST_SHA256
    rows = [_row(text, task_id=tasks[0], type="message" if i < 2 else "done")
            for i, text in enumerate(SEMANTIC_TEXTS[:count])]
    case = _case(reader, tmp_path, rows)
    policy = json.loads(case["policy_bytes"])
    policy["approved_tasks"] = tasks
    case.update(policy_bytes=_raw(policy), policy_sha256=_sha(_raw(policy)),
                approved_tasks_bytes=OPERATOR_TASK_BYTES, approved_tasks_sha256=_sha(OPERATOR_TASK_BYTES))
    message_policy = json.loads(case["messages"]["policy_bytes"])
    message_policy["approved_tasks"] = tasks
    case["messages"].update(policy_bytes=_raw(message_policy), policy_sha256=_sha(_raw(message_policy)))
    _edit_decision(case, nonreview_policy_sha256=case["policy_sha256"],
                   message_policy_sha256=case["messages"]["policy_sha256"],
                   approved_tasks_sha256=case["approved_tasks_sha256"])
    projection = _semantic_projection(case, rows)
    snapshot = _snapshot(case, projection)
    # Same-fixture positive control: a broken source fixture must not masquerade
    # as a missing semantic feature or a valid negative pin test.
    assert _check(reader, snapshot)["status"] == "MATCH"
    identity = {"model": "intfloat/multilingual-e5-small", "revision": "synthetic-revision-v1",
                "weights_sha256": _sha(b"synthetic-weights-no-real-model")}
    encoder_binding = {"schema": "wd.mind-readonly-encoder-binding.v1", "encoder": identity,
        "code_sha256": _sha(b"synthetic-encoder-code"), "query_policy": {
            "prefix": "query: ", "truncate_chars": 2000, "normalize": True, "dtype": "float32", "dim": SEMANTIC_DIM}}
    index_binding = {"schema": "wd.mind-readonly-index-binding.v1", "index_type": "IndexFlatIP",
        "metric_type": 0, "code_sha256": _sha(b"synthetic-index-reader-code")}
    encoder = _SyntheticEncoder(encoder_binding)
    cells, pointers, indices = {}, {}, {}
    for cell_id, records in (("general", [record for record in projection["messages"]["records"]
                                         if record["meta"]["type"] == "message"]),
                             ("system", [record for record in projection["messages"]["records"]
                                         if record["meta"]["type"] == "done"])):
        if not records:
            continue
        vectors = []
        for record in records:
            # The strongest lexical record is orthogonal to the query. A
            # keyword-only implementation therefore disagrees with this case.
            head = (0.0, 1.0) if record["stored_text"] == SEMANTIC_TEXTS[0] else (1.0, 0.0)
            vector = head + (0.0,) * (SEMANTIC_DIM - 2)
            vectors.extend((vector, vector))
        index_bytes = b"synthetic-faiss-flat-ip:" + cell_id.encode()
        indices[index_bytes] = vectors
        cells[cell_id], pointers[cell_id] = _semantic_cell(
            records, cell_id, index_bytes, vectors, identity, snapshot["projection_sha256"])
        pointers[cell_id]["count"] = len(vectors)
    manifest = {"schema": "wd.bridge-mind-hex-memory-step2.v1", "authority": "none", "scope": ROUTE_SCOPE,
        "dim": SEMANTIC_DIM, "encoder": identity, "projection_schema": SCHEMA,
        "cutoff_utc": AVAILABLE,
        "projection_sha256": snapshot["projection_sha256"], "cells": pointers,
        "records": count, "views": ["w", "d"], "independent_views": False}
    memory_bytes = _raw(manifest)
    descriptor = {"schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": ROUTE_SCOPE,
                  "memory_sha256": _sha(memory_bytes), "dim": SEMANTIC_DIM, "encoder": identity}
    return dict(snapshot, descriptor_bytes=_raw(descriptor), descriptor_sha256=_sha(_raw(descriptor)),
        memory_bytes=memory_bytes, memory_sha256=_sha(memory_bytes), cell_snapshots=cells,
        encoder_sha256=_sha(_raw(identity)), runtime_revision=identity["revision"],
        encoder_provider=encoder, encoder_binding_bytes=_raw(encoder_binding),
        encoder_binding_sha256=_sha(_raw(encoder_binding)),
        index_provider=_SyntheticIndexProvider(index_binding, indices),
        index_binding_bytes=_raw(index_binding), index_binding_sha256=_sha(_raw(index_binding)),
        stream=_ObservedStream(_raw({"situation": "retry network outage", "time": QUERY_TIME})))


def _semantic_entry(reader, case):
    entry = getattr(reader, "query_content_snapshot", None)
    assert callable(entry), "frozen messages-only semantic query is not implemented"
    return entry(**case)


def _semantic_refuses_without_callbacks(reader, case):
    encoder, index, stream = case["encoder_provider"], case["index_provider"], case["stream"]
    _refused(reader, lambda: _semantic_entry(reader, case))
    assert stream.reads == 0 and encoder.calls == [] and index.calls == []


def test_semantic_query_uses_both_vector_views_and_lexical_agreement(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    out = _semantic_entry(reader, case)
    assert out["schema"] == SEMANTIC_SCHEMA and out["rule"] == "q4-rule-v1.2"
    assert out["status"] == "ANSWER" and 1 <= len(out["results"]) <= 3
    assert all(hit["stored_text"] != SEMANTIC_TEXTS[0] for hit in out["results"])
    assert case["encoder_provider"].calls == ["query: retry network outage"]
    assert len(case["index_provider"].calls) == len(case["cell_snapshots"])
    projection = json.loads(case["projection_bytes"])
    records = projection["messages"]["records"]
    allowed = {(_sha(row["provenance"]["event_ref"].encode()), _sha(row["record_id"].encode())): row for row in records}
    for hit in out["results"]:
        row = allowed[(hit["ref_sha256"], hit["doc_id_sha256"])]
        assert hit["ref_kind"] == "sha256_of_projected_metadata" and hit["canonical_event"] is False
        assert hit["stored_text"] == row["stored_text"]
        assert hit["source_reference"] == {"sha256": row["provenance"]["source_event_ref"],
            "kind": "sha256_of_canonical_event_json", "verified": "MATCH_WITHIN_PREFIX", "sender_authentication": "UNKNOWN"}
    assert out["binding"]["memory_sha256"] == case["memory_sha256"]
    assert out["binding"]["encoder_sha256"] == case["encoder_sha256"]
    assert out["binding"]["approved_tasks_sha256"] == REQUIRED_RELEASE_TASK_LIST_SHA256
    assert out["publisher_authentication"] == "UNKNOWN"


@pytest.mark.parametrize("count", [1, 3])
def test_semantic_low_candidate_count_abstains_without_encoder(reader, tmp_path, count):
    case = _semantic_case(reader, tmp_path, count=count)
    out = _semantic_entry(reader, case)
    assert out["status"] == "ABSTAIN" and out["results"] == []
    assert case["encoder_provider"].calls == []


@pytest.mark.parametrize("enabled", [False, None, 1, "true"])
def test_semantic_query_is_default_closed_before_input_or_providers(reader, tmp_path, enabled):
    case = _semantic_case(reader, tmp_path)
    case["enabled"] = enabled
    _semantic_refuses_without_callbacks(reader, case)


@pytest.mark.parametrize("field", ["descriptor_sha256", "memory_sha256", "projection_sha256", "policy_sha256",
    "roster_sha256", "decision_sha256", "approved_tasks_sha256", "encoder_sha256", "encoder_binding_sha256", "index_binding_sha256"])
@pytest.mark.parametrize("bad", [None, "f" * 64])
def test_semantic_missing_or_wrong_pin_refuses_before_input_or_providers(reader, tmp_path, field, bad):
    case = _semantic_case(reader, tmp_path)
    case[field] = bad
    _semantic_refuses_without_callbacks(reader, case)


@pytest.mark.parametrize("field,value", [("dim", 383), ("scope", "other"),
    ("schema", "wd.bridge-mind-q4-memory-descriptor.v1"), ("memory_sha256", "f" * 64)])
def test_semantic_rehashed_descriptor_mismatch_refuses_before_callbacks(reader, tmp_path, field, value):
    case = _semantic_case(reader, tmp_path)
    descriptor = json.loads(case["descriptor_bytes"])
    descriptor[field] = value
    case.update(descriptor_bytes=_raw(descriptor), descriptor_sha256=_sha(_raw(descriptor)))
    _semantic_refuses_without_callbacks(reader, case)


@pytest.mark.parametrize("field", ["revision", "weights_sha256", "model"])
def test_semantic_provider_identity_mismatch_refuses_before_callbacks(reader, tmp_path, field):
    case = _semantic_case(reader, tmp_path)
    case["encoder_provider"].identity["encoder"][field] = "unbound"
    _semantic_refuses_without_callbacks(reader, case)


def test_semantic_runtime_revision_mismatch_refuses_before_callbacks(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    case["runtime_revision"] = "other-revision"
    _semantic_refuses_without_callbacks(reader, case)


@pytest.mark.parametrize("artifact", ["index", "meta", "provenance", "vectors"])
def test_semantic_second_cell_tampering_prevents_any_index_decode(reader, tmp_path, artifact):
    case = _semantic_case(reader, tmp_path)
    case["cell_snapshots"]["system"]["artifacts"][artifact] += b"synthetic-tamper"
    _semantic_refuses_without_callbacks(reader, case)


def test_semantic_coherently_repinned_same_tasks_require_original_operator_bytes(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    compact = _raw(json.loads(OPERATOR_TASK_BYTES))
    assert _sha(compact) != REQUIRED_RELEASE_TASK_LIST_SHA256
    case.update(approved_tasks_bytes=compact, approved_tasks_sha256=_sha(compact))
    _edit_decision(case, approved_tasks_sha256=_sha(compact))
    source_keys = ("enabled", "messages", "documents", "decision_bytes", "decision_sha256", "policy_bytes",
        "policy_sha256", "roster_bytes", "roster_sha256", "approved_tasks_bytes", "approved_tasks_sha256",
        "identifier_policy_binding", "projection_bytes", "projection_sha256")
    report = _check(reader, {name: case[name] for name in source_keys})
    assert report["status"] == "MATCH" and report["operator_scope_authorization"] == "UNKNOWN"
    _semantic_refuses_without_callbacks(reader, case)


def _semantic_repin_cell(case, cell_id):
    cell = case["cell_snapshots"][cell_id]
    data = cell["artifacts"]
    receipt = json.loads(data["receipt"])
    meta = json.loads(data["meta"])
    pairs = b"".join(doc_id.encode() + b"\0" + vector for doc_id, vector in zip(
        meta["doc_ids"], (data["vectors"][i:i + 4 * SEMANTIC_DIM]
                         for i in range(0, len(data["vectors"]), 4 * SEMANTIC_DIM))))
    receipt["content"].update(files={name: _sha(raw) for name, raw in data.items() if name != "receipt"},
        ids_sha256=_sha("\n".join(meta["doc_ids"]).encode()), pairs_sha256=_sha(pairs))
    receipt["content_sha256"] = _sha(_raw(receipt["content"]))
    data["receipt"] = _raw(receipt)
    digest = _sha(data["receipt"])
    pointer, log = json.loads(cell["pointer"]), json.loads(cell["log"])
    pointer["receipt_sha256"] = log["receipt_sha256"] = digest
    cell.update(pointer=_raw(pointer), log=_raw(log) + b"\n")
    manifest = json.loads(case["memory_bytes"])
    manifest["cells"][cell_id]["receipt_sha256"] = digest
    case.update(memory_bytes=_raw(manifest), memory_sha256=_sha(_raw(manifest)))
    descriptor = json.loads(case["descriptor_bytes"])
    descriptor["memory_sha256"] = case["memory_sha256"]
    case.update(descriptor_bytes=_raw(descriptor), descriptor_sha256=_sha(_raw(descriptor)))


def test_semantic_fully_rehashed_foreign_record_refuses_before_index_decode(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    data = case["cell_snapshots"]["system"]["artifacts"]
    rows = [json.loads(line) for line in data["provenance"].splitlines()]
    meta = json.loads(data["meta"])
    for i, view in enumerate(("w", "d")):
        forged = "content:" + _sha(b"outside-approved-projection")
        rows[i]["record_id"] = meta["doc_ids"][i] = forged + "#" + view
        rows[i]["provenance"].update(record=forged, event_ref=_sha(b"outside-chunk"))
    data.update(meta=_raw(meta), provenance=b"".join(_raw(row) + b"\n" for row in rows))
    _semantic_repin_cell(case, "system")
    _semantic_refuses_without_callbacks(reader, case)


@pytest.mark.parametrize("fault", ["dimension", "count", "metric", "pairs"])
def test_semantic_decoded_index_must_match_verified_metadata_before_query(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    def corrupt(index):
        if fault == "dimension":
            index.d += 1
        elif fault == "count":
            index.ntotal -= 1
        elif fault == "metric":
            index.metric_type = 1
        else:
            index._vectors = tuple((0.0,) * SEMANTIC_DIM for _ in index._vectors)
    case["index_provider"].on_load = corrupt
    _refused(reader, lambda: _semantic_entry(reader, case))
    assert case["index_provider"].calls
    assert case["encoder_provider"].calls == [] and case["stream"].reads == 0


def test_semantic_time_guard_abstains_at_exact_one_second_boundary(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    case["stream"] = _ObservedStream(_raw({"situation": "retry network outage", "time": "2026-01-01T00:01:01Z"}))
    out = _semantic_entry(reader, case)
    assert out["status"] == "ABSTAIN" and out["results"] == []
    assert case["encoder_provider"].calls == []


def test_semantic_source_change_during_query_refuses_final_output(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    path = Path(case["messages"]["source_path"])
    original = path.read_bytes()
    case["encoder_provider"].on_query = lambda: path.write_bytes(b" " + original[1:])
    _refused(reader, lambda: _semantic_entry(reader, case))
    assert case["encoder_provider"].calls == ["query: retry network outage"]


def test_semantic_query_leaves_environment_and_owned_input_files_unchanged(reader, tmp_path):
    import os
    case = _semantic_case(reader, tmp_path)
    before_env = dict(os.environ)
    before_files = {str(path): _sha(path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file()}
    out = _semantic_entry(reader, case)
    assert out["status"] == "ANSWER"
    assert dict(os.environ) == before_env
    assert {str(path): _sha(path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file()} == before_files



def test_semantic_fixture_query_avoids_short_query_self_match():
    import re
    joined = [" " + " ".join(re.findall(r"[^\W_]+", text.lower())) + " " for text in SEMANTIC_TEXTS]
    assert all(" network retry " in text for text in joined)
    assert any(" retry network " in text for text in joined)
    assert all(" retry network outage " not in text for text in joined)
    assert all("outage" not in text.split() for text in joined)


def test_semantic_verbatim_short_query_self_match_abstains_without_encoder(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    case["stream"] = _ObservedStream(_raw({"situation": "network retry", "time": QUERY_TIME}))
    out = _semantic_entry(reader, case)
    assert out["status"] == "ABSTAIN" and out["results"] == []
    assert case["encoder_provider"].calls == []


def _semantic_schema_preflight(reader, case, location, schema):
    """Repin controls coherently so a digest mismatch cannot mask schema checks."""
    if location == "memory":
        manifest = json.loads(case["memory_bytes"])
        manifest["projection_schema"] = schema
        case.update(memory_bytes=_raw(manifest), memory_sha256=_sha(_raw(manifest)))
        descriptor = json.loads(case["descriptor_bytes"])
        descriptor["memory_sha256"] = case["memory_sha256"]
        case.update(descriptor_bytes=_raw(descriptor), descriptor_sha256=_sha(_raw(descriptor)))
        return reader._q4_controls(case)[1]
    cell = case["cell_snapshots"][location]
    receipt = json.loads(cell["artifacts"]["receipt"])
    receipt["content"]["projection_schema"] = schema
    receipt["content_sha256"] = _sha(_raw(receipt["content"]))
    cell["artifacts"]["receipt"] = _raw(receipt)
    _semantic_repin_cell(case, location)
    manifest = json.loads(case["memory_bytes"])
    records = {row["record_id"]: row for row in json.loads(case["projection_bytes"])["messages"]["records"]}
    return reader._q4_cell_controls(location, cell, manifest["cells"][location], manifest, records)[1]


@pytest.mark.parametrize("location", ["memory", "general", "system"])
def test_semantic_preflight_requires_content_projection_schema(reader, tmp_path, location):
    case = _semantic_case(reader, tmp_path)
    content = _semantic_schema_preflight(reader, case, location, SCHEMA)
    assert content["projection_schema"] == SCHEMA
    assert case["stream"].reads == 0
    assert not case["encoder_provider"].calls and not case["index_provider"].calls


@pytest.mark.parametrize("location", ["memory", "general", "system"])
@pytest.mark.parametrize("schema", [
    "wd.mind-identifier-policy.v1", MESSAGE_SCHEMA, DECISION_SCHEMA, None, {"schema": SCHEMA},
], ids=["identifier-policy", "message-projection", "route-decision", "missing", "object"])
def test_semantic_preflight_rejects_other_schema_domains(reader, tmp_path, location, schema):
    case = _semantic_case(reader, tmp_path)
    token = "invalid_memory" if location == "memory" else "invalid_cell_receipt"
    with pytest.raises(ValueError, match="^" + token + "$"):
        _semantic_schema_preflight(reader, case, location, schema)
    assert case["stream"].reads == 0
    assert not case["encoder_provider"].calls and not case["index_provider"].calls


# Provider ABI class contract: fake float32 objects only; no NumPy/native/model.
import struct as _provider_struct


def _provider_f32(value):
    return _provider_struct.unpack("<f", _provider_struct.pack("<f", value))[0]


class _ProviderDtype:
    def __init__(self, text="<f4"):
        self.str = text


class _ProviderScalar32:
    def __init__(self, value):
        self.value = _provider_f32(value)
        self.dtype = _ProviderDtype()
        self.shape = ()

    def item(self):
        return self.value

    def tobytes(self):
        return _provider_struct.pack("<f", self.value)

    def __float__(self):
        raise AssertionError("generic float coercion must never run")


class _ProviderArray32:
    def __init__(self, values, *, matrix=False):
        self.matrix = matrix
        self.values = (tuple(tuple(_provider_f32(v) for v in row) for row in values) if matrix
                       else tuple(_provider_f32(v) for v in values))
        self.shape = ((len(self.values), len(self.values[0]) if self.values else SEMANTIC_DIM) if matrix
                      else (len(self.values),))
        self.dtype = _ProviderDtype()
        self.bytes_override = None
        self.calls = []

    def tolist(self):
        self.calls.append("tolist")
        return [list(row) for row in self.values] if self.matrix else list(self.values)

    def tobytes(self):
        self.calls.append("tobytes")
        if self.bytes_override is not None:
            return self.bytes_override
        flat = [v for row in self.values for v in row] if self.matrix else self.values
        return _provider_struct.pack("<" + "f" * len(flat), *flat)


def _provider_surface(values, mode, *, matrix=False):
    if mode == "array":
        return _ProviderArray32(values, matrix=matrix)
    if mode == "scalars":
        return (tuple(tuple(_ProviderScalar32(v) for v in row) for row in values) if matrix
                else tuple(_ProviderScalar32(v) for v in values))
    return values


class _ProviderNativeEncoder:
    def __init__(self, mode):
        self.vector = (1.0,) + (0.0,) * (SEMANTIC_DIM - 1)
        self.mode = mode
        self.result = None
        self.error = None
        self.calls = []

    def query(self, text):
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else _provider_surface(self.vector, self.mode)


class _ProviderNativeIndex:
    metric_type = 0

    def __init__(self, rows, mode):
        self.d = SEMANTIC_DIM
        self.ntotal = len(rows)
        self.rows = rows
        self.mode = mode
        self.result = None
        self.error = None
        self.calls = []

    def reconstruct_n(self, start, count):
        self.calls.append((start, count))
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else _provider_surface(
            self.rows[start:start + count], self.mode, matrix=True)


def _provider_objects(case, mode="array"):
    encoder = _ProviderNativeEncoder(mode)
    state = {"calls": [], "indices": [], "on_decode": None, "error": None}
    vectors = case["index_provider"].indices

    def decode(raw):
        state["calls"].append(raw)
        if state["error"] is not None:
            raise state["error"]
        index = _ProviderNativeIndex(vectors[raw], mode)
        state["indices"].append(index)
        if state["on_decode"] is not None:
            state["on_decode"](index)
        return index

    return encoder, decode, state


def _provider_arguments(case, encoder, decode):
    return dict(encoder=encoder, index_decoder=decode,
        encoder_binding_bytes=case["encoder_binding_bytes"],
        encoder_binding_sha256=case["encoder_binding_sha256"],
        index_binding_bytes=case["index_binding_bytes"],
        index_binding_sha256=case["index_binding_sha256"],
        index_sha256s=tuple(_sha(cell["artifacts"]["index"]) for cell in case["cell_snapshots"].values()))


def _provider_adapt(reader, arguments):
    entry = getattr(reader, "adapt_readonly_providers", None)
    assert callable(entry), "read-only native provider ABI adapter is not implemented"
    result = entry(**arguments)
    assert type(result) is tuple and len(result) == 2
    return result


def test_provider_adapter_fixture_is_float32_bit_exact():
    values = (0.6, 0.8, -0.0) + (0.0,) * (SEMANTIC_DIM - 3)
    raw = _provider_struct.pack("<" + "f" * SEMANTIC_DIM, *values)
    array = _ProviderArray32(values)
    assert array.tobytes() == raw
    assert _provider_struct.pack("<" + "f" * SEMANTIC_DIM, *array.tolist()) == raw
    scalar = _ProviderScalar32(0.6)
    assert scalar.item() != 0.6
    assert _provider_struct.pack("<f", scalar.item()) == scalar.tobytes()


@pytest.mark.parametrize("mode", ["array", "scalars", "plain"])
def test_provider_adapter_queries_frozen_sources_with_all_numeric_surfaces(reader, tmp_path, mode):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case, mode)
    args = _provider_arguments(case, encoder, decode)
    case["encoder_provider"], case["index_provider"] = _provider_adapt(reader, args)
    assert encoder.calls == [] and state["calls"] == []
    out = _semantic_entry(reader, case)
    assert out["status"] == "ANSWER" and out["results"]
    assert out["provider_authentication"] == "UNKNOWN"
    assert encoder.calls == ["retry network outage"]
    assert len(state["calls"]) == len(case["cell_snapshots"])
    assert all(type(raw) is bytes for raw in state["calls"])


@pytest.mark.parametrize("mode", ["array", "scalars"])
def test_provider_adapter_preserves_nontrivial_float32_and_negative_zero(reader, tmp_path, mode):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, _ = _provider_objects(case, mode)
    encoder.vector = (0.6, 0.8, -0.0) + (0.0,) * (SEMANTIC_DIM - 3)
    wrapped, _ = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    out = wrapped.query("synthetic bounded query")
    assert type(out) is tuple and all(type(v) is float for v in out)
    assert _provider_struct.pack("<" + "f" * SEMANTIC_DIM, *out) == _provider_struct.pack(
        "<" + "f" * SEMANTIC_DIM, *encoder.vector)


@pytest.mark.parametrize("direction", ["encoder", "index"])
@pytest.mark.parametrize("dtype", ["<f8", ">f4", "<i4", "|O"])
def test_provider_adapter_rejects_other_numeric_formats(reader, tmp_path, direction, dtype):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    wrapped, loader = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    if direction == "encoder":
        raw = _ProviderArray32(encoder.vector)
        raw.dtype.str = dtype
        encoder.result = raw
        _refused(reader, lambda: wrapped.query("synthetic query"))
    else:
        state["on_decode"] = lambda index: setattr(index, "result", _ProviderArray32(index.rows, matrix=True))
        index = loader.load(next(iter(case["index_provider"].indices)))
        raw = state["indices"][-1].result
        raw.dtype.str = dtype
        _refused(reader, lambda: index.reconstruct_n(0, index.ntotal))
    assert raw.calls == []


@pytest.mark.parametrize("direction", ["encoder", "index"])
@pytest.mark.parametrize("fault", ["shape", "short_bytes", "different_bits"])
def test_provider_adapter_checks_float32_shape_and_bytes(reader, tmp_path, direction, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    wrapped, loader = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    if direction == "encoder":
        raw = _ProviderArray32(encoder.vector)
        encoder.result = raw
        action = lambda: wrapped.query("synthetic query")
    else:
        state["on_decode"] = lambda index: setattr(index, "result", _ProviderArray32(index.rows, matrix=True))
        index = loader.load(next(iter(case["index_provider"].indices)))
        raw = state["indices"][-1].result
        action = lambda: index.reconstruct_n(0, index.ntotal)
    if fault == "shape":
        raw.shape = (1,)
    else:
        data = raw.tobytes()
        raw.bytes_override = data[:-1] if fault == "short_bytes" else bytes([data[0] ^ 1]) + data[1:]
        raw.calls.clear()
    _refused(reader, action)


@pytest.mark.parametrize("fault", ["short", "long", "nan", "inf", "zero", "norm", "bool"])
def test_provider_adapter_retains_vector_numeric_bounds(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, _ = _provider_objects(case, "plain")
    wrapped, _ = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    value = list(encoder.vector)
    if fault == "short":
        value.pop()
    elif fault == "long":
        value.append(0.0)
    elif fault == "zero":
        value = [0.0] * SEMANTIC_DIM
    else:
        value[0] = {"nan": float("nan"), "inf": float("inf"), "norm": 2.0, "bool": True}[fault]
    encoder.result = value
    _refused(reader, lambda: wrapped.query("synthetic query"))


@pytest.mark.parametrize("raw", [b"", b"not-approved", bytearray(b"x"), memoryview(b"x")])
def test_provider_adapter_rejects_unverified_index_before_decoder(reader, tmp_path, raw):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    _, loader = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    _refused(reader, lambda: loader.load(raw))
    assert state["calls"] == []


@pytest.mark.parametrize("fault", ["encoder_pin", "index_pin", "encoder_extra", "model", "prefix",
                                  "index_type", "metric_bool", "empty_index_pins", "bad_index_pin"])
def test_provider_adapter_factory_is_closed_before_any_callback(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    arguments = _provider_arguments(case, encoder, decode)
    if fault.endswith("_pin") and fault in ("encoder_pin", "index_pin"):
        arguments[fault.removesuffix("_pin") + "_binding_sha256"] = "f" * 64
    elif fault in ("empty_index_pins", "bad_index_pin"):
        arguments["index_sha256s"] = () if fault == "empty_index_pins" else ("bad",)
    else:
        role = "encoder" if fault in ("encoder_extra", "model", "prefix") else "index"
        value = json.loads(arguments[role + "_binding_bytes"])
        if fault == "encoder_extra":
            value["unexpected"] = True
        elif fault == "model":
            value["encoder"]["model"] = "other"
        elif fault == "prefix":
            value["query_policy"]["prefix"] = "passage: "
        elif fault == "index_type":
            value["index_type"] = "other"
        else:
            value["metric_type"] = False
        arguments[role + "_binding_bytes"] = _raw(value)
        arguments[role + "_binding_sha256"] = _sha(_raw(value))
    _refused(reader, lambda: _provider_adapt(reader, arguments))
    assert encoder.calls == [] and state["calls"] == []


@pytest.mark.parametrize("fault", ["dimension", "count", "metric", "matrix_count", "matrix_dimension",
                                  "dimension_type", "count_type", "metric_bool"])
def test_provider_adapter_retains_decoded_index_controls_before_query(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)

    def change(index):
        if fault == "dimension":
            index.d -= 1
        elif fault == "count":
            index.ntotal += 1
        elif fault == "metric":
            index.metric_type = 1
        elif fault == "dimension_type":
            index.d = float(SEMANTIC_DIM)
        elif fault == "count_type":
            index.ntotal = float(index.ntotal)
        elif fault == "metric_bool":
            index.metric_type = False
        elif fault == "matrix_count":
            index.result = _ProviderArray32(index.rows[:-1], matrix=True)
        else:
            index.result = _ProviderArray32([row[:-1] for row in index.rows], matrix=True)

    state["on_decode"] = change
    case["encoder_provider"], case["index_provider"] = _provider_adapt(
        reader, _provider_arguments(case, encoder, decode))
    _refused(reader, lambda: _semantic_entry(reader, case))
    assert state["calls"] and encoder.calls == []


@pytest.mark.parametrize("direction", ["encoder", "decoder"])
def test_provider_adapter_contains_underlying_exception_without_echo(reader, tmp_path, direction):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    wrapped, loader = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    error = RuntimeError("synthetic-private-provider-detail")
    if direction == "encoder":
        encoder.error = error
        _refused(reader, lambda: wrapped.query("synthetic query"))
    else:
        state["error"] = error
        _refused(reader, lambda: loader.load(next(iter(case["index_provider"].indices))))


def test_provider_adapter_never_coerces_arbitrary_float_like_objects(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, _ = _provider_objects(case, "plain")
    calls = []

    class FloatLike:
        def __float__(self):
            calls.append(True)
            return 1.0

    wrapped, _ = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    encoder.result = [FloatLike()] + [0.0] * (SEMANTIC_DIM - 1)
    _refused(reader, lambda: wrapped.query("synthetic query"))
    assert calls == []


def test_provider_adapter_identity_is_an_immutable_copy_not_authentication(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, _ = _provider_objects(case)
    wrapped, loader = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    identity = wrapped.identity
    identity["encoder"]["revision"] = "mutated"
    assert wrapped.identity == json.loads(case["encoder_binding_bytes"])
    with pytest.raises((AttributeError, TypeError)):
        wrapped.identity = identity
    with pytest.raises((AttributeError, TypeError)):
        loader.identity = {}


def test_provider_adapter_preserves_all_cell_preflight_before_decode(reader, tmp_path):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    case["encoder_provider"], case["index_provider"] = _provider_adapt(
        reader, _provider_arguments(case, encoder, decode))
    case["cell_snapshots"]["system"]["artifacts"]["index"] += b"tampered"
    _refused(reader, lambda: _semantic_entry(reader, case))
    assert state["calls"] == [] and encoder.calls == [] and case["stream"].reads == 0


@pytest.mark.parametrize("fault", ["dtype", "shape", "bits", "item_bool"])
def test_provider_adapter_validates_native_scalar_protocol(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, _ = _provider_objects(case, "scalars")
    wrapped, _ = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    values = [_ProviderScalar32(v) for v in encoder.vector]
    scalar = values[0]
    if fault == "dtype":
        scalar.dtype.str = "<f8"
    elif fault == "shape":
        scalar.shape = (1,)
    elif fault == "bits":
        scalar.tobytes = lambda: b"\0" * 4
    else:
        scalar.value = True
    encoder.result = values
    _refused(reader, lambda: wrapped.query("synthetic query"))


@pytest.mark.parametrize("fault", ["negative", "count_bool", "start_bool", "too_many", "partial"])
def test_provider_adapter_reconstruction_is_bounded_before_callback(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case)
    _, loader = _provider_adapt(reader, _provider_arguments(case, encoder, decode))
    index = loader.load(next(iter(case["index_provider"].indices)))
    start, count = 0, index.ntotal
    if fault == "negative":
        start = -1
    elif fault == "count_bool":
        count = True
    elif fault == "start_bool":
        start = False
    elif fault == "too_many":
        count += 1
    else:
        count -= 1
    _refused(reader, lambda: index.reconstruct_n(start, count))
    assert state["indices"][-1].calls == []


@pytest.mark.parametrize("fault", ["bool", "nan", "zero", "norm"])
def test_provider_adapter_applies_numeric_controls_to_index_rows(reader, tmp_path, fault):
    case = _semantic_case(reader, tmp_path)
    encoder, decode, state = _provider_objects(case, "plain")

    def change(index):
        rows = [list(row) for row in index.rows]
        if fault == "zero":
            rows[0] = [0.0] * SEMANTIC_DIM
        else:
            rows[0][0] = {"bool": True, "nan": float("nan"), "norm": 2.0}[fault]
        index.result = rows

    state["on_decode"] = change
    case["encoder_provider"], case["index_provider"] = _provider_adapt(
        reader, _provider_arguments(case, encoder, decode))
    _refused(reader, lambda: _semantic_entry(reader, case))
    assert state["calls"] and encoder.calls == []


def test_provider_adapter_does_not_change_environment_or_input_files(reader, tmp_path):
    import os
    case = _semantic_case(reader, tmp_path)
    before_env = dict(os.environ)
    before_files = {str(path): _sha(path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file()}
    encoder, decode, _ = _provider_objects(case)
    case["encoder_provider"], case["index_provider"] = _provider_adapt(
        reader, _provider_arguments(case, encoder, decode))
    assert _semantic_entry(reader, case)["status"] == "ANSWER"
    assert dict(os.environ) == before_env
    assert {str(path): _sha(path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file()} == before_files
