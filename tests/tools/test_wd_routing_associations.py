# SPDX-License-Identifier: BUSL-1.1
"""F26 S6 source preparation: wd.routing-claim-association.v1 from acquisition-recorded requests.

Every value is SYNTHETIC fixture data. Dispatches come from the real S1 producer; claims and releases have
the live Claim-AgentTask / Release-AgentTask file shape, plus the request_binding a future writer would
record (no writer records it today, so the live-shaped legacy twins must stay unlinked).
"""
from __future__ import annotations

import ast
import copy
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import wd_routing_associations as module  # noqa: E402
from tools.wd_routing_associations import associations  # noqa: E402
from tools.wd_routing_attempts import ASSOCIATION_FIELDS, attempts  # noqa: E402
from tools.wd_routing_dispatch import dispatches  # noqa: E402

NOW = datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc)
TASK = "codex-lead-1/f26-s6-fixture"
WORKER = "fable-5"
UUID = "0b0c0d0e-1111-4222-8333-444455556666"
SESSION = "wd-fixture-session-a"
TOKEN = "e" * 64
SCOPE = ["tools/wd_routing_associations.py"]


def request(request_id="req-s6-1", *, revision="r1", ts="2026-10-01T16:40:00Z", digest_char="a") -> dict:
    return {"ts_utc": ts, "agent": "codex-lead-1", "type": "wake_request", "task_id": TASK, "status": "assigned",
            "to": WORKER, "message": "Build S6.", "write_scope": list(SCOPE),
            "payload": {"task_revision": revision, "result_contract": {"schema": "wd.task-result-contract.v1"}},
            "request_id": request_id, "request_digest": digest_char * 64,
            "expected_responders": {WORKER: {"agent_uuid": UUID, "session_id": SESSION, "run_id": SESSION}}}


def dispatch(**over) -> dict:
    out = dispatches([request(**over)], NOW)
    assert len(out["dispatches"]) == 1, out
    return out["dispatches"][0]


R1 = dispatch()
R2 = dispatch(request_id="req-s6-2", revision="r2", ts="2026-10-01T16:45:00Z", digest_char="b")


def binding(d=R1, **over) -> dict:
    row = {"schema": module.BINDING_SCHEMA, "request_id": d["dispatch_id"], "request_digest": d["request_digest"],
           "task_revision": d["revision"]}
    row.update(over)
    return row


LEGACY = object()


def claim(bound=None, **over) -> dict:
    """A live-shaped claim file; ``bound`` = LEGACY leaves request_binding out (every claim today)."""
    row = {"claimed_at_utc": "2026-10-01T16:47:00Z", "last_heartbeat_utc": "2026-10-01T16:47:00Z",
           "agent": WORKER, "task_id": TASK, "summary": "S6", "mode": "write", "write_scope": list(SCOPE),
           "run_id": SESSION, "lease_seconds": 1800, "claim_lease_expires_utc": "2026-10-01T17:17:00Z",
           "owner_session_id": SESSION, "owner_token_sha256": TOKEN, "agent_uuid": UUID, "role": "fable-producer"}
    if bound is not LEGACY:
        row["request_binding"] = binding() if bound is None else bound
    row.update(over)
    return row


def release(bound=None, at="2026-10-01T16:55:00Z", **over) -> dict:
    row = claim(bound, **over)
    row.update({"released_at_utc": at, "release_status": "done", "release_message": "fixture"})
    return row


def control(d=R1, state="live", observed="2026-10-01T16:50:00Z", **over) -> dict:
    row = {"schema": module.CONTROL_SCHEMA, "task_id": d["task_id"], "request_id": d["dispatch_id"],
           "request_digest": d["request_digest"], "state": state, "observed_utc": observed}
    row.update(over)
    return row


