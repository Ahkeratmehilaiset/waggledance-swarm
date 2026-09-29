"""The CI caller must not import Python from the proposed checkout."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from tools import classify_bridge_ci_scope, select_affected_tests
from tools import run_bridge_ci_scope_trusted as caller


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


def _fixture(tmp_path: Path) -> tuple[Path, Path, str, str, Path]:
    # Git for Windows cannot parse SHA:path at very deep pytest basetemp paths.
    # Keep this disposable repo short and wholly under the lane audit directory.
    short_root = Path.cwd() / ".codex-audit" / ("tc-" + uuid.uuid4().hex[:8])
    short_root.mkdir(parents=True)
    checkout = short_root / "checkout"
    checkout.mkdir()
    audit = short_root / ".codex-audit"
    audit.mkdir()
    tools = checkout / "tools"
    tools.mkdir()
    shutil.copyfile(classify_bridge_ci_scope.__file__, tools / "classify_bridge_ci_scope.py")
    shutil.copyfile(select_affected_tests.__file__, tools / "select_affected_tests.py")
    source = checkout / ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1"
    source.parent.mkdir(parents=True)
    source.write_text("base\n", encoding="utf-8")
    mapped = checkout / "tests/tools/test_bridge_stale_routing.py"
    mapped.parent.mkdir(parents=True)
    mapped.write_text("def test_dummy(): pass\n", encoding="utf-8")
    _git(checkout, "init", "-q")
    _git(checkout, "config", "core.autocrlf", "false")
    _git(checkout, "config", "user.name", "CI fixture")
    _git(checkout, "config", "user.email", "ci-fixture@example.invalid")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-qm", "trusted base")
    base = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    source.write_text("bridge change\n", encoding="utf-8")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-qm", "bridge head")
    head = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    return checkout, audit, base, head, source


def test_reproduce_direct_pr_router_imports_shadowed_stdlib(tmp_path):
    checkout, audit, base, head, _ = _fixture(tmp_path)
    marker = audit / "pr-marker.txt"
    shadow = checkout / "tools/re.py"
    shadow.write_text(f"open({str(marker)!r}, 'w').write('executed')\n", encoding="utf-8")
    _git(checkout, "add", "--", "tools/re.py")
    _git(checkout, "commit", "-qm", "shadow stdlib")
    head = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    completed = subprocess.run([sys.executable, "-B", str(checkout / "tools/classify_bridge_ci_scope.py"),
                    "--base", base, "--head", head, "--repo-root", str(checkout)],
                   cwd=checkout, capture_output=True, check=False)
    assert marker.exists(), completed.stderr.decode(errors="replace")
    assert marker.read_text(encoding="utf-8") == "executed"


def _call(checkout: Path, audit: Path, base: str, head: str, **overrides):
    inputs = {"checkout": checkout, "audit_parent": audit, "base": base, "head": head,
              "expected_merge_base": base, "protected_base": base,
              "git_executable": shutil.which("git")}
    inputs.update(overrides)
    return caller.run_scope(**inputs)


def test_pristine_bridge_narrows_from_isolated_base_copy(tmp_path):
    checkout, audit, base, head, source = _fixture(tmp_path)
    result = _call(checkout, audit, base, head)
    assert result["scope"] == "bridge", result
    assert result["changed_files"] == [source.relative_to(checkout).as_posix()]
    assert result["tests"] == ["tests/tools/test_bridge_stale_routing.py"]


def test_shadowed_pr_stdlib_never_executes_in_trusted_caller(tmp_path, monkeypatch):
    checkout, audit, base, _, _ = _fixture(tmp_path)
    marker = audit / "safe-marker.txt"
    (checkout / "tools/re.py").write_text(
        f"open({str(marker)!r}, 'w').write('executed')\n", encoding="utf-8")
    _git(checkout, "add", "--", "tools/re.py")
    _git(checkout, "commit", "-qm", "shadow stdlib")
    head = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    monkeypatch.setenv("PYTHONPATH", str(checkout / "tools"))
    monkeypatch.setenv("GIT_TRACE", str(audit / "untrusted-git-trace"))
    result = _call(checkout, audit, base, head)
    assert result["scope"] == "full", result
    assert not marker.exists()
    assert not (audit / "untrusted-git-trace").exists()


def test_poisoned_head_map_never_executes(tmp_path):
    checkout, audit, base, _, _ = _fixture(tmp_path)
    marker = audit / "poisoned-map-marker.txt"
    mapping = checkout / "tools/select_affected_tests.py"
    mapping.write_text(f"open({str(marker)!r}, 'w').write('executed')\n" +
                       mapping.read_text(encoding="utf-8"), encoding="utf-8")
    _git(checkout, "add", "--", "tools/select_affected_tests.py")
    _git(checkout, "commit", "-qm", "poison map")
    head = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    result = _call(checkout, audit, base, head)
    assert result["scope"] == "full", result
    assert not marker.exists()


def test_stale_policy_and_wrong_merge_base_fail_full(tmp_path):
    checkout, audit, base, head, _ = _fixture(tmp_path)
    stale = "a" * 40 if base != "a" * 40 else "b" * 40
    assert _call(checkout, audit, base, head, protected_base=stale)["scope"] == "full"
    assert _call(checkout, audit, base, head, expected_merge_base=stale)["scope"] == "full"
    assert _call(checkout, audit, stale, head, protected_base=stale,
                 expected_merge_base=stale)["scope"] == "full"


def test_real_nonancestor_base_fails_full(tmp_path):
    checkout, audit, _, head, _ = _fixture(tmp_path)
    tree = _git(checkout, "write-tree").stdout.decode().strip()
    unrelated = _git(checkout, "commit-tree", tree, "-m", "unrelated base").stdout.decode().strip()
    result = _call(checkout, audit, unrelated, head, protected_base=unrelated,
                   expected_merge_base=unrelated)
    assert result["scope"] == "full"
    assert "merge-base" in result["reason"]


def test_missing_base_mapper_bootstrap_fails_full(tmp_path):
    checkout, audit, _, _, _ = _fixture(tmp_path)
    _git(checkout, "rm", "--", "tools/select_affected_tests.py")
    _git(checkout, "commit", "-qm", "policy absent base")
    base = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    source = checkout / ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1"
    source.write_text("next change\n", encoding="utf-8")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-qm", "head")
    head = _git(checkout, "rev-parse", "HEAD").stdout.decode().strip()
    result = _call(checkout, audit, base, head)
    assert result["scope"] == "full"
    assert "base policy missing" in result["reason"]


@pytest.mark.parametrize("raw", [b"not json", b"{}", b'{"scope":"bridge"}',
                                   b'{"schema":"wrong","scope":"bridge"}'])
def test_malformed_router_output_fails_full(tmp_path, monkeypatch, raw):
    checkout, audit, base, head, _ = _fixture(tmp_path)
    real_run = caller._run
    def malformed_router(argv, *, cwd, env):
        if argv[0] == sys.executable:
            return subprocess.CompletedProcess(argv, 0, raw, b"")
        return real_run(argv, cwd=cwd, env=env)
    monkeypatch.setattr(caller, "_run", malformed_router)
    assert _call(checkout, audit, base, head)["scope"] == "full"


def test_nonzero_router_and_unknown_scope_fail_full(tmp_path, monkeypatch):
    checkout, audit, base, head, _ = _fixture(tmp_path)
    real_run = caller._run
    for code, raw in ((3, b""), (0, json.dumps({
        "schema": caller.SCHEMA, "scope": "special", "base": base, "head": head,
        "changed_files": [], "tests": [], "reason": "unknown"}).encode())):
        def fake(argv, *, cwd, env):
            if argv[0] == sys.executable:
                return subprocess.CompletedProcess(argv, code, raw, b"")
            return real_run(argv, cwd=cwd, env=env)
        monkeypatch.setattr(caller, "_run", fake)
        assert _call(checkout, audit, base, head)["scope"] == "full"


@pytest.mark.parametrize("mutation", [
    {"schema": "unknown"}, {"base": "a" * 40}, {"head": "b" * 40},
    {"tests": ["../escape.py"]}, {"tests": []},
    {"tests": ["tests/tools/test_bridge_stale_routing.py"] * 2},
])
def test_wrong_binding_or_test_list_cannot_approve_bridge(tmp_path, mutation):
    checkout, _, base, head, _ = _fixture(tmp_path)
    candidate = {"schema": caller.SCHEMA, "scope": "bridge", "base": base, "head": head,
                 "changed_files": [".agent-bridge/bin/Invoke-StaleClaimSweep.ps1"],
                 "tests": ["tests/tools/test_bridge_stale_routing.py"], "reason": "mapped"}
    candidate.update(mutation)
    assert caller._validate_output(json.dumps(candidate).encode(), base, head, checkout) is None


def test_trusted_git_and_audit_parent_must_be_outside_checkout(tmp_path):
    checkout, audit, base, head, _ = _fixture(tmp_path)
    fake_git = checkout / "git.exe"
    fake_git.write_bytes(b"not executable")
    assert _call(checkout, audit, base, head, git_executable=fake_git)["scope"] == "full"
    nested_audit = checkout / ".codex-audit"
    nested_audit.mkdir()
    assert _call(checkout, audit, base, head, audit_parent=nested_audit)["scope"] == "full"

