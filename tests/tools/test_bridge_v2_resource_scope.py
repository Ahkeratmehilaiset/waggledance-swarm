"""Bridge v2 F22 resource scope (tools-owned): pure fixtures, authored NOT RUN.

Every case injects ``lstat``, so no filesystem path is ever touched, and imports nothing outside
``tools``. The roots follow the running platform's rule (a drive-letter path on Windows, "/" elsewhere).
"""
from __future__ import annotations

import os
import stat

import pytest

from tools.bridge_v2_resource_scope import (EXAMPLES, ResourceScope, ScopeError, _normalize_absolute,
                                            explain_scope, resolve_scopes, resources_overlap)

ROOT = "C:/work" if os.name == "nt" else "/work"
WORKTREE = ROOT + "/tree"
SHARED = ROOT + "/shared-root"
LOWER_WORKTREE, LOWER_SHARED = WORKTREE.lower(), SHARED.lower()


class _Info:
    def __init__(self, mode: int, attributes: int = 0) -> None:
        self.st_mode, self.st_file_attributes = mode, attributes


def _absent(path: str):
    raise FileNotFoundError(path)                      # nothing exists: the resolver never reads the disk


def _only(target: str, info: _Info):
    def lstat(path: str):
        if path == target:
            return info
        raise FileNotFoundError(path)
    return lstat


def _resolve(*scopes, lstat=_absent):
    return resolve_scopes(list(scopes), worktree=WORKTREE, bridge_root=SHARED, lstat=lstat)


# -- valid entries ------------------------------------------------------------------------------------

@pytest.mark.parametrize("scope,expected", [
    ("tools/bridge_v2_work_queue.py", ResourceScope("repo", "tools/bridge_v2_work_queue.py")),
    ("Tools\\Sub\\A.py", ResourceScope("repo", "tools/sub/a.py")),         # separators and ASCII case
    ("./tools/./a.py", ResourceScope("repo", "tools/a.py")),
    ("*", ResourceScope("repo", "*")),
    ("repo:docs/x.md", ResourceScope("repo", "docs/x.md")),
    (".codex-audit/wd-current-state.json", ResourceScope("worktree", ".codex-audit/wd-current-state.json",
                                                         LOWER_WORKTREE)),   # the checkpoint is per worktree
    ("worktree:.codex-audit/notes.md", ResourceScope("worktree", ".codex-audit/notes.md", LOWER_WORKTREE)),
    ("SHARED:work_queue/claims", ResourceScope("shared", "work_queue/claims", LOWER_SHARED)),
    (WORKTREE + "/tools/a.py", ResourceScope("repo", "tools/a.py")),        # absolute inside the worktree
    (SHARED + "/work_queue/x.json", ResourceScope("shared", "work_queue/x.json", LOWER_SHARED)),
])
def test_a_valid_entry_resolves_exactly(scope, expected):
    assert _resolve(scope) == (expected,)


def test_entries_split_on_commas_and_blank_entries_are_skipped():
    assert _resolve("tools/a.py, docs/x.md", " ,") == (ResourceScope("repo", "tools/a.py"),
                                                        ResourceScope("repo", "docs/x.md"))
    assert resolve_scopes("", worktree=WORKTREE, bridge_root=SHARED, lstat=_absent) == ()


# -- malformed entries ----------------------------------------------------------------------------------

@pytest.mark.parametrize("scope,reason", [
    ("../etc/passwd", "traversal"),
    ("tools/a.py:stream", "alternate stream"),
    ("unknown:tools/a.py", "unknown resource kind"),
    ("tools/a.py.", "ambiguous Windows path alias"),
    ("PROGRA~1/x.py", "ambiguous Windows path alias"),
    ("tools/*.py", "whole repository"),
    ("tools/a?.py", "whole repository"),
    ("t\u00f6ols/a.py", "must be ASCII"),
    ("worktree:tools/a.py", "must be under .codex-audit"),
    (ROOT + "/elsewhere/a.py", "outside the worktree/shared root"),
    ("//server/share/a.py", "a root must be a local"),                      # UNC-shaped: never a local root
])
def test_a_malformed_entry_is_refused_with_its_rule(scope, reason):
    with pytest.raises(ScopeError, match=reason.replace(".", r"\.").replace("?", r"\?")):
        _resolve(scope)
    assert _resolve("tools/a.py") == (ResourceScope("repo", "tools/a.py"),)   # success twin, same call


def test_a_non_string_entry_is_refused():
    with pytest.raises(ScopeError, match="must be strings"):
        resolve_scopes(["tools/a.py", 123], worktree=WORKTREE, bridge_root=SHARED, lstat=_absent)


@pytest.mark.parametrize("info", [_Info(stat.S_IFLNK), _Info(stat.S_IFDIR, 0x400)], ids=["symlink", "reparse"])
def test_a_link_or_reparse_point_on_the_path_is_refused(info):
    with pytest.raises(ScopeError, match="link/reparse point"):
        _resolve("tools/a.py", lstat=_only(WORKTREE + "/tools", info))
    assert _resolve("tools/a.py", lstat=_only(WORKTREE + "/tools", _Info(stat.S_IFDIR))) == (
        ResourceScope("repo", "tools/a.py"),)                                 # twin: a plain directory


def test_normalize_absolute_lowers_trims_and_refuses_aliases_and_traversal():
    assert _normalize_absolute(ROOT + "/Tree/", _absent) == LOWER_WORKTREE
    with pytest.raises(ScopeError, match="ambiguous Windows root alias"):
        _normalize_absolute(ROOT + "/PROGRA~1/x", _absent)
    # ".." ends with a dot, so the alias rule (checked first) already refuses it: the source's later
    # ".." traversal check is not reached for this input. Pinned as the actual behaviour, not changed.
    with pytest.raises(ScopeError, match="ambiguous Windows root alias"):
        _normalize_absolute(WORKTREE + "/../x", _absent)


