#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Carry-forward proof: may approvals given at a reviewed PR head count at its content-identical base update?

Operator 2026-10-12 (round 5; plan doc section "Carry-forward-todiste: kaikki ehdot vaaditaan"). Rule 9a
binds every approval to the exact head, so each base update strands them even when the PR content is
byte-identical (the 2026-07-02 audit: the 9a carry-forward text was never implemented). Every condition
below is required, and each is re-derived from git objects here; no receipt, flag or event is trusted.

``verify_carry_forward(repo, old_head, new_head, main_head)`` proves conditions 2-6 for one update step
and refuses with the first failing check as ``reason``, in this order:

* ``invalid_input``: the three heads are full lower-case object names; ``same_head``: they differ;
* ``grafts_present`` / ``shallow_repository``: no graft file is in effect and the repository is not
  shallow (a shallow boundary hides parents). Every git call runs with ``--no-replace-objects`` so
  ``refs/replace`` cannot make the checks read other objects than the commits name (count recorded),
  without the inherited variables that pick another repository, object store, index or graft file
  (``GIT_DIR``, ``GIT_WORK_TREE``, ...; the stripped names are recorded, never the values) and with
  ``GIT_TERMINAL_PROMPT=0``;
* ``unresolvable_commit``: each name is a commit in the repository;
* condition 4: ``new_head_not_a_two_parent_merge`` (exactly two parents, so an extra commit on top or
  a rebase needs new votes), ``first_parent_not_old_head`` (no force-push: the reviewed head is the
  first parent, so an extra commit below the merge needs new votes too) and ``second_parent_not_main``
  (the other parent is the current main tip);
* ``already_contains_main_or_empty``: the old head and main are not ancestors of each other;
* ``old_base_not_unique``: the old base, ``git merge-base --all old_head main_head``, is one commit (a
  criss-cross history has no single reviewed delta). A stacked PR whose reviewed base is not that merge
  base is refused through the overlap or delta checks, never carried;
* ``empty_pr_delta``: the PR (old base..old head) changes at least one path;
* condition 6, ``gate_or_governance_path``: no PR path is a gate, governance or charter-denylisted path
  (``classify_ab`` a-hits) or this module. Paths that are merely unrecognised there may carry;
* condition 2: ``merge_conflict`` / ``merge_tree_failed`` (``git merge-tree --write-tree
  --merge-base=<old base> <main> <old head>`` exits 0) and ``tree_mismatch`` (the new head's tree is
  exactly that tree: no hand edit);
* condition 5, ``paths_overlap``: no PR path is in the old base..main delta, compared case-folded (on
  a case-insensitive checkout ``Foo.txt`` and ``foo.txt`` are one file). That is RCO1's rule
  ("overlap only where the file is the same blob in both bases"): a path whose blob is equal in both
  bases is not in the delta. Any overlap needs fresh review, even when the merge is clean;
  ``case_collision``: no PR path shares a case-folded name with another file or a directory in the
  new tree, and no parent directory of a PR path case-folds to a file there;
* condition 3: ``delta_changed`` (the per-file delta main..new head equals old base..old head entry by
  entry: path, status, both modes, both blob ids; ``git diff-tree -r -z --no-renames``) and
  ``patch_id_changed`` (``git patch-id --stable`` of both deltas, ``git diff`` with pinned options).

A git failure is ``git_failed`` with the failing argv and exit code; any other error is
``internal_error``. Only objects are written (merge-tree trees); no ref, index or worktree changes.

``evaluate_carry_forward(...)`` adds conditions 1 and 7 and returns the decision with its receipt
(schema ``wd.carry-forward-receipt.v1``). It runs ``verify_carry_forward`` itself, so a caller cannot
hand it a proof. It carries only when the proof holds, the old head's verdict is a full DIRECT verdict
for exactly that head (``consensus_ok``, ``ci_all_green``, ``charter_ok`` and ``veto_clear`` are each
``True``, ``approvals_carried`` is ``False``: carried approvals never carry again), and CI and the veto
check pass again at the new head (``True`` only; truthy values do not count). The receipt stores both
heads, main, the old base, both patch-ids, the merge-tree oid, the path sets, the per-file entries and
the inputs, so a consumer can re-derive the decision.

