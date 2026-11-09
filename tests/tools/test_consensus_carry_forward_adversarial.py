"""Independent adversarial corpus for Rule 9a approval carry-forward (operator directive 2026-11-09).

Rule under test: build_consensus and RCO_PASS approvals given at an approved head may carry to a proposed head only
when ALL hold: the raw merge-base..head diff bytes are identical AND git patch-id --stable is identical, both
heads belong to the same PR, repository and base ref, every merge-base is unique and available, CI is green at
the proposed head (CI never carries), no recognized-RCO veto is unretracted at either head or the PR scope, the
accepted queue was scanned completely, and the result is re-derivable from its receipt. Operator signatures and
Rule-10 approvals never carry.

Two layers, both independent of the implementation:

1. GREEN reproduction tests build throwaway git repositories with fixed identities and clocks and check the facts
   recorded in tests/fixtures/bridge_consensus_carry_forward/git_reproductions.json: merge-base count, raw diff
   bytes under the pinned command, and git patch-id --stable. They prove with actual git output that patch-id
   equality never substitutes for byte equality, which histories are ambiguous, and which repository settings
   would make an unpinned diff hide a real change.
2. RED contract tests hand every git history plus structured synthetic bridge, CI and receipt evidence to the
   carry-forward entry points named in contract.json. They fail until those entry points exist; the expected
   verdicts are fixed here. No live bridge evidence, network or gate source is used; collisions are real git
   output, never fabricated hashes.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "bridge_consensus_carry_forward"
GIT = shutil.which("git")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

pytestmark = pytest.mark.skipif(GIT is None, reason="git executable is required for the reproduction corpus")


def _load(name):
    return json.loads((FIXTURES / name).read_bytes())


CONTRACT = _load("contract.json")
REPRODUCTIONS = _load("git_reproductions.json")
SYNTHETIC = _load("synthetic_cases.json")

# The reference pinned command (contract.json repeats it). Every flag neutralizes a repository or user setting that
# was shown to change the bytes or to hide a change: see test_pinned_diff_bytes_ignore_hostile_repository_settings.
PINNED_GLOBAL = tuple(CONTRACT["reference_diff_command"]["global_options"])
PINNED_DIFF = tuple(CONTRACT["reference_diff_command"]["diff_options"])


# --------------------------------------------------------------------------------------------------------------
# git plumbing with a closed environment: no system or user config, fixed identities, deterministic clock.
# --------------------------------------------------------------------------------------------------------------
class Repo:
    def __init__(self, path: Path):
        self.path = path
        self.clock = 0
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "core.autocrlf", "false")
        self.git("config", "core.fileMode", "false")

    def env(self):
        self.clock += 1
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_ATTR_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="corpus", GIT_AUTHOR_EMAIL="corpus@example.invalid",
                   GIT_COMMITTER_NAME="corpus", GIT_COMMITTER_EMAIL="corpus@example.invalid",
                   GIT_TERMINAL_PROMPT="0", LC_ALL="C")
        stamp = "2026-11-09T01:%02d:%02d+00:00" % (self.clock // 60 % 60, self.clock % 60)
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
        return env

    def git(self, *args, data=None, check=True):
        result = subprocess.run([GIT, *args], cwd=self.path, input=data, capture_output=True, env=self.env())
        if check and result.returncode:
            raise AssertionError((args, result.stderr.decode("utf-8", "replace")))
        return result.stdout

    def rev(self, ref):
        return self.git("rev-parse", "--verify", ref + "^{commit}").decode().strip()

    def commit(self, files=None, message="change", remove=()):
        for name, raw in (files or {}).items():
            target = self.path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            self.git("add", "--", name)
        for name in remove:
            self.git("rm", "-q", "--", name)
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.rev("HEAD")

    def checkout(self, branch, start):
        self.git("checkout", "-q", "-B", branch, start)

    def merge_bases(self, base, head):
        result = subprocess.run([GIT, "merge-base", "--all", base, head], cwd=self.path, capture_output=True,
                                env=self.env())
        return result.stdout.decode().split() if result.returncode == 0 else []

    def raw_diff(self, merge_base, head, *, pinned=True):
        args = (*PINNED_GLOBAL, "diff", *PINNED_DIFF) if pinned else ("diff",)
        return self.git(*args, merge_base, head)

    def patch_id(self, raw):
        out = self.git("patch-id", "--stable", data=raw).split()
        return out[0].decode() if out else None

    def identity(self, base, head, *, pinned=True):
        bases = self.merge_bases(base, head)
        if len(bases) != 1:
            return {"merge_base_count": len(bases), "diff_sha256": None, "diff_bytes": None, "patch_id": None}
        raw = self.raw_diff(bases[0], head, pinned=pinned)
        return {"merge_base_count": 1, "diff_sha256": hashlib.sha256(raw).hexdigest(), "diff_bytes": len(raw),
                "patch_id": self.patch_id(raw)}


BASE_FILES = {"README.md": b"corpus base\n", "src/app.txt": b"".join(b"line %02d\n" % i for i in range(1, 41))}


def _pr_files():
    return {"src/feature.txt": b"feature one\nfeature two\n"}


# --------------------------------------------------------------------------------------------------------------
# Scenario builders. Each returns a dict: repo, approved (head, base_sha), proposed (head, base_sha), notes.
# base_sha is the tip of the PR base branch the PR was evaluated against at that time.
# --------------------------------------------------------------------------------------------------------------
def _start(tmp):
    repo = Repo(tmp)
    main0 = repo.commit(BASE_FILES, "base")
    return repo, main0


def build_legit_rebase(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit(_pr_files(), "pr: add feature")
    repo.checkout("main", main0)
    main1 = repo.commit({"docs/notes.txt": b"unrelated main change\n"}, "main advances")
    repo.checkout("pr-rebased", main1)
    proposed = repo.commit(_pr_files(), "pr: add feature")
    return repo, (approved, main0), (proposed, main1)


def build_message_only(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit(_pr_files(), "pr: add feature")
    repo.git("commit", "-q", "--amend", "-m", "pr: add feature (reworded message only)")
    proposed = repo.rev("HEAD")
    return repo, (approved, main0), (proposed, main0)


def build_squash(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    repo.commit({"src/feature.txt": b"feature one\n"}, "pr: part one")
    approved = repo.commit({"src/feature.txt": b"feature one\nfeature two\n"}, "pr: part two")
    repo.checkout("pr-squashed", main0)
    proposed = repo.commit(_pr_files(), "pr: add feature (squashed)")
    return repo, (approved, main0), (proposed, main0)


def build_content_changing_rebase(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit({"src/app.txt": BASE_FILES["src/app.txt"].replace(b"line 10\n", b"line 10 pr\n")},
                           "pr: edit line 10")
    repo.checkout("main", main0)
    main1 = repo.commit({"src/app.txt": BASE_FILES["src/app.txt"].replace(b"line 10\n", b"line 10 main\n")},
                        "main: edit line 10")
    repo.checkout("pr-rebased", main1)
    # conflict resolution keeps both edits: the PR diff against the new merge-base is a different change
    proposed = repo.commit({"src/app.txt": BASE_FILES["src/app.txt"].replace(b"line 10\n", b"line 10 main pr\n")},
                           "pr: edit line 10")
    return repo, (approved, main0), (proposed, main1)


def build_whitespace_collision(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit({"x.txt": b"alpha gamma\n"}, "pr: add x")
    repo.checkout("pr-ws", main0)
    proposed = repo.commit({"x.txt": b"alpha    gamma\n"}, "pr: add x")
    return repo, (approved, main0), (proposed, main0)


def build_binary_change(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit({"assets/blob.bin": b"\x00\x01payload-A\x00\xff"}, "pr: add blob")
    repo.checkout("pr-bin", main0)
    proposed = repo.commit({"assets/blob.bin": b"\x00\x01payload-B\x00\xff"}, "pr: add blob")
    return repo, (approved, main0), (proposed, main0)


def build_mode_only(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit({"run.sh": b"echo corpus\n"}, "pr: add script")
    repo.checkout("pr-mode", main0)
    (repo.path / "run.sh").write_bytes(b"echo corpus\n")
    repo.git("add", "--", "run.sh")
    repo.git("update-index", "--chmod=+x", "run.sh")
    repo.git("commit", "-q", "-m", "pr: add script")
    return repo, (approved, main0), (repo.rev("HEAD"), main0)


def build_submodule_pointer(tmp):
    repo, main0 = _start(tmp)
    heads = []
    for branch, target in (("pr", "1" * 40), ("pr-sub", "2" * 40)):
        repo.checkout(branch, main0)
        repo.git("update-index", "--add", "--cacheinfo", "160000," + target + ",vendor/lib")
        repo.git("commit", "-q", "-m", "pr: pin vendor/lib")
        heads.append(repo.rev("HEAD"))
    return repo, (heads[0], main0), (heads[1], main0)


def build_rename_target(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    repo.git("mv", "README.md", "README-old.md")
    repo.git("commit", "-q", "-m", "pr: rename")
    approved = repo.rev("HEAD")
    repo.checkout("pr-rename", main0)
    repo.git("mv", "README.md", "README-new.md")
    repo.git("commit", "-q", "-m", "pr: rename")
    return repo, (approved, main0), (repo.rev("HEAD"), main0)


def build_empty_diff(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit(_pr_files(), "pr: add feature")
    repo.checkout("pr-empty", main0)
    proposed = repo.commit(None, "pr: emptied (no content change against the base)")
    return repo, (approved, main0), (proposed, main0)


def build_multiple_merge_bases(tmp):
    """Criss-cross history: two merge-bases between the base branch tip and the proposed head."""
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit(_pr_files(), "pr: add feature")
    repo.checkout("main", main0)
    m1 = repo.commit({"docs/a.txt": b"a\n"}, "main: a")
    repo.checkout("side", main0)
    s1 = repo.commit({"docs/b.txt": b"b\n"}, "side: b")
    repo.checkout("main", m1)
    repo.git("merge", "-q", "--no-ff", "-m", "main: merge side", s1)
    main_tip = repo.rev("HEAD")
    repo.checkout("pr-crossed", s1)
    repo.git("merge", "-q", "--no-ff", "-m", "pr: merge main a", m1)
    proposed = repo.commit(_pr_files(), "pr: add feature")
    return repo, (approved, main0), (proposed, main_tip)


def build_missing_object(tmp):
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    proposed = repo.commit(_pr_files(), "pr: add feature")
    # the approved head is a well-formed object name that this repository does not contain
    absent = hashlib.sha1(b"corpus: approved head that was never fetched").hexdigest()
    return repo, (absent, main0), (proposed, main0)


def _hostile_hiding_repo(tmp, mechanism):
    repo, main0 = _start(tmp)
    info = repo.path / ".git" / "info"
    info.mkdir(parents=True, exist_ok=True)
    if mechanism == "textconv":
        (info / "attributes").write_bytes(b"*.txt diff=hide\n")
        repo.git("config", "diff.hide.textconv", "true")
    else:
        script = repo.path / ".git" / "hide-diff.sh"
        # prints one fixed, well-formed patch whatever the content: plain bytes AND plain patch-id look identical
        script.write_bytes(b"#!/bin/sh\necho 'diff --git a/src/secret.txt b/src/secret.txt'\n"
                           b"echo '--- a/src/secret.txt'\necho '+++ b/src/secret.txt'\n"
                           b"echo '@@ -0,0 +1 @@'\necho '+approved content'\n")
        repo.git("config", "diff.external", str(script).replace("\\", "/"))
    repo.checkout("pr", main0)
    approved = repo.commit({"src/secret.txt": b"approved content\n"}, "pr: add secret")
    repo.checkout("pr-hidden", main0)
    proposed = repo.commit({"src/secret.txt": b"DIFFERENT content\n"}, "pr: add secret")
    return repo, (approved, main0), (proposed, main0)


def build_hostile_textconv(tmp):
    return _hostile_hiding_repo(tmp, "textconv")


def build_hostile_external_diff(tmp):
    return _hostile_hiding_repo(tmp, "external")


def build_legit_rebase_hostile_config(tmp):
    repo, approved, proposed = build_legit_rebase(tmp)
    for key, value in (("diff.noprefix", "true"), ("color.ui", "always"), ("color.diff", "always"),
                       ("core.quotePath", "false"), ("diff.algorithm", "histogram"), ("diff.context", "9"),
                       ("diff.renames", "copies"), ("diff.suppressBlankEmpty", "true")):
        repo.git("config", key, value)
    order = repo.path / ".git" / "order.txt"
    order.write_bytes(b"src/*\n")
    repo.git("config", "diff.orderFile", str(order).replace("\\", "/"))
    return repo, approved, proposed


MULTI = b"".join(b"line %02d\n" % i for i in range(1, 41))
MULTI_EDIT = MULTI.replace(b"line 05\n", b"line 05 pr\n").replace(b"line 30\n", b"line 30 pr\n")


def build_multi_hunk_legit_rebase(tmp):
    """Legit rebase of a PR touching two files with two separated hunks each (clean repository)."""
    repo = Repo(tmp)
    main0 = repo.commit({"a/first.txt": MULTI, "z/last.txt": MULTI}, "base")
    repo.checkout("pr", main0)
    approved = repo.commit({"a/first.txt": MULTI_EDIT, "z/last.txt": MULTI_EDIT}, "pr: two files two hunks")
    repo.checkout("main", main0)
    main1 = repo.commit({"docs/notes.txt": b"unrelated main change\n"}, "main advances")
    repo.checkout("pr-rebased", main1)
    proposed = repo.commit({"a/first.txt": MULTI_EDIT, "z/last.txt": MULTI_EDIT}, "pr: two files two hunks")
    return repo, (approved, main0), (proposed, main1)


def build_multi_hunk_hostile_order_and_inter_hunk(tmp):
    """Same commits as build_multi_hunk_legit_rebase; afterwards the repository reverses the file order and merges
    nearby hunks (Lead 2026-11-09T07:27:50Z). Observed against Tools e89f85b5: orderFile changes the diff bytes and
    interHunkContext changes the bytes and the stable patch-id unless the diff command pins both."""
    repo, approved, proposed = build_multi_hunk_legit_rebase(tmp)
    order = repo.path / ".git" / "order.txt"
    order.write_bytes(b"z/*\na/*\n")
    repo.git("config", "diff.orderFile", str(order).replace("\\", "/"))
    repo.git("config", "diff.interHunkContext", "20")
    return repo, approved, proposed


HOSTILE_TWINS = {"legit_rebase_under_hostile_repo_config": build_legit_rebase,
                 "multi_hunk_legit_rebase_under_hostile_order_and_inter_hunk": build_multi_hunk_legit_rebase}


def build_side_commit_base_substitution(tmp):
    """RCO2 hazard (Lead 2026-11-09T07:25:55Z): P was reviewed on main M0. An unreviewed side commit X is made from
    M0 and P is reapplied on X. With the caller-supplied base X the per-base diff bytes and patch-id equal the
    reviewed ones, yet merging the new head into the real main also brings X. main itself never contains X."""
    repo, main0 = _start(tmp)
    repo.checkout("pr", main0)
    approved = repo.commit(_pr_files(), "pr: add feature")
    repo.checkout("side", main0)
    side = repo.commit({"src/unreviewed.txt": b"unreviewed side change\n"}, "side: unreviewed change")
    repo.checkout("pr-on-side", side)
    proposed = repo.commit(_pr_files(), "pr: add feature")
    repo.git("checkout", "-q", "main")
    return repo, (approved, main0), (proposed, side)


# Gate-level cases: content identity holds for the SUPPLIED bases, so the refusal must come from the gate checking the
# supplied base against the base branch in the repository. Kept apart from BUILDERS (pure content cases).
GATE_BUILDERS = {"side_commit_base_substitution": build_side_commit_base_substitution}

BUILDERS = {
    "legit_rebase_onto_advanced_main": build_legit_rebase,
    "message_only_amend": build_message_only,
    "squash_same_tree": build_squash,
    "content_changing_rebase": build_content_changing_rebase,
    "whitespace_same_patch_id": build_whitespace_collision,
    "binary_content_change": build_binary_change,
    "mode_only_change": build_mode_only,
    "submodule_pointer_change": build_submodule_pointer,
    "rename_to_different_target": build_rename_target,
    "empty_proposed_diff": build_empty_diff,
    "multiple_merge_bases": build_multiple_merge_bases,
    "approved_head_object_missing": build_missing_object,
    "hostile_textconv_hides_change": build_hostile_textconv,
    "hostile_external_diff_hides_change": build_hostile_external_diff,
    "legit_rebase_under_hostile_repo_config": build_legit_rebase_hostile_config,
    "multi_hunk_legit_rebase_under_hostile_order_and_inter_hunk": build_multi_hunk_hostile_order_and_inter_hunk,
}


@pytest.fixture(scope="module")
def histories(tmp_path_factory):
    built = {}
    for name, builder in {**BUILDERS, **GATE_BUILDERS}.items():
        repo, approved, proposed = builder(tmp_path_factory.mktemp(name) / "repo")
        built[name] = {"repo": repo, "approved": {"head": approved[0], "base_sha": approved[1]},
                       "proposed": {"head": proposed[0], "base_sha": proposed[1]}}
    return built


def _facts(history, *, pinned=True):
    repo = history["repo"]
    out = {}
    for side in ("approved", "proposed"):
        head, base = history[side]["head"], history[side]["base_sha"]
        exists = subprocess.run([GIT, "cat-file", "-e", head + "^{commit}"], cwd=repo.path, capture_output=True,
                                env=repo.env()).returncode == 0
        out[side] = repo.identity(base, head, pinned=pinned) if exists else {
            "merge_base_count": 0, "diff_sha256": None, "diff_bytes": None, "patch_id": None}
    return out


# --------------------------------------------------------------------------------------------------------------
# Layer 1: GREEN reproduction facts (actual git output, recorded in git_reproductions.json).
# --------------------------------------------------------------------------------------------------------------
def test_every_git_case_is_recorded_and_classified():
    assert set(REPRODUCTIONS["cases"]) == set(BUILDERS)
    for name, case in REPRODUCTIONS["cases"].items():
        assert case["expected_carry"] in (True, False), name
        assert case["classification"], name
        if not case["expected_carry"]:
            assert case["content_refusal_class"] in CONTRACT["content_refusal_patterns"], name
    for case in SYNTHETIC["cases"]:
        assert case["expected_carry"] or case["reason"] in CONTRACT["gate_reason_patterns"], case["id"]


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_git_reproduction_matches_the_recorded_facts(histories, name):
    assert _facts(histories[name]) == REPRODUCTIONS["cases"][name]["facts"]


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_recorded_expectation_follows_from_bytes_and_patch_id_both(name):
    """The corpus's own oracle: carry is possible only when bytes AND patch-id are present and both equal."""
    facts = REPRODUCTIONS["cases"][name]["facts"]
    a, p = facts["approved"], facts["proposed"]
    identical = (a["merge_base_count"] == 1 and p["merge_base_count"] == 1 and a["diff_bytes"]
                 and a["patch_id"] is not None and a["patch_id"] == p["patch_id"]
                 and a["diff_sha256"] == p["diff_sha256"])
    expected = REPRODUCTIONS["cases"][name]["expected_carry"]
    assert expected == bool(identical), name


