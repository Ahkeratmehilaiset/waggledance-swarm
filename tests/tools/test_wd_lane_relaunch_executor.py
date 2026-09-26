# SPDX-License-Identifier: BUSL-1.1
"""Relaunch executor steps 3-9 with in-process fakes: no process, no bridge, no provider."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import tools.wd_lane_relaunch_executor as executor_module
from tools.bridge_capacity_recovery import RecoveryStore
from tools.lane_profile_catalog import load_catalog
from tools.wd_lane_relaunch_executor import ClaimConflict, Executor

ROOT = Path(__file__).resolve().parents[2]
BASE, DIGEST = load_catalog(ROOT / "configs" / "lane_profile_catalog.json")
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
T1 = "11111111-1111-4111-8111-111111111111"
T2 = "22222222-2222-4222-8222-222222222222"
T3 = "33333333-3333-4333-8333-333333333333"
LEAD_UUID = "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
MODELS = {"claude-sonnet-5-xhigh": ("claude-sonnet-5", "xhigh"),
          "claude-opus-5-5-xhigh": ("claude-opus-5-5", "xhigh")}


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def signed_catalog() -> dict:
    catalog = json.loads(json.dumps(BASE))
    catalog["operator_signature"] = "operator 2026-09-26 reviewed PR"
    for number, profile in enumerate(catalog["capacity_policy"]["profiles"].values()):
        profile.update(approved=True, qualification_ref=f"qual-2026-09-26-{number:03d}")
    return catalog


@pytest.fixture
def auto_mode(monkeypatch):
    """The advisor is shadow-only today; simulate the future auto mode for the executor."""
    monkeypatch.setattr(executor_module, "effective_mode", lambda catalog: "auto")


class FakePorts:
    def __init__(self, *, lane="claude-rco-1", launches=("good",), stop_ok=True, claim_conflict=False,
                 preflight=(), resume=True, checkpoint="ckpt-1", change_after_claim=False,
                 current_obs_profile="claude-sonnet-5-xhigh", evidence_pin="manifest_and_launcher_verified",
                 principal="codex-lead-1", resume_ok=True):
        self.clock = NOW
        self.principal, self.requester, self.auth_ok = principal, None, True
        self.request_ts, self.signature_ok, self.signature_checks = None, True, []
        self.resume_ok, self.resumed = resume_ok, []
        self.extra_processes: list = []
        self.lane = lane
        self.proc = {"pid": 100, "started": iso(NOW - timedelta(minutes=30)), "thread": T1, "session": "S1"}
        self.launches = list(launches)
        self.stop_ok, self.claim_conflict, self.preflight = stop_ok, claim_conflict, list(preflight)
        self.resume, self.ckpt, self.change_after_claim = resume, checkpoint, change_after_claim
        self.evidence_pin = evidence_pin
        self.obs = [{"provider": "claude", "native_thread_id": T1, "model": MODELS[current_obs_profile][0],
                     "effort": MODELS[current_obs_profile][1], "observed_at": iso(NOW - timedelta(minutes=1))}]
        self.records: dict = {}
        self.events: list = []
        self.stops: list = []
        self.claims: list = []
        self.released: list = []
        self.measures = 0
        self.next_pid, self.threads = 200, [T2, T3]

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def measure(self, lane):
        self.measures += 1
        session = "S1-changed" if (self.change_after_claim and self.measures > 1) else self.proc["session"]
        return {"lane": lane, "is_supervisor": False, "observed_at": iso(self.clock),
                "current_session_id": session, "idle": True, "pending_effects": False,
                "previous_turn_blocker": False, "open_claims": [], "unresolved_requests": [],
                "pid": self.proc["pid"], "process_started_at": self.proc["started"],
                "native_thread_id": self.proc["thread"], "resume_supported": self.resume}

    def authenticate(self, request):
        if not self.auth_ok:
            return None
        claimed = request.get("requested_by") if isinstance(request, dict) else None
        lead = self.principal == "codex-lead-1"
        executor = {"principal": self.principal, "agent_uuid": LEAD_UUID if lead else "sup-uuid",
                    "session_id": "lead-S" if lead else "sup-S", "verification_ref": "auth-1"}
        requester = dict(self.requester or claimed or {})
        requester.setdefault("request_ts_utc", self.request_ts or iso(self.clock - timedelta(seconds=30)))
        return {"executor": executor, "requester": requester}

    def verify_catalog_signature(self, catalog_sha256, operator_signature):
        self.signature_checks.append((catalog_sha256, operator_signature))
        if isinstance(self.signature_ok, Exception):
            raise self.signature_ok
        return self.signature_ok

    def processes(self, lane):
        # Built from evidence() so a test that forges evidence forges every enumeration.
        facts = self.evidence(lane)
        rows = [facts] if isinstance(facts, dict) and facts.get("pid") is not None else []
        return rows + [dict(row) for row in self.extra_processes]

    def resume_lane(self, lane, epoch, checkpoint):
        self.resumed.append((lane, epoch["pid"], checkpoint))
        if isinstance(self.resume_ok, Exception):
            raise self.resume_ok
        return self.resume_ok

    def evidence(self, lane):
        return {"pin_status": self.evidence_pin, "pid": self.proc["pid"],
                "process_started_at": self.proc["started"], "native_conversation_id": self.proc["thread"]}

    def observations(self, lane):
        return {"claude": list(self.obs), "codex": None}

    def take_claim(self, lane, scope, lease_seconds):
        if self.claim_conflict:
            raise ClaimConflict("held by another agent")
        self.claims.append((scope, lease_seconds))
        return "claim-1"

    def release_claim(self, claim_id):
        self.released.append(claim_id)

    def preflight_launch(self, lane, profile):
        return list(self.preflight)

    def checkpoint(self, lane):
        if isinstance(self.ckpt, Exception):
            raise self.ckpt
        return self.ckpt

    def stop(self, lane, pid, started_at):
        self.stops.append((pid, started_at))
        if not self.stop_ok or pid != self.proc["pid"] or started_at != self.proc["started"]:
            return False
        self.proc = {"pid": None, "started": None, "thread": None, "session": None}
        return True

    def launch(self, lane, profile):
        behaviour = self.launches.pop(0) if self.launches else "silent"
        if behaviour == "silent":
            return
        if behaviour == "raise":
            raise RuntimeError("launcher crashed")
        pid, thread = self.next_pid, self.threads.pop(0)
        self.next_pid += 1
        started = iso(self.clock)
        self.proc = {"pid": pid, "started": started, "thread": thread, "session": f"S-{pid}"}
        record = self.records[lane]
        record["launched"] = {"native_thread_id": thread, "pid": pid, "process_started_at": started,
                              "session_id": f"S-{pid}", "run_id": f"S-{pid}", "launched_at": started}
        model, effort = (profile["model"], profile["effort"])
        if behaviour == "wrong_model":
            model = "claude-haiku-4-5"
        self.obs.append({"provider": "claude", "native_thread_id": thread, "model": model,
                         "effort": effort, "observed_at": started})

    def read_record(self, lane):
        return json.loads(json.dumps(self.records[lane]))

    def write_record(self, lane, record):
        self.records[lane] = json.loads(json.dumps(record))

    def emit(self, event):
        self.events.append(event)


def request(lane="claude-rco-1", current="claude-sonnet-5-xhigh", target="claude-opus-5-5-xhigh"):
    return {"lane": lane, "current_profile": current, "target_profile": target, "request_id": "req-1",
            "reason": "review depth", "history": [],
            "requested_by": {"agent": "codex-lead-1", "agent_uuid": LEAD_UUID, "session_id": "lead-S"}}


def run(tmp_path, ports, *, catalog=None, req=None, executor="lead"):
    ports.principal = {"lead": "codex-lead-1", "supervisor": "supervisor"}.get(executor, executor)
    store = RecoveryStore(tmp_path / "journal.sqlite")
    ex = Executor(catalog=catalog or signed_catalog(), catalog_sha256=DIGEST, store=store, ports=ports,
                  runtime_root=str(tmp_path / "runtime"), request=req or request())
    receipt = ex.run()
    return receipt["payload"], store, ex


def phase(store, tid):
    return store.get(tid)["phase"] if tid is not None else None


# ------------------------------------------------------------ mode gates

def test_shadow_only_returns_would_relaunch_and_touches_nothing(tmp_path):
    ports = FakePorts()
    payload, _, ex = run(tmp_path, ports)
    assert payload["outcome"] == "would_relaunch" and payload["mode"] == "shadow"
    assert ports.stops == [] and ports.claims == [] and ports.records == {} and ex.tid is None


def test_unsigned_catalog_parks_even_in_auto(tmp_path, auto_mode):
    payload, _, _ = run(tmp_path, FakePorts(), catalog=json.loads(json.dumps(BASE)))
    assert (payload["outcome"], payload["reasons"]) == ("parked", ["catalog_unsigned"])


def test_approve_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(executor_module, "effective_mode", lambda catalog: "approve")
    payload, _, _ = run(tmp_path, FakePorts())
    assert (payload["outcome"], payload["reasons"]) == ("parked", ["operator_ack_unverifiable"])


def test_lead_self_transition_only_through_the_supervisor(tmp_path, auto_mode):
    lead = request(lane="codex-lead-1", current="codex-gpt-5.6-sol-medium", target="codex-gpt-6-sol-high")
    payload, _, _ = run(tmp_path, FakePorts(lane="codex-lead-1"), req=lead)
    assert payload["reasons"] == ["self_transition_requires_supervisor"]


def test_catalog_park_never_reaches_a_side_effect(tmp_path, auto_mode):
    ports = FakePorts()
    lowering = request(current="claude-opus-5-5-xhigh", target="claude-sonnet-5-xhigh")
    payload, _, _ = run(tmp_path, ports, req=lowering)
    assert (payload["outcome"], payload["reasons"]) == ("park", ["reviewer_lowering"])
    assert ports.claims == [] and ports.stops == []


# ------------------------------------------------------------ preconditions

def test_current_profile_is_verified_from_evidence_not_the_request(tmp_path, auto_mode):
    # The request claims sonnet, but the live process shows opus: never trust the claim.
    ports = FakePorts(current_obs_profile="claude-opus-5-5-xhigh")
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"] == ["current_profile_unverified"]
    assert ports.claims == []


def test_claim_conflict_aborts_before_any_stop(tmp_path, auto_mode):
    ports = FakePorts(claim_conflict=True)
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "aborted" and payload["reasons"][0] == "claim_conflict"
    assert ports.stops == []


def test_lane_change_between_check_and_claim_aborts_and_releases(tmp_path, auto_mode):
    ports = FakePorts(change_after_claim=True)
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"] == ["lane_changed_after_claim"]
    assert ports.stops == [] and ports.released == ["claim-1"]


def test_target_preconditions_are_checked_before_the_stop(tmp_path, auto_mode):
    ports = FakePorts(preflight=["manifest_anchor_missing"])
    payload, _, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["target_launch_preconditions_failed", "manifest_anchor_missing"]
    assert ports.stops == [] and ex.tid is None and ports.released == ["claim-1"]


@pytest.mark.parametrize("checkpoint", [RuntimeError("disk"), "", None])
def test_no_continuity_aborts(tmp_path, auto_mode, checkpoint):
    ports = FakePorts(resume=False, checkpoint=checkpoint)
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"][0] == "no_continuity" and ports.stops == []


def test_claim_scope_covers_record_lock_and_readiness(tmp_path, auto_mode):
    ports = FakePorts()
    run(tmp_path, ports)
    scope, lease = ports.claims[0]
    assert scope[0].endswith("claude-rco-1.json") and scope[1].endswith(".transition.lock")
    assert scope[2].endswith("readiness/claude-rco-1.json")
    assert lease >= BASE["fleet"]["verify_timeout_seconds"] + 600


# ------------------------------------------------------------ transitions

def test_happy_path_applies_and_journals_both_epochs(tmp_path, auto_mode):
    ports = FakePorts()
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "applied"
    assert ports.stops == [(100, iso(NOW - timedelta(minutes=30)))]
    assert payload["source_epoch"]["pid"] == 100
    assert payload["target_epoch"] == {"pid": 200, "process_started_at": iso(NOW), "native_thread_id": T2,
                                       "session_id": "S-200", "launched_at": iso(NOW),
                                       "profile": "claude-opus-5-5-xhigh"}
    assert phase(store, ex.tid) == "resumed"
    assert payload["catalog_sha256"] == DIGEST and ports.released == ["claim-1"]
    assert ports.events[-1]["status"] == "profile_transition"


def test_supervisor_can_transition_the_lead(tmp_path, auto_mode):
    lead = request(lane="codex-lead-1", current="codex-gpt-5.6-sol-medium", target="codex-gpt-6-sol-high")
    ports = FakePorts(lane="codex-lead-1")
    ports.obs = []
    payload, _, _ = run(tmp_path, ports, req=lead, executor="supervisor")
    # Codex evidence comes from the rollout; this fake has none, so it stays unverified.
    assert payload["reasons"] == ["current_profile_unverified"]


def test_source_stop_failure_frees_the_reservation(tmp_path, auto_mode):
    ports = FakePorts(stop_ok=False)
    payload, store, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("failed", ["source_stop_failed"])
    assert phase(store, ex.tid) == "cancelled_before_apply"


def test_silent_launcher_times_out_and_rolls_back(tmp_path, auto_mode):
    ports = FakePorts(launches=("silent", "good"))
    payload, store, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("rolled_back", ["verify_timeout"])
    assert payload["target_epoch"]["profile"] == "claude-sonnet-5-xhigh"
    assert phase(store, ex.tid) == "resumed"
    assert ports.records["claude-rco-1"]["previous_profile"] == "claude-sonnet-5-xhigh"


def test_wrong_model_is_stopped_and_rolled_back(tmp_path, auto_mode):
    ports = FakePorts(launches=("wrong_model", "good"))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back"
    # The stray target (pid 200) was stopped by its launcher-recorded identity.
    assert ports.stops[1][0] == 200


def test_rollback_failure_leaves_the_lane_stopped_and_the_reservation_held(tmp_path, auto_mode):
    ports = FakePorts(launches=("silent", "silent"))
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["verify_timeout", "rollback_failed", "operator_required"]
    assert phase(store, ex.tid) == "apply_pending"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reservations WHERE transition_id=?", (ex.tid,)).fetchone()[0] > 0


def test_evidence_ancestry_mismatch_never_binds_the_target(tmp_path, auto_mode):
    ports = FakePorts(launches=("good", "good"))
    original = ports.evidence

    def forged(lane):
        facts = original(lane)
        if facts["pid"] == 200:
            facts["pid"] = 999  # a caller-supplied pid that the launcher never recorded
        return facts
    ports.evidence = forged
    payload, _, _ = run(tmp_path, ports)
    # The target never binds; the stray cannot be proven either, so nothing is killed from the record.
    assert payload["outcome"] == "failed"
    assert payload["reasons"] == ["verify_timeout", "stray_identity_unproven", "operator_required"]
    assert [pid for pid, _ in ports.stops] == [100]


def test_unverified_pin_status_never_binds_the_target(tmp_path, auto_mode):
    ports = FakePorts(launches=("good", "good"))
    original = ports.evidence
    ports.evidence = lambda lane: dict(original(lane), pin_status="mismatch") if ports.proc["pid"] == 200 \
        else original(lane)
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "failed" and "stray_identity_unproven" in payload["reasons"]
    assert [pid for pid, _ in ports.stops] == [100]


def test_journal_refuses_a_second_transition_on_the_same_bucket(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(launches=("silent", "silent")),
                     runtime_root=str(tmp_path / "rt"), request=request()).run()
    assert first["payload"]["outcome"] == "failed"
    # claude-rco-2 has its own lane budget but shares the Claude quota bucket, still reserved by the
    # failed first transition: the journal, not the budget, refuses it.
    second_req = dict(request(lane="claude-rco-2"), request_id="req-2")
    ports = FakePorts(lane="claude-rco-2")
    second = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=ports,
                      runtime_root=str(tmp_path / "rt2"), request=second_req).run()
    assert (second["payload"]["outcome"], second["payload"]["reasons"][0]) == ("parked", "journal_refused")
    assert ports.stops == []


def test_unsafe_boundary_aborts_before_the_claim(tmp_path, auto_mode):
    ports = FakePorts()
    original = ports.measure
    ports.measure = lambda lane: dict(original(lane), idle=False)
    payload, _, _ = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["not_idle"])
    assert ports.claims == [] and ports.stops == []



# ---- claude-rco-2 residual A: a junctioned lane_profiles directory

def _junction(link: Path, target: Path) -> bool:
    import subprocess
    import sys
    if sys.platform != "win32":
        return False
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    return result.returncode == 0


def test_record_io_refuses_a_junctioned_ancestor(tmp_path):
    from tools.lane_profile_record import RecordError, read_record, record_path, write_record
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    if not _junction(runtime / "lane_profiles", outside):
        pytest.skip("junctions unavailable")
    path = record_path(runtime, "claude-rco-1")
    with pytest.raises(RecordError, match="ancestor is a symlink or reparse point"):
        write_record(path, {"schema": "x"})
    assert list(outside.iterdir()) == []
    (outside / "claude-rco-1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RecordError, match="ancestor is a symlink or reparse point"):
        read_record(path)


def test_record_io_still_works_on_a_plain_tree(tmp_path):
    from tools.lane_profile_record import read_record, record_path, write_record
    path = record_path(tmp_path / "runtime", "claude-rco-1")
    write_record(path, {"schema": "x"})
    assert read_record(path) == {"schema": "x"}



# ---- claude-rco-1 review of 486fe124 (B1-B5)

def seed_stop(store, lane, minutes_ago, *, task_id="lane-profile-switching", marker=True, key=None):
    """A journaled source stop, written the way the executor writes it."""
    import sqlite3
    db = sqlite3.connect(store.path)
    with db:
        plan = json.dumps({"binding": {"agent_id": lane, "task_id": task_id}})
        cursor = db.execute("INSERT INTO transitions(request_key,fingerprint,plan,phase) VALUES (?,?,?,?)",
                            (key or f"{lane}:{minutes_ago}:{task_id}", "f", plan, "resumed"))
        reason = json.dumps({"source_stopped_at": iso(NOW - timedelta(minutes=minutes_ago))}) if marker else None
        db.execute("INSERT INTO journal(transition_id,phase,observed_at,reason) VALUES (?,?,?,?)",
                   (cursor.lastrowid, "apply_pending", iso(NOW), reason))
    db.close()


def run_with_store(tmp_path, ports, store, req=None):
    ex = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=ports,
                  runtime_root=str(tmp_path / "runtime"), request=req or request())
    return ex.run()["payload"], ex


def test_r6_budget_counts_journaled_source_stops_not_the_request(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    seed_stop(store, "claude-rco-1", 10)
    req = request()
    req["history"] = []  # a requester blanking its history cannot defeat the budget
    ports = FakePorts()
    payload, _ = run_with_store(tmp_path, ports, store, req)
    assert (payload["outcome"], payload["reasons"]) == ("park", ["lane_budget_exhausted", "lane_cooldown"])
    assert ports.stops == [] and ports.claims == []


def test_r6_a_requester_claimed_history_is_ignored(tmp_path, auto_mode):
    req = request()
    req["history"] = [{"lane": "claude-rco-1", "ts_utc": iso(NOW - timedelta(minutes=1)), "outcome": "applied"}]
    payload, _, _ = run(tmp_path, FakePorts(), req=req)
    assert payload["outcome"] == "applied"


def test_r6_fleet_budget_is_counted_across_lanes(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    for i, lane in enumerate(["fable-5", "fable-5", "codex-tools-1", "codex-tools-1"]):
        seed_stop(store, lane, 10 + i)
    ports = FakePorts()
    payload, _ = run_with_store(tmp_path, ports, store)
    assert (payload["outcome"], payload["reasons"]) == ("park", ["fleet_budget_exhausted"])
    assert ports.stops == [] and ports.claims == []


def test_r6_three_fleet_stops_leave_room_for_a_fourth(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    for i, lane in enumerate(["fable-5", "fable-5", "codex-tools-1"]):
        seed_stop(store, lane, 10 + i)
    payload, _ = run_with_store(tmp_path, FakePorts(), store)
    assert payload["outcome"] == "applied"


def test_r6_other_tasks_in_the_store_are_not_counted(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    seed_stop(store, "claude-rco-1", 10, task_id="capacity-recovery")
    payload, _ = run_with_store(tmp_path, FakePorts(), store)
    assert payload["outcome"] == "applied"


def test_r6_a_stop_without_its_marker_makes_history_unknown(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    seed_stop(store, "codex-tools-1", 10, marker=False)
    ports = FakePorts()
    payload, _ = run_with_store(tmp_path, ports, store)
    assert (payload["outcome"], payload["reasons"]) == ("park", ["relaunch_history_unknown"])
    assert ports.claims == []


def test_r6_an_unreadable_journal_parks(tmp_path, auto_mode, monkeypatch):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    monkeypatch.setattr(store, "connect", lambda: (_ for _ in ()).throw(OSError("locked")))
    ports = FakePorts()
    payload, _ = run_with_store(tmp_path, ports, store)
    assert (payload["outcome"], payload["reasons"]) == ("park", ["relaunch_history_unknown"])
    assert ports.claims == []


def test_r6_an_applied_transition_counts_for_the_next_request(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first, _ = run_with_store(tmp_path, FakePorts(), store)
    assert first["outcome"] == "applied"
    # The same raise again (say the operator reverted the lane): the journaled stop is counted.
    second, _ = run_with_store(tmp_path, FakePorts(), store, dict(request(), request_id="req-2"))
    assert (second["outcome"], second["reasons"]) == ("park", ["lane_budget_exhausted", "lane_cooldown"])


@pytest.mark.parametrize("ports_kwargs", [dict(stop_ok=False), dict(claim_conflict=True), dict(preflight=("x",))])
def test_r6_attempts_that_never_stopped_the_source_do_not_count(tmp_path, auto_mode, ports_kwargs):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first, _ = run_with_store(tmp_path, FakePorts(**ports_kwargs), store)
    assert first["outcome"] in ("failed", "aborted")
    second, _ = run_with_store(tmp_path, FakePorts(), store, dict(request(), request_id="req-2"))
    assert second["outcome"] == "applied"





def test_b2_rollback_never_kills_a_pid_only_the_record_names(tmp_path, auto_mode):
    ports = FakePorts(launches=("wrong_model",))
    original_launch = ports.launch

    def launch_with_forged_record(lane, profile):
        original_launch(lane, profile)
        ports.records[lane]["launched"]["pid"] = 4242  # the record lies; evidence says 200
    ports.launch = launch_with_forged_record
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"] == ["verify_timeout", "stray_identity_unproven", "operator_required"]
    assert 4242 not in [pid for pid, _ in ports.stops]


def test_b2_live_unrecorded_target_is_never_doubled(tmp_path, auto_mode):
    # NB-d: the launcher started the target but never recorded it; do not launch a second one.
    ports = FakePorts(launches=("good",))
    original_launch = ports.launch

    def launch_unrecorded(lane, profile):
        original_launch(lane, profile)
        ports.records[lane]["launched"] = None
    ports.launch = launch_unrecorded
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "failed" and "stray_identity_unproven" in payload["reasons"]
    assert ports.launches == []  # no rollback launch happened


def test_b3_stop_failure_neutralises_the_target_record(tmp_path, auto_mode):
    ports = FakePorts(stop_ok=False)
    run(tmp_path, ports)
    record = ports.records["claude-rco-1"]
    assert record["desired_profile"] == record["previous_profile"] == "claude-sonnet-5-xhigh"


def test_b4_raising_launch_is_rolled_back_with_a_receipt(tmp_path, auto_mode):
    ports = FakePorts(launches=("raise", "good"))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back" and ports.released == ["claim-1"]


def test_b4_exception_after_stop_holds_the_reservation_and_reports(tmp_path, auto_mode):
    ports = FakePorts()
    original = ports.read_record
    # The record port fails only once the source is gone, i.e. after the stop. (An evidence port that
    # raises no longer escapes: enumeration failure is handled as unknown evidence.)
    ports.read_record = lambda lane: (original(lane) if ports.proc["pid"] == 100
                                      else (_ for _ in ()).throw(RuntimeError("record port down")))
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "failed"
    assert payload["reasons"][:2] == ["executor_exception", "RuntimeError"] and "operator_required" in payload["reasons"]
    assert phase(store, ex.tid) == "apply_pending" and ports.released == ["claim-1"]


def test_b4_request_without_requested_by_aborts_before_the_claim(tmp_path, auto_mode):
    ports = FakePorts()
    req = request()
    del req["requested_by"]
    payload, _, ex = run(tmp_path, ports, req=req)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["request_shape_invalid"])
    assert ports.claims == [] and ex.tid is None


def test_b4_exception_before_stop_cancels_and_neutralises(tmp_path, auto_mode):
    ports = FakePorts()
    ports.stop = lambda lane, pid, started: (_ for _ in ()).throw(RuntimeError("stop port down"))
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_exception", "RuntimeError"]
    assert phase(store, ex.tid) == "cancelled_before_apply"
    record = ports.records["claude-rco-1"]
    assert record["desired_profile"] == record["previous_profile"]
    # The quota window is free again: a valid follow-up is not blocked by the journal.
    follow = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                      runtime_root=str(tmp_path / "rt3"), request=dict(request(), request_id="req-9")).run()
    assert follow["payload"]["outcome"] == "applied"


@pytest.mark.parametrize("which", [1, 2])
def test_b5_measurement_of_another_lane_is_refused(tmp_path, auto_mode, which):
    ports = FakePorts()
    original = ports.measure
    calls = {"n": 0}

    def measure(lane):
        calls["n"] += 1
        state = original(lane)
        return dict(state, lane="codex-tools-1") if calls["n"] == which else state
    ports.measure = measure
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "aborted"
    assert payload["reasons"] in (["measurement_names_another_lane"], ["lane_changed_after_claim"])
    assert ports.stops == []


def test_nbc_start_time_precision_within_skew_still_binds(tmp_path, auto_mode):
    ports = FakePorts()
    original = ports.evidence

    def coarse(lane):
        facts = original(lane)
        if facts["pid"] == 200:
            facts["process_started_at"] = iso(datetime.fromisoformat(
                facts["process_started_at"].replace("Z", "+00:00")) + timedelta(milliseconds=900))
        return facts
    ports.evidence = coarse
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "applied"


def test_target_binding_requires_the_record_to_name_the_target_profile(tmp_path, auto_mode):
    # The process and its model match, but the launcher record says it launched a different profile.
    ports = FakePorts(launches=("good", "good"))
    original_launch = ports.launch
    calls = {"n": 0}

    def launch_mislabelled(lane, profile):
        calls["n"] += 1
        if calls["n"] == 1:  # a different, consistently observed profile is launched and recorded
            other = signed_catalog()["capacity_policy"]["profiles"]["claude-sonnet-5-xhigh"]
            original_launch(lane, other)
            ports.records[lane]["desired_profile"] = "claude-sonnet-5-xhigh"
        else:
            original_launch(lane, profile)
    ports.launch = launch_mislabelled
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] != "applied"



# ---- adopted from claude-rco-1's patch offer for #1739




def test_b3_neutralised_record_launches_the_previous_profile(tmp_path, auto_mode, monkeypatch):
    import tools.lane_profile_record as record_module
    ports = FakePorts(stop_ok=False)
    run(tmp_path, ports)
    monkeypatch.setattr(record_module, "effective_mode", lambda catalog: "auto")
    root = tmp_path / "rt"
    record_module.write_record(record_module.record_path(root, "claude-rco-1"), ports.records["claude-rco-1"])
    decision = record_module.launch_decision(root, "claude-rco-1", signed_catalog(), DIGEST,
                                             now=NOW + timedelta(hours=1))
    assert decision["profile"]["profile_id"] == "claude-sonnet-5-xhigh"


def test_b4_rollback_launch_that_raises_fails_closed(tmp_path, auto_mode):
    ports = FakePorts(launches=("raise", "raise"))
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "failed" and "operator_required" in payload["reasons"]
    assert phase(store, ex.tid) == "apply_pending" and ports.released == ["claim-1"]


@pytest.mark.parametrize("broken", ["write_record", "preflight_launch"])
def test_b4_exception_before_stop_frees_the_window_and_leaves_the_source(tmp_path, auto_mode, broken):
    ports = FakePorts()
    setattr(ports, broken, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("port failed")))
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"][:2] == ["executor_exception", "RuntimeError"]
    assert ports.released == ["claim-1"] and ports.proc["pid"] == 100 and ports.stops == []
    if ex.tid is not None:
        assert phase(store, ex.tid) == "cancelled_before_apply"
    follow = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                      runtime_root=str(tmp_path / "rt4"), request=dict(request(), request_id="req-2")).run()
    assert follow["payload"]["outcome"] == "applied"


def test_b4_failing_emit_still_returns_the_outcome_and_releases(tmp_path, auto_mode):
    ports = FakePorts(stop_ok=False)
    ports.emit = lambda event: (_ for _ in ()).throw(OSError("bridge down"))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "failed" and payload["reasons"] == ["source_stop_failed", "receipt_not_emitted"]
    assert ports.released == ["claim-1"]


@pytest.mark.parametrize("bad", [None, "lane", ["x"]])
def test_b4_non_object_request_aborts(tmp_path, auto_mode, bad):
    ports = FakePorts()
    ex = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=RecoveryStore(tmp_path / "j.sqlite"),
                  ports=ports, runtime_root=str(tmp_path / "rt"), request=bad)
    payload = ex.run()["payload"]
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["request_shape_invalid"])
    assert ports.claims == []



# ---- claude-rco-2 review of 66ee1732: source proof, unknown stop, verify window, unknown mode, stale observation

class _EvidenceFor(FakePorts):
    """Execution evidence that disagrees with the measured source, on every call or only on one of them."""

    def __init__(self, mutate, *, only_call=None, **kwargs):
        super().__init__(**kwargs)
        self._mutate, self._only_call, self.evidence_calls = mutate, only_call, 0

    def evidence(self, lane):
        self.evidence_calls += 1
        facts = super().evidence(lane)
        return self._mutate(facts) if self._only_call in (None, self.evidence_calls) else facts


_SOURCE_STARTED = iso(NOW - timedelta(minutes=30))


@pytest.mark.parametrize("mutate", [
    lambda f: {**f, "pid": 4242},
    lambda f: {**f, "pid": "100"},
    lambda f: {**f, "pin_status": "mismatch"},
    lambda f: {**f, "native_conversation_id": T3},
    lambda f: {**f, "process_started_at": iso(NOW - timedelta(minutes=30) + timedelta(seconds=10))},
    lambda f: None,
], ids=["other-pid", "string-pid", "unverified-pin", "other-conversation", "start-time-off-by-10s", "not-a-dict"])
def test_source_pid_must_be_proven_by_evidence_before_anything_is_claimed(tmp_path, auto_mode, mutate):
    ports = _EvidenceFor(mutate, only_call=1)
    payload, _, ex = run(tmp_path, ports)
    # A non-dict evidence row means no enumerated process at all: the count is not one.
    expected = "lane_process_count_not_one" if mutate({"pid": 1}) is None else "source_not_proven_by_evidence"
    assert (payload["outcome"], payload["reasons"]) == ("aborted", [expected])
    assert ports.stops == [] and ports.claims == [] and ex.tid is None


def test_source_pid_is_proven_again_after_the_claim(tmp_path, auto_mode):
    ports = _EvidenceFor(lambda f: {**f, "pid": 4242}, only_call=2)
    payload, _, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["source_not_proven_by_evidence"])
    assert ports.stops == [] and ports.released == ["claim-1"] and ex.tid is None


def test_unreadable_source_evidence_refuses(tmp_path, auto_mode):
    class Raises(FakePorts):
        def evidence(self, lane):
            raise OSError("oracle down")

    ports = Raises()
    payload, _, _ = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["source_evidence_unavailable"])
    assert ports.stops == [] and ports.claims == []


def test_source_evidence_within_the_start_time_skew_still_proves_it(tmp_path, auto_mode):
    shifted = iso(NOW - timedelta(minutes=30) + timedelta(seconds=1))
    ports = _EvidenceFor(lambda f: {**f, "process_started_at": shifted}, only_call=1)
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "applied" and ports.stops == [(100, _SOURCE_STARTED)]


class _StopKillsThenRaises(FakePorts):
    def stop(self, lane, pid, started_at):
        super().stop(lane, pid, started_at)  # the process really is gone
        raise TimeoutError("exit not confirmed")


def test_a_stop_that_raises_leaves_the_source_unknown_so_the_reservation_is_held(tmp_path, auto_mode):
    ports = _StopKillsThenRaises()
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "failed"
    assert payload["reasons"] == ["executor_exception", "TimeoutError", "operator_required"]
    assert phase(store, ex.tid) == "checkpointed"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reservations WHERE transition_id=?", (ex.tid,)).fetchone()[0] > 0
    # Whatever restarts the lane meanwhile must not pick up the aborted target.
    assert ports.records["claude-rco-1"]["desired_profile"] == "claude-sonnet-5-xhigh"
    assert ports.released == ["claim-1"]


def test_a_stop_that_raises_with_no_readable_evidence_is_treated_as_down(tmp_path, auto_mode):
    ports = _StopKillsThenRaises()
    original = ports.evidence
    ports.evidence = lambda lane: (original(lane) if ports.proc["pid"] == 100
                                   else (_ for _ in ()).throw(OSError("oracle down")))
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_exception", "TimeoutError", "operator_required"]
    assert phase(store, ex.tid) == "checkpointed"


def test_observations_older_than_the_limit_are_dropped_for_every_provider(tmp_path):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    ex = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(),
                  runtime_root=str(tmp_path / "runtime"), request=request())
    fresh, stale = iso(NOW - timedelta(seconds=60)), iso(NOW - timedelta(seconds=3600))
    kept = ex._fresh_observations({"claude": [{"observed_at": fresh}, {"observed_at": stale}, "junk", {}],
                                   "codex": {"observed_at": stale}})
    assert kept == {"claude": [{"observed_at": fresh}], "codex": None}
    assert ex._fresh_observations({"claude": None, "codex": {"observed_at": fresh}}) == {
        "claude": None, "codex": {"observed_at": fresh}}
    assert ex._fresh_observations(None) == {"claude": None, "codex": None}


@pytest.mark.parametrize("mutate", [
    lambda r: r["requested_by"].update(agent="mallory"),
    lambda r: r["requested_by"].update(role="extra"),
    lambda r: r["requested_by"].update(agent_uuid="not-a-uuid"),
    lambda r: r.update(reason="x" * 600),
], ids=["requester-not-a-lane", "requester-extra-key", "requester-uuid-malformed", "reason-too-long"])
def test_a_request_whose_record_would_be_invalid_aborts_before_the_claim(tmp_path, auto_mode, mutate):
    # Shaped like a request, but the D2 record built from it fails validate_record: the launcher would ignore the
    # record and the target would never bind, so the lane would be down for two verify windows. Refuse before any stop.
    req = request()
    mutate(req)
    ports = FakePorts()
    payload, _, ex = run(tmp_path, ports, req=req)
    # A forged requester is now refused earlier, by the authenticated principal binding (Lead review R7).
    assert payload["outcome"] == "aborted" and payload["reasons"][0] in (
        "record_would_be_invalid", "requester_is_not_lead", "executor_is_not_the_requesting_lead")
    assert ports.stops == [] and ports.claims == [] and ports.proc["pid"] == 100 and ex.tid is None


class _RemeasureDiffers(FakePorts):
    def __init__(self, field, value, **kwargs):
        super().__init__(**kwargs)
        self.field, self.value = field, value

    def measure(self, lane):
        state = super().measure(lane)
        if self.measures > 1:
            state[self.field] = self.value
        return state


@pytest.mark.parametrize("field,value", [("pid", 101), ("process_started_at", iso(NOW - timedelta(minutes=29)))])
def test_a_lane_process_that_changes_between_the_check_and_the_claim_aborts(tmp_path, auto_mode, field, value):
    ports = _RemeasureDiffers(field, value)
    payload, _, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["lane_changed_after_claim"])
    assert ports.stops == [] and ports.released == ["claim-1"] and ex.tid is None


class _SourceRisesAgain(FakePorts):
    """A silent launch, then evidence showing the SOURCE process alive again, and a record that also names it."""

    def launch(self, lane, profile):
        self.records[lane]["launched"] = {"native_thread_id": T1, "pid": 100, "process_started_at": _SOURCE_STARTED,
                                          "session_id": "S1", "run_id": "S1", "launched_at": _SOURCE_STARTED}

    def evidence(self, lane):
        return {"pin_status": "manifest_and_launcher_verified", "pid": 100, "process_started_at": _SOURCE_STARTED,
                "native_conversation_id": T1}


def test_a_source_that_is_alive_again_is_never_stopped_a_second_time_by_the_rollback(tmp_path, auto_mode):
    ports = _SourceRisesAgain()
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["verify_timeout", "stray_identity_unproven", "operator_required"]
    assert ports.stops == [(100, _SOURCE_STARTED)]
    assert phase(store, ex.tid) == "apply_pending"


def test_a_failing_claim_release_is_reported_and_never_raised(tmp_path, auto_mode):
    ports = FakePorts(stop_ok=False)
    ports.release_claim = lambda claim_id: (_ for _ in ()).throw(OSError("claims store locked"))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "failed" and "claim_not_released" in payload["reasons"]


def test_a_raising_stop_with_another_process_in_evidence_is_treated_as_down(tmp_path, auto_mode):
    ports = _StopKillsThenRaises()
    original = ports.evidence
    ports.evidence = lambda lane: (original(lane) if ports.proc["pid"] == 100
                                   else {**original(lane), "pid": 4242, "process_started_at": _SOURCE_STARTED})
    payload, store, ex = run(tmp_path, ports)
    assert "operator_required" in payload["reasons"] and phase(store, ex.tid) == "checkpointed"


def test_evidence_naming_another_conversation_never_binds_the_target(tmp_path, auto_mode):
    ports = FakePorts(launches=("good", "good"))
    original = ports.evidence
    ports.evidence = lambda lane: (dict(original(lane), native_conversation_id=T3) if ports.proc["pid"] == 200
                                   else original(lane))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back"





class _SlowStop(FakePorts):
    def __init__(self, seconds, **kwargs):
        super().__init__(**kwargs)
        self.seconds = seconds

    def stop(self, lane, pid, started_at):
        ok = super().stop(lane, pid, started_at)
        self.clock += timedelta(seconds=self.seconds)
        return ok


def test_the_verify_window_opens_when_the_launch_returns_not_before_the_stop(tmp_path, auto_mode):
    ports = _SlowStop(BASE["fleet"]["verify_timeout_seconds"] + 1)
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "applied" and ports.stops == [(100, _SOURCE_STARTED)]


def test_a_silent_launcher_still_gets_the_whole_window_after_a_slow_stop(tmp_path, auto_mode):
    timeout = BASE["fleet"]["verify_timeout_seconds"]
    ports = _SlowStop(120, launches=("silent", "good"))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back"
    assert ports.clock >= NOW + timedelta(seconds=120 + timeout)


@pytest.mark.parametrize("mode", ["disabled", "off", "dry-run", ""])
def test_an_unknown_mode_never_executes(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(executor_module, "effective_mode", lambda catalog: mode)
    ports = FakePorts()
    payload, _, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("parked", ["mode_not_auto"])
    assert ports.claims == [] and ports.stops == [] and ports.records == {} and ex.tid is None


@pytest.mark.parametrize("age_seconds,verified", [(299, True), (300, True), (301, False), (29 * 60, False), (-60, False)])
def test_the_current_profile_needs_a_recent_observation(tmp_path, auto_mode, age_seconds, verified):
    ports = FakePorts()
    ports.obs[0]["observed_at"] = iso(NOW - timedelta(seconds=age_seconds))
    payload, _, _ = run(tmp_path, ports)
    if verified:
        assert payload["outcome"] == "applied"
    else:
        assert payload["reasons"] == ["current_profile_unverified"] and ports.stops == [] and ports.claims == []



# ---------------------------------------------------------------- Lead review R7: principal

@pytest.mark.parametrize("breaks", ["none", "raises", "no-executor", "empty-ref", "requester-not-dict"])
def test_r7_an_unauthenticated_principal_parks_before_anything(tmp_path, auto_mode, breaks):
    ports = FakePorts()
    original = ports.authenticate

    def auth(request):
        if breaks == "none":
            return None
        if breaks == "raises":
            raise PermissionError("no token")
        result = original(request)
        if breaks == "no-executor":
            del result["executor"]
        elif breaks == "empty-ref":
            result["executor"]["verification_ref"] = ""
        else:
            result["requester"] = "codex-lead-1"
        return result
    ports.authenticate = auth
    payload, _, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("parked", ["principal_unauthenticated"])
    assert ports.claims == [] and ports.stops == [] and ex.tid is None and payload["executor"] is None


@pytest.mark.parametrize("field", ["agent_uuid", "session_id"])
def test_r7_a_request_claiming_another_identity_is_refused(tmp_path, auto_mode, field):
    ports = FakePorts()
    ports.requester = dict(request()["requested_by"], **{field: "someone-else"})
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"] == ["requester_not_authenticated_as_claimed"] and ports.claims == []


def test_r7_only_lead_requests_are_executed(tmp_path, auto_mode):
    req = request()
    req["requested_by"] = {"agent": "claude-rco-2", "agent_uuid": "rco2-uuid", "session_id": "rco2-S"}
    ports = FakePorts()
    payload, _, _ = run(tmp_path, ports, req=req)
    assert payload["reasons"] == ["requester_is_not_lead"] and ports.claims == []


def test_r7_lead_executes_only_its_own_session_request(tmp_path, auto_mode):
    req = request()
    req["requested_by"] = dict(req["requested_by"], session_id="an-older-lead-session")
    ports = FakePorts()
    ports.requester = dict(req["requested_by"])  # authenticated, but not the executing session
    payload, _, _ = run(tmp_path, ports, req=req)
    assert payload["reasons"] == ["executor_is_not_the_requesting_lead"] and ports.claims == []


def test_r7_the_supervisor_executes_only_leads_own_lane(tmp_path, auto_mode):
    ports = FakePorts()
    payload, _, _ = run(tmp_path, ports, executor="supervisor")
    assert payload["reasons"] == ["executor_is_not_the_requesting_lead"] and ports.claims == []


@pytest.mark.parametrize("principal", ["codex-tools-1", "operator", "codex-lead-1-shadow"])
def test_r7_no_other_principal_executes(tmp_path, auto_mode, principal):
    ports = FakePorts()
    payload, _, _ = run(tmp_path, ports, executor=principal)
    assert payload["outcome"] == "aborted" and ports.claims == []


def test_r7_the_journal_and_receipt_carry_the_authenticated_principal(tmp_path, auto_mode):
    ports = FakePorts()
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "applied" and payload["executor"] == "codex-lead-1"
    plan = json.loads(store.get(ex.tid)["plan"])
    assert plan["trusted_adapter_identity"] == {"principal": "codex-lead-1", "verification_ref": "auth-1"}


def test_r7_the_constructor_takes_no_executor_label(tmp_path):
    with pytest.raises(TypeError):
        Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=RecoveryStore(tmp_path / "j.sqlite"),
                 ports=FakePorts(), runtime_root=str(tmp_path), request=request(), executor="supervisor")


# ---------------------------------------------------------------- Lead review R7: resume

@pytest.mark.parametrize("resume_supported,delivered", [(False, "ckpt-1"), (True, "provider_resume")])
def test_r7_continuity_is_delivered_to_the_bound_session(tmp_path, auto_mode, resume_supported, delivered):
    ports = FakePorts(resume=resume_supported)
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "applied" and phase(store, ex.tid) == "resumed"
    assert ports.resumed == [("claude-rco-1", 200, delivered)]


@pytest.mark.parametrize("answer", [False, None, "yes", 1, RuntimeError("session gone")], ids=repr)
def test_r7_an_unconfirmed_resume_never_reaches_resumed(tmp_path, auto_mode, answer):
    ports = FakePorts(resume_ok=answer)
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "failed"
    assert payload["reasons"] == ["target_verified", "resume_not_confirmed", "operator_required"]
    assert phase(store, ex.tid) == "resume_pending" and ports.released == ["claim-1"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reservations WHERE transition_id=?", (ex.tid,)).fetchone()[0] > 0


def test_r7_a_rollback_also_needs_a_confirmed_resume(tmp_path, auto_mode):
    ports = FakePorts(launches=("wrong_model", "good"), resume_ok=False)
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["verify_timeout", "resume_not_confirmed", "operator_required"]
    assert phase(store, ex.tid) == "resume_pending"


def test_r7_a_confirmed_rollback_reaches_resumed(tmp_path, auto_mode):
    ports = FakePorts(launches=("wrong_model", "good"))
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back" and phase(store, ex.tid) == "resumed"
    assert [pid for _, pid, _ in ports.resumed] == [201]


# ---------------------------------------------------------------- Lead review R7: every process

LATE = {"pin_status": "manifest_and_launcher_verified", "pid": 777,
        "process_started_at": iso(NOW - timedelta(minutes=2)), "native_conversation_id": "late-thread"}


def test_r7_a_second_lane_process_before_the_stop_aborts(tmp_path, auto_mode):
    ports = FakePorts()
    ports.extra_processes = [dict(LATE)]
    payload, _, ex = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("aborted", ["lane_process_count_not_one"])
    assert ports.stops == [] and ports.claims == [] and ex.tid is None


@pytest.mark.parametrize("answer", [None, "x", [None], RuntimeError("down")], ids=repr)
def test_r7_unenumerable_processes_abort_before_the_claim(tmp_path, auto_mode, answer):
    ports = FakePorts()

    def processes(lane):
        if isinstance(answer, Exception):
            raise answer
        return answer
    ports.processes = processes
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"] == ["source_evidence_unavailable"] and ports.claims == []


class _LateTarget(FakePorts):
    """The launcher's first process shows up late, next to whatever else runs."""

    def launch(self, lane, profile):
        super().launch(lane, profile)
        if len(self.launches) == 1:  # after the first launch only
            self.extra_processes = [dict(LATE)]


