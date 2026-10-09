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


def _git_top_level(path):
    '''RS7: a claim cwd must be a git top level (.git with HEAD, objects/ and refs/).'''
    for child in ('objects', 'refs'):
        (path / '.git' / child).mkdir(parents=True)
    (path / '.git' / 'HEAD').write_text('ref: refs/heads/main\n', encoding='utf-8')


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
    for root in (work_a, work_b): _git_top_level(root)
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
    _git_top_level(work)
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


# -- RS7-D (Tools 9758A39B): a valid repository cwd nested inside another valid repository is refused --------------
# Both tools-owned resolvers (Python tools/bridge_v2_resource_scope.py and BridgeResourceScope.ps1) on real files. The
# fixtures need a tmp_path that is not itself inside a repository (pytest's default basetemp).

def _linked(main, worktree, name):
    '''A linked worktree whose admin dir lives in main/.git/worktrees/<name> with matching commondir and back-link.'''
    admin = main / '.git' / 'worktrees' / name
    admin.mkdir(parents=True)
    (admin / 'HEAD').write_text('ref: refs/heads/x\n', encoding='utf-8')
    (admin / 'commondir').write_text('../..\n', encoding='utf-8')
    (admin / 'gitdir').write_text(str(worktree / '.git') + '\n', encoding='utf-8')
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / '.git').write_text('gitdir: ' + str(admin) + '\n', encoding='utf-8')


def _resolve_engine(engine, entry, worktree, bridge):
    '''(accepted, path or error text) from one resolver run.'''
    if engine == 'python':
        from tools.bridge_v2_resource_scope import ScopeError, resolve_scopes
        try:
            return True, resolve_scopes([entry], worktree=str(worktree), bridge_root=str(bridge))[0].path
        except ScopeError as exc:
            return False, str(exc)
    script = (f"$ErrorActionPreference='Stop'; . '{ROOT / '.agent-bridge/bin/BridgeResourceScope.ps1'}'; "
              f"(Resolve-BridgeResourceScopes -Scopes '{entry}' -Worktree '{worktree}' -BridgeRoot '{bridge}').path")
    proc = subprocess.run([engine, '-NoProfile', '-NonInteractive', '-Command', script], capture_output=True,
                          text=True, timeout=60)
    return proc.returncode == 0, (proc.stdout.strip() if proc.returncode == 0 else proc.stderr)


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
@pytest.mark.parametrize('shape', ['nested_git_dir', 'nested_linked'])
def test_a_repository_nested_inside_another_repository_is_refused(tmp_path, engine, shape):
    if engine != 'python' and os.name != 'nt':
        pytest.skip('PowerShell resolver parity is pinned on Windows')
    outer, bridge = tmp_path / 'outer', tmp_path / 'bridge'
    bridge.mkdir()
    outer.mkdir()
    _git_top_level(outer)
    inner = outer / 'nested'
    if shape == 'nested_git_dir':
        inner.mkdir()
        _git_top_level(inner)
    else:
        _linked(outer, inner, 'nested')
    (inner / 'file.txt').write_text('x', encoding='utf-8')
    accepted, detail = _resolve_engine(engine, str(inner / 'file.txt'), inner, bridge)
    assert not accepted and 'nested inside another repository' in detail
    assert _resolve_engine(engine, '*', inner, bridge) == (True, '*')                  # * still overlaps everything
    # Twins: the outer repository names the same physical file once, by its outer path; the same cwd twice agrees.
    assert _resolve_engine(engine, str(inner / 'file.txt'), outer, bridge) == (True, 'nested/file.txt')
    assert _resolve_engine(engine, 'nested/file.txt', outer, bridge) == (True, 'nested/file.txt')


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
def test_an_external_linked_worktree_and_a_plain_repository_stay_accepted(tmp_path, engine):
    if engine != 'python' and os.name != 'nt':
        pytest.skip('PowerShell resolver parity is pinned on Windows')
    main, linked, plain, bridge = (tmp_path / n for n in ('main', 'linked', 'plain', 'bridge'))
    for path in (main, plain, bridge):
        path.mkdir()
    _git_top_level(main)
    _git_top_level(plain)
    _linked(main, linked, 'linked')                         # a sibling of main, not inside it
    for cwd in (linked, plain, main):
        (cwd / 'src').mkdir(exist_ok=True)
        assert _resolve_engine(engine, str(cwd / 'src' / 'a.py'), cwd, bridge) == (True, 'src/a.py')
    missing = tmp_path / 'no-git'
    missing.mkdir()
    accepted, detail = _resolve_engine(engine, 'src/a.py', missing, bridge)
    assert not accepted and 'not a repository top level' in detail


@pytest.mark.parametrize('engine', ['python'] + SHELLS)
def test_dot_segments_in_the_cwd_are_resolved_before_the_ancestor_walk(tmp_path, engine):
    # Grok self-challenge (cb0429db): the ancestors of "<repo>/." are the ancestors of <repo>, never <repo> itself, so a
    # plain repository named with a "." segment is not "nested inside" itself. A ".." segment stays refused by the
    # existing alias rule in both resolvers (it ends in "."), never by the nested rule.
    if engine != 'python' and os.name != 'nt':
        pytest.skip('PowerShell resolver parity is pinned on Windows')
    plain, bridge = tmp_path / 'plain', tmp_path / 'bridge'
    for path in (plain, bridge):
        path.mkdir()
    _git_top_level(plain)
    (plain / 'sub').mkdir()
    assert _resolve_engine(engine, 'src/a.py', str(plain) + os.sep + '.', bridge) == (True, 'src/a.py')
    accepted, detail = _resolve_engine(engine, 'src/a.py', str(plain / 'sub') + os.sep + '..', bridge)
    assert not accepted and 'alias' in detail and 'nested inside another repository' not in detail
