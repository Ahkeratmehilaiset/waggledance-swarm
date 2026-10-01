# SPDX-License-Identifier: BUSL-1.1
"""F26 S2: wd.routing-attempt.v1 from dispatches, claims and releases (pure ``attempts(...)``).

Every value is SYNTHETIC fixture data. Dispatches come from the real S1 producer over a live-shaped
wake_request; claims and releases have the live Claim-AgentTask / Release-AgentTask file shape.
"""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import wd_routing_attempts as module  # noqa: E402
from tools.wd_composer_select import digest  # noqa: E402
from tools.wd_routing_attempts import attempts  # noqa: E402
from tools.wd_routing_dispatch import dispatches  # noqa: E402
from tools.wd_task_router import ATTEMPT_KEYS, ATTEMPT_SCHEMA, _attempts  # noqa: E402

NOW = datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc)
TASK = "codex-lead-1/f26-s2-fixture"
WORKER = "fable-5"
UUID = "0b0c0d0e-1111-4222-8333-444455556666"
SESSION = "wd-fixture-session-a"
RUN = "wd-fixture-session-a"
SCOPE = ["tools/wd_routing_attempts.py", "tests/tools/test_wd_routing_attempts.py"]


def request(request_id="req-s2-1", *, task=TASK, revision="r1", ts="2026-10-01T16:46:22.9806570Z",
            scope=tuple(SCOPE), worker=WORKER) -> dict:
    return {"ts_utc": ts, "agent": "codex-lead-1", "type": "wake_request", "task_id": task, "status": "assigned",
            "to": worker, "message": "Build S2.", "write_scope": list(scope),
            "payload": {"task_revision": revision, "result_contract": {"schema": "wd.task-result-contract.v1"}},
            "request_id": request_id, "request_digest": "a" * 64,
            "expected_responders": {worker: {"agent_uuid": UUID, "session_id": SESSION, "run_id": RUN}}}


def dispatch(**over) -> dict:
    out = dispatches([request(**over)], NOW)
    assert len(out["dispatches"]) == 1, out
    return out["dispatches"][0]


def claim(**over) -> dict:
    row = {"claimed_at_utc": "2026-10-01T16:47:51.9878448Z", "last_heartbeat_utc": "2026-10-01T16:47:51.9878448Z",
           "agent": WORKER, "task_id": TASK, "summary": "S2", "mode": "write", "write_scope": list(SCOPE),
           "resources": [{"kind": "repo", "path": SCOPE[0], "root": ""}], "run_id": RUN, "lease_seconds": 1800,
           "claim_lease_expires_utc": "2026-10-01T17:17:51.9878448Z", "pid": 1, "cwd": "C:\\fixture",
           "git_branch": "fable-5/fixture", "owner_session_id": SESSION, "owner_token_sha256": "e" * 64,
           "role": "fable-producer", "agent_uuid": UUID, "capabilities": ["implementation"]}
    row.update(over)
    return row


def release(status="done", at="2026-10-01T16:55:00Z", **over) -> dict:
    row = claim(**over)
    row.update({"released_at_utc": at, "release_status": status, "release_message": "fixture"})
    return row


def association(dispatch_id="req-s2-1", *, request_digest="a" * 64, task=TASK, worker=WORKER, session=SESSION,
                token="e" * 64, basis="fixture: caller-recorded dispatch for this claim") -> dict:
    """An explicit caller association (wd.routing-claim-association.v1): the claim of this identity answers
    this dispatch. Without one a claim never binds (RCO1 provenance repair)."""
    return {"schema": module.ASSOCIATION_SCHEMA, "dispatch_id": dispatch_id, "request_digest": request_digest,
            "task_id": task, "worker": worker, "owner_session_id": session, "owner_token_sha256": token,
            "basis": basis}


EACH = object()   # run(): one explicit association per supplied dispatch for the standard fixture claim