def test_r7_a_late_extra_process_blocks_the_target_binding_and_the_stray_stop(tmp_path, auto_mode):
    ports = _LateTarget(launches=("good", "good"))
    payload, store, ex = run(tmp_path, ports)
    assert payload["outcome"] == "failed"
    assert payload["reasons"] == ["verify_timeout", "stray_identity_unproven", "operator_required"]
    assert [pid for pid, _ in ports.stops] == [100]  # neither the target nor the late process is killed
    assert phase(store, ex.tid) == "apply_pending"


def test_r7_a_late_process_during_the_rollback_blocks_its_binding(tmp_path, auto_mode):
    ports = FakePorts(launches=("wrong_model", "good"))
    original = ports.launch

    def launch(lane, profile):
        original(lane, profile)
        if profile["profile_id"] == "claude-sonnet-5-xhigh":  # the rollback launch
            ports.extra_processes = [dict(LATE)]
    ports.launch = launch
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["verify_timeout", "rollback_failed", "operator_required"]
    assert phase(store, ex.tid) == "apply_pending"


def test_r7_a_stray_is_stopped_only_when_it_is_the_single_corroborated_process(tmp_path, auto_mode):
    ports = FakePorts(launches=("wrong_model", "good"))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back" and [pid for pid, _ in ports.stops] == [100, 200]


