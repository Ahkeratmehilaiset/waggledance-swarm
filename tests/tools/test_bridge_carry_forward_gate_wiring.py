# SPDX-License-Identifier: BUSL-1.1
"""Opt-in carry-forward wiring of the merge gate (#1862, after the seven-condition proof).

``evaluate_auto_merge_gate(carry_forward=True)`` may let approvals given at a reviewed head count at
its content-identical base update. The old head is the new head's first parent. Its approvals are
evaluated at the old head with the same functions as today, its CI comes from
``pr_status["carry_forward_old_head_checks"]``, and tools/bridge_carry_forward_proof.py decides.
When it carries, only the two approval blockers (head-exact RCO_PASS and bridge consensus) are
lifted; CI, the peer gate and veto scan, mergeable, draft, path, diff, base, receipt and rate stay
computed at the new head. With the flag off (the default) the report is unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import pytest

from tools import bridge_rule12_review_eligibility as r12
from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.idle_consensus_auto_merge import (
    REVIEW_POLICY_LEGACY,
    REVIEW_POLICY_RULE12,
    AutoMergeGateError,
    evaluate_auto_merge_gate,
)

TASK = "fable-5/carry-forward-wiring-fixture"
NOW = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
DATE = "2026-10-12"
PR_PATH = "tools/idle_daily_summary.py"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}
RCO_BLOCKER = "missing exact-head RCO_PASS from recognized non-author RCO"
CONSENSUS_BLOCKER = "bridge consensus incomplete"
REPOSITORY_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                  "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
                  "GIT_REPLACE_REF_BASE")


# --- a real repository: reviewed head, main moved, content-identical update ----------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          check=True).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        _git(repo, "add", "--", name)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _update(repo: Path, head: str, main: str, tree: str | None = None) -> str:
    """The plan's content-identical update: merge-tree tree, the reviewed head as first parent."""
    if tree is None:
        base = _git(repo, "merge-base", head, main)
        tree = _git(repo, "merge-tree", "--write-tree", "--no-messages", f"--merge-base={base}", main,
                    head).split()[0]
    return _git(repo, "commit-tree", tree, "-p", head, "-p", main, "-m", "update")


class Heads:
    def __init__(self, repo: Path, base: str, old: str, main: str, new: str) -> None:
        self.repo, self.base, self.old, self.main, self.new = repo, base, old, main, new


@pytest.fixture
def git_env(tmp_path, monkeypatch):
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "wd-test")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "wd-test@example.invalid")
    for name in REPOSITORY_ENV:
        monkeypatch.delenv(name, raising=False)


def _make_heads(tmp_path: Path, pr_files: dict[str, str]) -> Heads:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "core.autocrlf", "false")
    base = _commit(repo, "base", {PR_PATH: "def helper():\n    return 0\n", "docs/notes.txt": "notes\n"})
    _git(repo, "checkout", "-q", "-b", "pr", base)
    old = _commit(repo, "pr change", pr_files)
    _git(repo, "checkout", "-q", "main")
    main = _commit(repo, "main moves", {"docs/other.txt": "other\n"})
    return Heads(repo, base, old, main, _update(repo, old, main))


@pytest.fixture
def heads(tmp_path, git_env) -> Heads:
    return _make_heads(tmp_path, {PR_PATH: "def helper():\n    return 1\n"})


# --- gate inputs -------------------------------------------------------------------------------


def _check(name: str = "unified", conclusion: str = "") -> dict:
    return {"name": name, "state": "success" if not conclusion else "", "status": "", "conclusion": conclusion}