def test_whitespace_case_is_a_real_patch_id_collision():
    facts = REPRODUCTIONS["cases"]["whitespace_same_patch_id"]["facts"]
    assert facts["approved"]["patch_id"] == facts["proposed"]["patch_id"] is not None
    assert facts["approved"]["diff_sha256"] != facts["proposed"]["diff_sha256"]


def test_empty_diff_has_no_patch_id_and_criss_cross_has_two_merge_bases():
    cases = REPRODUCTIONS["cases"]
    assert cases["empty_proposed_diff"]["facts"]["proposed"] == {
        "merge_base_count": 1, "diff_sha256": hashlib.sha256(b"").hexdigest(), "diff_bytes": 0, "patch_id": None}
    assert cases["multiple_merge_bases"]["facts"]["proposed"]["merge_base_count"] == 2
    assert cases["approved_head_object_missing"]["facts"]["approved"]["merge_base_count"] == 0


@pytest.mark.parametrize("name", ["hostile_textconv_hides_change", "hostile_external_diff_hides_change"])
def test_an_unpinned_diff_would_hide_a_real_change(histories, name):
    """Repository diff drivers make different content look identical to a plain `git diff`; the pinned command
    shows the difference. Observed with git 2.54: the external driver makes the plain bytes identical; textconv
    leaves only the abbreviated index line different and the plain stable patch-id identical (patch-id ignores
    index lines). An implementation that runs plain `git diff` would carry approvals across a change."""
    plain = _facts(histories[name], pinned=False)
    pinned = _facts(histories[name])
    plain_bytes_equal = plain["approved"]["diff_sha256"] == plain["proposed"]["diff_sha256"]
    plain_patch_id_equal = plain["approved"]["patch_id"] == plain["proposed"]["patch_id"] is not None
    assert plain_bytes_equal or plain_patch_id_equal
    assert pinned["approved"]["diff_sha256"] != pinned["proposed"]["diff_sha256"]
    assert pinned["approved"]["patch_id"] != pinned["proposed"]["patch_id"]