# ---------------------------------------------------------------- R2 tightening: unverified evidence

def test_after_a_raising_stop_unverified_evidence_of_the_source_still_holds(tmp_path, auto_mode):
    class _StopRaisesSourceStaysUnverified(FakePorts):
        def stop(self, lane, pid, started_at):
            self.stops.append((pid, started_at))
            self.evidence_pin = "mismatch"  # the source still runs, but the oracle is no longer verified
            raise TimeoutError("exit not confirmed")
    ports = _StopRaisesSourceStaysUnverified()
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_exception", "TimeoutError", "operator_required"]
    assert phase(store, ex.tid) == "checkpointed"


def test_after_a_raising_stop_verified_evidence_of_the_live_source_cancels(tmp_path, auto_mode):
    class _StopRaisesSourceAlive(FakePorts):
        def stop(self, lane, pid, started_at):
            self.stops.append((pid, started_at))
            raise TimeoutError("exit not confirmed")  # and the source is in fact still alive and verified
    ports = _StopRaisesSourceAlive()
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_exception", "TimeoutError"]
    assert phase(store, ex.tid) == "cancelled_before_apply"


def test_r7_a_non_lead_principal_is_refused_even_with_leads_session(tmp_path, auto_mode):
    # The principal name is load-bearing on its own: matching uuid and session do not make a supervisor
    # (or any other principal) the requesting Lead for another lane.
    ports = FakePorts()
    original = ports.authenticate

    def auth(request):
        result = original(request)
        result["executor"] = dict(result["executor"], principal="supervisor", agent_uuid=LEAD_UUID,
                                  session_id="lead-S")
        return result
    ports.authenticate = auth
    payload, _, _ = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_is_not_the_requesting_lead"] and ports.claims == []