def _status(heads: Heads, head: str | None = None, **overrides) -> dict:
    head = heads.new if head is None else head
    author = {"name": "fable-5", "email": "fable-5@users.noreply.github.com", "login": ""}
    material = github_pr_git_identity_evidence(
        {
            "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
            "commits": [{"oid": heads.old, "authors": [author]}, {"oid": head, "authors": [author]}],
        },
        expected_head_sha=head,
    )
    identities = material.pop("identities")
    status = {
        "pr_number": 1862,
        "head_sha": head,
        "head_ref": TASK,
        "base_sha": heads.main,
        "base_ref": "main",
        "base_tip_sha": heads.main,
        "title": "carry-forward wiring fixture",
        "mergeable": "clean",
        "state": "OPEN",
        "is_draft": False,
        "updated_at": "2026-10-12T17:00:00Z",
        "author_agent": "fable-5",
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": [PR_PATH],
        "diff_text": "-    return 0\n+    return 1\n",
        "checks": [_check()],
        "carry_forward_old_head_checks": [_check()],
        "git_identities": identities,
        "git_identity_evidence": material,
    }
    status.update(overrides)
    return status


def _event(agent: str, type_: str, status: str, ts: str, head: str | None) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": TASK,
        "message": f"{status} exact head {head}" if head is not None else "",
        "payload": {"pr": 1862, "head": head} if head is not None else {"pr": 1862},
        "agent_uuid": AGENT_UUIDS[agent],
    }


def _claim() -> dict:
    event = _event("fable-5", "claim", "active", "2026-10-12T16:00:00Z", None)
    event["payload"] = {}
    event["write_scope"] = ["*"]
    return event


def _rule12_events(head: str) -> list[dict]:
    return [
        _claim(),
        _event("codex-lead-1", "decision", "build_consensus_pass", "2026-10-12T17:10:00Z", head),
        _event("claude-rco-1", "decision", "rco_pass", "2026-10-12T17:20:00Z", head),
        _event("claude-rco-2", "decision", "rco_pass", "2026-10-12T17:25:00Z", head),
    ]


def _legacy_events(head: str) -> list[dict]:
    return _rule12_events(head) + [
        _event("codex-tools-1", "decision", "build_consensus_pass", "2026-10-12T17:12:00Z", head),
    ]


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(event, sort_keys=True) for event in events), encoding="utf-8")
    return path


def _gate(tmp_path: Path, heads: Heads, events: list[dict] | None = None, *, pr_status: dict | None = None,
          **overrides) -> dict:
    kwargs = dict(
        pr_status=_status(heads) if pr_status is None else pr_status,
        expected_head=(pr_status or {}).get("head_sha", heads.new),
        expected_base_sha=heads.main,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _rule12_events(heads.old) if events is None else events),
        bridge_task_id=TASK,
        utc_date=DATE,
        require_bridge_consensus=True,
        review_policy=REVIEW_POLICY_RULE12,
        now_utc=NOW,
        carry_forward=True,
        _carry_forward_repo=heads.repo,
    )
    kwargs.update(overrides)
    return evaluate_auto_merge_gate(**kwargs)


def _approval_blockers(report: dict) -> list[str]:
    return [r for r in report["reasons"] if r.startswith(RCO_BLOCKER) or r.startswith(CONSENSUS_BLOCKER)]


# --- off by default --------------------------------------------------------------------------------


def test_off_by_default_the_report_is_unchanged(tmp_path, heads):
    kwargs = dict(
        pr_status=_status(heads), expected_head=heads.new, expected_base_sha=heads.main,
        consensus_proposal_id=TASK, receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _rule12_events(heads.old)), bridge_task_id=TASK, utc_date=DATE,
        require_bridge_consensus=True, review_policy=REVIEW_POLICY_RULE12, now_utc=NOW,
    )

    omitted = evaluate_auto_merge_gate(**kwargs)
    explicit = evaluate_auto_merge_gate(**kwargs, carry_forward=False)

    assert omitted == explicit
    assert "carry_forward" not in omitted
    assert omitted["ok"] is False
    assert any(r.startswith(RCO_BLOCKER) for r in omitted["reasons"]), omitted["reasons"]
    assert any(r.startswith(CONSENSUS_BLOCKER) for r in omitted["reasons"]), omitted["reasons"]


@pytest.mark.parametrize("value", [1, "true", None])
def test_the_flag_must_be_a_real_bool(tmp_path, heads, value):
    with pytest.raises(AutoMergeGateError):
        _gate(tmp_path, heads, carry_forward=value)