def run(dispatch_list=(R1,), acquisitions=(), controls=None, now=NOW) -> dict:
    controls = [control()] if controls is None else list(controls)
    out = associations(list(dispatch_list), list(acquisitions), controls, now)
    assert set(out) == {"associations", "unlinked", "rejected", "coverage", "duplicates_ignored"}
    for record in out["associations"]:
        assert list(record) == list(ASSOCIATION_FIELDS) and record["basis"] == "acquisition_recorded_request_v1"
    cov = out["coverage"]
    assert cov["schema"] == "wd.routing-association-coverage.v1" and cov["acquisitions"] == len(acquisitions)
    assert cov["associated"] + cov["unlinked"] + cov["rejected"] + cov["duplicates"] == cov["acquisitions"]
    assert cov["unlinked"] == len(out["unlinked"])
    return out


def unlinked(out) -> list:
    return [r["reason"] for r in out["unlinked"]]


def reasons(out, source=None) -> list:
    return [r["reason"] for r in out["rejected"] if source is None or r["source"] == source]


# --- the valid link ------------------------------------------------------------------------------------

def test_a_recorded_exact_binding_with_a_live_control_yields_one_association():
    out = run(acquisitions=[claim()])
    assert out["associations"] == [{"schema": "wd.routing-claim-association.v1", "dispatch_id": "req-s6-1",
                                    "request_digest": "a" * 64, "task_id": TASK, "worker": WORKER,
                                    "owner_session_id": SESSION, "owner_token_sha256": TOKEN,
                                    "basis": "acquisition_recorded_request_v1"}]
    assert out["unlinked"] == [] and out["rejected"] == [] and out["coverage"]["associated"] == 1


def test_a_claim_and_its_release_with_the_same_binding_give_one_association():
    out = run(acquisitions=[claim(), release()])
    assert len(out["associations"]) == 1 and out["coverage"]["associated"] == 2 and out["unlinked"] == []


def test_the_association_feeds_s2_and_binds_exactly_the_recorded_dispatch():
    out = run(acquisitions=[claim()])
    s2 = attempts([R1], [claim()], [], [], [], NOW, associations=out["associations"])
    assert [a["state"] for a in s2["attempts"]] == ["active"] and s2["rejected"] == []


# --- legacy and unknown: never guessed -----------------------------------------------------------------

def test_live_shaped_legacy_claims_and_releases_without_a_binding_are_unlinked_never_guessed():
    # Same task, worker, session, token and a claim time right after the dispatch: still nothing.
    out = run(acquisitions=[claim(LEGACY), release(LEGACY)])
    assert out["associations"] == [] and unlinked(out) == ["no_recorded_request", "no_recorded_request"]
    assert out["coverage"]["associated"] == 0


@pytest.mark.parametrize("controls, reason", [
    ([], "coverage_unknown"),
    ([control(state="cancelled")], "request_cancelled"),
    ([control(R2)], "request_superseded"),
    ([control(), control(state="cancelled")], "control_conflict"),
    ([control(), control(R2)], "control_conflict"),
])
def test_coverage_cancellation_and_supersession_fail_closed(controls, reason):
    out = run((R1, R2), acquisitions=[claim()], controls=controls)
    assert out["associations"] == [] and unlinked(out) == [reason]


def test_the_same_control_observed_twice_counts_once():
    out = run(acquisitions=[claim()], controls=[control(), control(observed="2026-10-01T16:55:00Z")])
    assert len(out["associations"]) == 1 and out["duplicates_ignored"] == 1


@pytest.mark.parametrize("spoil", [
    lambda c: c.update(state="paused"), lambda c: c.update(extra=1), lambda c: c.update(observed_utc="16:50"),
    lambda c: c.update(request_digest="A" * 64), lambda c: c.update(observed_utc=float("nan")),
    lambda c: c.update(schema="wd.routing-task-control.v0"),
])
def test_a_malformed_control_copy_poisons_the_task(spoil):
    bad = control()
    spoil(bad)
    out = run(acquisitions=[claim()], controls=[control(), bad])
    assert out["associations"] == [] and unlinked(out) == ["control_conflict"]
    assert reasons(out, "control") == ["malformed"]


def test_a_future_dated_control_poisons_the_task():
    out = run(acquisitions=[claim()], controls=[control(), control(observed="2026-10-01T17:00:01Z")])
    assert out["associations"] == [] and unlinked(out) == ["control_conflict"]
    assert reasons(out, "control") == ["future_dated"]