class _StopRaisesAndADuplicateAppears(FakePorts):
    """Lead review PR1739-R7: stop() raises without killing the verified source, and meanwhile a
    second verified lane process appears."""

    def stop(self, lane, pid, started_at):
        self.stops.append((pid, started_at))
        self.extra_processes = [dict(LATE)]
        raise TimeoutError("exit not confirmed")


def test_r7_a_duplicate_during_a_raising_stop_is_an_unknown_fate(tmp_path, auto_mode):
    ports = _StopRaisesAndADuplicateAppears()
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_exception", "TimeoutError", "operator_required"]
    assert phase(store, ex.tid) == "checkpointed"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM reservations WHERE transition_id=?", (ex.tid,)).fetchone()[0] > 0


@pytest.mark.parametrize("rows", ["none", "empty", "mismatched-start"])
def test_r7_after_a_raising_stop_only_the_single_exact_source_cancels(tmp_path, auto_mode, rows):
    class _Stop(FakePorts):
        def stop(self, lane, pid, started_at):
            self.stops.append((pid, started_at))
            if rows == "empty":
                self.proc = {"pid": None, "started": None, "thread": None, "session": None}
            elif rows == "mismatched-start":
                self.proc = dict(self.proc, started=iso(NOW - timedelta(minutes=5)))
            raise TimeoutError("exit not confirmed")
    ports = _Stop()
    if rows == "none":
        original = ports.processes
        ports.processes = lambda lane: None if ports.stops else original(lane)
    payload, store, ex = run(tmp_path, ports)
    assert payload["reasons"] == ["executor_exception", "TimeoutError", "operator_required"]
    assert phase(store, ex.tid) == "checkpointed"


