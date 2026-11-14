"""Independent carry apply regression through both actual merge executors (fable-5, Lead request
2026-11-09T11:28:00Z, Tools apply head 211490de).

Executors: tools.merge_with_bridge_receipt:merge_with_bridge_receipt (writes its own MAGMA receipt, then applies)
and tools.idle_consensus_auto_merge:evaluate_auto_merge_gate with apply=True (consumes a MAGMA receipt written by
the actual writer). Both run on a real git history with the synthetic strict GitHub of the corpus (never a live
call) and must re-verify the MAGMA proof before any merge command. Every case names its intended verdict in
tests/fixtures/bridge_consensus_carry_forward/apply_executors_cases.json; "merge" means exactly one gh pr merge at the
proposed head, "refuse" means no merge command at all. Timing hooks only wrap published functions and then call
them unchanged. No Tools source is touched.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "apply_executors_cases.json")
                   .read_text(encoding="utf-8"))
_SPEC = importlib.util.spec_from_file_location(
    "fable5_apply_executors_receipt_adversary", ROOT / "tests" / "tools" / "test_consensus_carry_forward_receipt_adversary.py")
adv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(adv)
of = adv.of
anchored_fleet = of.anchored_fleet   # autouse: test-owned pinned git generation
magma_history = adv.magma_history    # real git history on an allowlisted path

import tools.idle_consensus_auto_merge as gate_module  # noqa: E402
import tools.merge_with_bridge_receipt as merge_module  # noqa: E402
import tools.write_bridge_consensus_merge_receipt as writer_module  # noqa: E402

EXECUTORS = ("merge", "idle")
MOVED_BASE = "9" * 40


def _require_apply_api():
    missing = [name for name, fn in (("merge_with_bridge_receipt", merge_module.merge_with_bridge_receipt),
                                     ("evaluate_auto_merge_gate", gate_module.evaluate_auto_merge_gate))
               if "carry_forward" not in inspect.signature(fn).parameters]
    if missing or not hasattr(writer_module, "verify_carry_forward_apply"):
        pytest.fail(f"RED: missing API: carry apply not published ({missing}; fixture tools_head {CASES['tools_head']})")


class LateBaseGitHub(of.ci_harness.FakeGitHub):
    """Synthetic strict GitHub whose base-branch ref read can be moved for the late pre-effect check only."""

    moved = None

    def __call__(self, command):
        args = list(command)
        if self.moved and args[:2] == ["gh", "api"] and any("/git/ref/heads/" in a for a in args):
            self.calls.append(args)
            return self._ok({"ref": "refs/heads/main", "object": {"type": "commit", "sha": self.moved}})
        return super().__call__(command)


class Env(adv._Magma):
    """One executor environment: ledger, synthetic strict GitHub, live queue observation (read at call time)."""

    def __init__(self, h, tmp, *, pr=None):
        super().__init__(h, tmp)
        self.gh = LateBaseGitHub(head=h.new, base=h.live_base, repo=of.REPO_NAME, pr=pr or of.PR, branch=of.TASK,
                                 changed_path=of.CHANGED_PATH, author_login="Ahkeratmehilaiset")
        self.pr = pr or of.PR

    def queue_checker(self):
        return lambda **kwargs: of._queue(self.queue)(**kwargs)

    def events_path(self):
        return of._events_file(self.tmp, self.events)

    def append(self, *events):
        self.events = self.events + list(events)
        self.events_path()


def _merges(gh):
    return of._merge_calls(gh)


def _run_merge_executor(env, *, pr_number=None, expected_base=None):
    return merge_module.merge_with_bridge_receipt(
        pr_number=env.pr if pr_number is None else pr_number, repo=of.REPO_NAME, events_path=env.events_path(),
        out_dir=env.tmp / "merge-out", expected_head=env.h.new,
        expected_base_sha=env.h.live_base if expected_base is None else expected_base,
        consensus_proposal_id=of.TASK, from_agent="codex-lead-1", bridge_task_id=of.TASK, apply=True,
        runner=env.gh, carry_forward=env.request, accepted_queue_checker=env.queue_checker())


def _write_receipt(env):
    manifest, verdict, note = env.write(env.caller_snapshot())
    assert verdict == "accept", note
    return manifest


def _run_idle_executor(env, manifest, *, pr_number=None, expected_base=None):
    snapshot = env.caller_snapshot()
    kwargs = {}
    if pr_number is not None:
        snapshot = dict(snapshot, pr_number=pr_number)
    return gate_module.evaluate_auto_merge_gate(
        pr_status=snapshot, expected_head=env.h.new,
        expected_base_sha=env.h.live_base if expected_base is None else expected_base,
        consensus_proposal_id=of.TASK, receipt_bundle_path=str(manifest), events_path=env.events_path(),
        bridge_task_id=of.TASK, repo=of.REPO_NAME, from_agent="codex-lead-1", require_bridge_consensus=True,
        accepted_queue_checker=env.queue_checker(), runner=env.gh, apply=True, carry_forward=env.request,
        **kwargs)


def _outcome(call):
    try:
        report = call()
    except (ValueError, TypeError, of.ci_harness.PrStatusSnapshotError,
            writer_module.BridgeConsensusMergeReceiptError) as exc:
        return {"ok": False, "decision": f"raised {type(exc).__name__}", "errors": [str(exc)[:300]]}
    return report


def _brief(report):
    return {k: report.get(k) for k in ("ok", "decision", "reasons", "errors", "blockers")}


# ----------------------------------------------------------------------------------------------------------------
# Mutations. "before" changes apply before the executor runs; "after_receipt" changes apply once the MAGMA receipt
# exists and before the apply re-check (merge executor: second snapshot read; idle: after the writer ran);
# "late_base" moves the base ref only for the read inside verify_carry_forward_apply.
# ----------------------------------------------------------------------------------------------------------------
def _objection(env, head, minute):
    return of._event("claude-rco-1", "decision", "changes_requested", head, minute)


def _mutate(env, case, monkeypatch):
    spec = CASES["cases"][case]
    kind = spec["mutation"]
    h = env.h
    if kind == "none":
        return
    if kind == "old_head_objection":
        env.append(_objection(env, h.old, 40))
    elif kind == "new_head_objection":
        env.append(_objection(env, h.new, 41))
    elif kind == "benign_note":
        env.append(of._event("claude-rco-2", "message", "info", None, 42, task="claude-rco-2/unrelated-note"))
    elif kind == "queue_incomplete":
        env.queue = False
    elif kind == "ci_failed":
        of.ci_harness._set(env.gh.runs, "unified", conclusion="failure")
    elif kind == "late_base_moved":
        real = writer_module.verify_carry_forward_apply

        def late(**kwargs):
            env.gh.moved = MOVED_BASE
            return real(**kwargs)
        monkeypatch.setattr(writer_module, "verify_carry_forward_apply", late)
    elif kind == "unbound_votes":
        env.events = of._approvals(h, bind=())
        env.events.insert(0, of._claim())
    elif kind in ("votes_bound_to_consistent_live_base", "votes_bound_to_other_diff"):
        if kind == "votes_bound_to_consistent_live_base":  # merge-base(live base, old head) is the old base: same diff
            basis = dict(of._basis(h), base_sha=h.live_base)
        else:
            basis = dict(of._basis(h), diff_sha256="e" * 64, patch_id="e" * 40)
        for event in env.events[1:]:
            event["payload"].update(review_basis=basis, review_basis_sha256=of._sha(basis))
    elif kind in ("vote_pr_true", "vote_pr_float"):
        for event in env.events[1:]:  # the original votes, as in the forgery corpus
            event["payload"]["pr"] = True if kind == "vote_pr_true" else float(env.pr)
    else:
        raise AssertionError(kind)


def _after_receipt_hook(env, case, monkeypatch, executor):
    """Merge executor: run the mutation right before its second snapshot read (receipt already written)."""
    seen = []
    real = merge_module.build_pr_status_snapshot

    def hooked(**kwargs):
        seen.append(sorted(p.name for p in (env.tmp / "merge-out").rglob("*.json")) if (env.tmp / "merge-out").exists()
                    else [])
        if len(seen) == 2:
            _mutate(env, case, monkeypatch)
        return real(**kwargs)
    monkeypatch.setattr(merge_module, "build_pr_status_snapshot", hooked)
    return seen


def _execute(env, case, executor, monkeypatch):
    spec = CASES["cases"][case]
    when = spec["when"]
    args = dict(pr_number=spec.get("executor_pr_number_arg"), expected_base="" if spec.get("empty_expected_base")
                else None)
    if args["pr_number"] == "true":
        args["pr_number"] = True
    elif args["pr_number"] == "float":
        args["pr_number"] = float(env.pr)
    if executor == "merge":
        seen = None
        if when == "before" or when == "late_base":
            _mutate(env, case, monkeypatch)
        elif when == "after_receipt":
            seen = _after_receipt_hook(env, case, monkeypatch, executor)
        report = _outcome(lambda: _run_merge_executor(env, **args))
        if seen is not None and _merges(env.gh):
            assert len(seen) >= 2 and seen[1], "the hook must fire after the receipt files exist"
        return report
    manifest = spec.get("receipt") == "foreign" and _foreign_manifest(env) or _write_receipt(env)
    if spec.get("reseal"):
        manifest = env.reseal(manifest, RESEALS[spec["reseal"]](env))
    if when in ("before", "after_receipt", "late_base"):
        _mutate(env, case, monkeypatch)
    return _outcome(lambda: _run_idle_executor(env, manifest, **args))


def _foreign_history(tmp):
    """A second legit rebase (other content, so other heads) on the same allowlisted path."""
    path = adv.MAGMA_PATH
    repo = of.corpus.Repo(tmp)
    main0 = repo.commit({path: b"other one\n", "docs/readme.txt": b"base\n"}, "base")
    repo.checkout("pr", main0)
    old = repo.commit({path: b"other one\nother two\n"}, "pr: other feature")
    repo.checkout("main", main0)
    main1 = repo.commit({"docs/notes.txt": b"unrelated main change\n"}, "main advances")
    repo.checkout("pr-rebased", main1)
    new = repo.commit({path: b"other one\nother two\n"}, "pr: other feature")
    repo.git("remote", "add", "origin", f"https://github.com/{of.REPO_NAME}.git")
    return SimpleNamespace(repo=repo, old=old, old_base=main0, new=new, live_base=main1, feature=None)


def _foreign_manifest(env):
    """A valid MAGMA carry receipt written by the actual writer for a different carry (another real history with
    other heads, same PR number and task), then offered to the idle executor of this carry."""
    other = Env(_foreign_history(env.tmp / "foreign-repo"), env.tmp / "foreign", pr=env.pr)
    return _write_receipt(other)


RESEALS = {
    "receipt_pr_float": lambda env: lambda p: p["carry_forward"]["approval_receipt"].update(
        pr_number=float(p["carry_forward"]["approval_receipt"]["pr_number"])),
    "payload_head_uppercase": lambda env: lambda p: p.update(head_sha=p["head_sha"].upper()),
    "approved_base_to_live_base": lambda env: lambda p: (
        p["carry_forward"]["approval_receipt"].update(approved_base_sha=env.h.live_base),
        adv._reseal(p["carry_forward"]["approval_receipt"])),
}

CASE_IDS = sorted((case, executor) for case, spec in CASES["cases"].items() for executor in spec["executors"])


@pytest.fixture
def env_for(magma_history, tmp_path, monkeypatch):
    monkeypatch.setattr(of, "CHANGED_PATH", adv.MAGMA_PATH)

    def make(case):
        spec = CASES["cases"][case]
        if spec.get("pr_one"):
            monkeypatch.setattr(of, "PR", 1)
            monkeypatch.setitem(of._basis.__kwdefaults__, "pr", 1)
        return Env(magma_history, tmp_path / "env", pr=1 if spec.get("pr_one") else None)
    return make


def test_every_fixture_case_is_well_formed():
    kinds = set(CASES["mutations"])
    for name, spec in CASES["cases"].items():
        assert spec["expect"] in ("merge", "refuse"), name
        assert spec["mutation"] in kinds, name
        assert spec["when"] in ("before", "after_receipt", "late_base"), name
        assert set(spec["executors"]) <= set(EXECUTORS) and spec["executors"], name


@pytest.mark.parametrize("case,executor", CASE_IDS, ids=[f"{c}-{e}" for c, e in CASE_IDS])
def test_apply_executor(env_for, monkeypatch, case, executor):
    _require_apply_api()
    spec = CASES["cases"][case]
    env = env_for(case)
    report = _execute(env, case, executor, monkeypatch)
    merges = _merges(env.gh)
    record = {"case": case, "executor": executor, "expect": spec["expect"], "merges": merges, "report": _brief(report)}
    if os.environ.get("F5_APPLY_LOG"):
        with open(os.environ["F5_APPLY_LOG"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    if spec["expect"] == "merge":
        assert len(merges) == 1, json.dumps(_brief(report), default=str)[:800]
        assert f"--match-head-commit={env.h.new}" in merges[0] and not any(env.h.old in a for a in merges[0])
        verification = report.get("carry_apply_verification") or {}
        assert verification.get("ok") is True and verification.get("atomic_base_guard") is False, verification
    elif merges:
        pytest.fail(f"actual defect: {case} via {executor} executor issued {merges} although the intended verdict is "
                    f"refuse; report {json.dumps(_brief(report), default=str)[:500]}")
    else:
        assert report.get("ok") is not True, _brief(report)