def _is_ancestor(repo, commit, ref):
    return subprocess.run([GIT, "merge-base", "--is-ancestor", commit, ref], cwd=repo.path, capture_output=True,
                          env=repo.env()).returncode == 0


def gate_level_facts(history):
    repo = history["repo"]
    main_tip = repo.rev("refs/heads/main")
    approved, proposed = history["approved"], history["proposed"]
    return {
        "approved_with_supplied_base": repo.identity(approved["base_sha"], approved["head"]),
        "proposed_with_supplied_base": repo.identity(proposed["base_sha"], proposed["head"]),
        "proposed_against_base_ref_tip": repo.identity(main_tip, proposed["head"]),
        "supplied_proposed_base_is_base_ref_tip": proposed["base_sha"] == main_tip,
        "supplied_proposed_base_on_base_ref": _is_ancestor(repo, proposed["base_sha"], "refs/heads/main"),
    }


def test_every_gate_level_case_is_recorded():
    assert set(REPRODUCTIONS["gate_level_cases"]) == set(GATE_BUILDERS)
    for name, case in REPRODUCTIONS["gate_level_cases"].items():
        assert case["expected_carry"] is False and case["classification"], name


@pytest.mark.parametrize("name", sorted(GATE_BUILDERS))
def test_gate_level_reproduction_matches_the_recorded_facts(histories, name):
    assert gate_level_facts(histories[name]) == REPRODUCTIONS["gate_level_cases"][name]["facts"]


