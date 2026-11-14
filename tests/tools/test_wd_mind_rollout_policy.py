"""Synthetic admission/closure tests; never read or activate an installed bundle."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

from tools import wd_mind_rollout_policy as policy


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "core.longpaths=true", "-c", "core.excludesFile=" + str(root / "ignore-empty"),
         "-c", "core.autocrlf=false", "-C", str(root), *args],
        capture_output=True, check=True, timeout=20,
    )
    return result.stdout.decode("utf-8").strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "synthetic-git"
    root.mkdir()
    # A new synthetic repository is a fixture, not a reconstructed task repository.
    git(root, "init", "-q")
    git(root, "config", "user.name", "Synthetic fixture")
    git(root, "config", "user.email", "synthetic@example.invalid")
    (root / ".gitignore").write_text("ignore-empty\n.codex-audit/\n", encoding="utf-8")
    (root / ".gitattributes").write_text("sandbox/mind/reader.py filter=synthetic\n", encoding="utf-8")
    (root / "ignore-empty").write_bytes(b"")
    (root / "sandbox/mind").mkdir(parents=True)
    (root / "sandbox/mind/reader.py").write_bytes(b"OLD\n")
    (root / "sandbox/mind/unchanged.py").write_bytes(b"dependency\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic installed source")
    previous = git(root, "rev-parse", "HEAD")
    (root / "sandbox/mind/reader.py").write_bytes(b"NEW\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic candidate")
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", head)
    return root, previous, head, git(root, "rev-parse", "HEAD^{tree}")


def closure(repository, members=None, **changes):
    root, previous, head, tree = repository
    options = dict(repo=root, installed_commit=previous, main_commit=head,
                   main_tree=tree, members=members or ["sandbox/mind/reader.py", "sandbox/mind/unchanged.py"])
    options.update(changes)
    return policy.build_tracked_closure(**options)


@pytest.mark.parametrize("path", [
    "sandbox/mind/reader.py", "configs/mind/session.json", "tools/wd_q4_reader.py",
    "tests/tools/test_wd_q4_reader.py", "ops/windows/reboot/Get-WdQ4State.ps1",
    "ops/windows/reboot/Invoke-WdQ4Recall.ps1",
    "sandbox/mind/capture/capacity_lens.py", "sandbox/mind/private/privacy_guard.py",
    "sandbox/mind/judge/source_guard.py", "configs/mind/sealed/policy.json",
])
def test_only_whole_file_rule14_paths_are_eligible(path):
    assert policy.mind_path(path) is True


@pytest.mark.parametrize("path", [
    "CLAUDE.md", ".agent-bridge/bin/foo.ps1", "tools/wd_mind_rollout_policy.py",
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1", "ops/windows/reboot/start-wd-agent.ps1",
    "sandbox/mind/control_plane/x.py", "configs/mind/runtime_path_bindings.json",
    "tools/wd_q4_x/nested.py", "Sandbox/mind/x.py", "tests/tools/test_wd_mind_x.py",
])
def test_governance_stage2_and_multipurpose_paths_are_ineligible(path):
    assert policy.mind_path(path) is False


@pytest.mark.parametrize("path", [
    "", "/sandbox/mind/x", "C:/sandbox/mind/x", "\\\\server\\x", "sandbox\\mind\\x",
    "sandbox/mind/../x", "sandbox//mind/x", "sandbox/mind/./x", "sandbox/mind/x:",
    "sandbox/mind/x ", "sandbox/mind/x.", "sandbox/mind/CON.py", "sandbox/mind/lpt1",
    "sandbox/mind/x\x00.py", "sandbox/mind/x\n.py", "sandbox/mind/x?.py",
])
def test_windows_path_aliases_are_refused(path):
    with pytest.raises(policy.Refused):
        policy.canonical_path(path)


def test_git_tree_bytes_not_untracked_or_worktree_bytes(repository):
    result = closure(repository)
    assert result.files["sandbox/mind/reader.py"] == b"NEW\n"
    assert result.files["sandbox/mind/unchanged.py"] == b"dependency\n"
    assert result.changed_paths == ("sandbox/mind/reader.py",)
    manifest = json.loads(result.manifest)
    assert manifest["main_commit"] == repository[2]
    assert manifest["main_tree"] == repository[3]
    assert result.manifest_sha256 == sha(result.manifest)
    with pytest.raises(TypeError):
        result.files["sandbox/mind/reader.py"] = b"replacement"
    # Even an index hint hiding a changed checkout cannot change exported source bytes.
    root = repository[0]
    git(root, "update-index", "--assume-unchanged", "sandbox/mind/reader.py")
    (root / "sandbox/mind/reader.py").write_bytes(b"UNAPPROVED CHECKOUT\n")
    assert closure(repository).files["sandbox/mind/reader.py"] == b"NEW\n"


@pytest.mark.parametrize("change", ["untracked", "dirty", "wrong_head", "wrong_tree", "wrong_main", "missing_member"])
def test_exact_main_clean_tree_and_complete_membership_are_required(repository, change):
    root, previous, head, tree = repository
    options = {}
    if change == "untracked":
        (root / "sandbox/mind/untracked.py").write_bytes(b"untracked")
    elif change == "dirty":
        (root / "sandbox/mind/reader.py").write_bytes(b"dirty")
    elif change == "wrong_head":
        options["main_commit"] = previous
    elif change == "wrong_tree":
        options["main_tree"] = "0" * 40
    elif change == "wrong_main":
        git(root, "update-ref", "refs/remotes/origin/main", previous)
    elif change == "missing_member":
        options["members"] = ["sandbox/mind/unchanged.py"]
    with pytest.raises(policy.Refused):
        closure(repository, **options)


@pytest.mark.parametrize("direction", ["into_mind", "out_of_mind"])
def test_both_endpoints_of_renames_are_classified(repository, direction):
    root, previous, head, tree = repository
    if direction == "into_mind":
        git(root, "reset", "--hard", previous)  # own disposable synthetic fixture only
        (root / "outside.py").write_bytes(b"outside")
        git(root, "add", ".")
        git(root, "commit", "-qm", "synthetic predecessor outside")
        previous = git(root, "rev-parse", "HEAD")
        git(root, "mv", "outside.py", "sandbox/mind/moved.py")
    else:
        git(root, "mv", "sandbox/mind/reader.py", "outside.py")
    git(root, "commit", "-qm", "synthetic rename")
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", head)
    with pytest.raises(policy.Refused, match="Mind"):
        closure((root, previous, head, git(root, "rev-parse", "HEAD^{tree}")))


def test_deleted_tracked_members_have_explicit_absence(repository):
    root, previous, head, tree = repository
    git(root, "rm", "sandbox/mind/reader.py")
    git(root, "commit", "-qm", "synthetic deletion")
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", head)
    result = closure((root, previous, head, git(root, "rev-parse", "HEAD^{tree}")))
    assert result.files["sandbox/mind/reader.py"] is None
    assert json.loads(result.manifest)["files"]["sandbox/mind/reader.py"] is None


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_link_or_gitlink_tree_member_is_never_exported(repository, mode):
    root, previous, head, tree = repository
    oid = git(root, "rev-parse", "HEAD" if mode == "160000" else "HEAD:sandbox/mind/reader.py")
    git(root, "update-index", "--add", "--cacheinfo", mode, oid, "sandbox/mind/link")
    git(root, "commit", "-qm", "synthetic linked member")
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", head)
    with pytest.raises(policy.Refused):
        closure((root, previous, head, git(root, "rev-parse", "HEAD^{tree}")),
                ["sandbox/mind/reader.py", "sandbox/mind/link"])


def test_case_colliding_members_refuse_even_on_case_sensitive_git_trees(repository):
    with pytest.raises(policy.Refused, match="duplicate|collision"):
        closure(repository, ["sandbox/mind/reader.py", "sandbox/mind/Reader.py"])


def test_unchanged_outside_mind_member_cannot_be_smuggled_into_closure(repository):
    with pytest.raises(policy.Refused):
        closure(repository, ["sandbox/mind/reader.py", ".gitignore"])


def test_git_admission_never_executes_a_configured_candidate_clean_filter(repository):
    root = repository[0]
    audit = root / ".codex-audit"
    audit.mkdir()
    marker = audit / "filter-ran"
    script = audit / "synthetic_filter.py"
    script.write_text("from pathlib import Path\nimport sys\nPath(" + repr(str(marker)) +
                      ").write_text('synthetic fixture only')\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n",
                      encoding="utf-8")
    command = '"' + Path(sys.executable).as_posix() + '" "' + script.as_posix() + '"'
    git(root, "config", "filter.synthetic.clean", command)
    reader = root / "sandbox/mind/reader.py"
    reader.write_bytes(b"BAD\n")  # same length: force Git to hash instead of its size shortcut
    os.utime(reader, ns=(1_000_000_000, 1_000_000_000))
    with pytest.raises(policy.Refused):
        closure(repository)
    assert not marker.exists(), "A read-only Git admission executed a candidate clean driver"


def test_nested_gitlink_filters_cannot_run_before_admission_refuses(repository):
    root = repository[0]
    nested = root / "nested"
    nested.mkdir()
    git(nested, "init", "-q")
    git(nested, "config", "user.name", "Synthetic fixture")
    git(nested, "config", "user.email", "synthetic@example.invalid")
    (nested / ".gitattributes").write_text("data.txt filter=synthetic\n", encoding="utf-8")
    (nested / "data.txt").write_bytes(b"AAA\n")
    git(nested, "add", ".")
    git(nested, "commit", "-qm", "synthetic nested source")
    git(root, "add", "nested")
    git(root, "commit", "-qm", "synthetic installed source with gitlink")
    previous = git(root, "rev-parse", "HEAD")
    (root / "sandbox/mind/reader.py").write_bytes(b"NXT\n")
    git(root, "add", "sandbox/mind/reader.py")
    git(root, "commit", "-qm", "synthetic Mind-only successor")
    head = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/main", head)
    audit = root / ".codex-audit"
    audit.mkdir()
    marker = audit / "nested-filter-ran"
    script = audit / "nested_filter.py"
    script.write_text("from pathlib import Path\nimport sys\nPath(" + repr(str(marker)) +
                      ").write_text('synthetic fixture only')\nsys.stdout.buffer.write(sys.stdin.buffer.read())\n",
                      encoding="utf-8")
    command = '"' + Path(sys.executable).as_posix() + '" "' + script.as_posix() + '"'
    git(nested, "config", "filter.synthetic.clean", command)
    (nested / "data.txt").write_bytes(b"BBB\n")
    os.utime(nested / "data.txt", ns=(1_000_000_000, 1_000_000_000))
    with pytest.raises(policy.Refused):
        closure((root, previous, head, git(root, "rev-parse", "HEAD^{tree}")))
    assert not marker.exists(), "Admission executed a nested Git filter before refusing"


@pytest.fixture
def installed(tmp_path):
    generation = "a" * 40
    root = tmp_path / generation
    target = root / "tools-bootstrap/tools/wd_mind_rollout_policy.py"
    target.parent.mkdir(parents=True)
    shutil.copyfile(policy.__file__, target)
    # Load the fixture's installed copy. Do not falsify the module's __file__.
    name = "synthetic_installed_policy_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, target)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    raw = json.dumps({"files": {"tools-bootstrap/tools/wd_mind_rollout_policy.py": sha(target.read_bytes())}},
                     sort_keys=True, separators=(",", ":")).encode()
    (root / "deployment-manifest.json").write_bytes(raw)
    yield module, root, sha(raw), generation
    sys.modules.pop(name, None)


def test_only_the_externally_anchored_installed_module_can_admit(installed):
    module, root, anchor, generation = installed
    verified = module.verify_installed_policy(root, anchor, generation)
    assert verified.manifest_sha256 == anchor
    assert verified.policy_sha256 == sha(Path(module.__file__).read_bytes())
    # Candidate/worktree code with the same bytes cannot claim that installed origin.
    with pytest.raises(policy.Refused, match="installed"):
        policy.verify_installed_policy(root, anchor, generation)


@pytest.mark.parametrize("change", ["anchor", "generation", "module", "missing", "extra", "case_collision", "duplicate_json", "escaping_path"])
def test_installed_trust_never_falls_back_to_candidate_or_partial_closure(installed, change):
    module, root, anchor, generation = installed
    manifest = root / "deployment-manifest.json"
    target = Path(module.__file__)
    if change == "anchor":
        anchor = "0" * 64
    elif change == "generation":
        generation = "b" * 40
    elif change == "module":
        target.write_bytes(target.read_bytes() + b"\n# changed\n")
    elif change == "missing":
        target.unlink()
    elif change == "extra":
        (root / "unmanifested.py").write_bytes(b"unexpected")
    else:
        payload = json.loads(manifest.read_bytes())
        if change == "case_collision":
            payload["files"]["TOOLS-BOOTSTRAP/tools/wd_mind_rollout_policy.py"] = sha(target.read_bytes())
        elif change == "escaping_path":
            payload["files"]["../outside"] = "0" * 64
        if change == "duplicate_json":
            raw = b'{"files":{},"files":{}}'
        else:
            raw = json.dumps(payload).encode()
        manifest.write_bytes(raw)
        anchor = sha(raw)  # malformed structure still refuses under a matching external hash
    with pytest.raises(module.Refused):
        module.verify_installed_policy(root, anchor, generation)
