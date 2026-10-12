"""Tools config selection is process-only, no credential read/login/copy."""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

TOOLS = REBOOT / "start-wd-tools-consumer.ps1"


def test_launcher_selects_operator_directory_before_children():
    text = TOOLS.read_text(encoding="utf-8-sig")
    assert "[string] $ConfigDirectory = 'C:\\Python\\wd-gh-tools'" in text
    call = "$null = Set-WdToolsGitHubConfigDirectory"
    assert text.count(call) == 1
    assert text.index(call) < text.index("$env:Path = $codexPathPlan.Path")
    assert "$info.UseShellExecute = $false" in text


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["present", "absent", "file", "reparse_refusal", "mutant"])
@pytest.mark.skipif(os.name != "nt", reason="Windows-only launcher directory policy")
def test_process_config_selection_preserves_credentials(tmp_path, ps, case):
    config = tmp_path / "gh-dir"
    if case == "file":
        config.write_text("ordinary non-credential file")
    elif case != "absent":
        config.mkdir()
        (config / "ordinary.txt").write_text("no credentials")
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    script = "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
    for name in ["Test-PathAtOrBelow", "Assert-DirectoryPathWithoutReparse", "Set-WdToolsGitHubConfigDirectory"]:
        script += load(TOOLS, name)
    if case == "reparse_refusal":
        script += "function Assert-DirectoryPathWithoutReparse {param($Candidate,$Root,[switch]$AllowMissing) throw 'reparse refused'}\n"
    if case == "mutant":
        # Executable in-memory mutant: remove the actual process env selection.
        script += """
$fn = (Get-Item Function:Set-WdToolsGitHubConfigDirectory).ScriptBlock.ToString()
$needle="[Environment]::SetEnvironmentVariable('GH_CONFIG_DIR', $candidate, 'Process')"
if(-not $fn.Contains($needle)){throw 'mutant anchor missing'}
$fn=$fn.Replace($needle,'$null = $candidate')
Set-Item Function:Set-WdToolsGitHubConfigDirectory ([scriptblock]::Create($fn))
""".replace('"[Environment]::SetEnvironmentVariable(\'GH_CONFIG_DIR\', $candidate, \'Process\')"',
             "'[Environment]::SetEnvironmentVariable(''GH_CONFIG_DIR'', $candidate, ''Process'')'")
    script += f"""
[Environment]::SetEnvironmentVariable('GH_CONFIG_DIR','inherited-directory','Process')
[Environment]::SetEnvironmentVariable('GH_TOKEN','inert-test-value','Process')
try {{ $result=Set-WdToolsGitHubConfigDirectory -ConfigDirectory {q(config)}; $ok=$true }}
catch {{ $result=$null; $ok=$false }}
@{{ok=$ok;selected=([Environment]::GetEnvironmentVariable('GH_CONFIG_DIR','Process') -eq {q(config)});
 preserved=([Environment]::GetEnvironmentVariable('GH_CONFIG_DIR','Process') -eq 'inherited-directory');
 token_unchanged=([Environment]::GetEnvironmentVariable('GH_TOKEN','Process') -eq 'inert-test-value');
 major=$PSVersionTable.PSVersion.Major}}|ConvertTo-Json -Compress
"""
    r = json.loads(_run_powershell(script, executable=ps).stdout)
    assert r["major"] >= 5
    assert r["token_unchanged"] is True
    assert r["ok"] is (case not in {"file", "reparse_refusal"})
    assert r["selected"] is (case == "present")
    assert r["preserved"] is (case != "present")
    assert {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_named_mutant_guard_on_invocation_order():
    source = TOOLS.read_text(encoding="utf-8-sig")
    call = "$null = Set-WdToolsGitHubConfigDirectory"
    assert call in source
    mutant = source.replace(call, "# MUTANT_SKIP_GH_CONFIG_INHERITANCE")
    assert call not in mutant