# ---------------------------------------------------------------- rco-2 F2/F3: stop intent counts

def test_f2_a_crash_between_the_stop_and_apply_pending_still_counts(tmp_path, auto_mode, monkeypatch):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    real_move = store.move

    def crashing_move(tid, expected, phase, **kwargs):
        if phase == "apply_pending":
            raise OSError("host lost power")
        return real_move(tid, expected, phase, **kwargs)
    monkeypatch.setattr(store, "move", crashing_move)
    ports = FakePorts()
    first, ex = run_with_store(tmp_path, ports, store)
    assert first["outcome"] == "failed" and ports.stops == [(100, _SOURCE_STARTED)]
    assert phase(store, ex.tid) == "checkpointed"
    assert [e["lane"] for e in ex._journal_history()] == ["claude-rco-1"]


def test_f3_a_transition_held_after_a_raising_stop_counts(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first, ex = run_with_store(tmp_path, _StopKillsThenRaises(), store)
    assert "operator_required" in first["reasons"] and phase(store, ex.tid) == "checkpointed"
    assert [(e["lane"], e["outcome"]) for e in ex._journal_history()] == [("claude-rco-1", "stop_attempted")]


def test_f3_a_raising_stop_that_left_the_source_alone_and_alive_is_not_counted(tmp_path, auto_mode):
    class _StopRaisesSourceAlive(FakePorts):
        def stop(self, lane, pid, started_at):
            self.stops.append((pid, started_at))
            raise TimeoutError("exit not confirmed")
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first, ex = run_with_store(tmp_path, _StopRaisesSourceAlive(), store)
    assert phase(store, ex.tid) == "cancelled_before_apply"
    assert ex._journal_history() == []


def test_f2_the_stop_intent_is_journaled_before_the_stop(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    seen = {}

    class _Watch(FakePorts):
        def stop(self, lane, pid, started_at):
            with store.connect() as db:
                seen["rows"] = [json.loads(r[0]) for r in db.execute(
                    "SELECT reason FROM journal WHERE phase='checkpointed' AND reason IS NOT NULL")]
            return super().stop(lane, pid, started_at)
    payload, _ = run_with_store(tmp_path, _Watch(), store)
    assert payload["outcome"] == "applied"
    assert seen["rows"] == [{"stop_intent_at": iso(NOW)}]


# ---------------------------------------------------------------- rco-2 F4: request age

@pytest.mark.parametrize("stamp,reason", [
    ("", "request_time_unauthenticated"),
    ("yesterday", "request_time_unauthenticated"),
    (iso(NOW - timedelta(seconds=901)), "request_stale_or_from_the_future"),
    (iso(NOW + timedelta(seconds=5)), "request_stale_or_from_the_future"),
])
def test_f4_request_age_is_authenticated_and_bounded(tmp_path, auto_mode, stamp, reason):
    ports = FakePorts()
    ports.request_ts = stamp or None
    if stamp == "":
        original = ports.authenticate
        ports.authenticate = lambda request: {**original(request),
                                              "requester": {k: v for k, v in original(request)["requester"].items()
                                                            if k != "request_ts_utc"}}
    payload, _, _ = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("parked", [reason])
    assert ports.claims == [] and ports.stops == []


@pytest.mark.parametrize("age", [0, 900])
def test_f4_a_request_within_the_age_bound_proceeds(tmp_path, auto_mode, age):
    ports = FakePorts()
    ports.request_ts = iso(NOW - timedelta(seconds=age))
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "applied"


def test_f4_the_request_time_comes_from_the_port_not_the_request(tmp_path, auto_mode):
    req = request()
    req["requested_by"] = dict(req["requested_by"], request_ts_utc=iso(NOW))  # a fresh label is ignored
    ports = FakePorts()
    ports.request_ts = iso(NOW - timedelta(hours=2))
    ports.requester = {k: v for k, v in request()["requested_by"].items()}
    payload, _, _ = run(tmp_path, ports, req=req)
    assert payload["reasons"] == ["request_stale_or_from_the_future"]


# ---------------------------------------------------------------- rco-2 F5: catalog signature

@pytest.mark.parametrize("answer", [False, None, "yes", 1, PermissionError("no key")], ids=repr)
def test_f5_auto_needs_a_verified_catalog_signature(tmp_path, auto_mode, answer):
    ports = FakePorts()
    ports.signature_ok = answer
    payload, _, _ = run(tmp_path, ports)
    assert (payload["outcome"], payload["reasons"]) == ("parked", ["catalog_signature_unverified"])
    assert ports.claims == [] and ports.stops == []


def test_f5_the_signature_is_checked_over_this_exact_catalog(tmp_path, auto_mode):
    ports = FakePorts()
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "applied"
    assert ports.signature_checks == [(DIGEST, "operator 2026-09-26 reviewed PR")]
