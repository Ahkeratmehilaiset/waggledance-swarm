"""Reject packages missing wake, request, result or reply-reader libraries."""
import json
import re
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = "BridgeWakeClass.ps1"
RELATIVE = rf"tools-bootstrap\.agent-bridge\bin\{MODULE}"
ENTRYPOINTS = {
    "Get-BridgeNextAction.ps1", "Read-AgentBridge.ps1",
    "Claim-AgentTask.ps1", "Release-AgentTask.ps1", "Write-AgentEvent.ps1",
    "Monitor-AgentBridge.ps1", "Write-BridgeTaskReply.ps1",
    "Start-BridgeRequestTurn.ps1", "Get-BridgeReplySnapshot.ps1",
    "Record-BridgeReplyObservation.ps1",
}
PREFIX = "tools-bootstrap/.agent-bridge/bin/"


def required_bridge_scripts():
    source = (ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8-sig")
    gate = source.split("foreach ($required in @(", 1)[1].split("$targetRoot =", 1)[0]
    return {name.removeprefix(PREFIX) for name in re.findall(r"'([^']+)'", gate)
            if name.startswith(PREFIX)}


def literal_sibling_closure(roots):
    # Conservative literal sibling references, including execution-evidence
    # hash lists. Not a PowerShell parser: computed paths and ops/Python
    # imports require their own integration acceptance.
    seen = set()
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        path = ROOT / ".agent-bridge/bin" / name
        # Keep unresolved names in the closure for an explicit test failure;
        # never filter away a real missing dependency or crash collection.
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8-sig")
        pending.extend(set(re.findall(r"['\"]([A-Za-z0-9_.-]+\.ps1)['\"]", source)) - seen)
    return seen


def test_declared_entrypoints_and_literal_sibling_closure_are_required():
    required = required_bridge_scripts()
    closure = literal_sibling_closure(required | ENTRYPOINTS)
    unresolved = sorted(name for name in closure if not (ROOT / ".agent-bridge/bin" / name).is_file())
    assert not unresolved, f"Unresolved bridge script literals: {unresolved}; inspect references, do not silently exclude them"
    missing = closure - required
    assert not missing, f"Missing required bridge scripts: {sorted(missing)}"


@pytest.mark.parametrize("source", [
    "# See 'Missing.ps1' outside this directory\n",
    ". (Join-Path $PSScriptRoot 'Missing.ps1')\n",
])
def test_unresolved_literal_is_reported_without_collection_crash(tmp_path, monkeypatch, source):
    bin_root = tmp_path / ".agent-bridge/bin"
    bin_root.mkdir(parents=True)
    (bin_root / "Entry.ps1").write_text(source, encoding="utf-8")
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "ENTRYPOINTS", {"Entry.ps1"})
    # Even listing the absent name as required must not hide its absence.
    monkeypatch.setitem(globals(), "required_bridge_scripts", lambda: {"Entry.ps1", "Missing.ps1"})
    assert literal_sibling_closure({"Entry.ps1"}) == {"Entry.ps1", "Missing.ps1"}
    with pytest.raises(AssertionError, match=r"Unresolved bridge script literals:.*Missing.ps1"):
        test_declared_entrypoints_and_literal_sibling_closure_are_required()


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("missing", [None, *sorted(literal_sibling_closure(required_bridge_scripts() | ENTRYPOINTS))])
def test_deploy_required_gate_rejects_missing_bridge_libraries(shell, missing):
    executable = shutil.which(shell)
    if executable is None:
        pytest.skip(f"{shell} unavailable")
    source = (ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8-sig")
    # Execute only the actual validation loop, never the deployment script.
    gate = "foreach ($required in @(" + source.split("foreach ($required in @(", 1)[1].split("$targetRoot =", 1)[0]
    required = re.findall(r"'([^']+)'", gate)
    files = set(required) | {
        "tools-bootstrap/.agent-bridge/bin/BridgeEventClassifier.ps1",
        f"tools-bootstrap/.agent-bridge/bin/{MODULE}",
    }
    files.update(PREFIX + name for name in literal_sibling_closure(required_bridge_scripts() | ENTRYPOINTS))
    if missing:
        files.remove(f"tools-bootstrap/.agent-bridge/bin/{missing}")
    entries = ";".join(f"'{name}'='hash'" for name in sorted(files))
    script = "$ErrorActionPreference='Stop'; $sourceHashes=[ordered]@{" + entries + "}; " + gate + "; 'accepted'"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("WD_", "AGENT_BRIDGE_", "CLAUDE", "GIT_"))}
    result = subprocess.run([executable, "-NoProfile", "-NonInteractive", "-Command", script], env=env, capture_output=True, text=True, timeout=30)
    if missing:
        assert result.returncode != 0, f"Missing {missing} accepted by {shell}"
        assert f"required reboot bundle file is missing: tools-bootstrap/.agent-bridge/bin/{missing}" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert "accepted" in result.stdout


def test_wake_module_is_an_attested_watcher_dependency():
    config = json.loads((ROOT / "ops/windows/reboot/wd_supervisor_loop.json").read_text())
    dependencies = config["watchers"]["dependency_relatives"]
    assert dependencies.count(RELATIVE) == 1
    source = (ROOT / "ops/windows/reboot/wd_supervisor.ps1").read_text(encoding="utf-8-sig")
    expected = source.split("$expectedWatcherDependencies = @(", 1)[1].split(")", 1)[0]
    assert expected.count(f"'{RELATIVE}'") == 1


def test_imported_wake_module_has_same_protocol_protection():
    denylist = (ROOT / "configs/eig2_self_modification_denylist.yaml").read_text()
    # Stop at the next two-space category, not its indented children.
    protocol = re.split(r"\n  [a-z_]+:", denylist.split("  bridge_protocol:", 1)[1], maxsplit=1)[0]
    assert f'".agent-bridge/bin/{MODULE}"' in protocol
    assert '".agent-bridge/bin/BridgeEventClassifier.ps1"' in protocol


def test_classifier_import_target_is_materialized():
    classifier = (ROOT / ".agent-bridge/bin/BridgeEventClassifier.ps1").read_text(encoding="utf-8-sig")
    assert f". (Join-Path $PSScriptRoot '{MODULE}')" in classifier
    assert (ROOT / ".agent-bridge/bin" / MODULE).is_file()
