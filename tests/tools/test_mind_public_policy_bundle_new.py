"""The installed bridge package includes the two public Mind policies."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "ops/windows/reboot/bridge-code-files.json"
POLICIES = (
    "configs/mind/pilot_non_review_policy.json",
    "configs/mind/pilot_public_status_policy.json",
)
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")


def test_mind_policy_files_are_in_package_once_and_exist():
    definition = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for relative_path in POLICIES:
        assert definition["python_files"].count(relative_path) == 1
        assert (ROOT / relative_path).is_file()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell package parser unavailable")
def test_existing_package_parser_accepts_mind_policies():
    context = ROOT / "ops/windows/reboot/BridgeCodeContext.ps1"

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    expected = ",".join(quote(path) for path in POLICIES)
    script = (
        "$ErrorActionPreference='Stop'; "
        f". {quote(context)}; "
        f"$package=Get-WdBridgeCodePackageDefinition -Path {quote(MANIFEST)}; "
        f"foreach($expected in @({expected})) {{ "
        "if (@($package.Definition.python_files | Where-Object { $_ -ceq $expected }).Count -ne 1) "
        "{ throw 'Expected policy missing or duplicated' } }; 'ok'"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"