def test_a_hostile_control_copy_poisons_by_its_exact_task_id_without_hooks():
    out = run(acquisitions=[claim()], controls=[control(), _Tripwire(control())])
    # A dict subclass cannot be attributed hook-free; it may be any task's cancellation, so coverage is
    # unknown and nothing is associated (RCO1, Lead 18:05Z; previously a named limit that linked).
    assert out["associations"] == [] and unlinked(out) == ["control_coverage_incomplete"]
    assert reasons(out, "control") == ["malformed"] and out["coverage"]["complete"] is False


# --- the binding must match the supplied request exactly -------------------------------------------------

@pytest.mark.parametrize("bound, reason", [
    (binding(request_id="req-s6-9"), "request_absent"),
    (binding(request_digest="f" * 64), "request_mismatch"),
    (binding(task_revision="r9"), "request_mismatch"),
])
def test_a_binding_that_does_not_match_the_supplied_request_is_unlinked(bound, reason):
    out = run(acquisitions=[claim(bound)])
    assert out["associations"] == [] and unlinked(out) == [reason]


def test_a_binding_to_a_superseded_request_is_never_moved_to_the_newer_one():
    # S1 keeps only r2; the claim recorded r1.
    s1 = dispatches([request(), request("req-s6-2", revision="r2", ts="2026-10-01T16:45:00Z", digest_char="b")], NOW)
    out = run(s1["dispatches"], acquisitions=[claim()], controls=[control(R2)])
    assert out["associations"] == [] and unlinked(out) == ["request_absent"]


def test_a_claim_of_another_task_naming_this_request_is_a_mismatch():
    out = run(acquisitions=[claim(task_id="codex-lead-1/other")])
    assert out["associations"] == [] and unlinked(out) == ["request_mismatch"]


@pytest.mark.parametrize("over", [
    {"agent": "codex-tools-1"}, {"owner_session_id": "wd-x"}, {"run_id": "wd-x"},
    {"agent_uuid": "0b0c0d0e-1111-4222-8333-444455556667"},
])
def test_owner_identity_must_equal_the_dispatch_responder(over):
    out = run(acquisitions=[claim(**over)])
    assert out["associations"] == [] and unlinked(out) == ["owner_mismatch"]


def test_a_poisoned_dispatch_id_cannot_be_linked():
    bad = dict(copy.deepcopy(R1), extra=1)
    out = run((R1, bad), acquisitions=[claim()])
    assert out["associations"] == [] and unlinked(out) == ["request_absent"]
    assert sorted(reasons(out, "dispatch")) == ["dispatch_conflict", "malformed"]


# --- one claim identity links all or nothing ----------------------------------------------------------

@pytest.mark.parametrize("other", [
    lambda: release(LEGACY),                                         # an unbound record of the same identity
    lambda: release(binding(R2)),                                    # another recorded request
    lambda: release(binding(request_digest="A" * 64)),               # a malformed binding
    lambda: release(run_id="wd-x"),                                  # same binding, owner mismatch
    lambda: dict(release(), extra=float("nan")),                    # a non-JSON copy, identity read hook-free
])
def test_any_other_or_missing_binding_of_the_same_identity_poisons_it(other):
    out = run((R1, R2), acquisitions=[claim(), other()], controls=[control()])
    assert out["associations"] == []
    assert "linkage_conflict" in unlinked(out)


def test_a_hostile_record_of_the_same_identity_is_refused_and_runs_no_hook():
    # A dict subclass cannot be attributed hook-free; it may be another binding of this identity, so
    # nothing is associated (RCO1, Lead 18:05Z; previously a named limit that linked).
    out = run(acquisitions=[claim(), _Tripwire(release())])
    assert out["associations"] == [] and unlinked(out) == ["acquisition_coverage_incomplete"]
    assert reasons(out, "acquisition") == ["malformed"] and out["coverage"]["complete"] is False


def test_r1_released_then_r2_reclaimed_by_one_session_token_is_a_linkage_conflict_not_r2():
    # The S2 key has no request in it, so one identity with two requests must not yield either.
    out = run((R2,), acquisitions=[release(binding(R1)), claim(binding(R2), claimed_at_utc="2026-10-01T16:56:00Z")],
              controls=[control(R2)])
    assert out["associations"] == [] and sorted(unlinked(out)) == ["linkage_conflict", "linkage_conflict"]


