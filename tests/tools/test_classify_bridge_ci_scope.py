"""Fail-closed exact-commit Bridge CI routing; never launches pytest."""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import copyfile

import pytest

from tools.classify_bridge_ci_scope import classify_scope
from tools import classify_bridge_ci_scope as ci_scope
from tools import select_affected_tests as selector
from tools.select_affected_tests import BRIDGE_EXPLICIT_TESTS, bridge_test_closure

BASE = "a" * 40
HEAD = "b" * 40
EIGHT = (
    ".agent-bridge/bin/BridgeEventClassifier.ps1",
    ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1",
    "ops/windows/reboot/Get-WdSwarmParallelStatus.ps1",
    "ops/windows/reboot/Send-WdContinuityAlert.ps1",
    "ops/windows/reboot/bridge-code-files.json",
    "ops/windows/reboot/start-wd-agent.ps1",
    "ops/windows/reboot/start-wd-tools-consumer.ps1",
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1",
)
CANDIDATE_SOURCES = (
    ".agent-bridge/bin/BridgeEventClassifier.ps1",
    ".agent-bridge/bin/Get-BridgeRequestInventory.ps1",
    ".agent-bridge/bin/Invoke-StaleClaimSweep.ps1",
    "docs/adr/ADR-continuity-recovery-20260929.md",
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1",
    "ops/windows/reboot/Get-WdNativeWakePrompt.ps1",
    "ops/windows/reboot/Get-WdSwarmParallelStatus.ps1",
    "ops/windows/reboot/Send-WdContinuityAlert.ps1",
    "ops/windows/reboot/WAKE_PROCEDURE_LEAD.md",
    "ops/windows/reboot/WAKE_PROCEDURE_TOOLS.md",
    "ops/windows/reboot/bridge-code-files.json",
    "ops/windows/reboot/start-wd-agent.ps1",
    "ops/windows/reboot/start-wd-tools-consumer.ps1",
    "tools/bridge_continuity_guard.py",
)
CANDIDATE_TESTS = (
    "tests/tools/test_bridge_continuity_guard.py",
    "tests/tools/test_bridge_request_inventory.py",
    "tests/tools/test_bridge_stale_routing.py",
    "tests/tools/test_bridge_wake_continuity.py",
    "tests/tools/test_wd_continuity_alert.py",
    "tests/tools/test_wd_continuity_controls.py",
    "tests/tools/test_wd_continuity_status.py",
    "tests/tools/test_wd_lead_continuity_imports.py",
    "tests/tools/test_wd_lead_reply_delivery.py",
    "tests/tools/test_wd_native_tools_wake.py",
    "tests/tools/test_wd_native_wake_prompt.py",
    "tests/tools/test_wd_tools_exact_incoming_retrieval.py",
)
RCO_REQUIRED_38 = frozenset(f"tests/tools/{name}.py" for name in """
test_bridge_continuity_guard test_wd_continuity_alert test_wd_continuity_controls
test_wd_continuity_status test_bridge_wake_continuity test_bridge_stale_routing
test_wd_native_tools_wake test_wd_native_tools test_wd_native_lead
test_wd_lead_reply_delivery test_wd_lead_continuity_imports test_wd_tools_conversation
test_wd_conversation_recovery test_wd_conversation_resume test_wd_startup_recovery
test_wd_startup_repair test_wd_dynamic_model_startup test_wd_event_driven_wake
test_wd_lane_context_window test_wd_launcher_claude_marker_scrub
test_wd_capacity_observer test_wd_supervisor_opaque_process
test_lane_launch_preflight test_lane_profile_launch_probe
test_wd_bridge_code_context test_bridge_code_package_closure test_wd_reboot_bundle
test_wd_grok_helper test_bridge_event_classifier_wake_request
test_bridge_control_routing test_bridge_interim_reply_parity test_bridge_task_result
test_bridge_wake_observation test_bridge_inbox_recovery
test_bridge_session_watcher_probe test_session_liveness_supervisor_report
test_wd_swarm_parallel_status test_bridge_final_acceptance_20260927
""".split())
PROVIDERS = (
    "tests/tools/test_wd_reboot_bundle.py",
    "tests/tools/test_wd_startup_recovery.py",
    "tests/tools/test_wd_bridge_code_context.py",
)