def run(dispatch_list=None, claims=(), releases=(), receipts=(), acceptances=(), now=NOW, links=EACH) -> dict:
    dispatch_list = [dispatch()] if dispatch_list is None else list(dispatch_list)
    if links is EACH:
        links = [association(d["dispatch_id"], request_digest=d["request_digest"], task=d["task_id"])
                 for d in dispatch_list if type(d) is dict and type(d.get("dispatch_id")) is str
                 and type(d.get("request_digest")) is str and type(d.get("task_id")) is str]
    out = attempts(dispatch_list, list(claims), list(releases), list(receipts), list(acceptances), now,
                   associations=list(links))
    assert set(out) == {"attempts", "expired_unreleased", "rejected", "duplicates_ignored"}
    for record in out["attempts"]:
        assert list(record) == list(ATTEMPT_KEYS) and record["schema"] == ATTEMPT_SCHEMA
        assert record["state"] in ("active", "released") and record["artifacts"] == []
    # Every produced record passes the router's own closed attempt validator.
    _attempts(out["attempts"], NOW)
    return out


def reasons(out, source=None) -> list:
    return [r["reason"] for r in out["rejected"] if source is None or r["source"] == source]


def attempt_id(dispatch_id="req-s2-1", session=SESSION) -> str:
    return digest({"dispatch_id": dispatch_id, "worker": WORKER, "owner_session_id": session})


# --- the record ------------------------------------------------------------------------------------------

def test_a_present_claim_is_one_active_attempt_bound_to_its_dispatch():
    d = dispatch()
    out = run([d], [claim()])
    assert out["rejected"] == [] and out["expired_unreleased"] == [] and out["duplicates_ignored"] == 0
    [record] = out["attempts"]
    assert record["attempt_id"] == attempt_id() and record["dispatch_key"] == d["dispatch_key"]
    assert (record["task_id"], record["worker"], record["state"]) == (TASK, WORKER, "active")
    assert record["scope"] == sorted(s.casefold() for s in SCOPE)
    assert record["lease_expires_utc"] == "2026-10-01T17:17:51.987844+00:00"


def test_a_force_refresh_keeps_the_attempt_id():
    first = run(claims=[claim()])["attempts"][0]
    refreshed = run(claims=[claim(claimed_at_utc="2026-10-01T16:58:00Z",
                                  claim_lease_expires_utc="2026-10-01T17:28:00Z")])["attempts"][0]
    assert refreshed["attempt_id"] == first["attempt_id"] and refreshed["lease_expires_utc"] != first["lease_expires_utc"]


def test_another_session_is_another_attempt_and_does_not_bind_this_dispatch():
    out = run(claims=[claim(owner_session_id="wd-other-session")])
    assert out["attempts"] == [] and reasons(out) == ["no_matching_dispatch"]


# --- released / expired ----------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["done", "handoff", "blocked", "abandoned", "stale_lease"])
def test_a_matching_release_without_a_present_claim_is_released(status):
    out = run(releases=[release(status)])
    [record] = out["attempts"]
    assert record["state"] == "released" and record["attempt_id"] == attempt_id()
    assert out["expired_unreleased"] == [] and out["rejected"] == []


def test_an_expired_claim_without_release_stays_active_and_is_listed_not_released():
    out = run(claims=[claim()], now=datetime(2026, 10, 1, 17, 30, tzinfo=timezone.utc))
    [record] = out["attempts"]
    assert record["state"] == "active" and out["expired_unreleased"] == [record["attempt_id"]]
    assert out["rejected"] == []


def test_a_reclaim_after_a_release_is_active_again_with_the_same_id():
    out = run(claims=[claim(claimed_at_utc="2026-10-01T16:58:00Z")], releases=[release(at="2026-10-01T16:55:00Z")])
    assert [(r["attempt_id"], r["state"]) for r in out["attempts"]] == [(attempt_id(), "active")]