def test_side_commit_base_substitution_hazard_is_real():
    """With the supplied side base the content looks identical (bytes AND patch-id), but against the real base
    branch tip the proposed head carries the unreviewed side change, and the supplied base is not on main."""
    facts = REPRODUCTIONS["gate_level_cases"]["side_commit_base_substitution"]["facts"]
    approved, supplied = facts["approved_with_supplied_base"], facts["proposed_with_supplied_base"]
    real = facts["proposed_against_base_ref_tip"]
    assert approved["merge_base_count"] == supplied["merge_base_count"] == real["merge_base_count"] == 1
    assert approved["patch_id"] == supplied["patch_id"] is not None
    assert approved["diff_sha256"] == supplied["diff_sha256"]
    assert real["diff_sha256"] != approved["diff_sha256"] and real["patch_id"] != approved["patch_id"]
    assert facts["supplied_proposed_base_is_base_ref_tip"] is False
    assert facts["supplied_proposed_base_on_base_ref"] is False


def test_existing_cases_supply_the_base_ref_tip_as_proposed_base(histories):
    """The base-branch rule added for the gate-level case does not change any existing verdict: every content case
    supplies the current base branch tip as the proposed base and an approved base on the base branch."""
    for name in BUILDERS:
        history = histories[name]
        repo = history["repo"]
        assert history["proposed"]["base_sha"] == repo.rev("refs/heads/main"), name
        assert _is_ancestor(repo, history["approved"]["base_sha"], "refs/heads/main"), name


