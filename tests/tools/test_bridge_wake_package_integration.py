"""Package closure for the classifier's unconditional wake-module import."""
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


@pytest.mark.parametrize("shell", ["powershell", "pwsh"])
@pytest.mark.parametrize("missing", [None, "BridgeEventClassifier.ps1", MODULE])
def test_deploy_required_gate_rejects_missing_classifier_modules(shell, missing):
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