def test_a_present_claim_older_than_its_release_gives_no_attempt():
    out = run(claims=[claim()], releases=[release(at="2026-10-01T16:55:00Z")])
    assert out["attempts"] == [] and reasons(out) == ["claim_release_inconsistent"]


@pytest.mark.parametrize("over, reason", [
    ({"release_status": "accepted"}, "malformed"),
    ({"release_status": None}, "malformed"),
    ({"released_at_utc": "2026-10-01T16:55:00"}, "malformed"),
    ({"released_at_utc": "2026-10-01T17:05:00Z"}, "future_dated"),
])
def test_release_record_refusals(over, reason):
    row = release()
    row.update(over)
    out = run(releases=[row])
    assert out["attempts"] == [] and reasons(out) == [reason]


# --- join ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("over", [
    {"task_id": "codex-lead-1/other-task"}, {"agent": "codex-tools-1"}, {"owner_session_id": "wd-x"},
    {"run_id": "wd-x"}, {"agent_uuid": "0b0c0d0e-1111-4222-8333-444455556667"}, {"agent": "Fable-5"},
])
def test_every_label_must_match_exactly(over):
    out = run(claims=[claim(**over)], releases=[release(**over)])
    assert out["attempts"] == [] and set(reasons(out)) == {"no_matching_dispatch"}


@pytest.mark.parametrize("missing", ["task_id", "agent", "owner_session_id", "run_id", "agent_uuid"])
def test_a_claim_without_a_label_is_malformed(missing):
    row = claim()
    row.pop(missing)
    out = run(claims=[row])
    assert out["attempts"] == [] and reasons(out) == ["malformed"]


def test_two_valid_dispatches_for_one_task_make_the_claim_ambiguous_never_newest_wins():
    old, new = dispatch(request_id="req-s2-1", revision="r1"), dispatch(request_id="req-s2-2", revision="r2",
                                                                        ts="2026-10-01T16:47:00Z")
    out = run([old, new], claims=[claim()], releases=[release()], links=[association("req-s2-2")])
    assert out["attempts"] == [] and reasons(out) == ["claim_dispatch_ambiguous", "claim_dispatch_ambiguous"]


def test_a_superseded_dispatch_removed_by_s1_cannot_be_revived():
    s1 = dispatches([request("req-s2-1", revision="r1"),
                     request("req-s2-2", revision="r2", ts="2026-10-01T16:47:00Z")], NOW)
    assert [d["dispatch_id"] for d in s1["dispatches"]] == ["req-s2-2"]
    # Provenance (RCO1): the claim answers req-s2-1; that dispatch is superseded, so it is never moved to r2.
    stale = run(s1["dispatches"], claims=[claim()], links=[association("req-s2-1")])
    assert stale["attempts"] == [] and reasons(stale) == ["association_dispatch_absent"]
    # Only an explicit association with req-s2-2 binds a claim to the revision S1 kept.
    out = run(s1["dispatches"], claims=[claim()], links=[association("req-s2-2")])
    assert [r["attempt_id"] for r in out["attempts"]] == [attempt_id("req-s2-2")]


def test_no_dispatch_means_no_attempt():
    out = run([], claims=[claim()])
    assert out["attempts"] == [] and reasons(out) == ["no_matching_dispatch"]


@pytest.mark.parametrize("scope, reason", [
    (SCOPE + ["tools/other.py"], "scope_exceeds_dispatch"),
    (["tools"], "scope_exceeds_dispatch"),
    (["repo:tools/wd_routing_attempts.py"], None),
    (["TOOLS/wd_routing_attempts.py"], None),
    ([], "malformed"),
    (["tools/*.py"], "malformed"),
])
def test_claim_scope_must_lie_inside_the_dispatch_scope(scope, reason):
    out = run(claims=[claim(write_scope=scope)])
    assert reasons(out) == ([reason] if reason else [])
    assert len(out["attempts"]) == (0 if reason else 1)


