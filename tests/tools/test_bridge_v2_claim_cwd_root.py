# SPDX-License-Identifier: BUSL-1.1
"""RS7 (Grok 44ce90e5, Root repro FC4C1D9B): a claim cwd must be a repository top level.

Scopes are repository-relative and an absolute entry is made relative to the claim cwd, so a cwd below the top level
(or outside any repository) named one file two ways and resources_overlap missed the conflict. Both resolvers now
refuse every entry except ``*`` unless the cwd passes git's own top-level test, read from the file system: ``.git``
is a git dir (HEAD, objects/, refs/) or a ``gitdir:`` file of a linked worktree whose dir has HEAD and whose common dir
has objects/ and refs/. An empty or dangling marker is refused; the top level is never guessed. Python runs the tools
resolver and work queue on real temporary directories; PowerShell 5.1 and 7 dot-source a COPY of
BridgeResourceScope.ps1, so no repository or runtime script runs in place.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import bridge_v2_work_queue as wq
from tools.bridge_v2_queue_transactions import QueueTransactions, Refused, claim_bytes
from tools.bridge_v2_resource_scope import ResourceScope, ScopeError, resolve_scopes, resources_overlap
from tools.bridge_v2_work_queue import OwnerIdentity

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
NOW = datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc)
TOP_LEVEL = "not a repository top level"


class _Lock:
    @contextmanager
    def hold(self, target, timeout_seconds):
        yield


def git_dir(path: Path) -> Path:
    """The minimum git accepts as a repository: HEAD, objects/ and refs/."""
    for child in ("objects", "refs"):
        (path / child).mkdir(parents=True)
    (path / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return path


@pytest.fixture
def layout(tmp_path):
    repo, outside, bridge = tmp_path / "repo", tmp_path / "outside", tmp_path / "bridge"
    for path in (repo / "sub", repo / ".codex-audit", outside, bridge):
        path.mkdir(parents=True)
    git_dir(repo / ".git")
    return repo, outside, bridge


def _resolve(entry: str, cwd: Path | str, bridge: Path):
    return resolve_scopes([entry], worktree=str(cwd), bridge_root=str(bridge))


def test_the_root_repro_pair_no_longer_resolves_one_file_two_ways(layout):
    repo, _, bridge = layout
    target = str(repo / "sub" / "file.txt")
    top = _resolve(target, repo, bridge)
    assert top == (ResourceScope("repo", "sub/file.txt"),)
    with pytest.raises(ScopeError, match=TOP_LEVEL):
        _resolve(target, repo / "sub", bridge)               # was ('repo', 'file.txt'): no overlap with sub/file.txt
    assert resources_overlap(top[0], _resolve("sub/file.txt", repo, bridge)[0])    # success twin: one name, one path


@pytest.mark.parametrize("where", ["sub", "codex-audit", "outside"])
@pytest.mark.parametrize("entry", ["ABS_FILE", "file.txt", "wd-current-state.json",
                                   ".codex-audit/wd-current-state.json", "worktree:.codex-audit/x.md",
                                   "shared:work_queue/x.json", "ABS_CHECKPOINT"])
def test_a_cwd_that_is_not_a_top_level_refuses_every_entry_but_the_whole_repository(layout, where, entry):
    repo, outside, bridge = layout
    cwd = {"sub": repo / "sub", "codex-audit": repo / ".codex-audit", "outside": outside}[where]
    entry = (entry.replace("ABS_FILE", str(repo / "sub" / "file.txt"))
             .replace("ABS_CHECKPOINT", str(repo / ".codex-audit" / "wd-current-state.json")))
    with pytest.raises(ScopeError, match=TOP_LEVEL):
        _resolve(entry, cwd, bridge)
    assert _resolve("*", cwd, bridge) == (ResourceScope("repo", "*"),)      # * overlaps every claim anyway


@pytest.mark.parametrize("marker", ["empty-nested-dir", "head-only-dir", "dangling-gitdir-file", "not-a-pointer-file",
                                    "admin-dir-without-common"])
def test_a_marker_that_is_not_a_real_git_top_level_is_refused(layout, marker):
    repo, _, bridge = layout
    sub = repo / "sub"
    if marker == "empty-nested-dir":
        (sub / ".git").mkdir()                               # Lead's counterexample: an empty nested marker
    elif marker == "head-only-dir":
        (sub / ".git").mkdir()
        (sub / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    elif marker == "dangling-gitdir-file":
        (sub / ".git").write_text("gitdir: " + str(repo / "missing") + "\n", encoding="utf-8")
    elif marker == "not-a-pointer-file":
        (sub / ".git").write_text("", encoding="utf-8")
    else:
        admin = repo / ".git" / "worktrees" / "w"
        admin.mkdir(parents=True)
        (admin / "HEAD").write_text("ref: refs/heads/w\n", encoding="utf-8")
        (admin / "commondir").write_text("../../missing\n", encoding="utf-8")
        (sub / ".git").write_text("gitdir: " + str(admin) + "\n", encoding="utf-8")
    with pytest.raises(ScopeError, match=TOP_LEVEL):
        _resolve(str(sub / "file.txt"), sub, bridge)
    assert _resolve(str(sub / "file.txt"), repo, bridge) == (ResourceScope("repo", "sub/file.txt"),)   # twin


@pytest.mark.parametrize("git", ["directory", "linked-worktree-absolute", "linked-worktree-relative"])
def test_a_top_level_cwd_resolves_absolute_relative_and_checkpoint_entries(tmp_path, layout, git):
    repo, _, bridge = layout
    if git != "directory":
        shutil.rmtree(repo / ".git")
        admin = git_dir(tmp_path / "main.git") / "worktrees" / "w"   # a linked worktree's admin dir
        admin.mkdir(parents=True)
        (admin / "HEAD").write_text("ref: refs/heads/w\n", encoding="utf-8")
        (admin / "commondir").write_text("../..\n", encoding="utf-8")
        pointer = str(admin) if git == "linked-worktree-absolute" else os.path.relpath(admin, repo)
        (repo / ".git").write_text("gitdir: " + pointer + "\n", encoding="utf-8")
    lowered = str(repo).replace("\\", "/").lower()
    assert resolve_scopes([str(repo / "sub" / "file.txt"), "sub/file.txt", ".codex-audit/wd-current-state.json",
                           str(repo / ".codex-audit" / "wd-current-state.json")],
                          worktree=str(repo), bridge_root=str(bridge)) == (
        ResourceScope("repo", "sub/file.txt"), ResourceScope("repo", "sub/file.txt"),
        ResourceScope("worktree", ".codex-audit/wd-current-state.json", lowered),
        ResourceScope("worktree", ".codex-audit/wd-current-state.json", lowered))


def test_a_cwd_less_legacy_entry_keeps_its_conservative_path_only_handling(layout):
    _, _, bridge = layout
    assert _resolve("tools/a.py", "", bridge) == (ResourceScope("repo", "tools/a.py"),)


def _queue(bridge: Path) -> QueueTransactions:
    return QueueTransactions(bridge, mutex=_Lock(), claim_lock=_Lock(), clock=lambda: NOW)


def _stored(bridge: Path, name: str, cwd: str) -> None:
    directory = bridge / "work_queue" / "claims"
    directory.mkdir(parents=True, exist_ok=True)
    record = {"agent": "fable-5", "task_id": name, "mode": "write", "cwd": cwd, "write_scope": ["tools/a.py"],
              "last_heartbeat_utc": NOW.isoformat()}
    (directory / f"{name}.json").write_bytes(claim_bytes(record))


@pytest.mark.parametrize("where", ["sub", "outside"])
def test_a_stored_claim_with_a_non_top_level_cwd_refuses_new_write_claims(layout, where):
    repo, outside, bridge = layout
    txns = _queue(bridge)
    _stored(bridge, "legacy", str(repo / "sub" if where == "sub" else outside))
    with pytest.raises(Refused, match="unresolvable write scope"):
        wq.claim_task(txns, agent="codex-lead-1", task_id="team/new", summary="work", mode="write",
                      write_scope=("tools/z.py",), identity=OwnerIdentity("s", "t"), cwd=str(repo), now=NOW)
    (bridge / "work_queue" / "claims" / "legacy.json").unlink()
    _stored(bridge, "legacy", str(repo))                     # success twin: a top-level stored cwd
    claimed = wq.claim_task(txns, agent="codex-lead-1", task_id="team/new", summary="work", mode="write",
                            write_scope=("tools/z.py",), identity=OwnerIdentity("s", "t"), cwd=str(repo), now=NOW)
    assert claimed["write_scope"] == ["tools/z.py"]


def test_a_new_claim_from_a_subdirectory_cwd_is_refused(layout):
    repo, _, bridge = layout
    with pytest.raises(wq.WorkQueueError, match=TOP_LEVEL):
        wq.claim_task(_queue(bridge), agent="codex-lead-1", task_id="team/sub", summary="work", mode="write",
                      write_scope=("tools/z.py",), identity=OwnerIdentity("s", "t"), cwd=str(repo / "sub"), now=NOW)


@pytest.mark.skipif(not SHELLS or os.name != "nt", reason="Windows PowerShell 5.1 / 7 fixtures")
@pytest.mark.parametrize("engine", SHELLS)
def test_powershell_resolver_copy_refuses_a_non_top_level_cwd(tmp_path, layout, engine):
    repo, outside, bridge = layout
    code = tmp_path / "code"
    code.mkdir()
    copy = code / "BridgeResourceScope.ps1"
    shutil.copyfile(ROOT / ".agent-bridge" / "bin" / "BridgeResourceScope.ps1", copy)

    def run(entry: str, cwd: Path) -> subprocess.CompletedProcess:
        script = (f"$ErrorActionPreference='Stop'; . '{copy}'; "
                  f"Resolve-BridgeResourceScopes -Scopes '{entry}' -Worktree '{cwd}' -BridgeRoot '{bridge}' "
                  "| ForEach-Object { $_.kind + '|' + $_.path } ")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_"))}
        return subprocess.run([engine, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                              capture_output=True, text=True, timeout=120)

    (repo / "sub" / ".git").mkdir()                          # the empty nested marker counterexample
    linked = tmp_path / "linked"
    (linked / "sub").mkdir(parents=True)
    admin = git_dir(tmp_path / "main.git") / "worktrees" / "w"
    admin.mkdir(parents=True)
    (admin / "HEAD").write_text("ref: refs/heads/w\n", encoding="utf-8")
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    (linked / ".git").write_text("gitdir: " + str(admin) + "\n", encoding="utf-8")
    target = str(repo / "sub" / "file.txt")
    for entry, cwd in ((target, repo / "sub"), ("file.txt", repo / "sub"), ("tools/a.py", outside),
                       (".codex-audit/wd-current-state.json", repo / ".codex-audit"),
                       (str(linked / "sub" / "f.txt"), linked / "sub")):
        refused = run(entry, cwd)
        assert refused.returncode != 0 and TOP_LEVEL in refused.stderr, (entry, cwd, refused.stdout, refused.stderr)
    accepted = run(target, repo)                                           # success twins
    assert accepted.returncode == 0 and accepted.stdout.strip() == "repo|sub/file.txt", accepted.stderr
    linked_ok = run(str(linked / "sub" / "f.txt"), linked)
    assert linked_ok.returncode == 0 and linked_ok.stdout.strip() == "repo|sub/f.txt", linked_ok.stderr
    whole = run("*", repo / "sub")
    assert whole.returncode == 0 and whole.stdout.strip() == "repo|*", whole.stderr
