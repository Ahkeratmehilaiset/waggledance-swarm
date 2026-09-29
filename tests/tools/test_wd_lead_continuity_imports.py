"""Fail-closed coverage for the Lead terminal's verified function imports."""

import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from test_wd_reboot_bundle import POWERSHELL, WINDOWS_POWERSHELL, REBOOT, _run_powershell
from test_wd_startup_recovery import load, q


CONTINUITY_FUNCTIONS = (
    'Invoke-WdContinuityDecision',
    'Invoke-WdNativeContinuityStep',
    'Test-WdContinuityControlEvents',
    'Invoke-WdContinuityOperatorNotice',
    'Get-WdContinuityRetryDelay',
)
SHELLS = list({os.path.normcase(path): path for path in filter(
    None, (shutil.which('pwsh'), WINDOWS_POWERSHELL, POWERSHELL),
)}.values())


@pytest.mark.parametrize('ps', SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('mutation', ('missing', 'duplicate'))
@pytest.mark.parametrize('name', CONTINUITY_FUNCTIONS)
def test_lead_rejects_incomplete_verified_continuity_imports_before_side_effects(
    tmp_path, ps, mutation, name,
):
    """The source AST, not a mock importer, must reject each bad snapshot."""
    cli = tmp_path / 'codex.exe'
    cli.write_bytes(b'fixture executable: never launch')
    runtime = tmp_path / 'runtime'
    runtime.mkdir()

    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for function in ('Assert-WdTurnPath', 'Write-WdTurnJson', 'Move-WdWakeSnapshot'):
        script += load(REBOOT / 'Invoke-WdLaneTurnLoop.ps1', function)
    for function in (
        'ConvertTo-WdToolsNativeArgument', 'Invoke-WdNativeToolsWakeStep',
        'Invoke-WdNativeToolsWakeRelay', *CONTINUITY_FUNCTIONS,
    ):
        script += load(REBOOT / 'start-wd-tools-consumer.ps1', function)
    script += """
$global:starts=0; $global:sends=0
function Start-WdToolsNativeProcess {
  $global:starts++; throw 'native process must not start'
}
function Send-WdNativeToolsQueueMessage {
  $global:sends++; throw 'queue must not receive a message'
}
"""
    # Keep the real launcher import algorithm; bypass only its console probe,
    # as the subprocess test has no interactive terminal.
    script += load(REBOOT / 'start-wd-agent.ps1', 'Invoke-WdNativeLeadTerminal').replace(
        '$fn.Extent.Text)',
        "$fn.Extent.Text.Replace('[Console]::IsInputRedirected','$false'))",
    )
    script += f"""
$verified=@{{}}
$groups=@{{
  'Invoke-WdLaneTurnLoop.ps1'=@('Assert-WdTurnPath','Write-WdTurnJson','Move-WdWakeSnapshot');
  'start-wd-tools-consumer.ps1'=@(
    'ConvertTo-WdToolsNativeArgument','Send-WdNativeToolsQueueMessage',
    'Invoke-WdNativeToolsWakeStep','Invoke-WdNativeToolsWakeRelay',
    'Start-WdToolsNativeProcess',
    'Invoke-WdContinuityDecision','Invoke-WdNativeContinuityStep',
    'Test-WdContinuityControlEvents','Invoke-WdContinuityOperatorNotice',
    'Get-WdContinuityRetryDelay')
}}
foreach($file in $groups.Keys){{
  $definitions=@()
  foreach($functionName in $groups[$file]){{
    if($file -eq 'start-wd-tools-consumer.ps1' -and
       $functionName -ceq {q(name)} -and {q(mutation)} -eq 'missing'){{continue}}
    $definition='function '+$functionName+' {{'+(Get-Command $functionName).ScriptBlock.ToString()+'}}'
    $definitions+=,$definition
    if($file -eq 'start-wd-tools-consumer.ps1' -and
       $functionName -ceq {q(name)} -and {q(mutation)} -eq 'duplicate'){{
      $definitions+=,$definition
    }}
  }}
  # These are synthetic already-verified snapshots. Their top-level trap must
  # never execute when the launcher extracts named function AST nodes.
  $verified[$file]='throw "top-level must not execute"'+"`n"+($definitions -join "`n")
}}
try {{
  Invoke-WdNativeLeadTerminal -CliPath {q(cli)} `
    -Arguments @('resume','01a0a654-12af-7d81-85fc-d75d515c5b65') `
    -ThreadId 01a0a654-12af-7d81-85fc-d75d515c5b65 `
    -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)} `
    -Generation fixture -SessionId session `
    -ExpectedCliHash {hashlib.sha256(cli.read_bytes()).hexdigest().upper()} `
    -VerifiedCode $verified
  $errorText='NO_ERROR'
}} catch {{ $errorText=$_.Exception.Message }}
@{{error=$errorText;starts=$global:starts;sends=$global:sends}} | ConvertTo-Json -Compress
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result == {
        'error': f'Native relay function is missing or ambiguous: {name}',
        'starts': 0,
        'sends': 0,
    }
    assert not (tmp_path / '.codex-audit' / 'wd-turn-loop' / 'native-terminal.json').exists()