def test_a_claim_under_a_dispatch_directory_is_inside():
    out = run([dispatch(scope=("tools/",))], claims=[claim(write_scope=["tools/wd_routing_attempts.py"])])
    assert len(out["attempts"]) == 1 and out["rejected"] == []


def test_a_read_only_claim_is_not_an_attempt():
    out = run(claims=[claim(mode="read-only")])
    assert out["attempts"] == [] and reasons(out) == ["claim_not_write"]


@pytest.mark.parametrize("over, reason", [
    ({"claimed_at_utc": "2026-10-01T16:40:00Z"}, "before_dispatch"),
    ({"claimed_at_utc": "2026-10-01T17:00:00.000001Z"}, "future_dated"),
    ({"claimed_at_utc": "2026-10-01T16:47:51"}, "malformed"),
    ({"claim_lease_expires_utc": None}, "malformed"),
])
def test_claim_time_rules(over, reason):
    out = run(claims=[claim(**over)])
    assert out["attempts"] == [] and reasons(out) == [reason]


# --- duplicates and conflicts ----------------------------------------------------------------------------

def test_exact_repeats_count_once():
    d = dispatch()
    out = run([d, copy.deepcopy(d)], claims=[claim(), claim()], releases=[], links=[association()])
    assert len(out["attempts"]) == 1 and out["duplicates_ignored"] == 2 and out["rejected"] == []


def test_two_different_present_claims_for_one_attempt_poison_it():
    out = run(claims=[claim(), claim(claimed_at_utc="2026-10-01T16:58:00Z")])
    assert out["attempts"] == [] and reasons(out) == ["attempt_conflict"]


def test_a_conflicting_dispatch_id_poisons_every_copy():
    d = dispatch()
    other = dict(d, revision="r9")
    out = run([d, other], claims=[claim()])
    assert out["attempts"] == [] and reasons(out) == ["no_matching_dispatch", "dispatch_conflict"]   # sorted by source: claim, dispatch


@pytest.mark.parametrize("change", [
    {"schema": "wd.routing-dispatch.v0"}, {"requester": "codex-tools-1"}, {"dispatch_key": "A" * 64},
    {"extra": 1}, {"expected_responders": {"fable-5": {"agent_uuid": UUID, "session_id": SESSION}}},
    {"expected_responders": {"codex-tools-1": {"agent_uuid": UUID, "session_id": SESSION, "run_id": RUN}}},
    {"dispatched_utc": "2026-10-01T16:46:22"},
])
def test_a_dispatch_outside_the_closed_s1_schema_is_malformed(change):
    d = dict(dispatch(), **change)
    out = run([d], claims=[claim()])
    assert out["attempts"] == [] and reasons(out) == ["no_matching_dispatch", "malformed"]


# --- hostile values ---------------------------------------------------------------------------------------

class Sneaky(dict):
    """A dict whose lookups lie: get/[] always answer with the fixture's expected value."""

    def get(self, key, default=None):
        return claim().get(key, default)

    def __getitem__(self, key):
        return claim()[key]