Not in this module: computing the old head's verdict and the new head's CI and veto results is the
merge gate's job, and wiring this module into the gate is a separate (a)-class change. This module is
(a)-class gate code (CLAUDE.md rule 9b scope); its own path never carries approvals.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_standing_consensus_sign_class import classify_ab  # noqa: E402

PROOF_SCHEMA = "wd.carry-forward-proof.v1"
RECEIPT_SCHEMA = "wd.carry-forward-receipt.v1"
SELF_PATH = "tools/bridge_carry_forward_proof.py"
VERDICT_FIELDS = ("consensus_ok", "ci_all_green", "charter_ok", "veto_clear")
_OBJECT_NAME = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REPOSITORY_ENV = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                             "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_NAMESPACE", "GIT_GRAFT_FILE", "GIT_SHALLOW_FILE",
                             "GIT_REPLACE_REF_BASE"})
_RAW_META = re.compile(r"^:(\d{6}) (\d{6}) ([0-9a-f]+) ([0-9a-f]+) ([A-Z])$")
# git diff for patch-ids: every option that changes the text of a diff is pinned
_PATCH_DIFF = ["-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false", "-c", "core.quotePath=true",
               "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--full-index",
               "--src-prefix=a/", "--dst-prefix=b/"]


class _Refused(Exception):
    def __init__(self, reason: str, argv: Sequence[str] | None = None, code: int | None = None):
        super().__init__(reason)
        self.reason = reason
        self.command = None if argv is None else {"argv": list(argv), "exit": code}


def _git(repo: str, args: Sequence[str], stdin: bytes | None = None) -> tuple[int, bytes]:
    env = {key: value for key, value in os.environ.items() if key.upper() not in _REPOSITORY_ENV}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(["git", "--no-optional-locks", "--no-replace-objects", "-C", repo, *args],
                              input=stdin, capture_output=True, env=env)
    except OSError:
        return 127, b""
    return proc.returncode, proc.stdout


def _must(repo: str, args: Sequence[str], stdin: bytes | None = None) -> bytes:
    code, out = _git(repo, args, stdin)
    if code != 0:
        raise _Refused("git_failed", args, code)
    return out


def _text(out: bytes) -> str:
    return out.decode("utf-8", errors="replace").strip()


def _is_ancestor(repo: str, ancestor: str, descendant: str) -> bool:
    args = ["merge-base", "--is-ancestor", ancestor, descendant]
    code, _ = _git(repo, args)
    if code not in (0, 1):
        raise _Refused("git_failed", args, code)
    return code == 0


def _delta(repo: str, old: str, new: str) -> dict[str, dict[str, str]]:
    """Per-file delta between two tree-ishes: path -> status, modes and object ids, no renames."""
    args = ["diff-tree", "-r", "-z", "--no-renames", "--no-commit-id", old, new]
    fields = _must(repo, args).split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise _Refused("git_failed", args, 0)
    delta = {}
    for meta, path in zip(fields[::2], fields[1::2]):
        match = _RAW_META.match(meta.decode("ascii", errors="replace"))
        if not match:
            raise _Refused("git_failed", args, 0)
        old_mode, new_mode, old_oid, new_oid, status = match.groups()
        delta[path.decode("utf-8", errors="surrogateescape")] = {
            "status": status, "old_mode": old_mode, "new_mode": new_mode, "old_oid": old_oid, "new_oid": new_oid}
    return delta


def _patch_id(repo: str, old: str, new: str) -> str | None:
    diff = _must(repo, [*_PATCH_DIFF, old, new])
    value = _text(_must(repo, ["patch-id", "--stable"], stdin=diff)).split()
    return value[0] if value else None


def _case_collisions(repo: str, tree: str, paths: Sequence[str]) -> list[str]:
    """PATHS that share a case-folded name with another file or a directory in TREE (one file on Windows)."""
    names = [name.decode("utf-8", errors="surrogateescape") for name in
             _must(repo, ["ls-tree", "-r", "-z", "--name-only", "--full-tree", tree]).split(b"\0") if name]
    files: dict[str, set[str]] = {}
    dirs: set[str] = set()
    for name in names:
        files.setdefault(name.casefold(), set()).add(name)
        parts = name.split("/")
        dirs.update("/".join(parts[:end]).casefold() for end in range(1, len(parts)))
    hits = set()
    for path in paths:
        parts = path.split("/")
        if (len(files.get(path.casefold(), ())) > 1 or path.casefold() in dirs
                or any("/".join(parts[:end]).casefold() in files for end in range(1, len(parts)))):
            hits.add(path)
    return sorted(hits)


