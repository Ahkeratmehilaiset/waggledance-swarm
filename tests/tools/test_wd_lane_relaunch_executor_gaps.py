# SPDX-License-Identifier: BUSL-1.1
"""Executor guards the main suite leaves unpinned: executor identity, Lead-lane session, journal reads."""
from __future__ import annotations

import pytest

from test_wd_lane_relaunch_executor import (  # sibling test module: its fakes are the fixtures here
    DIGEST, Executor, FakePorts, NOW, RecoveryStore, auto_mode, iso, phase, request, run, signed_catalog,
)

OTHER_UUID = "22222222-2222-4222-8222-222222222222"


def with_executor(ports, **changes):
    """The authenticate port answers as usual, except for the given executor fields."""
    original = ports.authenticate

    def authenticate(req):
        result = original(req)
        result["executor"] = {**result["executor"], **changes}
        return result

    ports.authenticate = authenticate
    return ports


def test_r7_the_executing_lead_must_carry_the_requesting_leads_uuid(tmp_path, auto_mode):
    # Same principal name and the same session, but another agent_uuid: not the requesting Lead.
    ports = with_executor(FakePorts(), agent_uuid=OTHER_UUID)
    payload, _, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["executor_is_not_the_requesting_lead"])
    assert ports.claims == [] and ports.stops == [] and ex.tid is None


@pytest.mark.parametrize("session", ["", None])
def test_r7_a_supervisor_without_a_session_identity_is_unauthenticated_for_the_lead_lane(tmp_path, auto_mode, session):
    lead = request(lane="codex-lead-1", current="codex-gpt-5.6-sol-medium", target="codex-gpt-6-sol-high")
    ports = with_executor(FakePorts(lane="codex-lead-1", principal="supervisor"), session_id=session)
    payload, _, _ = run(tmp_path, ports, req=lead)
    assert (payload["outcome"], payload["reasons"]) == ("parked", ["principal_unauthenticated"])
    assert ports.claims == [] and ports.stops == []


def test_r7_a_supervisor_with_a_session_identity_gets_past_authentication_for_the_lead_lane(tmp_path, auto_mode):
    # Control for the case above: the same lane and principal with a session id is not refused as unauthenticated.
    lead = request(lane="codex-lead-1", current="codex-gpt-5.6-sol-medium", target="codex-gpt-6-sol-high")
    ports = FakePorts(lane="codex-lead-1", principal="supervisor")
    payload, _, _ = run(tmp_path, ports, req=lead)
    assert payload["reasons"] != ["principal_unauthenticated"]


def counted(store, tmp_path):
    """The relaunches the journal counts, read by a fresh executor exactly as a later request would."""
    return Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                    runtime_root=str(tmp_path / "rt"), request=request())._journal_history()


def test_r6_a_transition_plan_that_is_not_an_object_makes_the_history_unknown_and_parks(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                     runtime_root=str(tmp_path / "rt0"), request=request())
    assert first.run()["payload"]["outcome"] == "applied"
    with store.connect() as db:
        db.execute("UPDATE transitions SET plan='[1, 2]' WHERE id=?", (first.tid,))
    assert counted(store, tmp_path) is None
    second = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                      runtime_root=str(tmp_path / "rt1"), request=dict(request(), request_id="req-2")).run()
    assert (second["payload"]["outcome"], second["payload"]["reasons"]) == ("park", ["relaunch_history_unknown"])


def test_r6_the_first_marker_of_a_transition_is_the_one_that_counts(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                     runtime_root=str(tmp_path / "rt0"), request=request())
    assert first.run()["payload"]["outcome"] == "applied"
    real = [{"lane": "claude-rco-1", "ts_utc": iso(NOW), "outcome": "stop_attempted"}]
    assert counted(store, tmp_path) == real
    # A later apply_pending row carrying an OLD stamp must not move the counted stop out of the window.
    with store.connect() as db:
        db.execute("INSERT INTO journal(transition_id,phase,observed_at,reason) VALUES (?,?,?,?)",
                   (first.tid, "apply_pending", "x", '{"source_stopped_at": "2026-09-26T09:00:00Z"}'))
    assert counted(store, tmp_path) == real