class Liar(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


@pytest.mark.parametrize("hostile", [
    Sneaky(task_id="x"),
    claim(agent=Liar("codex-tools-1")),
    claim(owner_session_id=Liar("wd-x")),
    claim(lease_seconds=float("nan")),
    claim(extra={1: "non-str key"}),
    "a claim as text",
    None,
])
def test_hostile_or_non_json_claims_are_malformed_and_never_run_hooks(hostile):
    out = run(claims=[hostile], releases=[hostile])
    assert out["attempts"] == [] and set(reasons(out)) == {"malformed"}


def test_a_hostile_dispatch_is_malformed():
    d = dispatch()
    lying = dict(d, worker=Liar(d["worker"]))
    out = run([lying], claims=[claim()])
    assert out["attempts"] == [] and reasons(out) == ["no_matching_dispatch", "malformed"]


def test_deep_nesting_is_refused_not_a_recursion_error():
    deep: list = []
    for _ in range(200):
        deep = [deep]
    out = run(claims=[claim(extra=deep)])
    assert out["attempts"] == [] and reasons(out) == ["malformed"]


# --- no accepted source exists yet ---------------------------------------------------------------------

def test_receipts_and_acceptances_never_make_an_accepted_or_verified_attempt():
    receipt = {"schema": "wd.push-receipt.v1", "attempt_id": attempt_id(), "commit": "c" * 40,
               "ls_remote_sha": "c" * 40, "remote_verified": True}
    acceptance = {"schema": "wd.routing-acceptance.v1", "attempt_id": attempt_id(), "commit": "c" * 40,
                  "decided_by": "codex-lead-1"}
    out = run(claims=[claim()], receipts=[receipt], acceptances=[acceptance])
    [record] = out["attempts"]
    assert record["state"] == "active" and record["artifacts"] == []
    assert reasons(out, "push_receipt") == ["push_receipt_source_unaccepted"]
    assert reasons(out, "acceptance") == ["acceptance_source_absent"]


def test_done_and_reply_shaped_events_are_not_claims():
    done_event = {"ts_utc": "2026-10-01T16:55:00Z", "agent": WORKER, "type": "done", "status": "done",
                  "task_id": TASK, "message": "done"}
    out = run(claims=[], releases=[done_event])
    assert out["attempts"] == [] and reasons(out) == ["malformed"]


# --- inputs, purity, cancellation -----------------------------------------------------------------------

@pytest.mark.parametrize("now", [None, "2026-10-01T17:00:00Z", datetime(2026, 10, 1, 17, 0)])
def test_now_must_be_an_offset_aware_datetime(now):
    with pytest.raises(ValueError):
        attempts([], [], [], [], [], now)


@pytest.mark.parametrize("position", range(5))
def test_every_input_must_be_a_list(position):
    args = [[], [], [], [], []]
    args[position] = ()
    with pytest.raises(ValueError):
        attempts(*args, NOW)


def test_an_offset_now_is_the_same_instant():
    helsinki = NOW.astimezone(timezone(timedelta(hours=3)))
    assert run(claims=[claim()], now=helsinki) == run(claims=[claim()])


def test_input_order_does_not_change_the_result():
    rows = [claim(), release(owner_session_id="wd-other", run_id="wd-other")]
    assert run(claims=rows[:1], releases=rows[1:]) == run(claims=rows[:1], releases=list(reversed(rows[1:])))


def test_inputs_are_not_mutated():
    args = ([dispatch()], [claim()], [release(at="2026-10-01T16:40:00Z")], [{"a": 1}], [{"b": 2}])
    before = copy.deepcopy(args)
    attempts(*args, NOW)
    assert args == before


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit, GeneratorExit])
def test_cancellation_propagates(monkeypatch, signal):
    def boom(*a, **k):
        raise signal()
    monkeypatch.setattr(module, "normalize_scope", boom)
    with pytest.raises(signal):
        run(claims=[claim()])


def test_the_module_reads_no_clock_environment_file_or_network():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names} | {node.module.split(".")[0] for node in ast.walk(tree)
                                            if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"os", "subprocess", "socket", "time", "pathlib", "io", "shutil", "urllib", "random"}
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"now", "utcnow", "today", "open", "getenv", "read_text", "write_text"}


# --- provenance (RCO1, Lead 17:13Z): no association from task/worker/session/time similarity alone ---------

def _r2_with_forced_r1_claim():
    """r2 dispatched 16:40; the r1 claim of the same task/worker/session is -Force-refreshed at 16:45, so its
    claimed_at no longer predates r2. S1 keeps only the newest dispatch."""
    r2 = dispatch(request_id="req-r2", revision="r2", ts="2026-10-01T16:40:00Z")
    forced = claim(claimed_at_utc="2026-10-01T16:45:00Z", last_heartbeat_utc="2026-10-01T16:45:00Z")
    return r2, forced