def fixture_repo(tmp_path: Path) -> Path:
    for module, relative in ((ci_scope, "tools/classify_bridge_ci_scope.py"),
                             (selector, "tools/select_affected_tests.py")):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copyfile(module.__file__, target)
    for test in sorted(bridge_test_closure(set().union(*(BRIDGE_EXPLICIT_TESTS[path] for path in EIGHT)))):
        target = tmp_path / test
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_dummy(): assert True\n", encoding="utf-8")
    for source in EIGHT:
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("source\n", encoding="utf-8")
    return tmp_path


def candidate_repo(tmp_path: Path) -> Path:
    root = fixture_repo(tmp_path)
    tests = bridge_test_closure(set(CANDIDATE_TESTS) | set().union(
        *(BRIDGE_EXPLICIT_TESTS[source] for source in CANDIDATE_SOURCES)))
    for relative in (*CANDIDATE_SOURCES, *sorted(tests)):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text("fixture\n", encoding="utf-8")
    return root


def test_complete_actual_candidate_path_list_and_new_fable_test(tmp_path):
    root = candidate_repo(tmp_path)
    changed = CANDIDATE_SOURCES + CANDIDATE_TESTS
    run, _ = fake_git(changed)
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "bridge", result
    assert result["changed_files"] == list(changed)
    assert set(CANDIDATE_TESTS) <= set(result["tests"])
    assert len(RCO_REQUIRED_38) == 38
    assert RCO_REQUIRED_38 <= set(result["tests"])


@pytest.mark.parametrize("ancestor", ["visible-fixture", ".hidden-fixture"])
def test_candidate_fixture_visible_and_hidden_nested_ancestor(tmp_path, ancestor):
    root = candidate_repo(tmp_path / ancestor)
    run, _ = fake_git(CANDIDATE_SOURCES + CANDIDATE_TESTS)
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "bridge"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_each_shared_provider_selects_reviewed_boundary_and_direct_extra_consumers(tmp_path, provider):
    root = candidate_repo(tmp_path)
    run, _ = fake_git((provider,))
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "bridge", result
    assert RCO_REQUIRED_38 <= set(result["tests"])
    assert {"tests/tools/test_bridge_request_preflight.py",
            "tests/tools/test_wd_task_console_containment_pin.py"} <= set(result["tests"])


@pytest.mark.parametrize("provider", PROVIDERS)
def test_each_shared_provider_missing_consumer_fails_full(tmp_path, provider):
    root = candidate_repo(tmp_path)
    (root / "tests/tools/test_bridge_request_preflight.py").unlink()
    run, _ = fake_git((provider,))
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "full", result
    assert "test_bridge_request_preflight.py" in result["reason"]


def test_unknown_provider_consumer_test_change_forces_full(tmp_path):
    root = candidate_repo(tmp_path)
    unknown = "tests/tools/test_unreviewed_provider_consumer.py"
    (root / unknown).write_text("from test_wd_reboot_bundle import REBOOT\n", encoding="utf-8")
    run, _ = fake_git((PROVIDERS[0], unknown))
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"


def test_candidate_missing_new_incoming_test_or_continuity_guard_fails_full(tmp_path):
    root = candidate_repo(tmp_path)
    incoming = "tests/tools/test_wd_tools_exact_incoming_retrieval.py"
    (root / incoming).unlink()
    run, _ = fake_git(CANDIDATE_SOURCES)
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "full"
    assert incoming in result["reason"]
    (root / incoming).write_text("fixture\n", encoding="utf-8")
    guard = "tools/bridge_continuity_guard.py"
    (root / guard).unlink()
    run, _ = fake_git(CANDIDATE_SOURCES)
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"


def test_shared_fixture_only_selects_both_direct_consumers_transitively(tmp_path):
    root = candidate_repo(tmp_path)
    fixture = "tests/tools/test_wd_native_wake_prompt.py"
    run, _ = fake_git((fixture,))
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "bridge", result
    assert set(result["tests"]) == {fixture,
        "tests/tools/test_wd_native_tools_wake.py",
        "tests/tools/test_wd_lead_reply_delivery.py"}


