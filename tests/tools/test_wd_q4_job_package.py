"""Inactive Q4 job helper packaging; synthetic files only, no install."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
NAME = "WdQ4ProcessJob.ps1"
SIBLINGS = ("Get-WdQ4ReadConfig.ps1", "Invoke-WdQ4Query.ps1")
DEPLOY = REBOOT / "Deploy-WdRebootBundle.ps1"
FLEET = REBOOT / "wd-fleet.json"


@pytest.mark.parametrize("source", ["deployer", "fleet"])
def test_deployer_and_fleet_require_inactive_job_helper_with_siblings(source):
    text = DEPLOY.read_text(encoding="utf-8-sig")
    match = re.search(r"(?ms)foreach \(\$required in @\((.*?)\)\) \{", text)
    assert match, "deployer required-file list missing"
    fleet = json.loads(FLEET.read_text(encoding="utf-8-sig"))
    for name in (NAME, *SIBLINGS):
        if source == "deployer":
            assert match.group(1).count(f"'{name}',") == 1
        else:
            assert fleet["deployment"]["required_bundle_files"].count(name) == 1
    assert fleet["q4_read"] == {"enabled": False}


def test_deployer_hash_map_is_the_manifest_and_copy_source():
    text = DEPLOY.read_text(encoding="utf-8-sig")
    assert "$sourceHashes = Get-RelativeFileHashMap -Root $materializedRebootRoot" in text
    assert re.search(r"(?m)^\s+files = \$sourceHashes\s*$", text)
    assert "if (-not $sourceHashes.Contains($required))" in text
    assert "Copy-Item -LiteralPath $sourcePaths[[string]$name] -Destination $destination" in text
    assert "copied reboot bundle hash mismatch" in text


def test_fleet_start_checks_each_required_name_against_hashed_manifest_entries():
    source = (REBOOT / "start-wd-all.ps1").read_text(encoding="utf-8-sig")
    assert "foreach ($requiredName in @($Manifest.deployment.required_bundle_files))" in source
    assert "$listed.ContainsKey(([string]$requiredName).ToLowerInvariant())" in source
    assert "deployment manifest does not cover required file" in source
    assert "Get-FileHash -LiteralPath $candidate -Algorithm SHA256" in source


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell.exe"), reason="Windows PowerShell required")
def test_actual_hash_map_pins_synthetic_helper_and_detects_change(tmp_path):
    text = DEPLOY.read_text(encoding="utf-8-sig")
    match = re.search(r"(?ms)^function Get-RelativeFileHashMap \{.*?^\}", text)
    assert match
    original = b"# synthetic helper v1; never executed\n"
    changed = b"# synthetic helper v2; never executed\n"
    helper = tmp_path / NAME
    helper.write_bytes(original)
    (tmp_path / SIBLINGS[0]).write_bytes(b"# synthetic sibling\n")
    (tmp_path / "deployment-manifest.json").write_bytes(b"{}")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'\n"
              "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility') -ErrorAction Stop\n"
              + match.group(0) + "\n"
              + "$map=Get-RelativeFileHashMap -Root " + quote(tmp_path) + "\n"
              "[ordered]@{files=$map}|ConvertTo-Json -Depth 8 -Compress\n")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    def read_map():
        run = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive",
                              "-EncodedCommand", encoded], capture_output=True, text=True, timeout=15)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)["files"]
    hashes = read_map()
    assert hashes[NAME] == hashlib.sha256(original).hexdigest().upper()
    assert SIBLINGS[0] in hashes and "deployment-manifest.json" not in hashes
    helper.write_bytes(changed)
    assert read_map()[NAME] == hashlib.sha256(changed).hexdigest().upper()
    assert read_map()[NAME] != hashes[NAME]