def _clean_twin(name, tmp_path, histories):
    repo, approved, proposed = HOSTILE_TWINS[name](tmp_path / "clean")
    hostile = histories[name]
    assert approved[0] == hostile["approved"]["head"] and proposed[0] == hostile["proposed"]["head"]
    return {"repo": repo, "approved": {"head": approved[0], "base_sha": approved[1]},
            "proposed": {"head": proposed[0], "base_sha": proposed[1]}}


@pytest.mark.parametrize("name", sorted(HOSTILE_TWINS))
def test_pinned_diff_bytes_ignore_hostile_repository_settings(histories, tmp_path, name):
    hostile = histories[name]
    clean_history = _clean_twin(name, tmp_path, histories)
    assert _facts(hostile) == _facts(clean_history)
    assert _facts(hostile, pinned=False) != _facts(clean_history, pinned=False)


# --------------------------------------------------------------------------------------------------------------
# Layer 2: RED contract tests against the entry points named in contract.json.
# --------------------------------------------------------------------------------------------------------------
def _entry_point(key):
    target = CONTRACT["entry_points"][key]
    module_name, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - RED until implemented
        pytest.fail(f"RED: carry-forward entry point {target} is not importable yet ({exc})")
    fn = getattr(module, attr, None)
    if not callable(fn):
        pytest.fail(f"RED: carry-forward entry point {target} is not implemented yet (contract.json)")
    return fn