def _gate_paths(paths: Sequence[str]) -> list[str]:
    hits = set(classify_ab(list(paths)).get("a_hits") or [])
    hits.update(path.lower() for path in paths if path.replace("\\", "/").lower() == SELF_PATH)
    return sorted(hits)


def _new_proof(old_head: Any, new_head: Any, main_head: Any) -> dict[str, Any]:
    return {
        "schema": PROOF_SCHEMA, "ok": False, "reason": None,
        "old_head": old_head, "new_head": new_head, "main_head": main_head, "old_base": None,
        "new_head_parents": None, "merge_tree": None, "merge_tree_exit": None,
        "pr_paths": None, "base_delta_paths": None, "overlap_paths": None, "gate_governance_paths": None,
        "case_collisions": None, "entries": None, "patch_id_old": None, "patch_id_new": None,
        "stripped_git_env": sorted({key.upper() for key in os.environ if key.upper() in _REPOSITORY_ENV}),
        "replace_refs": None, "grafts": None, "shallow": None, "failed_command": None, "error_type": None,
    }


def _check(repo: str, proof: dict[str, Any]) -> None:
    old_head, new_head, main = proof["old_head"], proof["new_head"], proof["main_head"]
    if not all(type(name) is str and _OBJECT_NAME.match(name) for name in (old_head, new_head, main)):
        raise _Refused("invalid_input")
    if new_head == old_head:
        raise _Refused("same_head")
    proof["replace_refs"] = len(_text(_must(repo, ["for-each-ref", "--format=%(refname)", "refs/replace/"]))
                                .splitlines())
    grafts = _text(_must(repo, ["rev-parse", "--git-path", "info/grafts"]))
    proof["grafts"] = os.path.exists(grafts if os.path.isabs(grafts) else os.path.join(repo, grafts))
    if proof["grafts"]:
        raise _Refused("grafts_present")
    proof["shallow"] = _text(_must(repo, ["rev-parse", "--is-shallow-repository"])) == "true"
    if proof["shallow"]:
        raise _Refused("shallow_repository")
    for name in (old_head, new_head, main):
        code, out = _git(repo, ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{name}^{{commit}}"])
        if code != 0 or _text(out) != name:
            raise _Refused("unresolvable_commit")

    # Condition 4: one merge step whose first parent is the reviewed head and whose other is main.
    parents = _text(_must(repo, ["rev-list", "--parents", "-n", "1", new_head])).split()
    proof["new_head_parents"] = parents[1:]
    if len(parents) != 3 or parents[0] != new_head:
        raise _Refused("new_head_not_a_two_parent_merge")
    if parents[1] != old_head:
        raise _Refused("first_parent_not_old_head")
    if parents[2] != main:
        raise _Refused("second_parent_not_main")
    if _is_ancestor(repo, main, old_head) or _is_ancestor(repo, old_head, main):
        raise _Refused("already_contains_main_or_empty")
    bases = _text(_must(repo, ["merge-base", "--all", old_head, main])).split()
    if len(bases) != 1:
        raise _Refused("old_base_not_unique")
    base = proof["old_base"] = bases[0]

    pr = _delta(repo, base, old_head)
    proof["pr_paths"] = sorted(pr)
    if not pr:
        raise _Refused("empty_pr_delta")
    # Condition 6: gate and governance PRs never carry approvals.
    proof["gate_governance_paths"] = _gate_paths(proof["pr_paths"])
    if proof["gate_governance_paths"]:
        raise _Refused("gate_or_governance_path")
    base_delta = _delta(repo, base, main)
    proof["base_delta_paths"] = sorted(base_delta)
    base_folded = {path.casefold() for path in base_delta}
    proof["overlap_paths"] = sorted(path for path in pr if path.casefold() in base_folded)

    # Condition 2: the new tree is merge-tree's, exit 0, unedited.
    code, out = _git(repo, ["merge-tree", "--write-tree", "--no-messages", f"--merge-base={base}", main, old_head])
    proof["merge_tree_exit"] = code
    if code == 1:
        raise _Refused("merge_conflict")
    tree = _text(out).split()[0] if _text(out) else ""
    if code != 0 or not _OBJECT_NAME.match(tree):
        raise _Refused("merge_tree_failed")
    proof["merge_tree"] = tree
    if _text(_must(repo, ["rev-parse", f"{new_head}^{{tree}}"])) != tree:
        raise _Refused("tree_mismatch")

    # Condition 5: no PR path in the base delta (same blob in both bases = not in the delta), case-folded,
    # and no PR path that is one file with another file or a directory on a case-insensitive checkout.
    if proof["overlap_paths"]:
        raise _Refused("paths_overlap")
    proof["case_collisions"] = _case_collisions(repo, tree, proof["pr_paths"])
    if proof["case_collisions"]:
        raise _Refused("case_collision")

    # Condition 3: every PR file's blob and the stable patch-id are unchanged.
    carried = _delta(repo, main, new_head)
    proof["entries"] = [{"path": path, "old": pr.get(path), "new": carried.get(path)}
                        for path in sorted(set(pr) | set(carried))]
    if carried != pr:
        raise _Refused("delta_changed")
    proof["patch_id_old"] = _patch_id(repo, base, old_head)
    proof["patch_id_new"] = _patch_id(repo, main, new_head)
    if proof["patch_id_old"] != proof["patch_id_new"]:
        raise _Refused("patch_id_changed")
    proof["ok"], proof["reason"] = True, "content_identical_base_update"


def verify_carry_forward(*, repo: str, old_head: str, new_head: str, main_head: str) -> dict[str, Any]:
    """Prove conditions 2-6 for one base-update step from git objects; fail closed on anything else."""
    proof = _new_proof(old_head, new_head, main_head)
    try:
        _check(repo, proof)
    except _Refused as refusal:
        proof["ok"], proof["reason"], proof["failed_command"] = False, refusal.reason, refusal.command
    except Exception as exc:  # fail closed: any other error is a refusal
        proof["ok"], proof["reason"], proof["error_type"] = False, "internal_error", type(exc).__name__
    return proof


def evaluate_carry_forward(*, repo: str, old_head: str, new_head: str, main_head: str,
                           old_head_verdict: Any, new_head_ci_all_green: Any,
                           new_head_veto_clear: Any) -> dict[str, Any]:
    """Conditions 1-7: whether the old head's approvals count at the new head, with the receipt."""
    proof = verify_carry_forward(repo=repo, old_head=old_head, new_head=new_head, main_head=main_head)
    reasons: list[str] = []
    if proof["ok"] is not True:
        reasons.append(f"carry-forward proof failed: {proof['reason']}")
    verdict = dict(old_head_verdict) if isinstance(old_head_verdict, Mapping) else None
    if verdict is None:
        reasons.append("old head verdict is missing")
    else:
        if verdict.get("head") != old_head:
            reasons.append("old head verdict is for another head")
        for field in VERDICT_FIELDS:
            if verdict.get(field) is not True:
                reasons.append(f"old head verdict {field} is not True")
        if verdict.get("approvals_carried") is not False:
            reasons.append("old head verdict approvals_carried is not False (carried approvals never carry again)")
    if new_head_ci_all_green is not True:
        reasons.append("new head ci_all_green is not True (CI must pass again at the new head)")
    if new_head_veto_clear is not True:
        reasons.append("new head veto_clear is not True (the veto check must pass again at the new head)")
    receipt = {
        "schema": RECEIPT_SCHEMA, "carried": not reasons, "reasons": list(reasons),
        "old_head": old_head, "new_head": new_head, "main_head": main_head, "old_base": proof["old_base"],
        "proof_reason": proof["reason"], "merge_tree": proof["merge_tree"],
        "patch_id_old": proof["patch_id_old"], "patch_id_new": proof["patch_id_new"],
        "pr_paths": proof["pr_paths"], "base_delta_paths": proof["base_delta_paths"],
        "overlap_paths": proof["overlap_paths"], "gate_governance_paths": proof["gate_governance_paths"],
        "case_collisions": proof["case_collisions"],
        "entries": proof["entries"], "new_head_parents": proof["new_head_parents"],
        "stripped_git_env": proof["stripped_git_env"],
        "replace_refs": proof["replace_refs"], "grafts": proof["grafts"], "shallow": proof["shallow"],
        "old_head_verdict": verdict, "new_head_ci_all_green": new_head_ci_all_green,
        "new_head_veto_clear": new_head_veto_clear,
    }
    return {"carried": not reasons, "reasons": reasons, "proof": proof, "receipt": receipt}