def fake_git(changed: tuple[str, ...], *, diff_code=0, object_code=0,
             status_code=0, head=HEAD):
    calls = []

    def run(command, **kwargs):
        calls.append(tuple(command))
        assert kwargs.get("shell") is not True
        assert command[0] == "git"
        if command[1:3] == ["cat-file", "-t"]:
            return subprocess.CompletedProcess(command, object_code, b"commit\n", b"")
        if command[1:3] == ["rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, (head + "\n").encode(), b"")
        if command[1] == "status":
            return subprocess.CompletedProcess(command, status_code, b"", b"")
        assert command[1:] == ["diff", "--name-only", "-z", "--no-renames", BASE, HEAD]
        return subprocess.CompletedProcess(command, diff_code,
                                           b"\0".join(p.encode() for p in changed) + (b"\0" if changed else b""), b"")

    return run, calls


def real_git_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Two clean commits in a disposable repo, never in the production checkout."""
    root = fixture_repo(tmp_path)
    def git(*args: str) -> str:
        completed = subprocess.run(["git", *args], cwd=root, capture_output=True,
                                   text=True, check=True)
        return completed.stdout.strip()

    git("init", "-q")
    git("config", "user.name", "Bridge Fixture")
    git("config", "user.email", "bridge-fixture@example.invalid")
    git("add", "-A")
    git("commit", "-qm", "base fixture")
    base = git("rev-parse", "HEAD")
    (root / EIGHT[4]).write_text("changed source\n", encoding="utf-8")
    git("add", "--", EIGHT[4])
    git("commit", "-qm", "head fixture")
    head = git("rev-parse", "HEAD")
    return root, base, head


def test_clean_real_git_exact_commits_narrow_and_ignored_audit_is_safe(tmp_path):
    root, base, head = real_git_repo(tmp_path)
    audit = root / ".codex-audit"
    audit.mkdir()
    (audit / "ignored.log").write_text("scratch", encoding="utf-8")
    # The disposable repo's local exclude models the production ignored audit dir.
    (root / ".git/info/exclude").write_text(".codex-audit/\n", encoding="utf-8")
    result = classify_scope(base, head, root)
    assert result["scope"] == "bridge", result
    assert result["changed_files"] == [EIGHT[4]]


@pytest.mark.parametrize("dirty", ["source", "mapped_test", "selector", "staged", "untracked_replacement"])
def test_real_git_dirty_checkout_fails_full(tmp_path, dirty):
    root, base, head = real_git_repo(tmp_path)
    mapped_test = sorted(BRIDGE_EXPLICIT_TESTS[EIGHT[4]])[0]
    if dirty == "source":
        (root / EIGHT[4]).write_text("dirty source\n", encoding="utf-8")
    elif dirty == "mapped_test":
        (root / mapped_test).write_text("def test_dirty(): assert False\n", encoding="utf-8")
    elif dirty == "selector":
        (root / "tools/select_affected_tests.py").write_text("# dirty map\n", encoding="utf-8")
    elif dirty == "staged":
        (root / EIGHT[4]).write_text("staged source\n", encoding="utf-8")
        subprocess.run(["git", "add", "--", EIGHT[4]], cwd=root, check=True, capture_output=True)
    else:
        subprocess.run(["git", "rm", "--cached", "--", mapped_test], cwd=root,
                       check=True, capture_output=True)
    result = classify_scope(base, head, root)
    assert result["scope"] == "full", result
    assert "dirty" in result["reason"] or "loaded tool differs" in result["reason"]


def test_complete_eight_path_bridge_fixture_narrows(tmp_path):
    root = fixture_repo(tmp_path)
    run, calls = fake_git(EIGHT)
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "bridge", result
    assert set(result["tests"]) == bridge_test_closure(set().union(*(BRIDGE_EXPLICIT_TESTS[path] for path in EIGHT)))
    assert result["changed_files"] == list(EIGHT)
    assert any(command[1:5] == ("diff", "--name-only", "-z", "--no-renames") for command in calls)


@pytest.mark.parametrize("extra", ["waggledance/x/app.py", "tests/tools/test_product_only.py", "pyproject.toml"])
def test_mixed_product_config_or_test_only_file_forces_full(tmp_path, extra):
    root = fixture_repo(tmp_path)
    target = root / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("product\n", encoding="utf-8")
    run, _ = fake_git((EIGHT[0], extra))
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"
    run, _ = fake_git((extra,))
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"


def test_test_only_bridge_map_member_is_allowed(tmp_path):
    root = fixture_repo(tmp_path)
    path = "tests/tools/test_wd_native_tools_wake.py"
    run, _ = fake_git((path,))
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "bridge"
    assert set(result["tests"]) == bridge_test_closure({path})


def test_missing_source_mapping_and_missing_mapped_test_fail_closed(tmp_path):
    root = fixture_repo(tmp_path)
    unknown = ".agent-bridge/bin/Unknown.ps1"
    (root / unknown).write_text("source\n", encoding="utf-8")
    run, _ = fake_git((unknown,))
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"
    (root / "tests/tools/test_wd_native_tools_wake.py").unlink()
    run, _ = fake_git(("ops/windows/reboot/bridge-code-files.json",))
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "full"
    assert "missing" in result["reason"]


def test_failed_git_diff_and_missing_git_fail_closed(tmp_path):
    root = fixture_repo(tmp_path)
    run, _ = fake_git(EIGHT, diff_code=128)
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"
    run, _ = fake_git(EIGHT, status_code=128)
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"

    def missing_git(command, **kwargs):
        raise FileNotFoundError("git missing")

    assert classify_scope(BASE, HEAD, root, run_git=missing_git)["scope"] == "full"


@pytest.mark.parametrize("raw", [b"tests/tools/test_wd_native_tools_wake.py", b"\xff\0"])
def test_malformed_or_non_utf8_diff_fails_closed(tmp_path, raw):
    root = fixture_repo(tmp_path)
    normal, _ = fake_git(EIGHT)

    def malformed_git(command, **kwargs):
        if command[1] == "diff":
            return subprocess.CompletedProcess(command, 0, raw, b"")
        return normal(command, **kwargs)

    assert classify_scope(BASE, HEAD, root, run_git=malformed_git)["scope"] == "full"


@pytest.mark.parametrize("changed", [(), ("tests/tools/test_wd_native_tools_wake.py\nother",),
                                     ("tests/tools/space name.py",), ("../escape.py",)])
def test_empty_newline_space_or_unsafe_path_does_not_under_run(tmp_path, changed):
    root = fixture_repo(tmp_path)
    run, _ = fake_git(changed)
    result = classify_scope(BASE, HEAD, root, run_git=run)
    assert result["scope"] == "full"
    assert result["changed_files"] == list(changed)


def test_rename_and_deletion_fail_closed(tmp_path):
    root = fixture_repo(tmp_path)
    new_name = "tests/tools/test_wd_native_tools_wake_renamed.py"
    (root / new_name).write_text("def test_new(): pass\n", encoding="utf-8")
    run, _ = fake_git(("tests/tools/test_wd_native_tools_wake.py", new_name))
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"
    (root / "tests/tools/test_wd_native_tools_wake.py").unlink()
    run, _ = fake_git(("tests/tools/test_wd_native_tools_wake.py",))
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"


@pytest.mark.parametrize("base,head", [("0" * 40, HEAD), ("abc", HEAD), (BASE, "x" * 40), (BASE, BASE)])
def test_invalid_or_equal_commit_ids_fail_without_git(tmp_path, base, head):
    def forbidden(*args, **kwargs):
        raise AssertionError("git must not be called")

    assert classify_scope(base, head, tmp_path, run_git=forbidden)["scope"] == "full"


def test_commit_not_found_or_checkout_head_mismatch_fails_closed(tmp_path):
    root = fixture_repo(tmp_path)
    run, _ = fake_git(EIGHT, object_code=128)
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"
    run, _ = fake_git(EIGHT, head="c" * 40)
    assert classify_scope(BASE, HEAD, root, run_git=run)["scope"] == "full"