ROSTER = {"codex-lead-1": "11111111-1111-4111-8111-111111111111",
          "codex-tools-1": "22222222-2222-4222-8222-222222222222",
          "claude-rco-1": "33333333-3333-4333-8333-333333333333",
          "claude-rco-2": "44444444-4444-4444-8444-444444444444"}
TASK = "fable-5/corpus-pr-9001"
PR = 9001
REPOSITORY = "Ahkeratmehilaiset/waggledance-swarm"
REQUIRED_CI = ["test (3.11)", "test (3.12)", "test (3.13)"]


def event_sha256(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
                          .encode("ascii")).hexdigest()


def _event(agent, typ, status, head, minute, *, task=TASK, message="", payload_key="exact_head", uuid=None):
    event = {"ts_utc": "2026-11-09T02:%02d:00.0000000Z" % minute, "agent": agent, "type": typ, "status": status,
             "task_id": task, "message": message, "payload": {payload_key: head} if head else {}}
    if uuid is not False:
        event["agent_uuid"] = ROSTER.get(agent, "99999999-9999-4999-8999-999999999999") if uuid is None else uuid
    return event


def approvals(head):
    return [_event("codex-lead-1", "decision", "build_consensus", head, 1),
            _event("codex-tools-1", "decision", "build_consensus", head, 2),
            _event("claude-rco-1", "decision", "rco_pass", head, 3)]


def ci_runs(head, conclusion="success", status="completed"):
    return [{"head": head, "name": name, "status": status, "conclusion": conclusion} for name in REQUIRED_CI]


def base_evidence(history):
    approved, proposed = history["approved"]["head"], history["proposed"]["head"]
    side = {"repository": REPOSITORY, "pr_number": PR, "base_ref": "main"}
    return {
        "schema": CONTRACT["evidence_schema"],
        "repo_path": str(history["repo"].path),
        "task_id": TASK,
        "pr_author": "fable-5",  # a non-voting author: no build slot is waived in the baseline (Tools 07:30:36Z)
        "approved": dict(side, head=approved, base_sha=history["approved"]["base_sha"]),
        "proposed": dict(side, head=proposed, base_sha=history["proposed"]["base_sha"]),
        "roster": dict(ROSTER),
        "recognized_rco_agents": ["claude-rco-1", "claude-rco-2"],
        "events": approvals(approved),
        "accepted_queue_complete": True,
        "ci": {"required": list(REQUIRED_CI), "runs": ci_runs(proposed)},
    }


def _apply(evidence, mutation):
    """Structured synthetic mutations named in synthetic_cases.json (no free-form code in the fixture)."""
    ev = copy.deepcopy(evidence)
    old, new = ev["approved"]["head"], ev["proposed"]["head"]
    third = hashlib.sha1(b"corpus: an unrelated third head").hexdigest()
    op = mutation["op"]
    if op == "none":
        pass
    elif op == "set":
        target = ev
        *path, last = mutation["path"]
        for key in path:
            target = target[key]
        target[last] = mutation["value"]
    elif op == "event_field":
        event = ev["events"][mutation["index"]]
        if mutation.get("delete"):
            event.pop(mutation["field"], None)
        else:
            event[mutation["field"]] = mutation["value"]
    elif op == "drop_event":
        del ev["events"][mutation["index"]]
    elif op == "rebind_approvals":
        ev["events"] = approvals({"old": old, "new": new, "third": third}[mutation["to"]])
    elif op == "author_without_own_vote":
        ev["pr_author"] = mutation["author"]
        del ev["events"][mutation["index"]]
    elif op == "duplicate_event":
        ev["events"].append(copy.deepcopy(ev["events"][mutation["index"]]))
    elif op == "prose_heads":
        heads = {"old": old, "new": new}
        event = ev["events"][2]
        event["payload"] = {}
        event["message"] = "RCO pass for " + " and also ".join(heads[h] for h in mutation["heads"])
    elif op == "append_events":
        heads = {"old": old, "new": new, "third": third, "none": None}
        for spec in mutation["events"]:
            ev["events"].append(_event(spec["agent"], spec["type"], spec["status"], heads[spec["head"]],
                                       spec["minute"], task=spec.get("task", TASK),
                                       payload_key=spec.get("payload_key", "exact_head")))
    elif op == "ci":
        head = {"old": old, "new": new}[mutation["head"]]
        ev["ci"]["runs"] = ci_runs(head, mutation["conclusion"], mutation["status"])
        if mutation.get("drop_one"):
            ev["ci"]["runs"].pop()
    else:  # pragma: no cover - fixture error
        raise AssertionError("unknown mutation " + op)
    return ev


def _carried_kinds(verdict):
    return {item["kind"] for item in verdict.get("carried", [])}


