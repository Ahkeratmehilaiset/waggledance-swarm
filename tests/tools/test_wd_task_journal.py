# SPDX-License-Identifier: BUSL-1.1
"""F27 task and operation journal (tools/wd_task_journal.py): fixture-only, default OFF.

Every journal lives under tmp_path. The crash-cut test truncates a complete journal after
every record (and tears the next one) and requires each cut to be reconciled or HELD.
The RCO1 9ee175eb findings (S1, N1-N4) each have a failure twin here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path

import pytest

import tools.wd_task_journal as journal_module
from tools.wd_task_journal import FENCE_EVIDENCE_KEYS, JournalError, TaskJournal, identity

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 30, 17, 40, tzinfo=timezone.utc)
TASK = "codex-lead-1/bridge-v2-switch-continuity-release-20260930-1645"
OWNER = {"agent": "claude-rco-2", "generation": "wd-reboot-20260930T162350Z", "token": "t-original-0123456789"}
STANDIN = {"agent": "claude-rco-1", "generation": "wd-reboot-20260930T162350Z", "token": "t-standin-0123456789"}
BASE, HEAD, HEAD2 = "7" * 40, "8" * 40, "9" * 40
FENCE = {key: True for key in FENCE_EVIDENCE_KEYS}


class Authority:
    """A fixture executor authority: an HMAC over the canonical fence payload."""

    def __init__(self, key: bytes = b"fixture-executor-key") -> None:
        self.key = key

    def _mac(self, fence: dict) -> str:
        return hmac.new(self.key, json.dumps(fence, sort_keys=True).encode("utf-8"), hashlib.sha256).hexdigest()

    def attest(self, fence: dict) -> str:
        return self._mac(fence)

    def verify(self, fence: dict, attestation: str) -> bool:
        return hmac.compare_digest(self._mac(fence), attestation)


def scenario() -> list[tuple[str, dict]]:
    """A complete, valid history: two steps, a non-idempotent push and an idempotent message."""
    return [
        ("task_opened", {"task_id": TASK, "revision": 1, "base_commit": BASE}),
        ("step_planned", {"step": 1, "title": "trusted inputs"}),
        ("step_started", {"step": 1}),
        ("wip_checkpoint", {"step": 1, "base": BASE, "head": HEAD, "artifact_sha256": "a" * 64, "tested_head": None,
                            "dirty": [{"path": "tools/x.py", "state": "added"}]}),
        ("op_intent", {"op_id": "push-1", "op_kind": "git_push", "idempotency_key": "k-push-1", "idempotent": False}),
        ("op_attempted", {"op_id": "push-1"}),
        ("op_applied", {"op_id": "push-1", "receipt": "remote tip " + HEAD}),
        ("op_verified", {"op_id": "push-1", "receipt": "ls-remote " + HEAD}),
        ("step_committed", {"step": 1, "head": HEAD}),
        ("step_planned", {"step": 2, "title": "ports"}),
        ("step_started", {"step": 2}),
        ("op_intent", {"op_id": "msg-1", "op_kind": "bridge_reply", "idempotency_key": "k-msg-1", "idempotent": True}),
        ("op_attempted", {"op_id": "msg-1"}),
        ("op_verified", {"op_id": "msg-1", "receipt": "event 17:05:19Z"}),
        ("step_committed", {"step": 2, "head": HEAD2}),
    ]


def build(root: Path, records, *, authority=None, revision: int = 1) -> TaskJournal:
    journal = TaskJournal(root, TASK, revision, fence_authority=authority, lock_wait_seconds=0.2)
    for seq, (kind, fields) in enumerate(records):
        journal.append(kind, fields, credential=OWNER, expected_seq=seq, now=NOW + timedelta(seconds=seq))
    return journal


def code(call) -> str:
    with pytest.raises(JournalError) as caught:
        call()
    return caught.value.code


def test_success_twin_reconciles_to_the_next_step_boundary(tmp_path):
    journal = build(tmp_path, scenario())
    verdict = journal.reconcile()
    assert verdict["verdict"] == "continue" and verdict["reasons"] == [] and verdict["reverify"] == []
    assert verdict["resume"] == {"step": 3, "from": "step_boundary", "last_committed": 2}
    assert verdict["owner"] == identity(OWNER) and verdict["seq"] == 15
    assert verdict["task"] == {"task_id": TASK, "revision": 1, "base_commit": BASE}


def test_s1_the_raw_token_is_never_stored_and_a_reader_cannot_append_as_the_owner(tmp_path):
    journal = build(tmp_path, scenario()[:3])
    raw = journal.path.read_text(encoding="utf-8")
    assert OWNER["token"] not in raw
    stored = json.loads(raw.splitlines()[0])["owner"]
    assert stored == identity(OWNER) and set(stored) == {"agent", "generation", "token_sha256"}
    # A reader that copies the stored identity has only the hash; presenting it as a token fails.
    forged = {"agent": stored["agent"], "generation": stored["generation"], "token": stored["token_sha256"]}
    assert code(lambda: journal.append("wip_checkpoint", scenario()[3][1], credential=forged, expected_seq=3,
                                       now=NOW)) == "writer_not_the_owner"
    assert code(lambda: journal.append("wip_checkpoint", scenario()[3][1], credential=dict(stored), expected_seq=3,
                                       now=NOW)) == "credential_invalid"
    journal.append("wip_checkpoint", scenario()[3][1], credential=OWNER, expected_seq=3, now=NOW)  # success twin


def test_s1_ownership_never_moves_on_a_self_asserted_fence(tmp_path):
    journal = build(tmp_path, scenario()[:3])
    # No authority: the public append refuses a fence, and append_fence refuses without the principal.
    assert code(lambda: journal.append("fence", {}, credential=STANDIN, expected_seq=3, now=NOW)) \
        == "fence_needs_the_executor_authority"
    assert code(lambda: journal.append_fence(identity(OWNER), identity(STANDIN), FENCE, expected_seq=3, now=NOW)) \
        == "fence_principal_unverified"
    # A fence forged straight into the file (a valid chain, a made-up attestation) is HELD, never obeyed.
    state = journal.replay()
    record = {"schema": journal_module.SCHEMA, "seq": 4, "prev_sha256": state.last_sha256, "kind": "fence",
              "owner": identity(STANDIN), "at_utc": "2026-09-30T17:40:00.000000Z",
              "previous_owner": identity(OWNER), "new_owner": identity(STANDIN), "evidence": FENCE,
              "attestation": "self-asserted"}
    with open(journal.path, "ab") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n")
    verdict = journal.reconcile()
    assert verdict["verdict"] == "hold" and verdict["reasons"] == ["fence_principal_unverified:4"]
    # With an authority, the same forged attestation is refused outright.
    checked = TaskJournal(tmp_path, TASK, 1, fence_authority=Authority(), lock_wait_seconds=0.2)
    assert checked.reconcile()["reasons"] == ["fence_attestation_invalid"]


def test_an_authority_attested_fence_moves_ownership_and_the_fenced_writer_is_locked_out(tmp_path):
    journal = build(tmp_path, scenario()[:3], authority=Authority())
    assert code(lambda: journal.append_fence(identity(OWNER), identity(STANDIN), dict(FENCE, descendants_verified=False),
                                             expected_seq=3, now=NOW)) == "fence_incomplete"
    assert code(lambda: journal.append_fence(identity(STANDIN), identity(STANDIN), FENCE, expected_seq=3, now=NOW)) \
        == "fence_same_token"
    assert code(lambda: journal.append_fence(identity(STANDIN), identity(OWNER) | {"agent": "x"}, FENCE,
                                             expected_seq=3, now=NOW)) == "fence_of_another_owner"
    journal.append_fence(identity(OWNER), identity(STANDIN), FENCE, expected_seq=3, now=NOW)
    # The fenced writer (an expired lease, old conversation metadata) can never append again.
    assert code(lambda: journal.append("step_committed", {"step": 1, "head": HEAD}, credential=OWNER, expected_seq=4,
                                       now=NOW)) == "writer_not_the_owner"
    journal.append("step_committed", {"step": 1, "head": HEAD}, credential=STANDIN, expected_seq=4, now=NOW)
    verdict = journal.reconcile()
    assert verdict["verdict"] == "continue" and verdict["owner"] == identity(STANDIN)
    # The same journal read by a reconciler WITHOUT the authority holds instead of trusting the fence.
    assert TaskJournal(tmp_path, TASK, 1, lock_wait_seconds=0.2).reconcile()["reasons"] == \
        ["fence_principal_unverified:4"]
    # A different key (another principal) is refused.
    assert TaskJournal(tmp_path, TASK, 1, fence_authority=Authority(b"other"), lock_wait_seconds=0.2) \
        .reconcile()["reasons"] == ["fence_attestation_invalid"]


def test_every_crash_cut_is_reconciled_or_explicitly_held(tmp_path):
    full = build(tmp_path / "full", scenario())
    lines = full.path.read_bytes().split(b"\n")[:-1]
    expected = {  # verdict after the first k records (k = 1..15)
        1: "continue", 2: "continue", 3: "continue", 4: "continue", 5: "continue",
        6: "hold", 7: "hold",  # a non-idempotent push attempted or applied, not verified: HOLD
        8: "continue", 9: "continue", 10: "continue", 11: "continue", 12: "continue",
        13: "reverify",  # an idempotent message attempted: re-check its receipt, never resend blindly
        14: "continue", 15: "continue"}
    for k in range(1, len(lines) + 1):
        cut = TaskJournal(tmp_path / ("cut-" + str(k)), TASK, 1, lock_wait_seconds=0.2)
        cut.root.mkdir(parents=True)
        cut.path.write_bytes(b"\n".join(lines[:k]) + b"\n")
        verdict = cut.reconcile()
        assert verdict["verdict"] == expected[k], (k, verdict)
        if k in (6, 7):
            assert verdict["reasons"] == ["unknown_external_outcome:push-1"]
        if k < len(lines):  # the NEXT append torn mid-write (a real crash, no lock held)
            cut.path.write_bytes(b"\n".join(lines[:k]) + b"\n" + lines[k][: len(lines[k]) // 2])
            assert cut.reconcile() == {"verdict": "hold", "reasons": ["journal_torn_tail"], "resume": None,
                                       "reverify": [], "owner": None}


def test_n1_each_revision_is_its_own_journal(tmp_path):
    first = build(tmp_path, scenario()[:1], revision=1)
    second = TaskJournal(tmp_path, TASK, 2, lock_wait_seconds=0.2)
    second.append("task_opened", {"task_id": TASK, "revision": 2, "base_commit": BASE}, credential=OWNER,
                  expected_seq=0, now=NOW)
    assert first.path != second.path and first.path.exists() and second.path.exists()
    assert second.reconcile()["task"]["revision"] == 2 and first.reconcile()["task"]["revision"] == 1
    assert code(lambda: first.append("task_opened", {"task_id": TASK, "revision": 2, "base_commit": BASE},
                                     credential=OWNER, expected_seq=1, now=NOW)) == "task_reopened"


def test_n2_a_foreign_task_or_revision_is_refused_before_anything_is_written(tmp_path):
    journal = TaskJournal(tmp_path, TASK, 1, lock_wait_seconds=0.2)
    for fields in ({"task_id": "another/task", "revision": 1, "base_commit": BASE},
                   {"task_id": TASK, "revision": 3, "base_commit": BASE}):
        assert code(lambda: journal.append("task_opened", fields, credential=OWNER, expected_seq=0, now=NOW)) \
            == "task_identity_mismatch"
    assert not journal.path.exists()


def test_n3_an_oversized_journal_is_refused_before_it_is_read(tmp_path, monkeypatch):
    journal = build(tmp_path, scenario()[:3])
    monkeypatch.setattr(journal_module, "MAX_JOURNAL_BYTES", journal.path.stat().st_size - 1)
    assert journal.reconcile()["reasons"] == ["journal_oversized"]


def test_n4_reconcile_waits_for_an_append_in_progress_instead_of_reporting_a_torn_tail(tmp_path):
    journal = build(tmp_path, scenario()[:3])
    with journal_module._exclusive_lock(journal.lock_path):
        with open(journal.path, "ab") as stream:
            stream.write(b'{"partial":')   # an append in progress under the writer's lock
        assert journal.reconcile()["reasons"] == ["journal_locked"]   # waited, then reported the lock, not a tear
    # Once the writer is gone and its bytes are still torn, that is a real torn tail.
    assert journal.reconcile()["reasons"] == ["journal_torn_tail"]


def test_an_open_step_resumes_from_its_wip_checkpoint_or_quarantines(tmp_path):
    with_wip = build(tmp_path / "a", scenario()[:4])
    resume = with_wip.reconcile()["resume"]
    assert resume["step"] == 1 and resume["from"] == "wip_checkpoint" and resume["wip"]["head"] == HEAD
    without = build(tmp_path / "b", scenario()[:3])
    resume = without.reconcile()["resume"]
    assert resume["from"] == "last_committed_step" and resume["last_committed"] == 0 and "never reset" in resume["note"]


def test_a_tampered_or_foreign_journal_is_held(tmp_path):
    journal = build(tmp_path, scenario())
    lines = journal.path.read_bytes().split(b"\n")
    lines[3] = lines[3].replace(b"tools/x.py", b"tools/y.py")
    journal.path.write_bytes(b"\n".join(lines))
    assert journal.reconcile()["reasons"] == ["journal_chain_broken"]
    other = TaskJournal(tmp_path, "another/task", 1, lock_wait_seconds=0.2)
    build(tmp_path / "src", scenario()).path.replace(other.path)
    assert other.reconcile()["reasons"] == ["task_identity_mismatch"]
    assert TaskJournal(tmp_path / "none", TASK, 1).reconcile()["reasons"] == ["journal_missing"]


def test_hand_back_only_at_a_step_boundary_with_no_open_operation(tmp_path):
    journal = build(tmp_path, scenario()[:3], authority=Authority())
    journal.append_fence(identity(OWNER), identity(STANDIN), FENCE, expected_seq=3, now=NOW)
    back = {"to_owner": identity(OWNER), "at_step": 0, "evidence": "c" * 64}
    assert code(lambda: journal.append("handback", back, credential=STANDIN, expected_seq=4, now=NOW)) \
        == "handback_not_at_a_boundary"
    journal.append("step_committed", {"step": 1, "head": HEAD}, credential=STANDIN, expected_seq=4, now=NOW)
    assert code(lambda: journal.append("handback", back, credential=STANDIN, expected_seq=5, now=NOW)) \
        == "handback_step_mismatch"
    journal.append("handback", dict(back, at_step=1), credential=STANDIN, expected_seq=5, now=NOW)
    assert journal.reconcile()["owner"] == identity(OWNER)


def test_step_and_operation_rules(tmp_path):
    journal = build(tmp_path, scenario()[:5])  # push-1 intended, not attempted
    assert code(lambda: journal.append("step_committed", {"step": 1, "head": HEAD}, credential=OWNER, expected_seq=5,
                                       now=NOW)) == "step_closed_with_open_operation"
    assert code(lambda: journal.append("op_verified", {"op_id": "push-1", "receipt": "r"}, credential=OWNER,
                                       expected_seq=5, now=NOW)) == "op_transition_invalid"
    assert code(lambda: journal.append("step_started", {"step": 1}, credential=OWNER, expected_seq=5, now=NOW)) \
        == "step_order_invalid"
    assert code(lambda: journal.append("wip_checkpoint", {"step": 2, "base": BASE, "head": HEAD,
                                                          "artifact_sha256": "a" * 64, "tested_head": None,
                                                          "dirty": []}, credential=OWNER, expected_seq=5, now=NOW)) \
        == "wip_without_open_step"
    assert code(lambda: journal.append("op_intent", {"op_id": "push-1", "op_kind": "git_push", "idempotency_key": "k",
                                                     "idempotent": 0}, credential=OWNER, expected_seq=5, now=NOW)) \
        == "record_fields_invalid"
    assert journal.reconcile()["verdict"] == "continue"  # an intent never attempted is not a hold


def test_holds_block_until_released(tmp_path):
    journal = build(tmp_path, scenario())
    journal.append("hold", {"reason": "operator freeze"}, credential=OWNER, expected_seq=15, now=NOW)
    verdict = journal.reconcile()
    assert verdict["verdict"] == "hold" and verdict["reasons"] == ["unreleased_hold:16"]
    journal.append("hold_released", {"hold_seq": 16, "evidence": "operator release 17:20Z"}, credential=OWNER,
                   expected_seq=16, now=NOW)
    assert journal.reconcile()["verdict"] == "continue"


def test_compare_and_swap_the_lock_and_the_inputs_refuse(tmp_path):
    journal = build(tmp_path, scenario()[:2])
    assert code(lambda: journal.append("step_started", {"step": 1}, credential=OWNER, expected_seq=1, now=NOW)) \
        == "journal_cas_conflict"
    with journal_module._exclusive_lock(journal.lock_path):
        assert code(lambda: journal.append("step_started", {"step": 1}, credential=OWNER, expected_seq=2, now=NOW)) \
            == "journal_locked"
    assert code(lambda: journal.append("step_started", {"step": 1}, credential=OWNER, expected_seq=2,
                                       now=datetime(2026, 9, 30, 17, 10))) == "time_unknown"
    assert code(lambda: journal.append("step_started", {"step": 1}, credential=dict(OWNER, token="short"),
                                       expected_seq=2, now=NOW)) == "credential_invalid"
    assert code(lambda: TaskJournal(tmp_path, TASK, 0)) == "revision_invalid"
    assert journal.replay().seq == 2


def test_the_journal_must_open_with_its_task_and_never_reopen(tmp_path):
    journal = TaskJournal(tmp_path, TASK, 1, lock_wait_seconds=0.2)
    assert code(lambda: journal.append("step_planned", {"step": 1, "title": "x"}, credential=OWNER, expected_seq=0,
                                       now=NOW)) == "journal_must_open_with_the_task"
    build(tmp_path, scenario()[:1])
    assert code(lambda: journal.append("task_opened", {"task_id": TASK, "revision": 1, "base_commit": BASE},
                                       credential=OWNER, expected_seq=1, now=NOW)) == "task_reopened"


def test_task_keys_are_collision_free_and_filesystem_safe():
    first, second = journal_module.task_key("a/b"), journal_module.task_key("a:b")
    assert first != second and first.startswith("a_b-") and second.startswith("a_b-")
    assert code(lambda: journal_module.task_key("../escape")) == "task_id_invalid"


def test_record_fields_never_collide_with_the_envelope():
    for kind, fields in journal_module.FIELDS.items():
        assert not (fields & journal_module.ENVELOPE), kind


def test_no_runtime_path_imports_the_journal():
    needle = "wd_task_journal"
    hits = []
    for base in ("ops", ".agent-bridge", "tools", "configs", "waggledance/core"):
        for path in (ROOT / base).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".ps1", ".psm1", ".json", ".cmd"} \
                    and path.name != "wd_task_journal.py":
                if needle in path.read_text(encoding="utf-8", errors="replace"):
                    hits.append(str(path.relative_to(ROOT)))
    assert hits == []


def _forge_fence(journal: TaskJournal) -> bytes:
    """A fence written straight into the file (a valid chain, a made-up attestation); returns the bytes after."""
    state = journal.replay()
    record = {"schema": journal_module.SCHEMA, "seq": state.seq + 1, "prev_sha256": state.last_sha256, "kind": "fence",
              "owner": identity(STANDIN), "at_utc": "2026-09-30T18:15:00.000000Z",
              "previous_owner": identity(OWNER), "new_owner": identity(STANDIN), "evidence": FENCE,
              "attestation": "self-asserted"}
    with open(journal.path, "ab") as stream:
        stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n")
    return journal.path.read_bytes()


def test_no_append_extends_a_journal_holding_an_unverified_fence_rco1_70716636(tmp_path):
    """Reproduced first at 55e076d9: the forged new owner appended and the rightful owner was refused."""
    journal = build(tmp_path, scenario()[:3])
    before = _forge_fence(journal)
    for credential in (OWNER, STANDIN):   # the rightful owner and the forged new owner alike
        assert code(lambda: journal.append("wip_checkpoint", scenario()[3][1], credential=credential,
                                           expected_seq=4, now=NOW)) == "fence_principal_unverified"
    # A stale expected seq still reports the unverified fence first: one stable reason.
    assert code(lambda: journal.append("hold", {"reason": "x"}, credential=OWNER, expected_seq=0, now=NOW)) \
        == "fence_principal_unverified"
    assert code(lambda: journal.append_fence(identity(STANDIN), identity(OWNER), FENCE, expected_seq=4, now=NOW)) \
        == "fence_principal_unverified"
    # With an authority the forged attestation fails replay itself, for fences and ordinary appends alike.
    checked = TaskJournal(tmp_path, TASK, 1, fence_authority=Authority(), lock_wait_seconds=0.2)
    assert code(lambda: checked.append_fence(identity(STANDIN), identity(OWNER), FENCE, expected_seq=4, now=NOW)) \
        == "fence_attestation_invalid"
    assert code(lambda: checked.append("wip_checkpoint", scenario()[3][1], credential=OWNER, expected_seq=4,
                                       now=NOW)) == "fence_attestation_invalid"
    assert journal.path.read_bytes() == before   # not one byte was written
    assert journal.reconcile()["reasons"] == ["fence_principal_unverified:4"]


def test_the_write_boundary_itself_refuses_before_any_state_mutation(tmp_path):
    journal = build(tmp_path, scenario()[:3])
    before = _forge_fence(journal)
    state = journal.replay()
    assert state.unverified_fences == [4] and state.steps == {1: "started"}
    record = {"schema": journal_module.SCHEMA, "seq": 5, "prev_sha256": state.last_sha256, "kind": "step_committed",
              "owner": identity(STANDIN), "at_utc": "2026-09-30T18:16:00.000000Z", "step": 1, "head": HEAD}
    assert code(lambda: journal._write(state, record)) == "fence_principal_unverified"
    assert state.steps == {1: "started"} and state.open_step == 1 and journal.path.read_bytes() == before


def test_success_twin_a_verified_fence_journal_keeps_growing(tmp_path):
    journal = build(tmp_path, scenario()[:3], authority=Authority())
    journal.append_fence(identity(OWNER), identity(STANDIN), FENCE, expected_seq=3, now=NOW)
    size = journal.path.stat().st_size
    journal.append("step_committed", {"step": 1, "head": HEAD}, credential=STANDIN, expected_seq=4, now=NOW)
    assert journal.path.stat().st_size > size and journal.replay().unverified_fences == []

def test_the_runtime_layout_is_returned_never_created(tmp_path):
    root = journal_module.runtime_journal_root(tmp_path)
    assert root == tmp_path / "bridge_v2" / "task_journal" and not root.exists()
    journal = TaskJournal(root, TASK, 1)
    assert journal.path == root / (journal_module.task_key(TASK) + "-r1.jsonl") and not root.exists()
    assert code(lambda: journal_module.runtime_journal_root(Path("relative/root"))) == "runtime_root_not_absolute"


def test_the_compatibility_receipt_pins_the_contract_and_the_committed_module_blob():
    assert journal_module.compatibility_receipt(b"")["module_git_blob"] == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    committed = (ROOT / "tools" / "wd_task_journal.py").read_bytes().replace(b"\r\n", b"\n")
    receipt = journal_module.compatibility_receipt(committed)
    header = b"blob " + str(len(committed)).encode() + b"\0"
    assert receipt["module_git_blob"] == hashlib.sha1(header + committed, usedforsecurity=False).hexdigest()
    assert {key: receipt[key] for key in ("schema", "journal_schema", "root_relative", "owner_identity",
                                          "fence_evidence_keys", "max_journal_bytes")} == {
        "schema": "wd.task-journal-compat.v1", "journal_schema": "wd.task-journal.v1",
        "root_relative": "bridge_v2/task_journal", "owner_identity": ["agent", "generation", "token_sha256"],
        "fence_evidence_keys": list(FENCE_EVIDENCE_KEYS), "max_journal_bytes": journal_module.MAX_JOURNAL_BYTES}
    assert "trusted executor" in receipt["fence_writer"] and "fence_principal_unverified" in receipt["unverified_fence"]
    assert code(lambda: journal_module.compatibility_receipt("text")) == "module_bytes_invalid"