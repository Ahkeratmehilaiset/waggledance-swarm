"""Synthetic PowerShell-only reboot status; never executes the fleet launcher."""
import json
import os
from pathlib import Path

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / 'ops/windows/reboot/start-wd-all.ps1'


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell required')
@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize('path,pin,descriptor', [('', '', 'OFF'),
    ('C:\\synthetic\\descriptor.json', 'a'*64, 'UNKNOWN'), ('C:\\synthetic\\descriptor.json', '', 'UNKNOWN')])
def test_status_is_only_a_forwarding_observation_without_io(ps, path, pin, descriptor):
    script = load(LAUNCHER, 'Get-WdQ4DryRunStatus') + r'''
function Test-Path { throw 'unexpected filesystem read' }
function Get-Content { throw 'unexpected filesystem read' }
function Get-FileHash { throw 'unexpected filesystem read' }
function Set-Content { throw 'unexpected filesystem write' }
function Out-File { throw 'unexpected filesystem write' }
function New-Item { throw 'unexpected filesystem write' }
function Remove-Item { throw 'unexpected filesystem write' }
function Start-Process { throw 'unexpected process' }
function Invoke-Expression { throw 'unexpected code evaluation' }
function python { throw 'unexpected Python execution' }
function python3 { throw 'unexpected Python execution' }
'''
    from test_wd_startup_recovery import q
    script += f"Get-WdQ4DryRunStatus -Path {q(path)} -Sha256 {q(pin)} | ConvertTo-Json -Compress\n"
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result == {'descriptor':descriptor, 'runtime':'UNKNOWN', 'interpreter':'UNKNOWN',
                      'stdlib':'UNKNOWN', 'guards':'UNKNOWN', 'query':'UNKNOWN'}


def test_status_is_reported_inside_dryrun_only():
    source = LAUNCHER.read_text(encoding='utf-8')
    marker = 'if ($DryRun) {'
    dryrun = source.rfind(marker)
    assert dryrun >= 0
    block = source[dryrun:source.index('$mutex =', dryrun)]
    assert 'Get-WdQ4DryRunStatus' in block and 'return' in block
    assert 'Get-WdQ4DryRunStatus' not in source[source.index('$mutex =', dryrun):]
