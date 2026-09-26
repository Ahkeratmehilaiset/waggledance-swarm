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
                 current_obs_profile="claude-sonnet-5-xhigh", evidence_pin="manifest_and_launcher_verified"):
        self.clock = NOW
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
    store = RecoveryStore(tmp_path / "journal.sqlite")
    ex = Executor(catalog=catalog or signed_catalog(), catalog_sha256=DIGEST, store=store, ports=ports,
                  runtime_root=str(tmp_path / "runtime"), request=req or request(), executor=executor)
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
    payload, _, _ = run(tmp_path, FakePorts(lane="codex-lead-1"), req=lead, executor="lead")
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
    assert payload["outcome"] == "rolled_back"


def test_unverified_pin_status_never_binds_the_target(tmp_path, auto_mode):
    ports = FakePorts(launches=("good", "good"))
    original = ports.evidence
    ports.evidence = lambda lane: dict(original(lane), pin_status="mismatch") if ports.proc["pid"] == 200 \
        else original(lane)
    payload, _, _ = run(tmp_path, ports)
    assert payload["outcome"] == "rolled_back"


def test_journal_refuses_a_second_transition_on_the_same_bucket(tmp_path, auto_mode):
    store = RecoveryStore(tmp_path / "journal.sqlite")
    first = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=FakePorts(launches=("silent", "silent")),
                     runtime_root=str(tmp_path / "rt"), request=request(), executor="lead").run()
    assert first["payload"]["outcome"] == "failed"
    second_req = dict(request(), request_id="req-2")
    ports = FakePorts()
    second = Executor(catalog=signed_catalog(), catalog_sha256=DIGEST, store=store, ports=ports,
                      runtime_root=str(tmp_path / "rt2"), request=second_req, executor="lead").run()
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