# -- disjoint and overlapping resources ----------------------------------------------------------------

@pytest.mark.parametrize("left,right,overlap", [
    (ResourceScope("repo", "tools/a.py"), ResourceScope("repo", "tools/b.py"), False),
    (ResourceScope("repo", "tools"), ResourceScope("repo", "tools/a.py"), True),         # a directory claim
    (ResourceScope("repo", "tools/a"), ResourceScope("repo", "tools/ab.py"), False),     # prefix, not a parent
    (ResourceScope("repo", "*"), ResourceScope("shared", "x", "/r"), True),
    (ResourceScope("repo", "work_queue/claims"), ResourceScope("shared", "work_queue/claims", "/r"), True),
    (ResourceScope("shared", "x", "/r1"), ResourceScope("shared", "x", "/r2"), False),   # other shared root
    (ResourceScope("worktree", ".codex-audit/a", "/w1"), ResourceScope("worktree", ".codex-audit/a", "/w2"), False),
    (ResourceScope("worktree", ".codex-audit", "/w1"), ResourceScope("worktree", ".codex-audit/a", "/w1"), True),
])
def test_resources_overlap_is_the_symmetric_legacy_rule(left, right, overlap):
    assert resources_overlap(left, right) is overlap
    assert resources_overlap(right, left) is overlap


# -- explain ------------------------------------------------------------------------------------------

def test_explain_scope_says_what_an_entry_is_or_why_it_is_refused():
    accepted = explain_scope("shared:work_queue/claims", worktree=WORKTREE, bridge_root=SHARED, lstat=_absent)
    assert accepted == {"entry": "shared:work_queue/claims", "accepted": True, "kind": "shared",
                        "path": "work_queue/claims", "root": LOWER_SHARED,
                        "meaning": "a physical path under the shared runtime root", "examples": list(EXAMPLES)}
    refused = explain_scope("x" * 300 + "/../y", worktree=WORKTREE, bridge_root=SHARED, lstat=_absent)
    assert (refused["accepted"], len(refused["entry"]), refused["examples"]) == (False, 256, list(EXAMPLES))
    assert "traversal" in refused["reason"] and set(refused) == {"entry", "accepted", "reason", "examples"}


# -- RCO2 d91feafd: no NTFS stream after a drive; an explicit shared: absolute path is rooted at shared ------

@pytest.mark.skipif(os.name != "nt", reason="only a drive-letter path carries ':' past the colon guard")
@pytest.mark.parametrize("suffix", ["a.py:stream", "a.py::$DATA", "dir:stream/a.py"])
def test_an_absolute_drive_path_with_a_stream_is_refused(suffix):
    with pytest.raises(ScopeError, match="alternate stream"):
        _resolve(WORKTREE + "/" + suffix)                # was ('repo', 'a.py:stream'); a.py::$DATA IS a.py
    with pytest.raises(ScopeError, match="alternate stream"):
        _normalize_absolute(WORKTREE + "/" + suffix, _absent)
    assert _resolve(WORKTREE + "/a.py") == (ResourceScope("repo", "a.py"),)   # the twin: the plain file resolves


def test_an_explicit_shared_absolute_scope_is_rooted_at_the_shared_root():
    assert _resolve("shared:" + SHARED + "/work_queue/x.json") == (
        ResourceScope("shared", "work_queue/x.json", LOWER_SHARED),)          # the twin: under shared
    with pytest.raises(ScopeError, match="under the shared runtime root"):
        _resolve("shared:" + WORKTREE + "/.codex-audit/x.md")   # was ('shared', '.codex-audit/x.md', SHARED)


# -- RCO2 a7169ffe: a non-disjoint (nested or equal) worktree/shared layout refuses every scope except * ------

NESTED_SHARED = WORKTREE + "/.agent-bridge"                              # a shared root INSIDE the worktree
OUTER_SHARED, INNER_WORKTREE = ROOT + "/rt", ROOT + "/rt/lanes/tree"     # a worktree INSIDE the shared root


@pytest.mark.parametrize("worktree, shared", [(WORKTREE, NESTED_SHARED), (INNER_WORKTREE, OUTER_SHARED),
                                              (WORKTREE, WORKTREE)], ids=["shared_in_worktree", "worktree_in_shared",
                                                                          "equal"])
@pytest.mark.parametrize("form", ["tools/a.py", "repo:docs/x.md", "shared:work_queue/x.json", "WT_ABS", "SH_ABS",
                                  "shared:SH_ABS", ".codex-audit/wd-current-state.json",
                                  "worktree:.codex-audit/notes.md"])
def test_a_non_disjoint_root_layout_refuses_every_entry_form(worktree, shared, form):
    entry = form.replace("WT_ABS", worktree + "/tools/a.py").replace("SH_ABS", shared + "/work_queue/x.json")
    with pytest.raises(ScopeError, match="overlap"):
        resolve_scopes([entry], worktree=worktree, bridge_root=shared, lstat=_absent)
    assert resolve_scopes(["*"], worktree=worktree, bridge_root=shared, lstat=_absent) == (
        ResourceScope("repo", "*"),)                        # the one provably safe entry: * overlaps every claim


def test_disjoint_roots_keep_their_exact_previous_resolution():
    for shared in (SHARED, WORKTREE + "2"):                 # siblings; the second shares a name prefix, no "/" boundary
        assert resolve_scopes(["tools/a.py", "shared:work_queue/x.json"], worktree=WORKTREE, bridge_root=shared,
                              lstat=_absent) == (ResourceScope("repo", "tools/a.py"),
                                                 ResourceScope("shared", "work_queue/x.json", shared.lower()))
