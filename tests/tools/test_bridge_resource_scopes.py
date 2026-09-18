"""Worktree-local state must not weaken repository-wide source claims."""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import subprocess

import pytest

from waggledance.core.work_queue import claim_task, WorkQueueError

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which('pwsh'), shutil.which('powershell.exe')])))


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
@pytest.mark.parametrize('case,allowed', [
    ('different_checkpoint', True), ('same_checkpoint', False), ('case_variant', False),
    ('source_same_logical_path', False), ('checkpoint_parent', False),
    ('shared', False), ('traversal', False), ('unknown_kind', False), ('missing_cwd', False),
    ('absolute_checkpoint', False), ('absolute_source', False), ('short_alias', False),
    ('trailing_dot', False), ('local_source_bypass', False),
])
def test_claim_resource_identity(tmp_path, monkeypatch, engine, case, allowed):
    if engine != 'python' and os.name != 'nt':
        pytest.skip('PowerShell claim writes are Windows-only')
    work_a, work_b, bridge = (tmp_path / n for n in ('work-a','work-b','bridge'))
    for root in (work_a, work_b, bridge): root.mkdir()
    scopes_a = scopes_b = ['.codex-audit/wd-current-state.json']
    cwd_b = work_b
    if case in ('same_checkpoint', 'case_variant', 'checkpoint_parent'): cwd_b = work_a
    if case == 'case_variant': scopes_b = ['.CODEX-AUDIT/WD-CURRENT-STATE.JSON']
    if case == 'source_same_logical_path': scopes_a = scopes_b = ['src/main.py']
    if case == 'checkpoint_parent': scopes_a = ['worktree:.codex-audit']
    if case == 'shared': scopes_a = scopes_b = ['shared:shared/events.jsonl']
    if case == 'traversal': scopes_b = ['worktree:.codex-audit/../src/main.py']
    if case == 'unknown_kind': scopes_b = ['invented:.codex-audit/wd-current-state.json']
    if case == 'absolute_checkpoint':
        cwd_b = work_a
        scopes_b = [str(work_a / '.codex-audit/wd-current-state.json')]
    if case == 'absolute_source':
        scopes_a = ['src/main.py']
        scopes_b = [str(work_b / 'src/main.py')]
    if case == 'short_alias': scopes_b = ['worktree:.codex~1/wd-current-state.json']
    if case == 'trailing_dot': scopes_b = ['worktree:.codex-audit./wd-current-state.json']
    if case == 'local_source_bypass': scopes_b = ['worktree:src/main.py']
    now = datetime.now(timezone.utc)
    claims = bridge / 'work_queue/claims'
    claims.mkdir(parents=True)
    (claims / 'foreign.json').write_text(json.dumps(dict(
        agent='fable-5', task_id='fixture/foreign', summary='fixture only', mode='write',
        write_scope=scopes_a, cwd='' if case == 'missing_cwd' else str(work_a),
        run_id='fixture', claimed_at_utc=now.isoformat(), last_heartbeat_utc=now.isoformat(),
        lease_seconds=900, claim_lease_expires_utc=(now+timedelta(minutes=15)).isoformat())))
    diagnostic = ''
    if engine == 'python':
        monkeypatch.chdir(cwd_b)
        try:
            claim_task(agent='codex-lead-1', task_id='fixture/ours', summary='fixture',
                       mode='write', write_scope=scopes_b, bridge_root=bridge)
            success = True
        except WorkQueueError as exc:
            diagnostic = str(exc)
            success = False
    else:
        env = {k:v for k,v in os.environ.items() if not k.startswith('AGENT_BRIDGE_')}
        env['AGENT_BRIDGE_RUNTIME_ROOT'] = str(bridge)
        proc = subprocess.run([engine, '-NoProfile', '-File', str(ROOT / '.agent-bridge/bin/Claim-AgentTask.ps1'),
                               '-Agent','codex-lead-1','-TaskId','fixture/ours','-Summary','fixture',
                               '-AgentUuid', 'd3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101',
                               '-Mode','write','-WriteScope',scopes_b[0]],
                              cwd=cwd_b, env=env, capture_output=True, text=True, timeout=35)
        success = proc.returncode == 0
        diagnostic = proc.stdout + proc.stderr
    assert success is allowed, diagnostic


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
def test_linked_checkpoint_directory_is_rejected(tmp_path, engine):
    from waggledance.core.bridge_resource_scope import resolve_resources
    real, work, bridge = (tmp_path / p for p in ('real', 'work', 'bridge'))
    for path in (real, work, bridge): path.mkdir()
    alias = work / '.codex-audit'
    try:
        alias.symlink_to(real, target_is_directory=True)
    except OSError:
        if os.name != 'nt': raise
        proc = subprocess.run([shutil.which('powershell.exe'), '-NoProfile', '-Command',
                               f"New-Item -ItemType Junction -Path '{alias}' -Target '{real}' | Out-Null"],
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
    if engine == 'python':
        with pytest.raises(ValueError, match='link/reparse'):
            resolve_resources(['.codex-audit/wd-current-state.json'], cwd=str(work), bridge_root=str(bridge))
    else:
        script = f"$ErrorActionPreference='Stop'; . '{ROOT / '.agent-bridge/bin/BridgeResourceScope.ps1'}'; Resolve-BridgeResourceScopes -Scopes '.codex-audit/wd-current-state.json' -Worktree '{work}' -BridgeRoot '{bridge}'"
        proc = subprocess.run([engine,'-NoProfile','-Command',script], capture_output=True, text=True)
        assert proc.returncode != 0 and 'reparse point' in proc.stderr
