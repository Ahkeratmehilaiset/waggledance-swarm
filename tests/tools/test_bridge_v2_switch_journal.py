"""W0/F17 switch journal: real state machine sequences, replay, crash cuts, CLI."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

import tools.bridge_v2_switch_journal as sj
from tools.bridge_v2_switch_journal import ContractError, Journal

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "bridge_v2_switch_journal.py"
T0 = datetime(2026, 9, 28, 12, 0, 0, tzinfo=timezone.utc)
D = "a" * 64


def utc(seconds: int) -> str:
    return (T0 + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ev(n: int) -> list[str]:
    return [f"{n:064x}"]


def binding(**over):
    row = {
        "schema": sj.BINDING_SCHEMA, "operation_kind": "model_switch", "lane": "fable-5",
        "task_id": "codex-lead-1/bridge-v2-switch-journal-20260928", "task_revision": 7,
        "source_generation": "gen-a", "target_generation": "gen-b", "intent_digest": D,
        "policy_digest": "b" * 64, "reservation_id": "res-1", "idempotency_key": "idem-1",
        "revocation_version": 4, "expires_at_utc": utc(3600),
    }
    row.update(over)
    return row


class Run:
    """Drive one operation through real records, keeping a clock."""

    def __init__(self, op="op-1", **bind):
        self.journal = Journal()
        self.op = op
        self.t = 0
        self.n = 0
        assert self.journal.append(sj.intent_record(op, binding(**bind), utc(0))) == "appended"

    def rec(self, phase, reason=None, evidence=True, dt=1, revocation=None):
        self.t += dt
        self.n += 1
        reason = reason or self.default_reason(phase)
        return sj.next_record(self.journal, self.op, phase, reason, utc(self.t),
                              ev(self.n) if evidence else [], revocation)

    def default_reason(self, phase):
        op = self.journal._ops[self.op]
        return op._default_reason(phase)

    def step(self, phase, reason=None, **kw):
        record = self.rec(phase, reason, **kw)
        assert self.journal.append(record) == "appended"
        return record

    def refuse(self, code, phase, reason=None, **kw):
        record = self.rec(phase, reason, **kw)
        before = json.dumps(self.state(), sort_keys=True)
        with pytest.raises(ContractError) as exc:
            self.journal.append(record)
        assert exc.value.code == code
        assert json.dumps(self.state(), sort_keys=True) == before  # refusal commits nothing
        self.t -= kw.get("dt", 1)
        self.n -= 1

    def state(self):
        return self.journal.operation(self.op)


# --- positive paths -----------------------------------------------------------------------

def test_attempted_applied_verified_continued_are_distinct_and_only_continued_succeeds():
    r = Run()
    seen = []
    for phase in ("attempted", "applied", "verified", "continued"):
        r.step(phase)
        s = r.state()
        seen.append((s["state"], s["attempted"], s["applied"], s["verified"], s["continued"], s["success"]))
    assert seen == [
        ("attempted", True, False, False, False, False),
        ("applied", True, True, False, False, False),
        ("verified", True, True, True, False, False),
        ("continued", True, True, True, True, True),
    ]
    assert r.state()["terminal"] and r.state()["next_phases"] == []
    fold = r.journal.fold()
    assert fold["authority_granted"] is False and fold["actuation_performed"] is False


def test_resume_pending_is_not_continued():
    r = Run()
    for phase in ("attempted", "applied", "verified", "resume_pending"):
        r.step(phase)
    s = r.state()
    assert s["resume_pending"] and not s["continued"] and not s["success"] and not s["terminal"]
    assert s["next_phases"] == ["continued", "rollback_attempted"]
    r.step("continued")
    assert r.state()["success"]


@pytest.mark.parametrize("cause", ["verification_failed", "revocation", "operator_hold"])
def test_rollback_verified_is_never_success(cause):
    r = Run()
    r.step("attempted")
    r.step("applied")
    if cause == "verification_failed":
        r.step("verification_failed", "identity_mismatch")
        r.step("rollback_attempted", "verification_failed")
    elif cause == "revocation":
        r.step("verified")
        r.step("rollback_attempted", "revocation_observed", revocation=5)
    else:
        r.step("verified")
        r.step("rollback_attempted", "operator_hold")
    r.step("rolled_back")
    r.step("rollback_verified")
    s = r.state()
    assert s["state"] == "rollback_verified" and s["terminal"]
    assert s["rollback_verified"] and s["rolled_back"]
    assert not s["success"] and not s["applied"] and not s["verified"] and not s["continued"]


def test_failed_attempt_retries_with_same_binding_until_success():
    r = Run()
    r.step("attempted")
    r.step("attempt_failed", "refused_by_target")
    assert r.state()["next_phases"] == ["abandoned", "attempted", "expired"]  # revoked needs a revocation
    r.refuse("invalid_reason", "attempted", "started")
    r.step("attempted", "retry")
    r.step("applied")
    assert r.state()["attempts"] == 2 and r.state()["applied"]


# --- unknown external effect -------------------------------------------------------------------

def test_unknown_effect_must_hold_and_reconcile_before_any_retry():
    r = Run()
    r.step("attempted")
    r.step("effect_unknown", "timeout", evidence=False)
    assert r.state()["hold_required"]
    r.refuse("unknown_effect_requires_reconcile", "attempted", "retry")
    r.refuse("unknown_effect_requires_reconcile", "abandoned", "requester_withdrawn")
    r.refuse("invalid_transition", "reconciled", "effect_absent")
    assert r.state()["next_phases"] == ["hold"]
    r.step("hold", "unknown_effect", evidence=False)
    r.refuse("unknown_effect_requires_reconcile", "attempted", "retry")
    r.refuse("unknown_effect_requires_reconcile", "revoked", "revocation_observed", revocation=9)
    r.refuse("evidence_required", "reconciled", "effect_absent", evidence=False)
    assert r.state()["next_phases"] == ["reconciled"]
    r.step("reconciled", "effect_absent")
    s = r.state()
    assert s["state"] == "attempt_failed" and s["unknown_effect_seen"] and not s["hold_required"]
    r.step("attempted", "retry")
    r.step("applied")
    r.step("verified")
    r.step("continued")
    assert r.state()["success"]


def test_reconciled_applied_effect_continues_to_verification_not_success():
    r = Run()
    r.step("attempted")
    r.step("effect_unknown", "crash", evidence=False)
    r.step("hold", "unknown_effect", evidence=False)
    r.step("reconciled", "effect_applied")
    s = r.state()
    assert s["state"] == "applied" and s["applied"] and not s["verified"] and not s["success"]
    r.refuse("invalid_transition", "continued")


def test_rollback_unknown_effect_holds_and_reconciles_on_the_rollback_track():
    r = Run()
    for phase, reason in (("attempted", None), ("applied", None), ("verification_failed", "timeout"),
                          ("rollback_attempted", "verification_failed")):
        r.step(phase, reason)
    r.step("rollback_effect_unknown", "ambiguous_receipt", evidence=False)
    r.refuse("unknown_effect_requires_reconcile", "rollback_attempted", "retry")
    r.refuse("unknown_effect_requires_reconcile", "rolled_back")
    r.step("hold", "unknown_effect", evidence=False)
    assert r.state()["rolled_back"] and not r.state()["success"]
    r.step("reconciled", "effect_applied")
    assert r.state()["state"] == "rolled_back"
    r.step("rollback_verified")
    assert not r.state()["success"]


def test_rollback_retries_are_bounded_then_hold():
    r = Run()
    for phase, reason in (("attempted", None), ("applied", None), ("verification_failed", "timeout")):
        r.step(phase, reason)
    r.step("rollback_attempted", "verification_failed")
    r.step("rollback_failed", "no_side_effect")
    r.refuse("rollback_not_exhausted", "hold", "rollback_exhausted", evidence=False)
    r.refuse("invalid_reason", "rollback_attempted", "operator_hold")
    for _ in range(2):
        r.step("rollback_attempted", "retry")
        r.step("rollback_failed", "refused_by_target")
    r.refuse("retry_exhausted", "rollback_attempted", "retry")
    r.step("hold", "rollback_exhausted", evidence=False)
    r.step("reconciled", "effect_absent")
    s = r.state()
    assert s["state"] == "rollback_failed" and s["rollback_attempts"] == 3
    assert s["next_phases"] == ["hold"]  # still no silent success or unbounded retry


def test_unverifiable_rollback_holds():
    r = Run()
    for phase, reason in (("attempted", None), ("applied", None), ("verification_failed", "timeout"),
                          ("rollback_attempted", "verification_failed"), ("rolled_back", None)):
        r.step(phase, reason)
    r.refuse("invalid_reason", "hold", "unknown_effect", evidence=False)
    r.step("hold", "rollback_unverified", evidence=False)
    assert r.state()["hold_required"] and not r.state()["success"]


# --- retry, expiry, revocation boundaries ------------------------------------------------------

def test_attempts_are_bounded_and_exhaustion_is_explicit():
    r = Run()
    r.refuse("invalid_reason", "abandoned", "retry_exhausted")
    for i in range(3):
        r.step("attempted", "started" if i == 0 else "retry")
        r.step("attempt_failed", "precondition_failed")
    r.refuse("retry_exhausted", "attempted", "retry")
    assert "attempted" not in r.state()["next_phases"]
    r.step("abandoned", "retry_exhausted", evidence=False)
    assert r.state()["terminal"] and not r.state()["success"]
    r.refuse("operation_terminal", "attempted", "retry")


def test_expired_intent_cannot_be_attempted_and_expiry_needs_the_deadline():
    r = Run(expires_at_utc=utc(100))
    r.refuse("not_expired", "expired", "deadline_passed", evidence=False, dt=50)
    r.refuse("intent_expired", "attempted", "started", dt=101)
    r.step("expired", "deadline_passed", evidence=False, dt=101)
    assert r.state()["terminal"]


def test_newer_revocation_blocks_start_and_continuation_but_allows_rollback():
    r = Run()
    r.refuse("revocation_not_observed", "revoked", "revocation_observed", evidence=False)
    r.refuse("revocation_blocks_phase", "attempted", "started", revocation=5)
    r.step("attempted")
    r.step("applied")
    r.step("verified", revocation=5)
    r.refuse("revocation_blocks_phase", "continued")
    r.refuse("revocation_blocks_phase", "resume_pending", "awaiting_requester")
    r.refuse("revocation_regression", "rollback_attempted", "operator_hold", revocation=4)
    r.step("rollback_attempted", "revocation_observed")
    assert r.state()["revocation_observed"]


def test_rollback_citing_revocation_requires_an_observed_revocation():
    r = Run()
    for phase in ("attempted", "applied", "verified"):
        r.step(phase)
    r.refuse("revocation_not_observed", "rollback_attempted", "revocation_observed")


def test_revoked_before_attempt_is_terminal():
    r = Run()
    r.step("revoked", "revocation_observed", evidence=False, revocation=5)
    r.refuse("operation_terminal", "attempted", "started")
    assert r.state()["terminal"] and not r.state()["attempted"]


# --- replay and chain integrity -----------------------------------------------------------------

def test_identical_replay_is_idempotent_and_conflicting_replay_is_refused():
    r = Run()
    a = r.step("attempted")
    b = r.step("applied")
    snapshot = json.dumps(r.journal.fold(), sort_keys=True)
    assert r.journal.append(json.loads(json.dumps(a))) == "duplicate"
    assert r.journal.append(dict(b)) == "duplicate"
    assert json.dumps(r.journal.fold(), sort_keys=True) == snapshot
    for field, value in (("evidence_digests", ev(999)), ("recorded_at_utc", utc(999)),
                         ("reason", "retry"), ("phase", "effect_unknown")):
        forged = dict(a, **{field: value})
        if field == "phase":
            forged["reason"] = "timeout"
        with pytest.raises(ContractError) as exc:
            r.journal.append(forged)
        assert exc.value.code == "conflicting_replay"
    intent = sj.intent_record("op-1", binding(task_revision=8), utc(0))
    with pytest.raises(ContractError) as exc:
        r.journal.append(intent)
    assert exc.value.code == "conflicting_replay"


@pytest.mark.parametrize("mutate,code", [
    (lambda rec: rec.update(seq=rec["seq"] + 1), "seq_gap"),
    (lambda rec: rec.update(prev_digest="c" * 64), "prev_digest_mismatch"),
    (lambda rec: rec.update(binding_digest="d" * 64), "binding_mismatch"),
    (lambda rec: rec.update(recorded_at_utc=utc(-5)), "time_regression"),
    (lambda rec: rec.update(observed_revocation_version=3), "revocation_regression"),
])
def test_chain_breaks_are_refused(mutate, code):
    r = Run()
    r.step("attempted")
    record = r.rec("applied")
    mutate(record)
    with pytest.raises(ContractError) as exc:
        r.journal.append(record)
    assert exc.value.code == code


def test_operation_must_start_with_its_intent_and_bindings_are_exact():
    j = Journal()
    r = Run()
    orphan = r.rec("attempted")
    with pytest.raises(ContractError) as exc:
        j.append(orphan)
    assert exc.value.code == "seq_gap"
    bad = sj.intent_record("op-2", binding(), utc(0))
    bad["binding"] = dict(bad["binding"], task_revision=8)
    with pytest.raises(ContractError) as exc:
        j.append(bad)
    assert exc.value.code == "binding_mismatch"
    for over, code in ((dict(source_generation="gen-b"), "same_generation"),
                       (dict(operation_kind="provision_account"), "invalid_operation_kind"),
                       (dict(task_revision=True), "invalid_number"),
                       (dict(task_id="a/../b"), "invalid_task_id"),
                       (dict(lane="Fable-5"), "invalid_identifier")):
        with pytest.raises(ContractError) as exc:
            sj.intent_record("op-3", binding(**over), utc(0))
        assert exc.value.code == code


def test_two_operations_fold_independently():
    j = Journal()
    for op, lane in (("op-a", "fable-5"), ("op-b", "codex-tools-1")):
        j.append(sj.intent_record(op, binding(lane=lane), utc(0)))
    j.append(sj.next_record(j, "op-b", "attempted", "started", utc(1), []))
    fold = j.fold()
    assert [o["operation_id"] for o in fold["operations"]] == ["op-a", "op-b"]
    assert [o["state"] for o in fold["operations"]] == ["intent_recorded", "attempted"]


# --- hard boundaries and forged authority ------------------------------------------------------

@pytest.mark.parametrize("field", ["grant", "approved", "authority", "max_attempts", "capabilities"])
def test_records_cannot_carry_authority_or_boundary_overrides(field):
    r = Run()
    record = r.rec("attempted")
    record[field] = True
    with pytest.raises(ContractError) as exc:
        r.journal.append(record)
    assert exc.value.code == "unknown_field"
    b = binding()
    b[field] = True
    with pytest.raises(ContractError) as exc:
        sj.parse_binding(b)
    assert exc.value.code == "unknown_field"


def test_hard_boundaries_are_immutable_and_not_overridable():
    with pytest.raises(TypeError):
        sj.HARD_BOUNDARIES["max_attempts"] = 99
    with pytest.raises(AttributeError):
        sj.HARD_BOUNDARIES["operation_kinds"].add("provision_account")
    assert sj.HARD_BOUNDARIES["grants_authority"] is False and sj.HARD_BOUNDARIES["actuates"] is False
    with pytest.raises(SystemExit):
        sj.main(["fold", "--journal", "x.jsonl", "--max-attempts", "9"])


@pytest.mark.parametrize("value,code", [
    (type("D", (dict,), {})(), "invalid_record"),
    ([], "invalid_record"),
    ("{}", "invalid_record"),
])
def test_non_plain_records_are_refused(value, code):
    with pytest.raises(ContractError) as exc:
        sj.parse_record(value)
    assert exc.value.code == code


def test_exact_types_inside_records():
    r = Run()
    base = r.rec("attempted")

    class S(str):
        pass
    for field, value, code in (("seq", True, "invalid_number"), ("seq", 2.0, "invalid_number"),
                               ("operation_id", S("op-1"), "invalid_identifier"),
                               ("phase", S("attempted"), "invalid_phase"),
                               ("evidence_digests", (D,), "invalid_evidence"),
                               ("evidence_digests", [D, D], "invalid_evidence"),
                               ("recorded_at_utc", "2026-09-28T12:00:01+00:00", "invalid_utc"),
                               ("binding", binding(), "binding_only_on_intent")):
        with pytest.raises(ContractError) as exc:
            sj.parse_record(dict(base, **{field: value}))
        assert exc.value.code == code, field


# Pinned here, not read from the module: a claim of an outside-world effect, its
# verification, continuation or absence needs evidence.
EVIDENCE_PHASES = {"attempt_failed", "applied", "verification_failed", "verified", "continued",
                   "rollback_failed", "rolled_back", "rollback_verified", "reconciled"}


def test_every_claim_about_the_world_needs_evidence():
    assert sj.EVIDENCE_REQUIRED == EVIDENCE_PHASES
    for phase in EVIDENCE_PHASES:
        r = Run()
        record = r.rec("attempted")
        record.update(phase=phase, reason=sorted(sj.REASONS[phase])[0], evidence_digests=[])
        with pytest.raises(ContractError) as exc:
            sj.parse_record(record)
        assert exc.value.code == "evidence_required", phase


# --- exhaustive model check -------------------------------------------------------------------

def _candidates(op_state):
    """Every (phase, reason) the vocabulary allows, not only the valid ones."""
    for phase in sorted(sj.PHASES - {"intent_recorded"}):
        for reason in sorted(sj.REASONS[phase]):
            yield phase, reason


def test_exhaustive_reachable_graph_upholds_the_safety_invariants():
    start = Run()
    queue = deque([(start, ())])
    seen = set()
    explored = 0
    while queue:
        run, path = queue.popleft()
        op = run.journal._ops[run.op]
        key = (op.state, op.attempts, op.rollback_attempts, op.held_track,
               op.revocation_seen > op.binding["revocation_version"], op.ever_unknown,
               run.t > 3600)
        if key in seen:
            continue
        seen.add(key)
        s = run.state()
        # Invariants on every reachable state.
        assert s["success"] == (s["state"] == "continued")
        assert not (s["success"] and s["rolled_back"])
        assert not (s["success"] and s["revocation_observed"])
        assert s["attempts"] <= sj.HARD_BOUNDARIES["max_attempts"]
        assert s["rollback_attempts"] <= sj.HARD_BOUNDARIES["max_rollback_attempts"]
        if s["state"] in ("effect_unknown", "rollback_effect_unknown"):
            assert s["next_phases"] == ["hold"]
        if s["state"] == "hold":
            assert s["next_phases"] == ["reconciled"]
        if s["state"] == "rollback_verified":
            assert not s["success"] and s["terminal"]
        if s["state"] == "resume_pending":
            assert not s["continued"]
        if s["continued"]:
            assert "verified" in {r["phase"] for r in op.records}
        assert all(r["recorded_at_utc"] <= op.binding["expires_at_utc"]
                   for r in op.records if r["phase"] == "attempted")
        accepted = set()
        for phase, reason in _candidates(op.state):
            for revocation, dt in ((None, 1), (op.revocation_seen + 1, 1), (None, 3601)):
                child = Run()
                for rec in op.records[1:]:
                    child.journal.append(rec)
                child.t, child.n = run.t, run.n
                record = child.rec(phase, reason, revocation=revocation, dt=dt)
                try:
                    child.journal.append(record)
                except ContractError as exc:
                    assert exc.code  # stable refusal, nothing committed
                    assert len(child.journal._ops[child.op].records) == len(op.records)
                    continue
                accepted.add(phase)
                explored += 1
                queue.append((child, path + (phase,)))
        # next_phases never advertises a phase that no record could satisfy.
        assert set(s["next_phases"]) <= accepted | {"expired"}
    states = {k[0] for k in seen}
    assert states == set(sj.TRANSITIONS) - {None}
    assert explored > 200


# --- file journal: durability, crash cuts, CLI -------------------------------------------------

def _full_sequence():
    r = Run()
    records = [r.journal._ops["op-1"].records[0]]
    for phase, reason, evidence in (("attempted", None, True), ("effect_unknown", "timeout", False),
                                    ("hold", "unknown_effect", False), ("reconciled", "effect_applied", True),
                                    ("verified", None, True), ("resume_pending", "awaiting_requester", True),
                                    ("continued", None, True)):
        records.append(r.step(phase, reason, evidence=evidence))
    return records


def test_file_append_fold_and_duplicate_is_not_rewritten(tmp_path):
    path = tmp_path / "journal.jsonl"
    records = _full_sequence()
    for record in records:
        assert sj.append_file(path, record)[0] == "appended"
    size = path.stat().st_size
    assert sj.append_file(path, records[3])[0] == "duplicate"
    assert path.stat().st_size == size
    journal, committed, torn = sj.read_journal(path)
    assert (committed, torn) == (size, 0)
    assert journal.operation("op-1")["success"]
    with pytest.raises(ContractError) as exc:
        sj.append_file(path, dict(records[2], evidence_digests=ev(77)))
    assert exc.value.code == "conflicting_replay"
    assert path.stat().st_size == size


def test_every_crash_cut_folds_to_a_committed_prefix_and_never_invents_progress(tmp_path):
    records = _full_sequence()
    lines = [sj.canonical_bytes(r) + b"\n" for r in records]
    data = b"".join(lines)
    boundaries = [0]
    for line in lines:
        boundaries.append(boundaries[-1] + len(line))
    expected = {}
    for count in range(len(records) + 1):
        j = Journal()
        for r in records[:count]:
            j.append(r)
        expected[count] = j.operation("op-1") if count else None
    path = tmp_path / "cut.jsonl"
    # Fold at every byte; the (fsync-heavy) append/repair path at every record
    # boundary +-2 bytes and a regular sample in between.
    heavy = {b + d for b in boundaries for d in (-2, -1, 0, 1, 2)} | set(range(0, len(data), 53))
    for cut in range(len(data) + 1):
        path.write_bytes(data[:cut])
        journal, committed, torn = sj.read_journal(path)
        count = sum(1 for b in boundaries[1:] if b <= cut)
        assert committed == boundaries[count] and torn == cut - committed
        got = journal.operation("op-1") if count else None
        assert got == expected[count], cut
        if cut not in heavy:
            continue
        if torn:
            with pytest.raises(ContractError) as exc:
                sj.append_file(path, records[min(count, len(records) - 1)])
            assert exc.value.code == "torn_tail_requires_repair"
            assert sj.repair_file(path) == torn
            assert path.read_bytes() == data[:committed]
        if count < len(records):
            assert sj.append_file(path, records[count])[0] == "appended"
            assert sj.read_journal(path)[0].operation("op-1") == (expected[count + 1])


@pytest.mark.parametrize("line,code", [
    (b'{"a":1}\n', "invalid_record"),
    (b"not json\n", "invalid_journal_line"),
    (b"\xff\n", "invalid_journal_line"),
])
def test_corrupt_committed_lines_are_refused(tmp_path, line, code):
    path = tmp_path / "bad.jsonl"
    path.write_bytes(line)
    with pytest.raises(ContractError) as exc:
        sj.read_journal(path)
    assert exc.value.code in (code, "missing_field")


def test_noncanonical_and_duplicate_key_lines_are_refused(tmp_path):
    record = _full_sequence()[0]
    path = tmp_path / "nc.jsonl"
    path.write_bytes(json.dumps(record, indent=1).replace("\n", "").encode() + b"\n")
    with pytest.raises(ContractError) as exc:
        sj.read_journal(path)
    assert exc.value.code == "noncanonical_journal_line"
    canon = sj.canonical_bytes(record)
    dup = b'{"seq":1,' + canon[1:]
    path.write_bytes(dup + b"\n")
    with pytest.raises(ContractError) as exc:
        sj.read_journal(path)
    assert exc.value.code == "noncanonical_journal_line"


def test_concurrent_modification_between_validate_and_write_is_refused(tmp_path, monkeypatch):
    records = _full_sequence()
    path = tmp_path / "race.jsonl"
    sj.append_file(path, records[0])
    real = sj.read_journal

    def racing(p):
        result = real(p)
        with open(p, "ab") as handle:  # another writer lands after validation
            handle.write(sj.canonical_bytes(records[1]) + b"\n")
        return result
    monkeypatch.setattr(sj, "read_journal", racing)
    with pytest.raises(ContractError) as exc:
        sj.append_file(path, records[1])
    assert exc.value.code == "concurrent_modification"


def test_live_bridge_paths_are_refused(tmp_path):
    live = tmp_path / ".agent-bridge" / "switch.jsonl"
    live.parent.mkdir()
    for call in (lambda: sj.append_file(live, _full_sequence()[0]), lambda: sj.repair_file(live)):
        with pytest.raises(ContractError) as exc:
            call()
        assert exc.value.code == "live_runtime_path_refused"
    assert not live.exists()


def _cli(*args):
    return subprocess.run([sys.executable, "-B", str(SCRIPT), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)


def test_cli_contract_append_fold_torn_repair(tmp_path):
    path = tmp_path / "cli.jsonl"
    contract = json.loads(_cli("contract").stdout)
    assert contract["hard_boundaries"]["grants_authority"] is False
    assert contract["transitions"]["effect_unknown"] == ["hold"]
    records = _full_sequence()
    for record in records[:3]:
        rf = tmp_path / "r.json"
        rf.write_text(json.dumps(record), encoding="utf-8")
        out = _cli("append", "--journal", str(path), "--record-file", str(rf))
        assert out.returncode == 0, out.stdout
        assert json.loads(out.stdout)["result"] == "appended"
    folded = _cli("fold", "--journal", str(path))
    assert folded.returncode == 0
    op = json.loads(folded.stdout)["operations"][0]
    assert op["state"] == "effect_unknown" and op["next_phases"] == ["hold"]
    rf.write_text(json.dumps(dict(records[1], reason="retry")), encoding="utf-8")
    bad = _cli("append", "--journal", str(path), "--record-file", str(rf))
    assert bad.returncode == 2 and json.loads(bad.stdout) == {"error": "conflicting_replay"}
    with open(path, "ab") as handle:
        handle.write(sj.canonical_bytes(records[3])[:20])
    torn = _cli("fold", "--journal", str(path))
    assert torn.returncode == 3 and json.loads(torn.stdout)["torn_tail_bytes"] == 20
    assert json.loads(_cli("repair", "--journal", str(path)).stdout) == {"removed_torn_bytes": 20}
    assert _cli("fold", "--journal", str(path)).returncode == 0
    nan = tmp_path / "nan.json"
    nan.write_text('{"seq": NaN}', encoding="utf-8")
    assert json.loads(_cli("append", "--journal", str(path), "--record-file", str(nan)).stdout) == {
        "error": "io_or_parse_error"}


def test_module_has_no_actuation_or_network_surface():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("subprocess", "socket", "urllib", "http", "Stop-Process", "taskkill",
                      "os.kill", "os.system", "Popen", "shutil.rmtree", "os.environ"):
        assert forbidden not in source, forbidden