def test_another_session_token_is_another_identity():
    out = run(acquisitions=[claim(), release(LEGACY, owner_token_sha256="d" * 64)])
    assert len(out["associations"]) == 1 and unlinked(out) == ["no_recorded_request"]


def test_exact_repeats_count_once():
    out = run(acquisitions=[claim(), claim()])
    assert len(out["associations"]) == 1 and out["coverage"]["duplicates"] == 1 and out["duplicates_ignored"] == 1


# --- malformed inputs ---------------------------------------------------------------------------------

@pytest.mark.parametrize("bound", [
    None, "req-s6-1", [], {}, binding(extra=1), binding(schema="wd.claim-request-binding.v2"),
    binding(request_digest="a" * 63), binding(task_revision=""),
    {k: v for k, v in binding().items() if k != "task_revision"},
])
def test_a_malformed_binding_is_rejected_and_binds_nothing(bound):
    row = claim()
    row["request_binding"] = bound
    out = run(acquisitions=[row])
    assert out["associations"] == [] and reasons(out, "acquisition") == ["malformed"]


@pytest.mark.parametrize("over", [{"owner_token_sha256": "E" * 64}, {"owner_token_sha256": None},
                                  {"run_id": ""}, {"agent_uuid": 7}])
def test_malformed_owner_fields_are_rejected(over):
    out = run(acquisitions=[claim(**over)])
    assert out["associations"] == [] and reasons(out, "acquisition") == ["malformed"]


class _Liar(str):
    def __eq__(self, other):
        return True

    __hash__ = str.__hash__


class _HookRan(Exception):
    pass


class _Tripwire(dict):
    def _trip(self, *args, **kwargs):
        raise _HookRan("a hostile record hook ran")

    get = __getitem__ = __contains__ = __iter__ = keys = items = values = __eq__ = __len__ = _trip
    __hash__ = None


@pytest.mark.parametrize("hostile", [
    lambda: _Tripwire(claim()), lambda: claim(binding(request_id=_Liar("req-s6-1"))),
    lambda: claim(lease_seconds=float("inf")), lambda: claim(extra={1: "non-str key"}), lambda: "a claim", lambda: None,
])
def test_hostile_or_non_json_acquisitions_are_rejected_without_hooks(hostile):
    out = run(acquisitions=[hostile()])
    assert out["associations"] == [] and reasons(out, "acquisition") == ["malformed"]


def test_deep_nesting_is_refused_not_a_recursion_error():
    deep: list = []
    for _ in range(200):
        deep = [deep]
    out = run(acquisitions=[claim(extra=deep)])
    assert out["associations"] == [] and reasons(out) == ["malformed"]


# --- boundaries -------------------------------------------------------------------------------------

@pytest.mark.parametrize("now", [datetime(2026, 10, 1, 17, 0), "2026-10-01T17:00:00Z", None,
                                 datetime(1, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None)])
def test_now_must_be_an_offset_aware_datetime(now):
    with pytest.raises(ValueError):
        associations([R1], [claim()], [control()], now)


@pytest.mark.parametrize("position", range(3))
def test_every_input_must_be_a_list(position):
    args = [[R1], [claim()], [control()]]
    args[position] = tuple(args[position])
    with pytest.raises(ValueError):
        associations(*args, NOW)


def test_inputs_are_not_mutated():
    args = ([R1, R2], [claim(), release(LEGACY)], [control()])
    before = copy.deepcopy(args)
    associations(*args, NOW)
    assert args == before


@pytest.mark.parametrize("target", ["_link", "_strict_items", "_binding", "digest", "_dispatch_ok"])
@pytest.mark.parametrize("signal", [KeyboardInterrupt, SystemExit, GeneratorExit, RuntimeError])
def test_cancellation_and_unexpected_errors_propagate(monkeypatch, target, signal):
    def boom(*a, **k):
        raise signal()
    monkeypatch.setattr(module, target, boom)
    with pytest.raises(signal):
        run(acquisitions=[claim()])