def test_provenance_default_absent_associations_fail_closed():
    r2, forced = _r2_with_forced_r1_claim()
    out = attempts([r2], [forced], [release(at="2026-10-01T16:50:00Z", claimed_at_utc="2026-10-01T16:45:00Z")],
                   [], [], NOW)
    assert out["attempts"] == [] and reasons(out) == ["dispatch_association_missing"] * 2


def test_provenance_a_forced_old_revision_claim_never_moves_to_the_newer_dispatch():
    r2, forced = _r2_with_forced_r1_claim()
    # The caller knows it answered r1 (req-r1): that dispatch is superseded and not supplied.
    out = run([r2], claims=[forced], links=[association("req-r1")])
    assert out["attempts"] == [] and reasons(out, "claim") == ["association_dispatch_absent"]
    # Only an explicit association naming r2 binds it to r2.
    bound = run([r2], claims=[forced], links=[association("req-r2")])
    assert [a["dispatch_key"] for a in bound["attempts"]] == [r2["dispatch_key"]]


def test_provenance_unknown_and_wrong_identity_associations_do_not_bind():
    d = dispatch()
    for link, reason in [
        (association(request_digest="b" * 64), "association_mismatch"),         # another request
        (association(token="f" * 64), "dispatch_association_missing"),          # another owner token
        (association(session="wd-other-session"), "dispatch_association_missing"),
        (association(task="codex-lead-1/other-task"), "dispatch_association_missing"),
    ]:
        out = run([d], claims=[claim()], links=[link])
        assert out["attempts"] == [] and reasons(out, "claim") == [reason], (link, out["rejected"])


def test_provenance_two_different_associations_for_one_claim_poison_it():
    d = dispatch()
    out = run([d], claims=[claim()], links=[association(), association(basis="another source")])
    assert out["attempts"] == []
    assert sorted(reasons(out)) == ["association_conflict", "association_conflict"]


def test_provenance_an_exact_repeat_association_counts_once():
    out = run(claims=[claim()], links=[association(), association()])
    assert len(out["attempts"]) == 1 and out["duplicates_ignored"] == 1 and out["rejected"] == []


def test_provenance_a_release_needs_its_association_too():
    out = run(releases=[release()], links=[])
    assert out["attempts"] == [] and reasons(out, "release") == ["dispatch_association_missing"]
    assert [a["state"] for a in run(releases=[release()])["attempts"]] == ["released"]


def test_provenance_a_claim_without_a_hex_owner_token_is_malformed():
    out = run(claims=[claim(owner_token_sha256="not-hex")])
    assert out["attempts"] == [] and reasons(out, "claim") == ["malformed"]


def test_provenance_the_claim_token_itself_selects_the_association():
    # Causal twin for the owner token in the association key: a claim of another session token binds only
    # through an association carrying that same token, never through the default-token one.
    other = claim(owner_token_sha256="d" * 64)
    bound = run(claims=[other], links=[association(token="d" * 64)])
    assert [a["attempt_id"] for a in bound["attempts"]] == [attempt_id()] and bound["rejected"] == []
    crossed = run(claims=[other], links=[association()])
    assert crossed["attempts"] == [] and reasons(crossed) == ["dispatch_association_missing"]