def _check_verdict(verdict, evidence, expected, reason_class=None):
    assert isinstance(verdict, dict) and verdict.get("carry") in (True, False), verdict
    assert verdict["carry"] is expected, verdict.get("reason")
    if not expected:
        assert reason_class, "every refusal case names the check it must be refused by"
        pattern = CONTRACT["gate_reason_patterns"][reason_class]
        assert re.search(pattern, str(verdict.get("reason", ""))), (reason_class, verdict.get("reason"))
    allowed = set(CONTRACT["carriable_kinds"])
    assert _carried_kinds(verdict) <= allowed
    if verdict["carry"]:
        refs = [item["event_sha256"] for item in verdict["carried"]]
        assert len(refs) == len(set(refs)), "one original event must never count twice"
        by_ref = {event_sha256(e): e for e in evidence["events"]}
        for item in verdict["carried"]:
            original = by_ref[item["event_sha256"]]
            assert item["agent"] == original["agent"] and item["agent_uuid"] == evidence["roster"][original["agent"]]
            assert item["approved_head"] == evidence["approved"]["head"]
        rco = {item["agent"] for item in verdict["carried"] if item["kind"] == "rco_pass"}
        assert rco and rco <= set(evidence["recognized_rco_agents"]) and evidence["pr_author"] not in rco
        build = {item["agent"] for item in verdict["carried"] if item["kind"] == "build_consensus"}
        # Rule 9a waives the author's build slot: the author's own vote is never required and never carried
        assert build == {"codex-lead-1", "codex-tools-1"} - {evidence["pr_author"]}
    else:
        assert not verdict.get("carried"), "a refused carry must not carry anything"


def _identity_kwargs(history):
    """Tools' published convention (e89f85b5, adopted per Lead 07:27:50Z): keyword-only, repo_root a pathlib.Path."""
    return {"repo_root": Path(history["repo"].path),
            "old_base": history["approved"]["base_sha"], "old_head": history["approved"]["head"],
            "new_base": history["proposed"]["base_sha"], "new_head": history["proposed"]["head"]}


def _refused_for(call, refusal_class):
    """Run call; it must raise ValueError whose text matches the contract pattern of the intended check, so a
    refusal for another reason (argument type, unavailable git, wrong check) cannot pass the test."""
    with pytest.raises(ValueError) as caught:
        call()
    pattern = CONTRACT["content_refusal_patterns"][refusal_class]
    assert re.search(pattern, str(caught.value)), (refusal_class, str(caught.value))


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_content_identity_proof_verdict(histories, name):
    """Tools' lower-level proof: derive refuses with ValueError from the intended check unless the content is
    identical; a derived proof verifies against the same tuple."""
    derive = _entry_point("derive_content_identity")
    verify = _entry_point("verify_content_identity")
    kwargs = _identity_kwargs(histories[name])
    case = REPRODUCTIONS["cases"][name]
    if case["expected_carry"]:
        proof = derive(**kwargs)
        assert proof
        assert verify(proof=copy.deepcopy(proof), **kwargs) is not False
    else:
        _refused_for(lambda: derive(**kwargs), case["content_refusal_class"])


@pytest.mark.parametrize("other", ["whitespace_same_patch_id", "message_only_amend", "content_changing_rebase"])
def test_content_identity_proof_does_not_verify_for_another_tuple(histories, other):
    derive = _entry_point("derive_content_identity")
    verify = _entry_point("verify_content_identity")
    proof = derive(**_identity_kwargs(histories["legit_rebase_onto_advanced_main"]))
    _refused_for(lambda: verify(proof=copy.deepcopy(proof), **_identity_kwargs(histories[other])), "rederivation")


def test_content_identity_proof_swapped_heads_does_not_verify(histories):
    derive = _entry_point("derive_content_identity")
    verify = _entry_point("verify_content_identity")
    kwargs = _identity_kwargs(histories["legit_rebase_onto_advanced_main"])
    proof = derive(**kwargs)
    swapped = dict(kwargs, old_base=kwargs["new_base"], old_head=kwargs["new_head"],
                   new_base=kwargs["old_base"], new_head=kwargs["old_head"])
    _refused_for(lambda: verify(proof=copy.deepcopy(proof), **swapped), "rederivation")


@pytest.mark.parametrize("name", sorted(HOSTILE_TWINS))
def test_content_identity_proof_is_independent_of_repository_diff_settings(histories, tmp_path, name):
    """The same commits in a clean repository and in one with hostile diff settings must give the same proof, so a
    receipt re-derived on another clone reproduces it. Against Tools e89f85b5 the multi-hunk case differs
    (diff.orderFile and diff.interHunkContext are not pinned there)."""
    derive = _entry_point("derive_content_identity")
    clean = _clean_twin(name, tmp_path, histories)
    assert derive(**_identity_kwargs(histories[name])) == derive(**_identity_kwargs(clean))


def test_gate_refuses_side_commit_base_substitution(histories):
    """Gate-level: the caller supplies the side commit as the proposed base; content identity holds for that base,
    so only the gate's check of the supplied base against the base branch in the repository can refuse."""
    evaluate = _entry_point("evaluate")
    history = histories["side_commit_base_substitution"]
    evidence = base_evidence(history)
    verdict = evaluate(copy.deepcopy(evidence))
    _check_verdict(verdict, evidence, False, "base")


def test_gate_refuses_side_commit_head_against_the_real_base(histories):
    evaluate = _entry_point("evaluate")
    history = histories["side_commit_base_substitution"]
    evidence = base_evidence(history)
    evidence["proposed"]["base_sha"] = history["repo"].rev("refs/heads/main")
    verdict = evaluate(copy.deepcopy(evidence))
    _check_verdict(verdict, evidence, False, "content")


