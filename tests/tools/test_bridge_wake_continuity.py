"""Awaited outcomes must survive an accidentally informational envelope."""
import json
import os
import subprocess

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, ROOT


@pytest.mark.parametrize('ps', LANE_TEST_SHELLS)
@pytest.mark.parametrize('kind,status,wakes', [
    ('message', 'full_suite_result', True),
    ('message', 'future_result_kind', True),
    ('decision', 'rco_pass', True),
    ('release', 'stale_lease', True),
    ('message', 'notice', False),
    ('message', 'received', False),
    ('heartbeat', 'active', False),
])
def test_actual_watcher_informational_result(tmp_path, ps, kind, status, wakes):
    # Original incident envelope: no request binding. Waking is not acceptance.
    event = dict(ts_utc='2026-09-28T22:59:32.1625339Z',
                 agent='claude-rco-2', to='codex-lead-1', type=kind,
                 status=status, task_id='package-closure-review',
                 payload={'notification': 'informational'})
    (tmp_path / 'shared').mkdir()
    (tmp_path / 'shared/events.jsonl').write_text(json.dumps(event) + '\n', encoding='utf-8')
    result = subprocess.run([
        ps, '-NoProfile', '-NonInteractive', '-File',
        str(ROOT / '.agent-bridge/bin/Watch-Bridge.ps1'),
        '-Agent', 'codex-lead-1', '-RuntimeRoot', str(tmp_path),
        '-StartLineCount', '0', '-MaxIterations', '1',
        '-PollIntervalMs', '1', '-DebounceMs', '1',
    ], capture_output=True, text=True, timeout=30,
        env=dict(os.environ, WAGGLE_BRIDGE_WAKE_ENABLED='1'))
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'wake_codex-lead-1').exists() is wakes