def test_the_repository_seam_needs_the_flag(tmp_path, heads):
    with pytest.raises(AutoMergeGateError):
        _gate(tmp_path, heads, carry_forward=False)


def test_carry_forward_is_evaluation_only_until_the_merge_receipt_records_it(tmp_path, heads):
    # The apply path re-snapshots the PR, and the merge receipt has no carry-forward fields yet.
    with pytest.raises(AutoMergeGateError, match="carry_forward"):
        _gate(tmp_path, heads, apply=True)


# --- condition 1 + 7: the old head's direct verdict carries, everything else stays at the new head ---


@pytest.mark.parametrize("policy,events", [(REVIEW_POLICY_RULE12, _rule12_events),
                                           (REVIEW_POLICY_LEGACY, _legacy_events)])
def test_old_head_approvals_carry_to_a_content_identical_update(tmp_path, heads, policy, events):
    report = _gate(tmp_path, heads, events(heads.old), review_policy=policy,
                   now_utc=NOW if policy == REVIEW_POLICY_RULE12 else None)

    assert report["decision"] == "auto_merge_plan_ready", report["reasons"]
    carry = report["carry_forward"]
    assert (carry["enabled"], carry["needed"], carry["applied"]) == (True, True, True), carry
    assert carry["old_head"] == heads.old
    assert any(b.startswith(RCO_BLOCKER) for b in carry["lifted_blockers"]), carry
    assert any(b.startswith(CONSENSUS_BLOCKER) for b in carry["lifted_blockers"]), carry
    receipt = carry["receipt"]
    assert receipt["schema"] == "wd.carry-forward-receipt.v1" and receipt["carried"] is True
    assert (receipt["old_head"], receipt["new_head"], receipt["main_head"]) == (heads.old, heads.new, heads.main)
    assert receipt["patch_id_old"] and receipt["patch_id_old"] == receipt["patch_id_new"]
    assert receipt["old_head_verdict"]["head"] == heads.old
    assert receipt["old_head_verdict"]["approvals_carried"] is False
    assert (receipt["new_head_ci_all_green"], receipt["new_head_veto_clear"]) == (True, True)
    # The report still says honestly that the new head itself has no direct approvals.
    assert report["bridge_consensus"]["ok"] is False
    assert carry["old_head_consensus"]["ok"] is True


def test_head_exact_approvals_need_no_carry(tmp_path, heads):
    report = _gate(tmp_path, heads, _rule12_events(heads.new))

    assert report["decision"] == "auto_merge_plan_ready", report["reasons"]
    carry = report["carry_forward"]
    assert (carry["enabled"], carry["needed"], carry["applied"]) == (True, False, False)
    assert carry["receipt"] is None and carry["lifted_blockers"] == []


def test_new_head_ci_must_pass_again(tmp_path, heads):
    status = _status(heads, checks=[_check(conclusion="failure")])

    report = _gate(tmp_path, heads, pr_status=status)

    assert report["ok"] is False
    assert any(r.startswith("status checks not green") for r in report["reasons"])
    assert report["carry_forward"]["applied"] is False
    assert _approval_blockers(report)


@pytest.mark.parametrize("old_checks", [[], [_check(conclusion="failure")], None])
def test_old_head_ci_must_have_passed(tmp_path, heads, old_checks):
    status = _status(heads)
    if old_checks is None:
        del status["carry_forward_old_head_checks"]
    else:
        status["carry_forward_old_head_checks"] = old_checks

    report = _gate(tmp_path, heads, pr_status=status)

    assert report["ok"] is False
    carry = report["carry_forward"]
    assert carry["applied"] is False
    assert any("ci_all_green" in reason for reason in carry["reasons"]), carry
    assert _approval_blockers(report)


def test_an_rco_veto_after_the_pass_blocks_the_carry(tmp_path, heads):
    events = _rule12_events(heads.old) + [
        _event("claude-rco-2", "finding", "changes_requested", "2026-10-12T17:40:00Z", heads.new),
    ]

    report = _gate(tmp_path, heads, events)

    assert report["ok"] is False
    assert report["carry_forward"]["applied"] is False
    assert _approval_blockers(report)


