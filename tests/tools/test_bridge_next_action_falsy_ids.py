"""Falsy request_id values in the Python wake-request deduplication (tools/bridge_next_action.py).

A wake_request whose request_id is present but falsy and not a string (false, 0, 0.0, [], {}) is an INVALID id, not a
missing one: it must use the shared typed request_key (invalid-id + canonical content), so two different such requests
never collapse into one legacy (agent, task, status) entry, while an exact retry still coalesces. Only null, "" and an
absent request_id keep the legacy wake key. Self-contained: no audit-tree imports.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import bridge_next_action as nxt  # noqa: E402
from waggledance.core.bridge_request_contract import request_key  # noqa: E402

TARGET = "codex-tools-1"
LEAD = {"agent": "codex-lead-1", "agent_uuid": "uuid-lead-1", "session_id": "sess-a", "run_id": "run-a"}
FALSY = [False, 0, 0.0, [], {}]
SHY = "­"
MISSING = object()


def req(rid=MISSING, *, message="m", task="codex-lead-1/t", status="assigned", ts="2026-10-02T03:50:00Z",
        payload_rid=MISSING, digest="a" * 64, kind="wake_request"):
    row = dict(LEAD, ts_utc=ts, type=kind, status=status, task_id=task, to=TARGET, message=message,
               request_digest=digest, payload={"task_revision": "r1"})
    if rid is not MISSING:
        row["request_id"] = rid
    if payload_rid is not MISSING:
        row["payload"]["request_id"] = payload_rid
    return row


def dedup(rows):
    return nxt._deduplicate_repeated_wake_requests(copy.deepcopy(rows), agent=TARGET)


def messages(rows):
    return [r["message"] for r in rows]


@pytest.mark.parametrize("bad", FALSY, ids=repr)
def test_two_different_falsy_id_requests_stay_apart(bad):
    out = dedup([req(bad, message="one"), req(copy.deepcopy(bad), message="two", ts="2026-10-02T03:51:00Z")])
    assert messages(out) == ["one", "two"], out
    assert not any(r.get("request_binding_conflict") for r in out)


@pytest.mark.parametrize("bad", FALSY, ids=repr)
def test_an_exact_falsy_id_retry_coalesces_to_the_first(bad):
    first = req(bad, message="one")
    retry = dict(copy.deepcopy(first), ts_utc="2026-10-02T03:59:00Z")
    out = dedup([first, retry])
    assert len(out) == 1 and out[0]["ts_utc"] == "2026-10-02T03:50:00Z" and not out[0].get("request_binding_conflict")


@pytest.mark.parametrize("bad", FALSY, ids=repr)
def test_different_falsy_ids_with_the_same_message_stay_apart(bad):
    other = 1 if bad in (False, 0, 0.0) and not isinstance(bad, (list, dict)) else ["x"]
    out = dedup([req(bad), req(other, ts="2026-10-02T03:51:00Z")])
    assert len(out) == 2, out


@pytest.mark.parametrize("bad, valid", [(0, "0"), (False, "false"), ([], "[]"), ({}, "{}"), (0.0, "0.0")], ids=repr)
def test_a_falsy_invalid_id_never_poisons_the_valid_string_id(bad, valid):
    out = dedup([req(valid, message="valid"), req(bad, message="bad", ts="2026-10-02T03:51:00Z"),
                 req(valid, message="valid", ts="2026-10-02T03:52:00Z")])
    assert messages(out) == ["valid", "bad"], out
    assert not any(r.get("request_binding_conflict") for r in out)


def test_valid_and_falsy_keys_are_typed_and_distinct():
    keys = {request_key(req(v), TARGET) for v in ["0", 0, False, 0.0, [], {}]}
    assert len(keys) == 6
    assert request_key(req(0), TARGET)[0] == "invalid-id" and request_key(req("0"), TARGET)[0] == "id"


@pytest.mark.parametrize("rid", [None, "", MISSING], ids=["null", "empty", "absent"])
def test_null_empty_and_absent_ids_keep_the_legacy_wake_key(rid):
    out = dedup([req(rid, message="one"), req(rid, message="two", ts="2026-10-02T03:51:00Z")])
    assert messages(out) == ["two"], out       # unchanged: latest same agent/task/status poke wins
    other_status = dedup([req(rid, message="one"), req(rid, message="two", status="blocked")])
    assert messages(other_status) == ["one", "two"]


def test_top_payload_conflicts_stay_apart_and_exact_repeats_coalesce():
    a = req("a1", message="one", payload_rid="a1" + SHY)
    b = req("b1", message="two", payload_rid="b1" + SHY, ts="2026-10-02T03:51:00Z")
    assert messages(dedup([a, b])) == ["one", "two"]
    assert len(dedup([a, dict(a, ts_utc="2026-10-02T03:59:00Z")])) == 1
    falsy_conflict = req(0, message="three", payload_rid=False)
    assert messages(dedup([a, falsy_conflict])) == ["one", "three"]


def test_valid_ids_content_and_digest_compare_exactly():
    assert messages(dedup([req("r-1", message="one"), req("r-1" + SHY, message="two")])) == ["one", "two"]
    same_id_shy_message = dedup([req("r-1", message="go"), req("r-1", message="go" + SHY, ts="2026-10-02T03:51:00Z")])
    assert len(same_id_shy_message) == 1 and same_id_shy_message[0]["request_binding_conflict"] is True
    shy_digest = dedup([req("r-1"), req("r-1", digest="a" * 63 + "a" + SHY, ts="2026-10-02T03:51:00Z")])
    assert len(shy_digest) == 1 and shy_digest[0]["request_binding_conflict"] is True
    assert dedup([req("r-1"), req("r-1", ts="2026-10-02T03:51:00Z")])[0].get("request_binding_conflict") is None


@pytest.mark.parametrize("bad", FALSY + [None, ""], ids=repr)
def test_non_wake_rows_with_a_falsy_id_pass_through_unchanged(bad):
    rows = [req(bad, message="one", kind="message"), req(bad, message="two", kind="message")]
    assert dedup(rows) == rows


@pytest.mark.parametrize("bad", FALSY, ids=repr)
def test_a_falsy_id_request_stays_bound_and_no_reply_can_close_it(bad):
    request = req(bad)
    request["payload"] = {}
    assert nxt.request_is_bound(request)     # it is a correlated request, never an unbound legacy one
    reply = {"agent": TARGET, "agent_uuid": "uuid-tools", "session_id": "s-t", "run_id": "r-t",
             "ts_utc": "2026-10-02T03:55:00Z", "type": "message", "status": "answered", "task_id": request["task_id"],
             "to": LEAD["agent"], "message": "done", "in_reply_to_request_id": copy.deepcopy(bad),
             "in_reply_to_request_digest": request["request_digest"],
             "in_reply_to_requester": {k: LEAD[k] for k in ("agent", "agent_uuid", "session_id", "run_id")}}
    assert not nxt.reply_matches_request(request, reply, TARGET)
    # positive twin: the same reply shape closes a valid string id, so the refusal above is about the id alone
    valid = dict(request, request_id="r-1")
    assert nxt.reply_matches_request(valid, dict(reply, in_reply_to_request_id="r-1"), TARGET)
