"""N4 replay matrix through the actual MAGMA writer, consumer and idle executor (fable-5, Lead request
2026-11-09T12:53:22Z, Tools apply head 211490de, design sha256 078cf23f).

Each case writes a MAGMA carry bundle B with the actual writer at a source tuple, proves B valid there with the
actual consumer, then changes exactly the outer tuple dimension(s) of the case to a target that is itself a valid
carry (proved by a fresh bundle from the actual writer). B offered to the idle executor at the target must refuse
with the tuple diagnostic and zero synthetic effects; the fresh target bundle must merge exactly once. GitHub is
the synthetic strict GitHub of the corpus (recorded calls, never a live PR); git is real. The merge executor has no
caller receipt path, so it is not a replay surface here. No Tools source is touched.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward" / "n4_replay_cases.json")
                   .read_text(encoding="utf-8"))
_SPEC = importlib.util.spec_from_file_location(
    "fable5_n4_apply_executors", ROOT / "tests" / "tools" / "test_consensus_carry_forward_apply_executors.py")
ax = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ax)
adv, of = ax.adv, ax.of
anchored_fleet = of.anchored_fleet   # autouse: test-owned pinned git generation

TUPLE = tuple(CASES["tuple_fields"])
TUPLE_ERROR = CASES["tuple_error"]
ORDERED = sorted(CASES["cases"], key=lambda name: CASES["cases"][name]["order"])
VOTER_NAMES = [agent for agent, _, _ in of.VOTERS]


def _history(path):
    """Fresh legit rebase on the allowlisted MAGMA path (same shape as the receipt corpus magma_history)."""
    repo = of.corpus.Repo(path)
    main0 = repo.commit({adv.MAGMA_PATH: b"base one\n", "docs/readme.txt": b"base\n"}, "base")
    repo.checkout("pr", main0)
    old = repo.commit({adv.MAGMA_PATH: b"base one\nfeature two\n"}, "pr: add feature")
    repo.checkout("main", main0)
    main1 = repo.commit({"docs/notes.txt": b"unrelated main change\n"}, "main advances")
    repo.checkout("pr-rebased", main1)
    new = repo.commit({adv.MAGMA_PATH: b"base one\nfeature two\n"}, "pr: add feature")
    repo.git("remote", "add", "origin", f"https://github.com/{of.REPO_NAME}.git")
    return SimpleNamespace(repo=repo, old=old, old_base=main0, new=new, live_base=main1, feature=None)


def _commit_tree(repo, tree_of, parent, message):
    tree = repo.git("rev-parse", tree_of + "^{tree}").decode().strip()
    return repo.git("commit-tree", tree, "-p", parent, "-m", message).decode().strip()


def _later(events, offset):
    for event in events:
        minute = int(event["ts_utc"][14:16]) + offset
        event["ts_utc"] = "2026-11-09T08:%02d:00Z" % minute
    return events


# ----------------------------------------------------------------------------------------------------------------
# Target constructors: each returns (target history, ledger builder). Module constants are switched with
# monkeypatch only after the source phase is complete.
# ----------------------------------------------------------------------------------------------------------------
def _target_proposed_head(h, tmp, monkeypatch, original):
    c = _commit_tree(h.repo, h.new, h.live_base, "pr: add feature (re-pushed, same tree)")
    return SimpleNamespace(**dict(vars(h), new=c)), lambda: copy.deepcopy(original)


def _target_base_advance(h, tmp, monkeypatch, original):
    y = _commit_tree(h.repo, h.live_base, h.live_base, "main: empty-content advance")
    c = _commit_tree(h.repo, h.new, y, "pr: add feature")
    h.repo.git("update-ref", "refs/heads/main", y)
    return SimpleNamespace(**dict(vars(h), new=c, live_base=y)), lambda: copy.deepcopy(original)


def _target_repository(h, tmp, monkeypatch, original):
    target_repo = CASES["repository_target"]
    repo2 = of.corpus.Repo(tmp / "repo-target")
    repo2.git("fetch", "-q", "--update-head-ok", str(h.repo.path), "+refs/heads/*:refs/heads/*")
    repo2.git("reset", "-q", "--hard", "pr-rebased")
    repo2.git("remote", "add", "origin", f"https://github.com/{target_repo}.git")
    monkeypatch.setattr(of, "REPO_NAME", target_repo)
    t = SimpleNamespace(**dict(vars(h), repo=repo2))

    def ledger():
        appended = _later(of._approvals(t, bind=VOTER_NAMES), 20)  # basis repository = target (read at call)
        return copy.deepcopy(original) + appended
    return t, ledger


def _target_task(h, tmp, monkeypatch, original):
    task = CASES["task_target"]
    monkeypatch.setattr(of, "TASK", task)

    def ledger():
        appended = [of._claim(task=task)] + of._approvals(h, bind=VOTER_NAMES, task=task)
        return copy.deepcopy(original) + _later(appended, 20)
    return SimpleNamespace(**vars(h)), ledger


TARGETS = {
    "proposed_head_replay": _target_proposed_head,
    "base_advance_replay": _target_base_advance,
    "repository_replay": _target_repository,
    "task_replay": _target_task,
}


def _payload(manifest):
    return json.loads((Path(manifest).parent / "payload-001-merge.json").read_bytes())


def _tuple_of(payload):
    return {key: payload.get(key) for key in TUPLE}


def _expected_tuple(h):
    return dict(repo=of.REPO_NAME, pr_number=of.PR, task_id=of.TASK, head_sha=h.new, base_sha=h.live_base)


def _effects(gh):
    """Merge commands and any GitHub call that is not a read."""
    out = []
    for call in gh.calls:
        if call[:3] == ["gh", "pr", "merge"]:
            out.append(call)
            continue
        for i, arg in enumerate(call):
            if arg in ("-X", "--method") and i + 1 < len(call) and str(call[i + 1]).upper() != "GET":
                out.append(call)
                break
    return out


def _target_env(t, tmp, name, ledger):
    env = ax.Env(t, tmp / name)
    env.events = ledger()
    return env


def test_fixture_matches_the_published_design_axes():
    assert ORDERED[0] == "proposed_head_replay"
    assert set(ORDERED) == set(TARGETS)
    for name, spec in CASES["cases"].items():
        assert spec["changed_receipt_fields"] and set(spec["changed_receipt_fields"]) <= set(TUPLE), name


@pytest.mark.parametrize("case", ORDERED)
def test_n4_replay(case, tmp_path, monkeypatch):
    ax._require_apply_api()
    spec = CASES["cases"][case]
    monkeypatch.setattr(of, "CHANGED_PATH", adv.MAGMA_PATH)
    h = _history(tmp_path / "repo")

    # 1. source: bundle B written and consumed by the actual MAGMA writer/consumer at the source tuple
    source = ax.Env(h, tmp_path / "source")
    if spec.get("source_claim") == "task_only":  # task axis: the original claim binds its task, not the PR number
        assert source.events[0]["type"] == "claim"
        source.events[0]["payload"].pop("pr")
    original = copy.deepcopy(source.events)
    bundle = ax._write_receipt(source)
    created = source.consume(bundle)
    source_tuple = _tuple_of(_payload(bundle))
    assert created[0] == "accept", f"creation-time validity of B: {created}"
    assert source_tuple == _expected_tuple(h), source_tuple

    # 2. target: complete live derivation through a fresh bundle from the actual writer
    t, ledger = TARGETS[case](h, tmp_path, monkeypatch, original)
    fresh_env = _target_env(t, tmp_path, "target-fresh", ledger)
    manifest, verdict, note = fresh_env.write(fresh_env.caller_snapshot())
    if verdict != "accept":
        pytest.fail(f"constructor failure (not a receipt-binding result): target carry did not derive: {note}")
    target_tuple = _expected_tuple(t)
    changed = sorted(k for k in TUPLE if source_tuple[k] != target_tuple[k])
    assert changed == sorted(spec["changed_receipt_fields"]), (changed, source_tuple, target_tuple)
    assert _tuple_of(_payload(manifest)) == target_tuple

    # 3. replay: B at the target through the idle executor (apply true) and the direct consumer
    replay_env = _target_env(t, tmp_path, "target-replay", ledger)
    report = ax._outcome(lambda: ax._run_idle_executor(replay_env, bundle))
    effects = _effects(replay_env.gh)
    consumed = replay_env.consume(bundle)
    verification = report.get("carry_apply_verification") or {}

    # 4. positive control: the fresh target bundle merges exactly once at the target head
    control = ax._outcome(lambda: ax._run_idle_executor(fresh_env, manifest))
    control_merges = ax._merges(fresh_env.gh)

    record = {"case": case, "changed": changed, "source_tuple": source_tuple, "target_tuple": target_tuple,
              "created": created[0], "replay": ax._brief(report), "replay_verification": verification,
              "replay_effects": effects, "consumer": consumed, "control": ax._brief(control),
              "control_merges": control_merges}
    if os.environ.get("F5_N4_LOG"):
        with open(os.environ["F5_N4_LOG"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    if effects:
        pytest.fail(f"actual defect: replayed {case} bundle produced synthetic effects {effects}")
    assert report.get("ok") is not True and report.get("decision") == "operator_review_required", ax._brief(report)
    assert verification.get("ok") is False and verification.get("errors") == [TUPLE_ERROR], (
        ax._brief(report), verification)
    assert consumed[0] == "refuse" and consumed[1] == [TUPLE_ERROR], consumed
    assert len(control_merges) == 1, json.dumps(ax._brief(control), default=str)[:800]
    assert f"--match-head-commit={t.new}" in control_merges[0]
    assert (control.get("carry_apply_verification") or {}).get("ok") is True, control.get("carry_apply_verification")
    assert fresh_env.consume(manifest)[0] == "accept"


def test_task_axis_with_pr_bound_original_claim_has_no_valid_target(tmp_path, monkeypatch):
    """Control for the task axis: when the original claim also binds the PR number, the published author
    resolution treats it as a contributor claim on the target task, so no valid target carry exists (the design's
    constructor-failure branch). B offered there still refuses with zero synthetic effects."""
    ax._require_apply_api()
    control = CASES["task_axis_pr_bound_claim_control"]
    monkeypatch.setattr(of, "CHANGED_PATH", adv.MAGMA_PATH)
    h = _history(tmp_path / "repo")
    source = ax.Env(h, tmp_path / "source")
    assert "pr" in source.events[0]["payload"]
    original = copy.deepcopy(source.events)
    bundle = ax._write_receipt(source)
    assert source.consume(bundle)[0] == "accept"
    t, ledger = _target_task(h, tmp_path, monkeypatch, original)
    fresh_env = _target_env(t, tmp_path, "target-fresh", ledger)
    manifest, verdict, note = fresh_env.write(fresh_env.caller_snapshot())
    replay_env = _target_env(t, tmp_path, "target-replay", ledger)
    report = ax._outcome(lambda: ax._run_idle_executor(replay_env, bundle))
    record = {"case": "task_axis_pr_bound_claim_control", "writer": [verdict, note], "replay": ax._brief(report),
              "replay_effects": _effects(replay_env.gh)}
    if os.environ.get("F5_N4_LOG"):
        with open(os.environ["F5_N4_LOG"], "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    assert verdict == "refuse" and control["writer_error"] in str(note), note
    assert _effects(replay_env.gh) == [] and report.get("ok") is not True, ax._brief(report)
