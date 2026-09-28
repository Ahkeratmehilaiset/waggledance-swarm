"""Required B7 helper cannot disappear from a verified reboot bundle."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
HELPERS = ["ClaimLeaseHeartbeat.ps1", "BridgeNamedMutex.ps1"]


@pytest.mark.parametrize("helper", HELPERS)
def test_fleet_requires_session_owned_lease_helper(helper):
    fleet = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    assert "tools-bootstrap/.agent-bridge/bin/" + helper in fleet["deployment"]["required_bundle_files"]
    assert (ROOT / ".agent-bridge/bin" / helper).is_file()


@pytest.mark.parametrize(
    ("script", "prefix"),
    [
        ("Deploy-WdRebootBundle.ps1", "tools-bootstrap/.agent-bridge/bin/"),
        ("start-wd-tools-consumer.ps1", ""),
    ],
)
@pytest.mark.parametrize("helper", HELPERS)
def test_installer_and_consumer_require_lease_helper(script, prefix, helper):
    source = (REBOOT / script).read_text(encoding="utf-8-sig")
    assert f"'{prefix}{helper}'," in source