class _Liar(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


class _HookRan(Exception):
    pass


class _Tripwire(dict):
    def _trip(self, *args, **kwargs):
        raise _HookRan("a hostile association hook ran")

    get = __getitem__ = __contains__ = __iter__ = keys = items = values = __eq__ = __len__ = _trip
    __hash__ = None


@pytest.mark.parametrize("spoil", [
    lambda a: a.pop("basis"),
    lambda a: a.__setitem__("extra", "x"),
    lambda a: a.__setitem__("schema", "wd.routing-claim-association.v0"),
    lambda a: a.__setitem__("request_digest", "A" * 64),
    lambda a: a.__setitem__("owner_token_sha256", "e" * 63),
    lambda a: a.__setitem__("dispatch_id", _Liar("anything")),
    lambda a: a.__setitem__("basis", float("nan")),
    lambda a: a.__setitem__("basis", ""),
])
def test_provenance_a_malformed_association_binds_nothing(spoil):
    link = association()
    spoil(link)
    out = run(claims=[claim()], links=[link])
    assert out["attempts"] == []
    assert reasons(out, "association") == ["malformed"] and reasons(out, "claim") == ["dispatch_association_missing"]


def test_provenance_a_hostile_association_is_refused_without_running_hooks():
    out = run(claims=[claim()], links=[_Tripwire(association()), association()])   # _HookRan would propagate
    assert len(out["attempts"]) == 1 and reasons(out, "association") == ["malformed"]


@pytest.mark.parametrize("links", [(association(),), {"a": association()}, "x"])
def test_provenance_associations_must_be_a_list(links):
    with pytest.raises(ValueError):
        attempts([dispatch()], [claim()], [], [], [], NOW, associations=links)


def test_provenance_an_association_never_makes_an_accepted_or_verified_attempt():
    out = run(claims=[claim()], links=[association()])
    assert [(a["state"], a["artifacts"]) for a in out["attempts"]] == [("active", [])]


# --- A2/A3/A4 (Tools 121f audit, Lead 17:26Z): poison by exact id, closed dispatch scope, chronology ------
# Every case passes an explicit valid association, so the default refusal cannot hide the defect.

class _IdTripwire(dict):
    """A dict subclass that fails the test if any of its hooks runs while its id is read."""
    def _trip(self, *args, **kwargs):
        raise _HookRan("dict hook ran")
    get = __getitem__ = keys = items = values = __iter__ = __contains__ = __len__ = __eq__ = _trip
    __hash__ = None


@pytest.mark.parametrize("spoil", [
    lambda d: d.update(extra=1),
    lambda d: d.update(schema="wd.routing-dispatch.v0"),
    lambda d: d.update(scope=[float("nan")]),
    lambda d: d.update(revision=float("inf")),
    lambda d: d.update(scope=["tools/*.py"]),
    lambda d: d.update(worker=Liar(d["worker"])),
    lambda d: d.pop("dispatch_key"),
])
def test_a2_a_malformed_copy_poisons_every_valid_copy_of_its_dispatch_id(spoil):
    d = dispatch()
    bad = copy.deepcopy(d)
    spoil(bad)
    for order in ([d, bad], [bad, d], [d, bad, copy.deepcopy(d)]):
        out = run(order, claims=[claim()], releases=[release(at="2026-10-01T16:48:00Z")], links=[association()])
        assert out["attempts"] == [] and out["expired_unreleased"] == []
        assert sorted(reasons(out, "dispatch")) == ["dispatch_conflict", "malformed"]
        assert reasons(out, "claim") == reasons(out, "release") == ["no_matching_dispatch"]


def test_a2_valid_twin_identical_copies_still_bind():
    d = dispatch()
    out = run([d, copy.deepcopy(d)], claims=[claim()], links=[association()])
    assert [a["attempt_id"] for a in out["attempts"]] == [attempt_id()] and out["rejected"] == []


def test_a2_a_malformed_record_of_another_id_poisons_nothing_here():
    d = dispatch()
    other = dict(copy.deepcopy(d), dispatch_id="req-s2-other", extra=1)
    out = run([d, other], claims=[claim()], links=[association()])
    assert [a["attempt_id"] for a in out["attempts"]] == [attempt_id()] and reasons(out) == ["malformed"]


@pytest.mark.parametrize("make", [
    lambda d: _IdTripwire(d),                                   # a dict subclass: its id is never read
    lambda d: dict(d, dispatch_id=_Liar(d["dispatch_id"])),     # a str-subclass id is not an exact id
    lambda d: "req-s2-1",                                        # not a record at all
])
def test_a2_ids_are_read_only_from_exact_dicts_with_exact_str_values_and_no_hook_runs(make):
    d = dispatch()
    out = run([d, make(copy.deepcopy(d))], claims=[claim()], links=[association()])
    # Limit (named): such an item cannot be attributed to an id, so it poisons nothing; it is still refused.
    assert [a["attempt_id"] for a in out["attempts"]] == [attempt_id()] and reasons(out) == ["malformed"]


def test_a2_a_str_subclass_key_is_not_the_id_key():
    d = dispatch()
    bad = {_Liar("dispatch_id") if k == "dispatch_id" else k: v for k, v in d.items()}
    out = run([d, bad], claims=[claim()], links=[association()])
    assert len(out["attempts"]) == 1 and reasons(out) == ["malformed"]


@pytest.mark.parametrize("entries", [
    [1], [None], [True], [{"path": "tools"}], [["tools"]], [""], ["tools/*.py"], ["tools/../x.py"],
    ["TOOLS/wd_routing_attempts.py"], ["tools/wd_routing_attempts.py", "tests/tools/test_wd_routing_attempts.py"],
    ["tests/tools/test_wd_routing_attempts.py", "tests/tools/test_wd_routing_attempts.py",
     "tools/wd_routing_attempts.py"],
])
def test_a3_an_invalid_or_unnormalized_dispatch_scope_is_malformed_not_an_exception(entries):
    d = dict(dispatch(), scope=entries)
    out = run([d], claims=[claim()], links=[association()])
    assert out["attempts"] == [] and reasons(out) == ["no_matching_dispatch", "malformed"]


def test_a3_valid_twins_the_s1_scopes_still_bind():
    for scope in (tuple(SCOPE), ("tools/",), ("repo:tools/wd_routing_attempts.py", "tests/tools/")):
        out = run([dispatch(scope=scope)], claims=[claim(write_scope=["tools/wd_routing_attempts.py"])],
                  links=[association()])
        assert len(out["attempts"]) == 1 and out["rejected"] == [], scope


@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit, GeneratorExit, RuntimeError])
def test_a3_the_scope_check_lets_cancellation_and_unexpected_errors_through(monkeypatch, signal):
    def boom(*a, **k):
        raise signal()
    monkeypatch.setattr(module, "normalize_scope", boom)
    with pytest.raises(signal):
        run(claims=[], links=[association()])


