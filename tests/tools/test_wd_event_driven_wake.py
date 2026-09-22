"""Scheduling admission and real monitor tests; no provider/model calls."""
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / 'ops/windows/reboot'
HOSTS = list(dict.fromkeys(filter(None, [shutil.which('pwsh'), shutil.which('powershell.exe')])))


@pytest.mark.parametrize('host', HOSTS or [None])
@pytest.mark.parametrize('tamper', [False, True])
def test_actual_claude_launch_arguments_pin_cron_disable_and_preserve_resume(tmp_path, host, tamper):
    if host is None or os.name != 'nt': pytest.skip('Windows launcher path rules')
    source = (REBOOT/'start-wd-agent.ps1').read_text(encoding='utf-8')
    block = source.split('$launchArguments = @()\n', 1)[1].split('$previousPreference =', 1)[0]
    settings = tmp_path/'wd-claude-event-driven-settings.json'
    shutil.copyfile(REBOOT/settings.name, settings)
    value = json.loads(settings.read_text())
    assert value == {'env': {'CLAUDE_CODE_DISABLE_CRON': '1'}}
    pin = hashlib.sha256(settings.read_bytes()).hexdigest().upper()
    if tamper: settings.write_text('{"env":{"CLAUDE_CODE_DISABLE_CRON":"0"}}')
    script = tmp_path/'probe.ps1'
    script.write_text(f"""
$ErrorActionPreference='Stop';Set-StrictMode -Version Latest
$ast=[Management.Automation.Language.Parser]::ParseFile('{REBOOT / 'start-wd-agent.ps1'}',[ref]$null,[ref]$null)
$fn=$ast.Find({{param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Assert-LanePathWithoutReparse'}},$true)
. ([scriptblock]::Create($fn.Extent.Text))
$laneTrustedDrive=[IO.Path]::GetPathRoot($PSScriptRoot)
$deploymentAnchor=[pscustomobject]@{{files=[pscustomobject]@{{'wd-claude-event-driven-settings.json'='{pin}'}}}}
$cliName='claude.cmd';$model='opus';$effort='max';$Agent='fable-5'
$claudeResume=[pscustomobject]@{{thread_id='9f375967-f824-4e2e-8104-7f0011117cf5'}}
$continuationPrompt='preserve work';$startupPrompt='initial'
$env:CLAUDE_CODE_DISABLE_CRON='0'
$launchArguments=@()
{block}
[pscustomobject]@{{args=$launchArguments;cron=$env:CLAUDE_CODE_DISABLE_CRON}}|ConvertTo-Json -Depth 8 -Compress
""", encoding='utf-8')
    # A PS7 parent can export its module path into PS5. Let each host build
    # its own standard module path, as in the installed Windows launcher.
    environment = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    p = subprocess.run([host, '-NoProfile', '-NonInteractive', '-File', str(script)],
                       capture_output=True, text=True, timeout=30, env=environment)
    if tamper:
        assert p.returncode != 0 and 'not pinned' in p.stderr
    else:
        assert p.returncode == 0, p.stderr
        result = json.loads(p.stdout)
        assert result['args'][:2] == ['--resume', '9f375967-f824-4e2e-8104-7f0011117cf5']
        assert result['args'][2:4] == ['--settings', str(settings)]
        assert result['args'][4:8] == ['--model', 'opus', '--effort', 'max']
        assert result['args'][-1] == 'preserve work'
        assert result['cron'] == '1'


@pytest.mark.parametrize('host', HOSTS or [None])
def test_real_monitor_quiet_noise_then_request_revision_and_late_reply(tmp_path, host):
    if host is None: pytest.skip('PowerShell unavailable')
    shared = tmp_path/'shared'
    shared.mkdir()
    events = shared/'events.jsonl'
    # Initialize a complete log so the real reader can establish its identity.
    heartbeat = dict(agent='operator', to='claude-rco-2', type='heartbeat', status='alive',
                     task_id='idle', ts_utc='2026-09-22T00:00:00Z')
    events.write_text(json.dumps(heartbeat)+'\n')
    state = shared/'monitor_claude-rco-2.cursor.json'
    command = [host, '-NoProfile', '-NonInteractive', '-File',
               str(ROOT/'.agent-bridge/bin/Monitor-AgentBridge.ps1'), '-Agent', 'claude-rco-2',
               '-RuntimeRoot', str(tmp_path), '-TargetedOnly', '-IncludeWakeRequests',
               '-Json', '-PollIntervalMs', '50']
    p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    output = queue.Queue()
    thread = threading.Thread(target=lambda: [output.put(line) for line in p.stdout], daemon=True)
    thread.start()
    try:
        deadline = time.monotonic()+20
        while not state.exists() and time.monotonic()<deadline:
            assert p.poll() is None
            time.sleep(.05)
        assert state.exists()
        def append(*rows):
            with events.open('a', encoding='utf-8') as f:
                for row in rows: f.write(json.dumps(row)+'\n')
        append(heartbeat, dict(heartbeat, type='message', status='received'),
               dict(heartbeat, type='message', status='notice', payload={'notification':'informational'}))
        time.sleep(float(os.environ.get('WD_MONITOR_QUIET_SECONDS', '1')))
        assert output.empty(), 'idle/infrastructure traffic must not emit a model wake'
        request = dict(heartbeat, type='message', status='request', request_id='r1')
        revision = dict(request, request_id='r2')
        late = dict(heartbeat, type='message', status='answered', in_reply_to_request_id='r1')
        correction = dict(late, message='corrected result')
        append(request, request, revision, late, correction)
        received = [json.loads(output.get(timeout=10)) for _ in range(4)]
        assert [x.get('request_id') for x in received] == ['r1', 'r2', None, None]
        assert received[-1]['message'] == 'corrected result'
        time.sleep(.3)
        assert output.empty()
    finally:
        p.terminate()  # only this fixture-owned monitor, never a fleet process
        p.wait(timeout=10)
        thread.join(timeout=2)
        p.stdout.close()
        p.stderr.close()
