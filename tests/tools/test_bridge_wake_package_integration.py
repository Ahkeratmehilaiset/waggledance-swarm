"""Package closure for the classifier's unconditional wake-module import."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE = "BridgeWakeClass.ps1"
RELATIVE = rf"tools-bootstrap\.agent-bridge\bin\{MODULE}"


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
