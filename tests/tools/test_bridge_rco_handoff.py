# SPDX-License-Identifier: BUSL-1.1
"""Provider-free tests of review custody, never real RCO approvals."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.bridge_rco_handoff import HandoffError, HandoffStore, digest, load_json  # noqa: E402


NOW = datetime(2026, 9, 19, 6, tzinfo=timezone.utc)


def policy():
    profiles = {}
    for slot in ("rco1", "rco2"):
        for provider in ("claude", "codex"):
            key = f"{provider}-{slot}"
            profiles[key] = {
                "agent": key, "agent_uuid": key + "-uuid", "slot": slot,
                "provider": provider, "model": provider + "-test-model",
                "effort": "test-effort", "account_pool": provider + "-pool",
                "qualification_ref": "TEST-ONLY:qualified:" + key,
            }
    return {
        "schema": "wd.rco-handoff-policy.v1", "mode": "advisory_only",
        "authority_ref": "TEST-ONLY:operator-policy", "max_age_seconds": 60,
        "recovery_stability_seconds": 120, "cooldown_seconds": 30,
        "profiles": profiles,
        "slots": {s: {"primary": "claude-" + s,
                       "substitutes": ["codex-" + s]} for s in ("rco1", "rco2")},
    }


def actor(profile="claude-rco1"):
    p = policy()["profiles"][profile]
    return {**{k: p[k] for k in ("agent", "agent_uuid", "provider", "model",
                                "effort", "account_pool")},
            "profile_id": profile, "session_id": profile + "-session",
            "native_thread_id": profile + "-thread"}


def task():
    return {"task_id": "test-review", "head": "a" * 40,
            "base": "d" * 40, "pr_ref": "TEST-ONLY:pr/1", "request_digest": "e" * 64,
            "request_id": "original-request", "claim_id": "test-claim",
            "scope_digest": "b" * 64, "required_reviewers": ["rco1", "rco2"],
            "author_uuid": "implementation-uuid", "author_thread_id": "impl-thread",
            "author_provider": "codex"}


def capacity(who, state="available", now=NOW):
    return {"provider": who["provider"], "account_pool": who["account_pool"],
            "state": state, "observed_at": now.isoformat(),
            "available_since": (now - timedelta(minutes=5)).isoformat(),
            "source_ref": "TEST-ONLY:normalized-provider-observation"}


def checkpoint(state, remaining=None):
    return {"binding": deepcopy(state["task"]), "source_actor": deepcopy(state["owner"]),
            "source_epoch": state["epoch"], "completed_checks": ["read-exact-head"],
            "remaining_checks": ["security-review"] if remaining is None else remaining,
            "findings": ["finding-1"], "veto_refs": ["veto-1"],
            "evidence_refs": ["TEST-ONLY:log-1"]}


@pytest.fixture
def store(tmp_path):
    with HandoffStore(tmp_path / "handoff.sqlite", policy()) as result:
        yield result


def create(store, review="review-1", slot="rco1", now=NOW):
    return store.execute({"command_id": "create-" + review, "op": "create",
                          "review_id": review, "expected_revision": -1,
                          "task": task(), "slot": slot,
                          "actor": actor("claude-" + slot)}, now=now)["state"]


def command(state, op, **kw):
    return {"command_id": f"{state['review_id']}-{state['revision']}-{op}",
            "review_id": state["review_id"], "op": op,
            "expected_revision": state["revision"], "binding": deepcopy(state["task"]),
            "actor": deepcopy(state["owner"]), "epoch": state["epoch"], **kw}


def begin(store, state, now=NOW, target=None, reason="quota"):
    target = target or actor("codex-" + state["slot"])
    return store.execute(command(state, "begin", target=target, reason=reason,
                                 next_request_id=f"next-{state['revision']}",
                                 source_capacity=capacity(state["owner"], "exhausted", now),
                                 target_capacity=capacity(target, now=now)), now=now)["state"]


def release(store, state, now=NOW):
    cp = checkpoint(state)
    proof = {"actor": state["owner"], "epoch": state["epoch"],
             "observed_at": now.isoformat(), "idle": True, "pending_effects": False,
             "evidence_ref": "TEST-ONLY:owner-quiescence"}
    return store.execute(command(state, "release", checkpoint=cp, quiescence=proof),
                         now=now)["state"]


def accept(store, state, now=NOW):
    target = state["pending"]["target"]
    return store.execute(command(state, "accept", actor=target,
                                 assignment_request_id=state["pending"]["request_id"],
                                 checkpoint_sha256=state["checkpoint_sha256"],
                                 target_capacity=capacity(target, now=now)), now=now)["state"]


def test_full_handoff_and_handback_preserves_evidence(store):
    state = create(store)
    state = begin(store, state)
    assert state["phase"] == "releasing"
    state = release(store, state)
    assert state["owner"] is None
    assert state["epoch"] == 1
    state = accept(store, state)
    assert state["owner"]["provider"] == "codex"
    later = NOW + timedelta(minutes=3)
    state = begin(store, state, now=later, target=actor(), reason="recovered")
    state = release(store, state, now=later)
    state = accept(store, state, now=later)
    assert state["owner"]["provider"] == "claude"
    assert state["epoch"] == 2
    assert state["checkpoint"]["veto_refs"] == ["veto-1"]
    assert state["task"] == task()


def test_retry_does_not_repeat_transition_or_return_stale_ownership(store):
    state = create(store)
    c = command(state, "begin", target=actor("codex-rco1"), reason="quota",
                next_request_id="request-2", source_capacity=capacity(actor(), "exhausted"),
                target_capacity=capacity(actor("codex-rco1")))
    first = store.execute(c, now=NOW)
    current = accept(store, release(store, first["state"]))
    retry = store.execute(c, now=NOW)
    assert retry["replayed"] is True
    assert retry["state"] == current
    assert retry["execution_allowed"] is False
    assert retry["rco_approval_allowed"] is False


def test_late_old_owner_progress_is_rejected(store):
    original = create(store)
    current = accept(store, release(store, begin(store, original)))
    stale = command(current, "progress", actor=actor(), epoch=0,
                    checkpoint=checkpoint(original))
    with pytest.raises(HandoffError):
        store.execute(stale, now=NOW)


def test_unknown_capacity_cannot_begin(store):
    state = create(store)
    c = command(state, "begin", target=actor("codex-rco1"), reason="quota",
                next_request_id="request-2", source_capacity=capacity(actor(), "exhausted"),
                target_capacity=capacity(actor("codex-rco1"), "unknown"))
    with pytest.raises(HandoffError):
        store.execute(c, now=NOW)


def test_completed_reviewer_cannot_fill_other_slot_with_same_thread(store):
    s = create(store)
    store.execute(command(s, "complete", checkpoint=checkpoint(s, remaining=[])), now=NOW)
    other = actor("claude-rco2")
    other["native_thread_id"] = actor()["native_thread_id"]
    with pytest.raises(HandoffError, match="reviewer_independence_conflict"):
        store.execute({"command_id": "other", "op": "create", "review_id": "review-2",
                       "expected_revision": -1, "task": task(), "slot": "rco2",
                       "actor": other}, now=NOW)


def test_cannot_create_replacement_review_for_held_slot(store):
    s = create(store)
    control(store, s, "hold")
    with pytest.raises(HandoffError, match="review_slot_already_owned"):
        create(store, review="replacement")


def control(store, state, op):
    c = command(state, op, control_ref="TEST-ONLY:operator-control")
    del c["actor"], c["epoch"]
    return store.execute(c, now=NOW)["state"]


def test_recovery_must_still_be_stable_at_accept(store):
    s = accept(store, release(store, begin(store, create(store))))
    later = NOW + timedelta(minutes=3)
    s = release(store, begin(store, s, now=later, target=actor(), reason="recovered"), now=later)
    observation = capacity(actor(), now=later)
    observation["available_since"] = later.isoformat()
    with pytest.raises(HandoffError, match="recovery_not_stable"):
        store.execute(command(s, "accept", actor=actor(),
                              assignment_request_id=s["pending"]["request_id"],
                              checkpoint_sha256=s["checkpoint_sha256"],
                              target_capacity=observation), now=later)


def test_review_slots_cannot_bind_different_head(store):
    create(store)
    changed = task()
    changed["head"] = "c" * 40
    with pytest.raises(HandoffError, match="review_round_binding_conflict"):
        store.execute({"command_id": "other", "op": "create", "review_id": "review-2",
                       "expected_revision": -1, "task": changed, "slot": "rco2",
                       "actor": actor("claude-rco2")}, now=NOW)


def begin_command(s):
    target = actor("codex-" + s["slot"])
    return command(s, "begin", target=target, reason="quota", next_request_id="new-request",
                   source_capacity=capacity(s["owner"], "exhausted"),
                   target_capacity=capacity(target))


def accept_command(s, now=NOW):
    target = s["pending"]["target"]
    return command(s, "accept", actor=target,
                   assignment_request_id=s["pending"]["request_id"],
                   checkpoint_sha256=s["checkpoint_sha256"],
                   target_capacity=capacity(target, now=now))


def assert_refused_unchanged(store, s, c, match=None, now=NOW):
    ledger = store.db.execute("SELECT * FROM commands").fetchall()
    history = store.db.execute("SELECT * FROM transitions").fetchall()
    with pytest.raises(HandoffError, match=match):
        store.execute(c, now=now)
    assert store.get(s["review_id"]) == s
    assert store.db.execute("SELECT * FROM commands").fetchall() == ledger
    assert store.db.execute("SELECT * FROM transitions").fetchall() == history


@pytest.mark.parametrize("field", list(task()))
def test_every_task_binding_is_immutable(store, field):
    s = create(store)
    c = begin_command(s)
    c["binding"][field] = ["rco1"] if field == "required_reviewers" else "changed"
    assert_refused_unchanged(store, s, c, "task_binding_changed")


@pytest.mark.parametrize("field", list(actor()))
def test_every_owner_identity_field_is_bound(store, field):
    s = create(store)
    c = begin_command(s)
    c["actor"][field] = "different"
    assert_refused_unchanged(store, s, c, "not_current_owner")


@pytest.mark.parametrize("field", ["model", "effort", "agent_uuid", "provider", "account_pool"])
def test_observed_target_must_match_qualified_profile(store, field):
    s = create(store)
    c = begin_command(s)
    c["target"][field] = "unqualified"
    assert_refused_unchanged(store, s, c, "observed_actor_profile_mismatch")


@pytest.mark.parametrize("field", ["agent_uuid", "native_thread_id"])
def test_author_cannot_be_reviewer(store, field):
    t = task()
    t["author_uuid" if field == "agent_uuid" else "author_thread_id"] = actor()[field]
    with pytest.raises(HandoffError, match="author_cannot_review"):
        store.execute({"command_id": "bad-create", "op": "create", "review_id": "bad",
                       "expected_revision": -1, "task": t, "slot": "rco1",
                       "actor": actor()}, now=NOW)


@pytest.mark.parametrize("which", ["source_capacity", "target_capacity"])
@pytest.mark.parametrize("delta", [-61, 1])
def test_capacity_must_be_fresh_not_future(store, which, delta):
    s = create(store)
    c = begin_command(s)
    c[which]["observed_at"] = (NOW + timedelta(seconds=delta)).isoformat()
    assert_refused_unchanged(store, s, c, "stale_or_future_observation")


@pytest.mark.parametrize("value", [None, True, 0, [], {}, "unknown", "available"])
def test_only_explicit_exhaustion_triggers_substitution(store, value):
    s = create(store)
    c = begin_command(s)
    c["source_capacity"]["state"] = value
    assert_refused_unchanged(store, s, c, "capacity_not_confirmed")


@pytest.mark.parametrize("field", ["completed_checks", "findings", "veto_refs", "evidence_refs"])
def test_checkpoint_cannot_drop_prior_evidence(store, field):
    s = create(store)
    s = store.execute(command(s, "progress", checkpoint=checkpoint(s)), now=NOW)["state"]
    cp = checkpoint(s)
    cp[field] = ["other"]
    assert_refused_unchanged(store, s, command(s, "progress", checkpoint=cp),
                            "checkpoint_dropped_evidence")


@pytest.mark.parametrize("field", ["binding", "source_actor", "source_epoch"])
def test_checkpoint_origin_is_exact(store, field):
    s = create(store)
    cp = checkpoint(s)
    cp[field] = "incorrect"
    assert_refused_unchanged(store, s, command(s, "progress", checkpoint=cp))


@pytest.mark.parametrize("field", ["epoch", "assignment_request_id", "checkpoint_sha256", "actor"])
def test_accept_binding_cannot_be_substituted(store, field):
    s = release(store, begin(store, create(store)))
    c = accept_command(s)
    c[field] = "incorrect"
    assert_refused_unchanged(store, s, c)


def test_target_capacity_is_rechecked_after_release(store):
    s = release(store, begin(store, create(store)))
    c = accept_command(s)
    c["target_capacity"]["observed_at"] = (NOW - timedelta(seconds=1)).isoformat()
    assert_refused_unchanged(store, s, c, "capacity_predates_release")
    c["target_capacity"] = capacity(c["actor"], "exhausted")
    assert_refused_unchanged(store, s, c, "capacity_not_confirmed")


@pytest.mark.parametrize("phase", ["active", "releasing", "awaiting_accept"])
@pytest.mark.parametrize("op", ["hold", "cancel"])
def test_control_fences_every_transfer_phase(store, phase, op):
    s = create(store)
    if phase != "active":
        s = begin(store, s)
    if phase == "awaiting_accept":
        s = release(store, s)
    original_epoch = s["epoch"]
    s = control(store, s, op)
    assert s["owner"] is None and s["epoch"] == original_epoch + 1
    assert s["phase"] == ("held" if op == "hold" else "cancelled")
    assert_refused_unchanged(store, s, begin_command({**s, "owner": actor()}))
    if s["pending"]:
        assert_refused_unchanged(store, s, accept_command(s))


def test_cooldown_blocks_model_flapping(store):
    s = accept(store, release(store, begin(store, create(store))))
    with pytest.raises(HandoffError, match="transfer_cooldown"):
        begin(store, s, target=actor(), reason="recovered")


def test_completed_work_never_triggers_unnecessary_handback(store):
    s = accept(store, release(store, begin(store, create(store))))
    s = store.execute(command(s, "complete", checkpoint=checkpoint(s, remaining=[])), now=NOW)["state"]
    assert s["completed_by"]["provider"] == "codex"
    assert s["checkpoint"]["veto_refs"] == ["veto-1"]
    with pytest.raises(HandoffError, match="review_terminal"):
        begin(store, {**s, "owner": s["completed_by"]}, target=actor(), reason="recovered")


def host_release_command(s):
    return command(s, "host_release", checkpoint_sha256=s["checkpoint_sha256"],
                   source_capacity=capacity(s["owner"], "exhausted"),
                   lease_expired_at=NOW.isoformat(), control_ref="TEST-ONLY:host-control",
                   quiescence={"actor": s["owner"], "epoch": s["epoch"],
                               "observed_at": NOW.isoformat(), "idle": True,
                               "pending_effects": False, "execution_fenced": True,
                               "evidence_ref": "TEST-ONLY:host-verified-fence"})


def test_silent_exhausted_owner_can_be_host_released_using_durable_checkpoint(store):
    s = create(store)
    s = store.execute(command(s, "progress", checkpoint=checkpoint(s)), now=NOW)["state"]
    s = begin(store, s)
    s = store.execute(host_release_command(s), now=NOW)["state"]
    s = accept(store, s)
    assert s["owner"]["provider"] == "codex"
    assert s["checkpoint"]["source_actor"] == actor()


@pytest.mark.parametrize("missing", ["checkpoint", "fence", "lease", "exhaustion", "idle", "pending_effects"])
def test_silence_or_expiry_alone_never_permits_host_release(store, missing):
    s = create(store)
    if missing != "checkpoint":
        s = store.execute(command(s, "progress", checkpoint=checkpoint(s)), now=NOW)["state"]
    s = begin(store, s)
    c = host_release_command(s)
    if missing == "fence":
        c["quiescence"]["execution_fenced"] = False
    elif missing == "lease":
        c["lease_expired_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif missing == "exhaustion":
        c["source_capacity"]["state"] = "unknown"
    elif missing == "idle":
        c["quiescence"]["idle"] = False
    elif missing == "pending_effects":
        c["quiescence"]["pending_effects"] = True
    assert_refused_unchanged(store, s, c)


def test_two_connections_cannot_both_win_cas(tmp_path):
    path = tmp_path / "concurrent.sqlite"
    with HandoffStore(path, policy()) as db:
        s = create(db)
    barrier = Barrier(2)

    def run(n):
        with HandoffStore(path, policy()) as db:
            c = begin_command(s)
            c["command_id"] = "racing-" + str(n)
            c["next_request_id"] = "racing-request-" + str(n)
            barrier.wait(timeout=5)
            try:
                return db.execute(c, now=NOW)["state"]["revision"]
            except HandoffError as exc:
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, [1, 2]))
    assert sorted(map(str, results)) == ["1", "revision_conflict"]
    with HandoffStore(path, policy()) as db:
        assert db.get(s["review_id"])["revision"] == 1
        assert db.db.execute("SELECT count(*) FROM commands").fetchone()[0] == 2


def test_restart_every_phase_preserves_custody_and_provenance(tmp_path):
    path = tmp_path / "restart.sqlite"
    s = None
    for step in (create, begin, release, accept):
        with HandoffStore(path, policy()) as db:
            if s:
                assert db.get(s["review_id"]) == s
            s = step(db) if s is None else step(db, s)
    with HandoffStore(path, policy()) as db:
        assert db.get(s["review_id"]) == s
        history = db.db.execute("SELECT command_json,state_json FROM transitions ORDER BY rowid").fetchall()
        assert len(history) == 4
        assert [json.loads(row[1])["phase"] for row in history] == [
            "active", "releasing", "awaiting_accept", "active"]
        assert json.loads(history[-2][1])["checkpoint"]["source_actor"] == actor()


def test_command_id_conflict_never_mutates(store):
    s = create(store)
    c = begin_command(s)
    s = store.execute(c, now=NOW)["state"]
    c["next_request_id"] = "different"
    assert_refused_unchanged(store, s, c, "command_id_content_conflict")


def test_storage_failure_rolls_back_entire_transition(store):
    s = create(store)
    store.db.execute("CREATE TRIGGER refuse_insert BEFORE INSERT ON commands "
                     "BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.execute(begin_command(s), now=NOW)
    assert store.get(s["review_id"]) == s
    assert store.db.execute("SELECT count(*) FROM transitions").fetchone()[0] == 1


def test_process_crash_before_commit_rolls_back(tmp_path):
    path = tmp_path / "crash.sqlite"
    with HandoffStore(path, policy()) as db:
        s = create(db)
    script = (
        "import json,os,sys; from datetime import datetime; "
        "from tools.bridge_rco_handoff import HandoffStore; "
        "db=HandoffStore(sys.argv[1],json.loads(sys.argv[2])); "
        "db._result=lambda *a,**k: os._exit(23); "
        "db.execute(json.loads(sys.argv[3]),now=datetime.fromisoformat(sys.argv[4]))"
    )
    child = subprocess.run([sys.executable, "-B", "-c", script, str(path), json.dumps(policy()),
                            json.dumps(begin_command(s)), NOW.isoformat()], cwd=ROOT,
                           capture_output=True, timeout=10)
    assert child.returncode == 23, child.stderr
    with HandoffStore(path, policy()) as db:
        assert db.get(s["review_id"]) == s
        assert db.db.execute("SELECT count(*) FROM commands").fetchone()[0] == 1


def test_reopen_replay_and_policy_mismatch(tmp_path):
    path = tmp_path / "replay.sqlite"
    with HandoffStore(path, policy()) as db:
        s = create(db)
        c = begin_command(s)
        first = db.execute(c, now=NOW)
    with HandoffStore(path, policy()) as db:
        again = db.execute(c, now=NOW)
        assert again["replayed"] and again["state"] == first["state"]
        assert db.db.execute("SELECT count(*) FROM transitions").fetchone()[0] == 2
    changed = policy()
    changed["authority_ref"] = "changed-policy"
    with HandoffStore(path, changed) as db:
        with pytest.raises(HandoffError, match="policy_changed_reconcile_required"):
            db.execute(c, now=NOW)


@pytest.mark.parametrize("raw", ['{"op":"begin","op":"accept"}', 'NaN', '{',
                                  '[1,2]', 'null', '{"op":"grant_approval"}'])
def test_cli_rejects_bad_command_without_changing_existing_rows(tmp_path, raw):
    path = tmp_path / "cli.sqlite"
    with HandoffStore(path, policy()) as db:
        s = create(db)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy()), encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/bridge_rco_handoff.py"),
                             "--db", str(path), "--policy", str(policy_path), "--stdin"],
                            input=raw, text=True, capture_output=True, timeout=10, cwd=ROOT)
    assert result.returncode == 2, result.stderr
    assert json.loads(result.stdout)["execution_allowed"] is False
    with HandoffStore(path, policy()) as db:
        assert db.get(s["review_id"]) == s


def test_cli_reopens_existing_state_and_never_grants_approval(tmp_path):
    path = tmp_path / "cli.sqlite"
    with HandoffStore(path, policy()) as db:
        s = accept(db, release(db, begin(db, create(db))))
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy()), encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/bridge_rco_handoff.py"),
                             "--db", str(path), "--policy", str(policy_path), "--show", "review-1"],
                            text=True, capture_output=True, timeout=10, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == s
    assert payload["same_provider_as_author"] is True
    assert payload["execution_allowed"] is False
    assert payload["rco_approval_allowed"] is False
    assert payload["release_allowed"] is False


def test_two_independent_slots_remain_separate(store):
    a, b = create(store), create(store, review="review-2", slot="rco2")
    a = accept(store, release(store, begin(store, a)))
    b = accept(store, release(store, begin(store, b)))
    assert a["owner"]["agent_uuid"] != b["owner"]["agent_uuid"]
    assert a["task"]["required_reviewers"] == b["task"]["required_reviewers"] == ["rco1", "rco2"]


@pytest.mark.parametrize("raw", ["{", "null", "{}", '[1,2]', '{"policy_digest":"bad"}'])
def test_corrupt_state_returns_controlled_refusal(store, raw):
    create(store)
    store.db.execute("UPDATE reviews SET state=?", (raw,))
    with pytest.raises(HandoffError, match="corrupt_state"):
        store.get("review-1")
    with pytest.raises(HandoffError, match="corrupt_state"):
        create(store, review="review-2", slot="rco2")


def test_valid_json_state_tamper_cannot_override_journal(store):
    s = create(store)
    s["epoch"] = 88
    store.db.execute("UPDATE reviews SET state=?", (json.dumps(s),))
    with pytest.raises(HandoffError, match="corrupt_state"):
        store.get("review-1")


@pytest.mark.parametrize("raw", ['1e999', '-1e999', '{"x":1e999}', '[1e999]'])
def test_overflow_float_is_refused_at_parser_boundary(raw):
    with pytest.raises(HandoffError):
        load_json(raw)


@pytest.mark.parametrize("now", [False, 0, "", [], datetime(2026, 1, 1)])
def test_invalid_clock_never_silently_falls_back(store, now):
    with pytest.raises(HandoffError, match="aware_clock_required"):
        create(store, now=now)


@pytest.mark.parametrize("next_op", ["hold", "cancel"])
def test_repeated_control_preserves_original_suspended_owner(store, next_op):
    s = control(store, create(store), "hold")
    suspended = deepcopy(s["suspended_owner"])
    s = control(store, s, next_op)
    assert s["suspended_owner"] == suspended == actor()