def test_the_module_reads_no_clock_environment_file_or_network():
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in node.names} | {node.module.split(".")[0] for node in ast.walk(tree)
                                            if isinstance(node, ast.ImportFrom) and node.module}
    assert not imported & {"os", "subprocess", "socket", "time", "pathlib", "io", "shutil", "urllib", "random"}
    called = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Attribute)}
    assert not called & {"now", "utcnow", "today", "open", "getenv", "read_text", "write_text"}


# --- unknown coverage stays unknown (RCO1, Lead 18:05Z) ---------------------------------------------------

COVERAGE_KEYS = {"schema", "acquisitions", "associated", "unlinked", "rejected", "duplicates", "complete"}


def test_coverage_a_fully_attributable_batch_is_complete_and_links():
    out = run(acquisitions=[claim(), release()])
    assert len(out["associations"]) == 1 and out["unlinked"] == []
    assert set(out["coverage"]) == COVERAGE_KEYS and out["coverage"]["complete"] is True


class _Hidden(dict):
    """A dict subclass that is never read (no hook is overridden): unattributable by exact type alone."""


@pytest.mark.parametrize("hidden", [
    lambda: _Hidden(control(state="cancelled", observed="2026-10-01T16:55:00Z")),   # maybe this task's cancel
    lambda: ["not", "a", "control"],
    lambda: {k: v for k, v in control(state="cancelled").items() if k != "task_id"},  # strict, no task_id
    lambda: dict(control(), task_id=""),
])
def test_coverage_an_unattributable_refused_control_leaves_every_task_unknown(hidden):
    out = run(acquisitions=[claim()], controls=[control(), hidden()])
    assert out["associations"] == [] and unlinked(out) == ["control_coverage_incomplete"]
    assert reasons(out, "control") == ["malformed"] and out["coverage"]["complete"] is False
    assert (out["coverage"]["associated"], out["coverage"]["unlinked"]) == (0, 1)


def test_coverage_a_readable_malformed_control_still_poisons_only_its_own_task():
    out = run(acquisitions=[claim()], controls=[control(), dict(control(), task_id="codex-lead-1/other", state="x")])
    assert len(out["associations"]) == 1 and reasons(out, "control") == ["malformed"]
    assert out["coverage"]["complete"] is True


@pytest.mark.parametrize("hidden", [
    lambda: 42,
    lambda: _Hidden(release(binding(R2))),                     # maybe another binding of this identity
    lambda: {k: v for k, v in claim().items() if k != "owner_token_sha256"},   # strict, identity unreadable
])
def test_coverage_an_unattributable_refused_acquisition_links_nothing(hidden):
    out = run((R1, R2), acquisitions=[claim(), hidden()])
    assert out["associations"] == [] and unlinked(out) == ["acquisition_coverage_incomplete"]
    assert reasons(out, "acquisition") == ["malformed"] and out["coverage"]["complete"] is False


def test_coverage_an_attributable_malformed_acquisition_of_another_identity_does_not_block():
    other = dict(claim(owner_token_sha256="d" * 64), extra=float("nan"))   # readable identity, other token
    out = run(acquisitions=[claim(), other])
    assert len(out["associations"]) == 1 and out["coverage"]["complete"] is True


def test_coverage_control_incompleteness_is_reported_before_acquisition_incompleteness():
    out = run(acquisitions=[claim(), 42], controls=[control(), ["x"]])
    assert out["associations"] == [] and unlinked(out) == ["control_coverage_incomplete"]


def test_coverage_a_non_json_control_with_a_readable_task_poisons_only_that_task():
    # Refused by the strict gate (NaN) but its task_id is readable hook-free: it is attributable.
    elsewhere = dict(control(), task_id="codex-lead-1/other", observed_utc=float("nan"))
    out = run(acquisitions=[claim()], controls=[control(), elsewhere])
    assert len(out["associations"]) == 1 and out["coverage"]["complete"] is True
    here = dict(control(state="cancelled"), observed_utc=float("nan"))
    same = run(acquisitions=[claim()], controls=[control(), here])
    assert same["associations"] == [] and unlinked(same) == ["control_conflict"]
    assert same["coverage"]["complete"] is True