def test_a4_a_lease_ending_before_its_claim_is_refused_not_active():
    out = run(claims=[claim(claim_lease_expires_utc="2026-10-01T16:47:51.98Z")], links=[association()])
    assert out["attempts"] == [] and out["expired_unreleased"] == [] and reasons(out) == ["chronology_invalid"]


def test_a4_a_release_before_its_own_claim_does_not_invent_a_released_attempt():
    out = run(releases=[release(at="2026-10-01T16:47:51.98Z")], links=[association()])
    assert out["attempts"] == [] and reasons(out) == ["chronology_invalid"]


def test_a4_a_release_whose_lease_ends_before_its_claim_is_refused():
    out = run(releases=[release(claim_lease_expires_utc="2026-10-01T16:47:00Z")], links=[association()])
    assert out["attempts"] == [] and reasons(out) == ["chronology_invalid"]


def test_a4_valid_twins_equal_instants_and_an_honestly_expired_claim():
    stamp = "2026-10-01T16:47:51.9878448Z"
    zero = run(claims=[claim(claim_lease_expires_utc=stamp)], links=[association()])
    assert [a["state"] for a in zero["attempts"]] == ["active"] and zero["expired_unreleased"] == [attempt_id()]
    same = run(releases=[release(at=stamp)], links=[association()])
    assert [a["state"] for a in same["attempts"]] == ["released"] and same["rejected"] == []
    late = run(claims=[claim()], links=[association()], now=datetime(2026, 10, 1, 17, 30, tzinfo=timezone.utc))
    assert [a["state"] for a in late["attempts"]] == ["active"] and late["expired_unreleased"] == [attempt_id()]
