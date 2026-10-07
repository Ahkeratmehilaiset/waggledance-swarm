"""Operator visibility of continuity failures is independent of wake delivery."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize('case', ['missing', 'alert', 'cleared', 'invalid', 'foreign'])
def test_operator_sees_guard_alert_without_claiming_runtime_health(tmp_path, ps, case):
    path = tmp_path / '.codex-audit/wd-turn-loop/continuity-v1-alert.json'
    path.parent.mkdir(parents=True)
    if case != 'missing':
        value = dict(schema='wd.native-continuity-alert.v1', agent='codex-lead-1',
                     status='unknown', error='work needs reconciliation',
                     observed_at_utc='2026-09-29T05:00:00Z')
        if case == 'foreign':
            value['agent'] = 'codex-tools-1'
        if case == 'cleared':
            value['status'] = 'cleared'
        path.write_text('broken' if case == 'invalid' else json.dumps(value))
    source = REBOOT / 'Get-WdSwarmParallelStatus.ps1'
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ('Read-WdStatusRecord', 'Get-WdStatusContinuityAlert'):
        script += load(source, name)
    script += f"Get-WdStatusContinuityAlert -Worktree {q(tmp_path)} -Agent codex-lead-1 | ConvertTo-Json -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report['status'] == (case if case in ('alert', 'cleared') else 'not_observed' if case == 'missing' else 'unknown')
    assert report['task_completion_verified'] is False
    assert report['runtime_health_verified'] is False
