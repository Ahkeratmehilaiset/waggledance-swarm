"""RED-first integration tests for Rule 9a approval carry-forward through the REAL merge callers.

Requested by codex-lead-1 at 2026-11-09T07:51:27Z (lead-carry-real-callers-fable-20261109-v1), building on the
consumer map at main 23564ad4 and the adversarial corpus at 4d7eb1c7. Every test drives the real callers:
tools.pr_status_snapshot.build_pr_status_snapshot, tools.idle_consensus_auto_merge.evaluate_auto_merge_gate and
tools.merge_with_bridge_receipt.merge_with_bridge_receipt. The only seams are the ones those callers already
publish: the gh runner (no network), the accepted-queue checker of evaluate_auto_merge_gate, and one timing hook
that appends a bridge event before the apply snapshot and then calls the real snapshot builder unchanged. No gate
function is patched and no approval is faked.

Two failure classes (fixture real_callers_cases.json, failure_classes):

* "RED: missing API: ..." -- the opt-in carry parameter of a real caller does not exist yet. These failures are
  expected until Tools wires carry-forward; they never count as verified behaviour.
* any other failure -- the real caller behaved differently from the recorded expectation.

The GREEN controls verify today's exact-head behaviour through the same harness, so a RED is never a broken
harness, and they must stay green after wiring: without the opt-in, the merge path and the non-opt-in consumers
(bridge_loop_tick, check_promotion_eligible) remain exact-head.
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import tools.merge_with_bridge_receipt as merge_tool
from tools.check_rco_pass_present import check_rco_pass_present
from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate, verify_bridge_consensus
from tools.merge_with_bridge_receipt import merge_with_bridge_receipt
from tools.pr_status_snapshot import build_pr_status_snapshot

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "real_callers_cases.json")
                   .read_bytes())
REGISTRY = json.loads((ROOT / "configs" / "bridge_identity_registry.json").read_bytes())["identities"]
GIT = shutil.which("git")
REPO_NAME = "example/repo"
PR = 9101
TASK = "claude-rco-2/real-callers-carry-pr-9101"
AUTHOR = "claude-rco-2"
CHANGED_PATH = "tools/agent_next_task.py"
MERGE_SHA = "2222222222222222222222222222222222222222"
NOW = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
GATE = "tools.idle_consensus_auto_merge:evaluate_auto_merge_gate"
MERGE = "tools.merge_with_bridge_receipt:merge_with_bridge_receipt"
DESCRIPTIVE_TASK = "rco1-review-real-callers-carry-descriptive"

pytestmark = pytest.mark.skipif(GIT is None, reason="git executable required for real heads")


# --------------------------------------------------------------------------------------------------------------
# Real git heads (own minimal builders, independent of the corpus module; fixed identities and clock)
# --------------------------------------------------------------------------------------------------------------
class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.clock = 0
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        self.clock += 1
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        stamp = "2026-11-09T03:%02d:%02d+00:00" % (self.clock // 60 % 60, self.clock % 60)
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0", LC_ALL="C",
                   GIT_AUTHOR_NAME="corpus", GIT_AUTHOR_EMAIL="corpus@example.invalid",
                   GIT_COMMITTER_NAME="corpus", GIT_COMMITTER_EMAIL="corpus@example.invalid",
                   GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        result = subprocess.run([GIT, *args], cwd=self.path, capture_output=True, env=env)
        assert result.returncode == 0, (args, result.stderr.decode("utf-8", "replace"))
        return result.stdout.decode().strip()

    def commit(self, files, message):
        for name, raw in files.items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            self.git("add", "--", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


APP = b"".join(b"line %02d\n" % i for i in range(1, 41))


def _legit_rebase(repo):
    main0 = repo.commit({"README.md": b"base\n", "src/app.txt": APP}, "base")
    repo.git("checkout", "-q", "-b", "pr", main0)
    old = repo.commit({"src/feature.txt": b"feature one\n"}, "pr: feature")
    repo.git("checkout", "-q", "main")
    main1 = repo.commit({"docs/notes.txt": b"unrelated\n"}, "main advances")
    repo.git("checkout", "-q", "-b", "pr-rebased", main1)
    new = repo.commit({"src/feature.txt": b"feature one\n"}, "pr: feature")
    return old, main0, new, main1


def _content_changing_rebase(repo):
    main0 = repo.commit({"README.md": b"base\n", "src/app.txt": APP}, "base")
    repo.git("checkout", "-q", "-b", "pr", main0)
    old = repo.commit({"src/app.txt": APP.replace(b"line 10\n", b"line 10 pr\n")}, "pr: edit")
    repo.git("checkout", "-q", "main")
    main1 = repo.commit({"src/app.txt": APP.replace(b"line 10\n", b"line 10 main\n")}, "main: edit")
    repo.git("checkout", "-q", "-b", "pr-rebased", main1)
    new = repo.commit({"src/app.txt": APP.replace(b"line 10\n", b"line 10 main pr\n")}, "pr: edit")
    return old, main0, new, main1


HISTORIES = {"legit_rebase": _legit_rebase, "content_changing_rebase": _content_changing_rebase}


@pytest.fixture(scope="module")
def histories(tmp_path_factory):
    built = {}
    for name, builder in HISTORIES.items():
        repo = Repo(tmp_path_factory.mktemp(name) / "repo")
        old, old_base, new, new_base = builder(repo)
        assert len({old, new, old_base, new_base}) == 4
        built[name] = SimpleNamespace(repo=repo, old=old, old_base=old_base, new=new, new_base=new_base)
    return built


def _physical_git() -> Path:
    executable = Path(GIT).resolve()
    if executable.suffix.lower() in {".cmd", ".bat"}:  # Windows shim: resolve the physical git.exe
        exec_path = Path(subprocess.run([GIT, "--exec-path"], capture_output=True, text=True).stdout.strip())
        for candidate in (exec_path.parents[2] / "bin" / "git.exe", exec_path / "git.exe"):
            if candidate.is_file():
                return candidate
    return executable


@pytest.fixture(autouse=True)
def anchored_fleet(tmp_path, monkeypatch):
    """Wholly synthetic, test-owned generation pinning the physical git (same model as the corpus fixture), so a
    wired carry proof runs on an anchored git; no installed pin is read or changed."""
    executable = _physical_git()
    root = tmp_path / "anchored-bundle" / hashlib.sha1(b"fable-5 real callers generation").hexdigest()
    root.mkdir(parents=True)
    fleet = json.dumps({"schema_version": 2, "git_executable": str(executable)}).encode("ascii")
    (root / "wd-fleet.json").write_bytes(fleet)
    manifest = json.dumps({"source_commit": root.name,
                           "files": {"wd-fleet.json": hashlib.sha256(fleet).hexdigest()}}).encode("ascii")
    (root / "deployment-manifest.json").write_bytes(manifest)
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", str(root))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", hashlib.sha256(manifest).hexdigest())


# --------------------------------------------------------------------------------------------------------------
# Bridge events (canonical registry identities, structured head binding, append order = list order)
# --------------------------------------------------------------------------------------------------------------
def _event(agent, typ, status, head, minute, *, task=TASK, message=None):
    return {"ts_utc": "2026-11-09T07:%02d:00Z" % minute, "agent": agent, "agent_uuid": REGISTRY[agent],
            "type": typ, "status": status, "task_id": task,
            "message": f"{status} at head {head}" if message is None else message,
            "payload": {"head": head, "pr": PR}}


def _claim():
    event = _event(AUTHOR, "claim", "active", None, 0, message="claim for the PR task")
    event["payload"] = {"pr": PR}
    event["write_scope"] = [CHANGED_PATH]
    return event


def _approvals(head):
    return [_claim(),
            _event("codex-lead-1", "decision", "build_consensus_pass", head, 1),
            _event("codex-tools-1", "decision", "build_consensus_pass", head, 2),
            _event("claude-rco-1", "decision", "rco_pass", head, 3)]


def _objection(head, *, task, minute=4, with_pr=False):
    """A recognized-RCO objection bound only by its structured head (no SHA in the message). Without a pr number in
    the payload (map bypass 3 shape) neither shared check matches it by PR scope; with one, the consensus block scope
    matches it by PR (probe_real_callers_v2/v3 in the audit dir)."""
    event = _event("claude-rco-1", "finding", "changes_requested", head, minute, task=task,
                   message="objection at the bound head")
    if not with_pr:
        event["payload"] = {"head": head}
    return event


OBJECTION_SHAPES = {"no_pr": False, "with_pr": True}


def _events_file(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in events), encoding="utf-8")
    return path


def _append(path, event):
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + json.dumps(event, sort_keys=True))


# --------------------------------------------------------------------------------------------------------------
# gh stub runner (the published seam of the snapshot builder and the merge tool; no network)
# --------------------------------------------------------------------------------------------------------------
GREEN = [{"name": "unified", "conclusion": "SUCCESS", "status": "COMPLETED"},
         {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"}]
PENDING = [{"name": "unified", "conclusion": "", "status": "IN_PROGRESS"},
           {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"}]
FAILED = [{"name": "unified", "conclusion": "FAILURE", "status": "COMPLETED"},
          {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"}]


def _runner(*, head, base, checks=GREEN):
    calls = []
    payload = {
        "number": PR, "title": "feat(tools): carry test", "headRefOid": head, "headRefName": TASK,
        "baseRefOid": base, "baseRefName": "main", "mergeable": "MERGEABLE", "state": "OPEN", "isDraft": False,
        "url": f"https://github.com/{REPO_NAME}/pull/{PR}", "reviewDecision": "", "updatedAt": "2026-11-09T07:30:00Z",
        "changedFiles": 1, "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
        "commits": [{"oid": head, "authors": [{"name": "Jani", "email": "jani@jkhservice.fi", "login": ""}]}],
        "statusCheckRollup": checks,
    }

    def run(command):
        call = list(command)
        calls.append(call)
        if call[:3] == ["gh", "pr", "view"]:
            if "number,state,mergeCommit,headRefOid,headRefName,baseRefName" in call:
                return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                    {"number": PR, "state": "MERGED", "headRefOid": head, "headRefName": TASK,
                     "baseRefName": "main", "mergeCommit": {"oid": MERGE_SHA}}))
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(payload))
        if call[:2] == ["gh", "api"] and "/git/ref/heads/" in call[4]:
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                {"ref": "refs/heads/main", "object": {"type": "commit", "sha": base}}))
        if call[:2] == ["gh", "api"]:
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(
                [{"filename": CHANGED_PATH, "status": "modified"}]))
        if call[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stderr="",
                                   stdout=f"diff --git a/{CHANGED_PATH} b/{CHANGED_PATH}\n")
        if call[:3] == ["gh", "pr", "merge"]:
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected command: {call}")

    return calls, run


def _merge_calls(calls):
    return [c for c in calls if c[:3] == ["gh", "pr", "merge"]]


def _snapshot(*, head, base, checks=GREEN):
    """pr_status built by the real snapshot builder, exactly as the merge tool builds it at apply."""
    _, run = _runner(head=head, base=base, checks=checks)
    return build_pr_status_snapshot(pr_number=PR, repo=REPO_NAME, receipt_verified=True,
                                    expected_base_sha=base, runner=run)


def _queue(complete=True):
    def checker(**_kwargs):
        return {"ok": True, "complete": complete, "errors": [],
                "decision": "accepted_queue_complete" if complete else "accepted_queue_incomplete"}
    return checker


def _gate(tmp_path, *, pr_status, expected_head, base, events, queue=True, **extra):
    return evaluate_auto_merge_gate(
        pr_status=pr_status, expected_head=expected_head, expected_base_sha=base,
        consensus_proposal_id=TASK, receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_file(tmp_path, events), bridge_task_id=TASK, repo=REPO_NAME,
        from_agent="codex-lead-1", require_bridge_consensus=True, accepted_queue_checker=_queue(queue), **extra)


def _merge(tmp_path, *, events_path, runner, apply, expected_head, expected_base_sha, **extra):
    return merge_with_bridge_receipt(
        pr_number=PR, repo=REPO_NAME, events_path=events_path, out_dir=tmp_path / "out",
        expected_head=expected_head, expected_base_sha=expected_base_sha, consensus_proposal_id=TASK,
        from_agent="codex-lead-1", bridge_task_id=TASK, apply=apply, now_utc=NOW, runner=runner, **extra)


READY = {"auto_merge_plan_ready", "auto_merged", "merge_receipt_ready", "merge_executed_after_receipt"}


def _refused(report):
    return report.get("ok") is not True and report.get("decision") not in READY


def _reasons(report):
    return json.dumps({k: report.get(k) for k in ("decision", "reasons", "errors", "blockers")}, default=str)


# --------------------------------------------------------------------------------------------------------------
# Opt-in adapter: the single adaptation point once Tools publishes the wired parameter
# --------------------------------------------------------------------------------------------------------------
def _require_opt_in(key, fn):
    name = CASES["opt_in"]["parameter"][key]
    if name not in inspect.signature(fn).parameters:
        pytest.fail(f"RED: missing API: {key} has no opt-in carry parameter {name!r} yet (Tools wiring not landed; "
                    f"fixture real_callers_cases.json opt_in.parameter names it)")
    return name


def _carry_opt_in(key, fn, history):
    name = _require_opt_in(key, fn)
    return {name: {"approved_head": history.old, "approved_base_sha": history.old_base,
                   "repo_root": Path(history.repo.path)}}


def _carry_records(node):
    """Every mapping in a report or receipt that records approved_head and proposed_head."""
    if isinstance(node, dict):
        if "approved_head" in node and "proposed_head" in node:
            yield node
        for value in node.values():
            yield from _carry_records(value)
    elif isinstance(node, list):
        for value in node:
            yield from _carry_records(value)


def _assert_carry_record(node, history):
    records = [r for r in _carry_records(node)
               if r["approved_head"] == history.old and r["proposed_head"] == history.new]
    assert records, "no carry record naming the approved and proposed heads"
    missing = [f for f in CASES["carry_record_fields"] if f not in records[0]]
    assert not missing, missing
    assert records[0]["approved_diff_sha256"] == records[0]["proposed_diff_sha256"]
    return records[0]


def test_every_fixture_case_has_a_carry_test():
    tests = [name for name in globals() if name.startswith("test_carry_")]
    uncovered = [case for case in CASES["cases"] if not any(name.startswith("test_carry_" + case) for name in tests)]
    assert uncovered == []


# ==============================================================================================================
# GREEN controls: today's exact-head behaviour through the same real callers (must stay green after wiring)
# ==============================================================================================================
def test_control_exact_head_approvals_make_the_real_gate_ready(tmp_path, histories):
    h = histories["legit_rebase"]
    report = _gate(tmp_path, pr_status=_snapshot(head=h.new, base=h.new_base), expected_head=h.new,
                   base=h.new_base, events=_approvals(h.new))
    assert report["decision"] == "auto_merge_plan_ready", _reasons(report)
    assert report["rco_pass_gate"]["ok"] is True
    assert report["bridge_consensus"]["ok"] is True


def test_control_without_opt_in_old_head_approvals_stay_exact_head_refused(tmp_path, histories):
    h = histories["legit_rebase"]
    report = _gate(tmp_path, pr_status=_snapshot(head=h.new, base=h.new_base), expected_head=h.new,
                   base=h.new_base, events=_approvals(h.old))
    assert _refused(report), _reasons(report)
    assert report["rco_pass_gate"]["ok"] is False
    assert report["bridge_consensus"]["ok"] is False
    assert "missing exact-head RCO_PASS from recognized non-author RCO" in report["reasons"]


def test_control_incomplete_queue_refuses_exact_head(tmp_path, histories):
    h = histories["legit_rebase"]
    report = _gate(tmp_path, pr_status=_snapshot(head=h.new, base=h.new_base), expected_head=h.new,
                   base=h.new_base, events=_approvals(h.new), queue=False)
    assert _refused(report), _reasons(report)
    assert any("accepted bridge queue" in r for r in report["reasons"]), _reasons(report)


@pytest.mark.parametrize("checks", [PENDING, FAILED], ids=["pending", "failed"])
def test_control_non_green_ci_refuses_exact_head(tmp_path, histories, checks):
    h = histories["legit_rebase"]
    report = _gate(tmp_path, pr_status=_snapshot(head=h.new, base=h.new_base, checks=checks),
                   expected_head=h.new, base=h.new_base, events=_approvals(h.new))
    assert _refused(report), _reasons(report)
    assert any("status checks not green" in r for r in report["reasons"]), _reasons(report)


def test_control_stale_snapshot_head_refuses(tmp_path, histories):
    h = histories["legit_rebase"]
    report = _gate(tmp_path, pr_status=_snapshot(head=h.old, base=h.new_base), expected_head=h.new,
                   base=h.new_base, events=_approvals(h.new))
    assert _refused(report), _reasons(report)
    assert "exact head mismatch" in report["reasons"], _reasons(report)


@pytest.mark.parametrize("shape", sorted(OBJECTION_SHAPES))
def test_control_exact_head_objection_under_descriptive_task_refuses_through_consensus(tmp_path, histories, shape):
    """Today, at the exact head, verify_bridge_consensus sees a head-bound objection under a descriptive task id
    and the gate refuses; check_rco_pass_present alone does not see it (recorded, not asserted as desired)."""
    h = histories["legit_rebase"]
    events = _approvals(h.new) + [_objection(h.new, task=DESCRIPTIVE_TASK, with_pr=OBJECTION_SHAPES[shape])]
    report = _gate(tmp_path, pr_status=_snapshot(head=h.new, base=h.new_base), expected_head=h.new,
                   base=h.new_base, events=events)
    assert _refused(report), _reasons(report)
    assert report["bridge_consensus"]["ok"] is False
    assert any("recognized RCO veto blocks consensus: claude-rco-1" in r
               for r in report["bridge_consensus"]["reasons"]), _reasons(report)


def test_control_exact_head_apply_merges_with_match_head_commit(tmp_path, histories):
    h = histories["legit_rebase"]
    calls, run = _runner(head=h.new, base=h.new_base)
    report = _merge(tmp_path, events_path=_events_file(tmp_path, _approvals(h.new)), runner=run, apply=True,
                    expected_head=h.new, expected_base_sha=h.new_base)
    assert report["decision"] == "merge_executed_after_receipt", _reasons(report)
    merges = _merge_calls(calls)
    assert len(merges) == 1 and f"--match-head-commit={h.new}" in merges[0]


def _append_before_apply_snapshot(monkeypatch, events_path, event):
    """Timing hook only: append a bridge event right before the apply snapshot (second snapshot call), then call
    the real builder. Each entry records the receipt files already on disk under the merge out dir."""
    seen = []
    real = merge_tool.build_pr_status_snapshot
    out_dir = events_path.parent / "out"

    def hooked(**kwargs):
        seen.append(sorted(p.name for p in out_dir.rglob("*") if p.is_file()) if out_dir.exists() else [])
        if len(seen) == 2:
            _append(events_path, event)
        return real(**kwargs)

    monkeypatch.setattr(merge_tool, "build_pr_status_snapshot", hooked)
    return seen


@pytest.mark.parametrize("appended", ["objection", "benign_message"])
def test_control_fresh_apply_sees_objection_appended_after_the_receipt(tmp_path, histories, monkeypatch, appended):
    """The benign twin proves the timing hook itself never causes the refusal."""
    h = histories["legit_rebase"]
    events_path = _events_file(tmp_path, _approvals(h.new))
    event = _objection(h.new, task=TASK) if appended == "objection" else _benign_note()
    seen = _append_before_apply_snapshot(monkeypatch, events_path, event)
    calls, run = _runner(head=h.new, base=h.new_base)
    report = _merge(tmp_path, events_path=events_path, runner=run, apply=True,
                    expected_head=h.new, expected_base_sha=h.new_base)
    assert len(seen) == 2, "the hook must fire at the apply snapshot"
    assert seen[0] == [] and seen[1], "the receipt is written between the two snapshots"
    if appended == "objection":
        assert _refused(report), _reasons(report)
        assert _merge_calls(calls) == []
    else:
        assert report["decision"] == "merge_executed_after_receipt", _reasons(report)
        assert len(_merge_calls(calls)) == 1


def test_control_non_opt_in_consumers_stay_exact_head(histories):
    """The two shared functions called exactly as bridge_loop_tick (768/778) and check_promotion_eligible
    (767/777/816) call them: approvals at the approved head never satisfy the proposed head."""
    h = histories["legit_rebase"]
    events = _approvals(h.old)
    loop_rco = check_rco_pass_present(events=events, task_id=TASK, head=h.new, author_agent=AUTHOR)
    loop_consensus = verify_bridge_consensus(events=events, task_id=TASK, head_sha=h.new, pr_number=PR,
                                             author_agent=AUTHOR)
    promo_rco = check_rco_pass_present(events=events, task_id=TASK, head=h.new,
                                       rco_agent=["claude-rco-1", "claude-rco-2"], author_agent=AUTHOR)
    promo_consensus = verify_bridge_consensus(events=events, task_id=TASK, head_sha=h.new, pr_number=PR,
                                              rco_agent="claude-rco-1", author_agent=AUTHOR)
    assert [loop_rco["ok"], loop_consensus["ok"], promo_rco["ok"], promo_consensus["ok"]] == [False] * 4
    same_head = _approvals(h.new)
    assert check_rco_pass_present(events=same_head, task_id=TASK, head=h.new, author_agent=AUTHOR)["ok"] is True
    assert verify_bridge_consensus(events=same_head, task_id=TASK, head_sha=h.new, pr_number=PR,
                                   author_agent=AUTHOR)["ok"] is True  # so the refusals above are about the head


@pytest.mark.parametrize("consumer", sorted(CASES["non_opt_in_consumers"]))
def test_control_non_opt_in_consumer_call_sites_pass_no_carry_argument(consumer):
    """Static negative control: the non-merge consumers never pass a carry keyword (default exact-head)."""
    tree = ast.parse((ROOT / consumer).read_text(encoding="utf-8"))
    targets = set(CASES["non_opt_in_consumers"][consumer])
    sites = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "id", getattr(node.func, "attr", None)) in targets]
    assert len(sites) >= 2, consumer
    offending = [(node.lineno, kw.arg) for node in sites for kw in node.keywords
                 if kw.arg is None or "carry" in kw.arg.lower()]
    assert offending == [], offending


# ==============================================================================================================
# RED until Tools wires the opt-in carry: expected verdicts through the real callers
# ==============================================================================================================
def test_carry_valid_carry_end_to_end_gate(tmp_path, histories):
    h = histories["legit_rebase"]
    opt = _carry_opt_in(GATE, evaluate_auto_merge_gate, h)
    report = _gate(tmp_path, pr_status=_snapshot(head=h.new, base=h.new_base), expected_head=h.new,
                   base=h.new_base, events=_approvals(h.old), **opt)
    assert report["decision"] == "auto_merge_plan_ready", _reasons(report)
    assert report["rco_pass_gate"]["ok"] is True and report["bridge_consensus"]["ok"] is True
    record = _assert_carry_record(report, h)
    assert record["proposed_base_sha"] == h.new_base and record["approved_base_sha"] == h.old_base
    assert {c["agent"] for c in record["carried"]} == {"codex-lead-1", "codex-tools-1", "claude-rco-1"}
    assert all(c["agent_uuid"] == REGISTRY[c["agent"]] for c in record["carried"])


def test_carry_valid_carry_end_to_end_receipt_writer(tmp_path, histories):
    h = histories["legit_rebase"]
    opt = _carry_opt_in(MERGE, merge_with_bridge_receipt, h)
    calls, run = _runner(head=h.new, base=h.new_base)
    report = _merge(tmp_path, events_path=_events_file(tmp_path, _approvals(h.old)), runner=run, apply=False,
                    expected_head=h.new, expected_base_sha=h.new_base, **opt)
    assert report["decision"] == "merge_receipt_ready", _reasons(report)
    assert _merge_calls(calls) == []
    bundle = Path(report["receipt_bundle_path"])
    documents = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(bundle.parent.rglob("*.json"))]
    _assert_carry_record(documents, h)


def test_carry_match_head_commit_is_the_proposed_head(tmp_path, histories):
    h = histories["legit_rebase"]
    opt = _carry_opt_in(MERGE, merge_with_bridge_receipt, h)
    calls, run = _runner(head=h.new, base=h.new_base)
    report = _merge(tmp_path, events_path=_events_file(tmp_path, _approvals(h.old)), runner=run, apply=True,
                    expected_head=h.new, expected_base_sha=h.new_base, **opt)
    assert report["decision"] == "merge_executed_after_receipt", _reasons(report)
    merges = _merge_calls(calls)
    assert len(merges) == 1
    assert f"--match-head-commit={h.new}" in merges[0]
    assert not any(h.old in part for part in merges[0])


def _carry_refusal(tmp_path, histories, case, *, events=None, checks=GREEN, snapshot_head=None, queue=True):
    spec = CASES["cases"][case]
    h = histories[spec["history"]]
    opt = _carry_opt_in(GATE, evaluate_auto_merge_gate, h)
    report = _gate(tmp_path, pr_status=_snapshot(head=snapshot_head or h.new, base=h.new_base, checks=checks),
                   expected_head=h.new, base=h.new_base,
                   events=_approvals(h.old) if events is None else events(h), queue=queue, **opt)
    assert _refused(report), _reasons(report)
    assert re.search(spec["reason"], json.dumps(report.get("reasons", []))), _reasons(report)


def test_carry_content_change(tmp_path, histories):
    _carry_refusal(tmp_path, histories, "content_change")


@pytest.mark.parametrize("shape", sorted(OBJECTION_SHAPES))
def test_carry_old_head_objection_under_descriptive_task(tmp_path, histories, shape):
    _carry_refusal(tmp_path, histories, "old_head_objection_under_descriptive_task",
                   events=lambda h: _approvals(h.old) + [_objection(h.old, task=DESCRIPTIVE_TASK,
                                                                    with_pr=OBJECTION_SHAPES[shape])])


def test_carry_incomplete_queue(tmp_path, histories):
    _carry_refusal(tmp_path, histories, "incomplete_queue", queue=False)


def test_carry_ci_pending_at_proposed_head(tmp_path, histories):
    _carry_refusal(tmp_path, histories, "ci_pending_at_proposed_head", checks=PENDING)


def test_carry_ci_failed_at_proposed_head(tmp_path, histories):
    _carry_refusal(tmp_path, histories, "ci_failed_at_proposed_head", checks=FAILED)


def test_carry_snapshot_head_is_the_approved_head(tmp_path, histories):
    h = histories["legit_rebase"]
    _carry_refusal(tmp_path, histories, "snapshot_head_is_the_approved_head", snapshot_head=h.old)


def _benign_note():
    return _event("codex-lead-1", "message", "", None, 5, task="lead-unrelated-note", message="unrelated note")


@pytest.mark.parametrize("shape", sorted(OBJECTION_SHAPES) + ["benign_message"])
def test_carry_fresh_apply_after_objection_append(tmp_path, histories, monkeypatch, shape):
    """A late recognized-RCO objection on the approved head must refuse at the fresh apply gate; a harmless
    unrelated ledger append between receipt and apply must NOT refuse (no full-ledger digest false refusal)."""
    h = histories["legit_rebase"]
    opt = _carry_opt_in(MERGE, merge_with_bridge_receipt, h)
    events_path = _events_file(tmp_path, _approvals(h.old))
    appended = (_benign_note() if shape == "benign_message"
                else _objection(h.old, task=DESCRIPTIVE_TASK, with_pr=OBJECTION_SHAPES[shape]))
    seen = _append_before_apply_snapshot(monkeypatch, events_path, appended)
    calls, run = _runner(head=h.new, base=h.new_base)
    report = _merge(tmp_path, events_path=events_path, runner=run, apply=True,
                    expected_head=h.new, expected_base_sha=h.new_base, **opt)
    assert len(seen) == 2 and seen[1], "receipt must have been written before the append"
    if shape == "benign_message":
        assert report["decision"] == "merge_executed_after_receipt", _reasons(report)
        merges = _merge_calls(calls)
        assert len(merges) == 1 and f"--match-head-commit={h.new}" in merges[0]
    else:
        assert _refused(report), _reasons(report)
        assert _merge_calls(calls) == []


def test_carry_content_change_never_reaches_merge(tmp_path, histories):
    h = histories["content_changing_rebase"]
    opt = _carry_opt_in(MERGE, merge_with_bridge_receipt, h)
    calls, run = _runner(head=h.new, base=h.new_base)
    report = _merge(tmp_path, events_path=_events_file(tmp_path, _approvals(h.old)), runner=run, apply=True,
                    expected_head=h.new, expected_base_sha=h.new_base, **opt)
    assert _refused(report), _reasons(report)
    assert _merge_calls(calls) == []