def test_approvals_at_neither_head_never_carry(tmp_path, heads):
    report = _gate(tmp_path, heads, [_claim()])

    assert report["ok"] is False
    assert report["carry_forward"]["applied"] is False
    assert _approval_blockers(report)


# --- conditions 2-6 come from the proof; the gate never lifts on a failed proof --------------------


def test_a_hand_edited_update_never_carries(tmp_path, heads):
    _git(heads.repo, "checkout", "-q", "--detach", heads.new)
    edited = _commit(heads.repo, "edit", {"docs/extra.txt": "unreviewed\n"})
    tree = _git(heads.repo, "rev-parse", f"{edited}^{{tree}}")
    hand_edited = _update(heads.repo, heads.old, heads.main, tree=tree)

    report = _gate(tmp_path, heads, pr_status=_status(heads, hand_edited))

    assert report["ok"] is False
    carry = report["carry_forward"]
    assert carry["applied"] is False
    assert carry["receipt"]["carried"] is False
    assert carry["receipt"]["proof_reason"] == "tree_mismatch"
    assert _approval_blockers(report)


def test_an_extra_commit_on_top_needs_new_votes(tmp_path, heads):
    _git(heads.repo, "checkout", "-q", "--detach", heads.new)
    extra = _commit(heads.repo, "extra", {PR_PATH: "def helper():\n    return 2\n"})

    report = _gate(tmp_path, heads, pr_status=_status(heads, extra))

    assert report["ok"] is False
    assert report["carry_forward"]["applied"] is False
    assert report["carry_forward"]["old_head"] == heads.new
    assert _approval_blockers(report)


def test_carried_approvals_never_carry_twice(tmp_path, heads):
    _git(heads.repo, "checkout", "-q", "main")
    main2 = _commit(heads.repo, "main moves again", {"docs/third.txt": "third\n"})
    second = _update(heads.repo, heads.new, main2)

    report = _gate(tmp_path, heads, pr_status=_status(heads, second, base_sha=main2, base_tip_sha=main2),
                   expected_base_sha=main2)

    assert report["ok"] is False
    carry = report["carry_forward"]
    assert carry["applied"] is False and carry["old_head"] == heads.new
    assert _approval_blockers(report)


def test_a_gate_or_governance_change_never_carries(tmp_path, git_env):
    gate_path = "tools/check_rco_pass_present.py"
    heads = _make_heads(tmp_path, {gate_path: "# changed\n"})
    status = _status(heads, changed_paths=[gate_path], diff_text="+# changed\n")

    report = _gate(tmp_path, heads, pr_status=status)

    assert report["ok"] is False
    carry = report["carry_forward"]
    assert carry["applied"] is False
    assert carry["receipt"]["proof_reason"] == "gate_or_governance_path"


def test_the_carry_needs_the_expected_main(tmp_path, heads):
    report = _gate(tmp_path, heads, expected_base_sha="")

    assert report["ok"] is False
    carry = report["carry_forward"]
    assert carry["applied"] is False
    assert any("expected_base_sha" in reason for reason in carry["reasons"]), carry
    assert _approval_blockers(report)


# --- only the two approval blockers are ever lifted --------------------------------------------------


@pytest.mark.parametrize("override,blocker", [
    ({"is_draft": True}, "PR must not be a draft"),
    ({"mergeable": "dirty"}, "mergeable state is not clean"),
])
def test_a_carry_never_lifts_any_other_blocker(tmp_path, heads, override, blocker):
    report = _gate(tmp_path, heads, pr_status=_status(heads, **override))

    assert report["ok"] is False
    assert report["carry_forward"]["applied"] is True
    assert any(r.startswith(blocker) for r in report["reasons"]), report["reasons"]
    assert _approval_blockers(report) == []


# --- each input of the old head's verdict decides on its own (mutant survivors at 699389aa) ----------


