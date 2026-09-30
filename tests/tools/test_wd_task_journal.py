# SPDX-License-Identifier: BUSL-1.1
"""F27 task and operation journal (tools/wd_task_journal.py): fixture-only, default OFF.

Every journal lives under tmp_path. The crash-cut test truncates a complete journal after
every record (and tears the next one) and requires each cut to be reconciled or HELD.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import tools.wd_task_journal as journal_module
from tools.wd_task_journal import FENCE_EVIDENCE_KEYS, JournalError, TaskJournal

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 30, 17, 10, tzinfo=timezone.utc)
TASK = "codex-lead-1/bridge-v2-switch-continuity-release-20260930-1645"
OWNER = {"agent": "claude-rco-2", "generation": "wd-reboot-20260930T162350Z", "token": "t-original"}
STANDIN = {"agent": "claude-rco-1", "generation": "wd-reboot-20260930T162350Z", "token": "t-standin"}
BASE, HEAD, HEAD2 = "7" * 40, "8" * 40, "9" * 40
FENCE = {key: True for key in FENCE_EVIDENCE_KEYS}


def scenario() -> list[tuple[str, dict, dict]]:
    """A complete, valid history: two steps, a non-idempotent push and an idempotent message."""
    return [
        ("task_opened", {"task_id": TASK, "revision": 1, "base_commit": BASE}, OWNER),
        ("step_planned", {"step": 1, "title": "trusted inputs"}, OWNER),
        ("step_started", {"step": 1}, OWNER),
        ("wip_checkpoint", {"step": 1, "base": BASE, "head": HEAD, "artifact_sha256": "a" * 64, "tested_head": None,
                            "dirty": [{"path": "tools/x.py", "state": "added"}]}, OWNER),
        ("op_intent", {"op_id": "push-1", "op_kind": "git_push", "idempotency_key": "k-push-1", "idempotent": False}, OWNER),
        ("op_attempted", {"op_id": "push-1"}, OWNER),
        ("op_applied", {"op_id": "push-1", "receipt": "remote tip " + HEAD}, OWNER),
        ("op_verified", {"op_id": "push-1", "receipt": "ls-remote " + HEAD}, OWNER),
        ("step_committed", {"step": 1, "head": HEAD}, OWNER),
        ("step_planned", {"step": 2, "title": "ports"}, OWNER),
        ("step_started", {"step": 2}, OWNER),
        ("op_intent", {"op_id": "msg-1", "op_kind": "bridge_reply", "idempotency_key": "k-msg-1", "idempotent": True}, OWNER),
        ("op_attempted", {"op_id": "msg-1"}, OWNER),
        ("op_verified", {"op_id": "msg-1", "receipt": "event 17:05:19Z"}, OWNER),
        ("step_committed", {"step": 2, "head": HEAD2}, OWNER),
    ]


def build(root: Path, records) -> TaskJournal:
    journal = TaskJournal(root, TASK)
    for seq, (kind, fields, owner) in enumerate(records):
        journal.append(kind, fields, owner=owner, expected_seq=seq, now=NOW + timedelta(seconds=seq))
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
    assert verdict["owner"] == OWNER and verdict["seq"] == 15
    assert verdict["task"] == {"task_id": TASK, "revision": 1, "base_commit": BASE}


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
        cut = TaskJournal(tmp_path / ("cut-" + str(k)), TASK)
        cut.root.mkdir(parents=True)
        cut.path.write_bytes(b"\n".join(lines[:k]) + b"\n")
        verdict = cut.reconcile()
        assert verdict["verdict"] == expected[k], (k, verdict)
        if k in (6, 7):
            assert verdict["reasons"] == ["unknown_external_outcome:push-1"]
        if k < len(lines):  # the NEXT append torn mid-write
            cut.path.write_bytes(b"\n".join(lines[:k]) + b"\n" + lines[k][: len(lines[k]) // 2])
            assert cut.reconcile() == {"verdict": "hold", "reasons": ["journal_torn_tail"], "resume": None,
                                       "reverify": [], "owner": None}


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
    other = TaskJournal(tmp_path, "another/task")
    build(tmp_path / "src", scenario()).path.replace(other.path)
    assert other.reconcile()["reasons"] == ["journal_of_another_task"]
    assert TaskJournal(tmp_path / "none", TASK).reconcile()["reasons"] == ["journal_missing"]


def test_only_the_owner_writes_and_only_a_complete_fence_moves_ownership(tmp_path):
    journal = build(tmp_path, scenario()[:3])
    assert code(lambda: journal.append("step_planned", {"step": 2, "title": "x"}, owner=STANDIN, expected_seq=3,
                                       now=NOW)) == "writer_not_the_owner"
    incomplete = dict(FENCE, descendants_verified=False)
    assert code(lambda: journal.append("fence", {"previous_owner": OWNER, "new_owner": STANDIN,
                                                 "evidence": incomplete}, owner=STANDIN, expected_seq=3,
                                       now=NOW)) == "fence_incomplete"
    assert code(lambda: journal.append("fence", {"previous_owner": STANDIN, "new_owner": STANDIN, "evidence": FENCE},
                                       owner=STANDIN, expected_seq=3, now=NOW)) == "fence_same_token"
    assert code(lambda: journal.append("fence", {"previous_owner": OWNER, "new_owner": STANDIN, "evidence": FENCE},
                                       owner=OWNER, expected_seq=3, now=NOW)) == "fence_writer_invalid"
    journal.append("fence", {"previous_owner": OWNER, "new_owner": STANDIN, "evidence": FENCE}, owner=STANDIN,
                   expected_seq=3, now=NOW)
    # The fenced writer (an expired lease, old conversation metadata) can never append again.
    assert code(lambda: journal.append("step_committed", {"step": 1, "head": HEAD}, owner=OWNER, expected_seq=4,
                                       now=NOW)) == "writer_not_the_owner"
    journal.append("step_committed", {"step": 1, "head": HEAD}, owner=STANDIN, expected_seq=4, now=NOW)
    assert journal.reconcile()["owner"] == STANDIN


def test_hand_back_only_at_a_step_boundary_with_no_open_operation(tmp_path):
    journal = build(tmp_path, scenario()[:3])
    journal.append("fence", {"previous_owner": OWNER, "new_owner": STANDIN, "evidence": FENCE}, owner=STANDIN,
                   expected_seq=3, now=NOW)
    back = {"to_owner": OWNER, "at_step": 0, "evidence": "c" * 64}
    assert code(lambda: journal.append("handback", back, owner=STANDIN, expected_seq=4, now=NOW)) \
        == "handback_not_at_a_boundary"
    journal.append("step_committed", {"step": 1, "head": HEAD}, owner=STANDIN, expected_seq=4, now=NOW)
    assert code(lambda: journal.append("handback", back, owner=STANDIN, expected_seq=5, now=NOW)) \
        == "handback_step_mismatch"
    journal.append("handback", dict(back, at_step=1), owner=STANDIN, expected_seq=5, now=NOW)
    assert journal.reconcile()["owner"] == OWNER


def test_step_and_operation_rules(tmp_path):
    journal = build(tmp_path, scenario()[:5])  # push-1 intended, not attempted
    assert code(lambda: journal.append("step_committed", {"step": 1, "head": HEAD}, owner=OWNER, expected_seq=5,
                                       now=NOW)) == "step_closed_with_open_operation"
    assert code(lambda: journal.append("op_verified", {"op_id": "push-1", "receipt": "r"}, owner=OWNER,
                                       expected_seq=5, now=NOW)) == "op_transition_invalid"
    assert code(lambda: journal.append("step_started", {"step": 1}, owner=OWNER, expected_seq=5, now=NOW)) \
        == "step_order_invalid"
    assert code(lambda: journal.append("wip_checkpoint", {"step": 2, "base": BASE, "head": HEAD,
                                                          "artifact_sha256": "a" * 64, "tested_head": None,
                                                          "dirty": []}, owner=OWNER, expected_seq=5, now=NOW)) \
        == "wip_without_open_step"
    assert code(lambda: journal.append("op_intent", {"op_id": "push-1", "op_kind": "git_push", "idempotency_key": "k",
                                                     "idempotent": 0}, owner=OWNER, expected_seq=5, now=NOW)) \
        == "record_fields_invalid"
    # An intent that was never attempted left nothing outside the worktree: not a hold.
    assert journal.reconcile()["verdict"] == "continue"


def test_holds_block_until_released(tmp_path):
    journal = build(tmp_path, scenario())
    journal.append("hold", {"reason": "operator freeze"}, owner=OWNER, expected_seq=15, now=NOW)
    verdict = journal.reconcile()
    assert verdict["verdict"] == "hold" and verdict["reasons"] == ["unreleased_hold:16"]
    journal.append("hold_released", {"hold_seq": 16, "evidence": "operator release 17:20Z"}, owner=OWNER,
                   expected_seq=16, now=NOW)
    assert journal.reconcile()["verdict"] == "continue"


def test_compare_and_swap_and_the_lock_refuse(tmp_path):
    journal = build(tmp_path, scenario()[:2])
    assert code(lambda: journal.append("step_started", {"step": 1}, owner=OWNER, expected_seq=1, now=NOW)) \
        == "journal_cas_conflict"
    with journal_module._exclusive_lock(journal.lock_path):
        assert code(lambda: journal.append("step_started", {"step": 1}, owner=OWNER, expected_seq=2, now=NOW)) \
            == "journal_locked"
    assert journal.replay().seq == 2
    assert code(lambda: journal.append("step_started", {"step": 1}, owner=OWNER, expected_seq=2,
                                       now=datetime(2026, 9, 30, 17, 10))) == "time_unknown"


def test_the_journal_must_open_with_its_task_and_never_reopen(tmp_path):
    journal = TaskJournal(tmp_path, TASK)
    assert code(lambda: journal.append("step_planned", {"step": 1, "title": "x"}, owner=OWNER, expected_seq=0,
                                       now=NOW)) == "journal_must_open_with_the_task"
    build(tmp_path, scenario()[:1])
    assert code(lambda: journal.append("task_opened", {"task_id": TASK, "revision": 2, "base_commit": BASE},
                                       owner=OWNER, expected_seq=1, now=NOW)) == "task_reopened"


def test_task_keys_are_collision_free_and_filesystem_safe():
    first, second = journal_module.task_key("a/b"), journal_module.task_key("a:b")
    assert first != second and first.startswith("a_b-") and second.startswith("a_b-")
    assert code(lambda: journal_module.task_key("../escape")) == "task_id_invalid"


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


def test_record_fields_never_collide_with_the_envelope():
    for kind, fields in journal_module.FIELDS.items():
        assert not (fields & journal_module.ENVELOPE), kind
