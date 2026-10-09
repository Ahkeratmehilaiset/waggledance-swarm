"""RS7 on the legacy core resolver (COMPOSE-RS7-LEGACY-PYTHON, Tools v5 proof 56E99791).

The core claim path (waggledance.core.work_queue -> bridge_resource_scope) made a repository scope relative to
whatever cwd the claim came from, so one file claimed as "sub/file.txt" from the top level and as "file.txt" from
"sub" (or from a repository nested at "nested") was two disjoint scopes and both writers were persisted. The core
resolver now applies the v2 contract: a claim cwd must be a git top level and must not be nested inside another one;
"*" and a claim without cwd keep their previous handling.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from waggledance.core.bridge_resource_scope import resolve_resources, resources_overlap
from waggledance.core.work_queue import WorkQueueError, claim_task

_ENV_PREFIXES = ("AGENT_BRIDGE_", "WD_")
_IDENTITY_REFUSALS = ("identity_mismatch", "already claimed", "held by another session", "force claim")


def _git_dir(path: Path) -> None:
    """A minimal git top level: .git with HEAD, objects/ and refs/."""
    for child in ("objects", "refs"):
        (path / ".git" / child).mkdir(parents=True)
    (path / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


@pytest.fixture
def layout(tmp_path, monkeypatch):
    outer = [str(p) for p in (tmp_path, *tmp_path.parents) if (p / ".git").exists()]
    assert outer == [], f"tmp_path must lie outside every repository for this fixture: {outer}"
    for key in list(os.environ):
        if key.upper().startswith(_ENV_PREFIXES):
            monkeypatch.delenv(key)
    root = tmp_path / "outer"
    sub, nested, bridge = root / "sub", root / "nested", tmp_path / "bridge"
    for path in (root, sub, nested, bridge):
        path.mkdir(parents=True, exist_ok=True)
    _git_dir(root)
    _git_dir(nested)
    assert not (sub / ".git").exists()   # sub is a plain directory of the same top level
    return {"outer": root, "sub": sub, "nested": nested, "bridge": bridge}


def _claims(bridge: Path) -> dict[str, bytes]:
    claims = bridge / "work_queue" / "claims"
    return {p.name: p.read_bytes() for p in sorted(claims.glob("*.json"))} if claims.exists() else {}


def _claim(monkeypatch, cwd: Path, bridge: Path, agent: str, task_id: str, scope: str):
    monkeypatch.chdir(cwd)
    try:
        claim_task(agent=agent, task_id=task_id, summary="rs7 fixture", mode="write", write_scope=[scope],
                   bridge_root=bridge)
    except WorkQueueError as exc:
        return str(exc)
    return None


# (case, first scope from the top level, second cwd, second scope, expected denial text or None when admitted).
# The second scope names the same absolute file as the first in every case except outer_disjoint.
MATRIX = [
    ("outer_relative_overlap", "file.txt", "outer", "file.txt", "write-scope conflict with active claim"),
    ("outer_absolute_overlap", "file.txt", "outer", "@outer/file.txt", "write-scope conflict with active claim"),
    ("sub_relative_same_target", "sub/file.txt", "sub", "file.txt", "not a repository top level"),
    ("sub_absolute_same_target", "sub/file.txt", "sub", "@sub/file.txt", "not a repository top level"),
    ("nested_relative_same_target", "nested/file.txt", "nested", "file.txt", "nested inside another repository"),
    ("nested_absolute_same_target", "nested/file.txt", "nested", "@nested/file.txt",
     "nested inside another repository"),
    ("outer_disjoint", "file.txt", "outer", "other.txt", None),
    ("outer_star", "file.txt", "outer", "*", "write-scope conflict with active claim"),
    ("sub_star", "sub/file.txt", "sub", "*", "write-scope conflict with active claim"),
    ("nested_star", "nested/file.txt", "nested", "*", "write-scope conflict with active claim"),
]


@pytest.mark.parametrize("case,first,second_cwd,second,denial", MATRIX, ids=[row[0] for row in MATRIX])
def test_a_second_writer_on_the_same_file_is_never_persisted(layout, monkeypatch, case, first, second_cwd, second,
                                                            denial):
    bridge = layout["bridge"]
    assert _claims(bridge) == {}
    assert _claim(monkeypatch, layout["outer"], bridge, "codex-lead-1", f"rs7/{case}/a", first) is None
    after_first = _claims(bridge)
    assert len(after_first) == 1
    if second.startswith("@"):
        name, rest = second[1:].split("/", 1)
        second = str(layout[name] / rest)
    error = _claim(monkeypatch, layout[second_cwd], bridge, "codex-tools-1", f"rs7/{case}/b", second)
    after_second = _claims(bridge)
    if denial is None:
        assert error is None
        assert len(after_second) == 2 and {k: after_second[k] for k in after_first} == after_first
        return
    assert error is not None and denial in error, error
    assert not any(text in error for text in _IDENTITY_REFUSALS), error   # a scope denial, not an identity refusal
    assert after_second == after_first   # nothing new persisted, the first claim's bytes unchanged
    if denial.startswith("write-scope conflict"):
        assert f"rs7/{case}/a" in error


def test_the_sub_and_nested_scopes_still_resolve_from_the_top_level(layout):
    """Twin: from the valid top level the same files keep their repository paths, so the outer writer is unchanged."""
    bridge = str(layout["bridge"])
    for entry, expected in (("sub/file.txt", "sub/file.txt"), (str(layout["sub"] / "file.txt"), "sub/file.txt"),
                            ("nested/file.txt", "nested/file.txt"),
                            (str(layout["nested"] / "file.txt"), "nested/file.txt")):
        (scope,) = resolve_resources([entry], cwd=str(layout["outer"]), bridge_root=bridge)
        assert (scope.kind, scope.path, scope.root) == ("repo", expected, "")


def test_star_and_a_claim_without_cwd_keep_their_handling(layout):
    bridge = str(layout["bridge"])
    for cwd in (layout["sub"], layout["nested"]):
        assert resolve_resources(["*"], cwd=str(cwd), bridge_root=bridge)[0].path == "*"
    (legacy,) = resolve_resources(["sub/file.txt"], cwd="", bridge_root=bridge)
    assert (legacy.kind, legacy.path) == ("repo", "sub/file.txt")
    (other,) = resolve_resources(["sub/file.txt"], cwd=str(layout["outer"]), bridge_root=bridge)
    assert resources_overlap(legacy, other)   # a cwd-less claim stays conservative


def test_repository_logical_paths_overlap_across_top_levels(tmp_path, layout):
    """Two separate top levels (two worktrees) claiming one repository path still conflict; worktree/shared stay
    physical."""
    other = tmp_path / "other"
    other.mkdir()
    _git_dir(other)
    bridge = str(layout["bridge"])
    left = resolve_resources(["src/main.py", ".codex-audit/wd-current-state.json"], cwd=str(layout["outer"]),
                             bridge_root=bridge)
    right = resolve_resources(["src/main.py", ".codex-audit/wd-current-state.json"], cwd=str(other),
                              bridge_root=bridge)
    assert resources_overlap(left[0], right[0])
    assert (left[1].kind, right[1].kind) == ("worktree", "worktree") and not resources_overlap(left[1], right[1])
    (shared,) = resolve_resources(["shared:work_queue/claims"], cwd=str(layout["outer"]), bridge_root=bridge)
    assert shared.kind == "shared"


def _linked(main: Path, worktree: Path, name: str) -> Path:
    admin = main / ".git" / "worktrees" / name
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/x\n", encoding="utf-8")
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    (admin / "gitdir").write_text(str(worktree / ".git") + "\n", encoding="utf-8")
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / ".git").write_text("gitdir: " + str(admin) + "\n", encoding="utf-8")
    return admin


def test_a_linked_worktree_is_a_top_level_and_a_subdirectory_pointer_is_not(tmp_path, layout):
    bridge = str(layout["bridge"])
    linked = tmp_path / "linked"
    _linked(layout["outer"], linked, "linked")
    assert resolve_resources(["src/main.py"], cwd=str(linked), bridge_root=bridge)[0].path == "src/main.py"
    # RS7-L2: git follows "gitdir: ../.git" from a subdirectory, but that is not a top level of its own
    pointer = layout["sub"] / "deeper"
    pointer.mkdir()
    (pointer / ".git").write_text("gitdir: ../../.git\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a repository top level"):
        resolve_resources(["file.txt"], cwd=str(pointer), bridge_root=bridge)


@pytest.mark.parametrize("marker", ["absent", "empty_dir", "empty_file", "no_objects"])
def test_an_invalid_top_level_marker_is_refused(tmp_path, layout, marker):
    cwd = tmp_path / f"cwd-{marker}"
    cwd.mkdir()
    if marker == "empty_dir":
        (cwd / ".git").mkdir()
    elif marker == "empty_file":
        (cwd / ".git").write_text("", encoding="utf-8")
    elif marker == "no_objects":
        (cwd / ".git" / "refs").mkdir(parents=True)
        (cwd / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a repository top level"):
        resolve_resources(["file.txt"], cwd=str(cwd), bridge_root=str(layout["bridge"]))