def _carry_reasons(report: dict, field: str) -> list[str]:
    return [reason for reason in report["carry_forward"]["reasons"] if field in reason]


def test_a_peer_block_at_the_new_head_stops_the_carry(tmp_path, heads):
    events = _rule12_events(heads.old) + [
        _event("codex-tools-1", "decision", "changes_requested", "2026-10-12T17:40:00Z", heads.new),
    ]

    report = _gate(tmp_path, heads, events)

    carry = report["carry_forward"]
    assert carry["old_head_rco_pass_gate"]["ok"] is True and carry["old_head_consensus"]["ok"] is True, carry
    assert carry["applied"] is False
    assert _carry_reasons(report, "veto_clear"), carry
    assert _approval_blockers(report)


def test_a_superseded_rco_veto_still_stops_the_carry(tmp_path, heads):
    # rco-2 vetoes, then passes. The peer gate and the RCO_PASS gate take the later pass; the
    # recognized-RCO veto scan honours no retraction, so the carry stays off (a spurious hold is safe).
    events = _rule12_events(heads.old)
    events.insert(3, _event("claude-rco-2", "finding", "changes_requested", "2026-10-12T17:15:00Z", heads.old))
    assert [event["status"] for event in events[3:]] == ["changes_requested", "rco_pass"]

    report = _gate(tmp_path, heads, events)

    carry = report["carry_forward"]
    assert report["bridge_peer_gate"]["clear_to_merge"] is True, report["bridge_peer_gate"]
    assert carry["old_head_rco_pass_gate"]["ok"] is True and carry["old_head_consensus"]["ok"] is True, carry
    assert carry["applied"] is False
    assert _carry_reasons(report, "veto_clear"), carry
    assert _approval_blockers(report)


def test_rco_passes_without_the_build_vote_never_carry(tmp_path, heads):
    events = [event for event in _rule12_events(heads.old) if event["agent"] != "codex-lead-1"]

    report = _gate(tmp_path, heads, events)

    carry = report["carry_forward"]
    assert carry["old_head_rco_pass_gate"]["ok"] is True and carry["old_head_consensus"]["ok"] is False, carry
    assert carry["applied"] is False
    assert _carry_reasons(report, "consensus_ok"), carry
    assert _approval_blockers(report)


def test_a_pool_held_rco_slot_at_the_old_head_never_carries(tmp_path, heads):
    # Direct approvals only: the rule12 review may accept a pool lane in the RCO slot, the carry does not.
    pool_pass = _event("codex-tools-1", "decision", "rco_pass", "2026-10-12T17:12:00Z", heads.old)
    pool_pass["payload"][r12.POOL_ATTESTATION_KEY] = r12.POOL_ATTESTATION_VALUE
    events = [
        _claim(),
        _event("claude-rco-1", "message", "rco_recused", "2026-10-12T17:00:00Z", heads.old),
        _event("claude-rco-2", "message", "rco_recused", "2026-10-12T17:01:00Z", heads.old),
        _event("codex-lead-1", "decision", "build_consensus_pass", "2026-10-12T17:10:00Z", heads.old),
        pool_pass,
    ]

    report = _gate(tmp_path, heads, events)

    carry = report["carry_forward"]
    assert carry["old_head_consensus"]["ok"] is True and carry["old_head_rco_pass_gate"]["ok"] is False, carry
    assert carry["applied"] is False
    assert _carry_reasons(report, "consensus_ok"), carry
    assert _approval_blockers(report)


def test_a_charter_refusal_never_carries(tmp_path, heads):
    status = _status(heads, diff_text="-    return 0\n+    gate_skip=True\n")

    report = _gate(tmp_path, heads, pr_status=status)

    carry = report["carry_forward"]
    assert any(r.startswith("diff gate failed") for r in report["reasons"]), report["reasons"]
    assert carry["old_head_rco_pass_gate"]["ok"] is True and carry["old_head_consensus"]["ok"] is True, carry
    assert carry["applied"] is False
    assert _carry_reasons(report, "charter_ok"), carry
    assert _approval_blockers(report)