def test_content_identity_against_the_real_base_refuses_the_side_commit(histories):
    """Content layer, with the real base branch tip as new_base: the identity truthfully differs."""
    derive = _entry_point("derive_content_identity")
    history = histories["side_commit_base_substitution"]
    kwargs = dict(_identity_kwargs(history), new_base=history["repo"].rev("refs/heads/main"))
    _refused_for(lambda: derive(**kwargs), "content_differs")


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_git_case_verdict(histories, name):
    evaluate = _entry_point("evaluate")
    evidence = base_evidence(histories[name])
    verdict = evaluate(copy.deepcopy(evidence))
    _check_verdict(verdict, evidence, REPRODUCTIONS["cases"][name]["expected_carry"], "content")


@pytest.mark.parametrize("case", SYNTHETIC["cases"], ids=lambda c: c["id"])
def test_synthetic_evidence_case_verdict(histories, case):
    evaluate = _entry_point("evaluate")
    evidence = _apply(base_evidence(histories["legit_rebase_onto_advanced_main"]), case["mutation"])
    verdict = evaluate(copy.deepcopy(evidence))
    _check_verdict(verdict, evidence, case["expected_carry"], case.get("reason"))
    if case.get("never_carried_kind"):
        assert case["never_carried_kind"] not in _carried_kinds(verdict)


def test_the_evaluator_does_not_trust_caller_supplied_identity_facts(histories):
    """Caller-asserted equality fields are ignored: whitespace collision stays refused even if the evidence claims
    identical diff hashes and patch ids."""
    evaluate = _entry_point("evaluate")
    history = histories["whitespace_same_patch_id"]
    evidence = base_evidence(history)
    facts = REPRODUCTIONS["cases"]["whitespace_same_patch_id"]["facts"]["approved"]
    for side in ("approved", "proposed"):
        evidence[side].update(diff_sha256=facts["diff_sha256"], patch_id=facts["patch_id"], content_identical=True)
    assert evaluate(evidence)["carry"] is False


def test_receipt_records_both_heads_merge_bases_bytes_patch_id_and_refs(histories):
    evaluate = _entry_point("evaluate")
    history = histories["legit_rebase_onto_advanced_main"]
    evidence = base_evidence(history)
    verdict = evaluate(copy.deepcopy(evidence))
    receipt = verdict["receipt"]
    facts = REPRODUCTIONS["cases"]["legit_rebase_onto_advanced_main"]["facts"]
    for key in CONTRACT["receipt_required_fields"]:
        assert key in receipt, key
    assert receipt["approved_head"] == evidence["approved"]["head"]
    assert receipt["proposed_head"] == evidence["proposed"]["head"]
    assert receipt["approved_diff_sha256"] == receipt["proposed_diff_sha256"] == facts["approved"]["diff_sha256"]
    assert receipt["patch_id"] == facts["approved"]["patch_id"]
    assert sorted(receipt["approval_event_sha256"]) == sorted(event_sha256(e) for e in evidence["events"])
    assert receipt["ci_head"] == evidence["proposed"]["head"]


def test_receipt_diff_command_is_config_independent(histories, tmp_path):
    """The receipt names its exact diff argv; re-running that argv in a clean repository and in a repository with
    hostile settings yields the recorded bytes."""
    evaluate = _entry_point("evaluate")
    hostile = histories["legit_rebase_under_hostile_repo_config"]
    verdict = evaluate(base_evidence(hostile))
    assert verdict["carry"] is True
    receipt = verdict["receipt"]
    argv = receipt["diff_argv"]
    assert isinstance(argv, list) and "--no-ext-diff" in argv and "--no-textconv" in argv and "--binary" in argv
    merge_base = receipt["proposed_merge_base"]
    raw = hostile["repo"].git(*argv, merge_base, receipt["proposed_head"])
    assert hashlib.sha256(raw).hexdigest() == receipt["proposed_diff_sha256"]


@pytest.mark.parametrize("field", ["patch_id", "approved_diff_sha256", "proposed_diff_sha256", "approved_head",
                                   "proposed_head", "pr_number", "approval_event_sha256", "ci_head",
                                   "swap_heads"])
def test_tampered_receipt_does_not_re_derive(histories, field):
    evaluate = _entry_point("evaluate")
    verify = _entry_point("verify_receipt")
    history = histories["legit_rebase_onto_advanced_main"]
    evidence = base_evidence(history)
    receipt = evaluate(copy.deepcopy(evidence))["receipt"]
    assert verify(copy.deepcopy(receipt), copy.deepcopy(evidence)) is True
    bad = copy.deepcopy(receipt)
    if field == "swap_heads":
        bad["approved_head"], bad["proposed_head"] = bad["proposed_head"], bad["approved_head"]
    elif field == "approval_event_sha256":
        bad[field] = bad[field][:-1]
    elif field == "pr_number":
        bad[field] = PR + 1
    else:
        bad[field] = "0" * len(str(bad[field]))
    assert verify(bad, copy.deepcopy(evidence)) is False


def test_a_receipt_from_another_case_does_not_replay(histories):
    evaluate = _entry_point("evaluate")
    verify = _entry_point("verify_receipt")
    legit = base_evidence(histories["legit_rebase_onto_advanced_main"])
    other = base_evidence(histories["message_only_amend"])
    receipt = evaluate(copy.deepcopy(other))["receipt"]
    assert verify(receipt, legit) is False
