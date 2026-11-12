"""Direct Mind startup guard over owned synthetic packages; never start a lane.

Only the new function-only startup helper and a deliberately inert fake wrapper
execute. No real Mind reader, publication, bridge log, agent or model is used.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / 'ops/windows/reboot'
HELPER = REBOOT / 'Invoke-WdMindStartupRead.ps1'
SHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
WINDOWS = pytest.mark.skipif(os.name != 'nt', reason='synthetic Windows PowerShell startup guard')


def _literal(text):
    return "'" + str(text).replace("'", "''") + "'"


def _view():
    lanes = ['codex-lead-1', 'codex-tools-1', 'claude-rco-1', 'claude-rco-2', 'fable-5']
    identity = 'windows-v1:00000001:0000000000000001'
    return dict(schema='wd.bridge-mind-lane-read.v1', authority='none',
                scope='read_only_lane_view_not_gate', lane='codex-tools-1',
                as_of_utc='2026-11-12T18:00:00Z',
                binding=dict(file_identity=identity, generation='g1', origin='lane_reader_view_not_authenticated'),
                pointer=dict(cycle_dir='20261112T180000Z', cycle_generation=1),
                freshness=dict(age_seconds=1, max_age_seconds=900, status='fresh'),
                identity=dict(roster='caller_declared', bound_lanes=sorted(lanes)),
                source=dict(log_identity='capture-window:' + identity, scope='bounded_window_of_bridge_log_not_whole_log',
                            generation='g1', as_of_utc='2026-11-12T18:00:00Z', max_event_utc='UNKNOWN',
                            event_count=0, unique_event_count=0, duplicate_count=0,
                            window=dict(cursor_start=0, cursor_end=0, coverage='complete_over_window_only'), blocked=None),
                lanes={lane: dict(observed_in_window=False, status='not_seen_in_window') for lane in lanes},
                prs={}, refusal=None)


def _package(tmp_path, *, view=None, exit_code=0, delay=0, disabled=False, check_scrub=False):
    bundle = tmp_path / 'synthetic-bundle'
    bundle.mkdir()
    files = {
        'wd-fleet.json': '{}',
        'Get-WdMindReadConfig.ps1': 'function Get-WdMindReadConfig { param($FleetPath,$FleetSha256) ' + (
            'return $null' if disabled else
            '[pscustomobject]@{WD_MIND_READ_ENABLED="1";WD_MIND_LATEST="synthetic-never-read";'
            'WD_MIND_AUDIT_ROOT="synthetic-never-read";WD_MIND_FLEET=$FleetPath;WD_MIND_FLEET_SHA256=$FleetSha256}'
        ) + ' }',
        'tools-bootstrap/tools/wd_bridge_mind_session.py': '# inert pin target; never executed\n',
    }
    output = json.dumps(_view() if view is None else view, separators=(',', ':'))
    files['Invoke-WdBridgePython.ps1'] = (
        "if(Test-Path Env:WD_MIND_OLD_UNTRUSTED){exit 9};" if check_scrub else ''
    ) + ('Start-Sleep -Seconds ' + str(delay) + ';' if delay else '') + (
        '[Console]::Out.Write(' + _literal(output) + ');exit ' + str(exit_code)
    )
    pins = {}
    for relative, text in files.items():
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))
        pins[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_bytes(json.dumps({'files': pins}, separators=(',', ':')).encode('ascii'))
    return bundle, hashlib.sha256(manifest.read_bytes()).hexdigest()


def _run(bundle, manifest_pin, *, timeout=5, inherited=False, worktree=None, setup='', cleanup=''):
    assert HELPER.is_file(), 'Direct startup helper is missing'
    script = r'''
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
. HELPER_PATH
INHERITED
try {
SETUP
$result=Invoke-WdMindStartupRead -BundleRoot BUNDLE_PATH -ExpectedManifestHash MANIFEST_PIN -Lane codex-tools-1 -TimeoutSeconds TIMEOUT RECEIPT
} finally {
CLEANUP
}
$result|ConvertTo-Json -Depth 16 -Compress
'''
    script = script.replace('HELPER_PATH', _literal(HELPER))
    script = script.replace('BUNDLE_PATH', _literal(bundle)).replace('MANIFEST_PIN', _literal(manifest_pin))
    script = script.replace('TIMEOUT', str(timeout))
    script = script.replace('INHERITED', "$env:WD_MIND_OLD_UNTRUSTED='synthetic'" if inherited else '')
    script = script.replace('SETUP', setup)
    script = script.replace('CLEANUP', cleanup)
    script = script.replace('RECEIPT', '-Worktree ' + _literal(worktree) if worktree is not None else '')
    # Child tests receive no provider, bridge ownership or inherited Python settings.
    # The Lead's outer owned job is still required before any test execution.
    child_env = {key: os.environ[key] for key in ['SystemRoot', 'SystemDrive', 'WINDIR', 'TEMP', 'TMP'] if key in os.environ}
    windows = Path(child_env.get('SystemRoot', 'C:/Windows'))
    child_env['PATH'] = str(SHELL.parent) + os.pathsep + str(windows / 'System32')
    child_env['PSModulePath'] = str(SHELL.parent / 'Modules')
    profile = bundle.parent / 'synthetic-profile'
    roaming, local = profile / 'AppData/Roaming', profile / 'AppData/Local'
    roaming.mkdir(parents=True, exist_ok=True)
    local.mkdir(parents=True, exist_ok=True)
    child_env.update(USERPROFILE=str(profile), APPDATA=str(roaming), LOCALAPPDATA=str(local))
    result = subprocess.run([str(SHELL), '-NoProfile', '-NonInteractive', '-EncodedCommand',
                             base64.b64encode(script.encode('utf-16-le')).decode('ascii')],
                            text=True, capture_output=True, timeout=20, check=False, env=child_env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.lstrip('\ufeff'))


@pytest.mark.parametrize('filename,mode', [('start-wd-agent.ps1', 'if ($DryRun)'),
                                         ('start-wd-tools-consumer.ps1', 'if ($ValidateOnly)')])
def test_direct_read_wiring_is_after_inert_mode_exit(filename, mode):
    text = (REBOOT / filename).read_text(encoding='utf-8-sig')
    call = text.index('Invoke-WdMindStartupRead -BundleRoot')
    assert text.index(mode) < call
    assert text.index("'Invoke-WdMindStartupRead.ps1'") < call
    assert 'WD_MIND_STARTUP_VIEW' in text
    assert "outcome -ceq 'success'" in text
    assert 'wd_q4_lane_caller_cli.py' not in text


def test_package_contains_direct_session_and_unchanged_core_interfaces():
    package = json.loads((REBOOT / 'bridge-code-files.json').read_bytes())
    for name in ['tools/wd_bridge_mind.py', 'tools/wd_bridge_mind_lane_read.py', 'tools/wd_bridge_mind_session.py']:
        assert name in package['python_files']
    missing = {'tools/wd_mind_content_prefix_readonly.py'} - set(package['python_files'])
    missing |= {'tools.wd_bridge_mind_lane_read', 'tools.wd_bridge_mind_session',
                'tools.wd_mind_content_prefix_readonly'} - set(package['import_smoke']['package_modules'])
    assert not missing, f'missing packaged modules: {sorted(missing)}'
    assert 'tools/wd_bridge_mind_session.py' in package['python_entrypoints'].values()
    assert 'tools/wd_q4_lane_caller_cli.py' not in package['python_files']


def test_installer_registers_config_and_function_only_startup_helpers():
    text = (REBOOT / 'Deploy-WdRebootBundle.ps1').read_text(encoding='utf-8-sig')
    assert "'Get-WdMindReadConfig.ps1'" in text
    assert "'Invoke-WdMindStartupRead.ps1'" in text


@WINDOWS
def test_disabled_config_never_invokes_reader(tmp_path):
    bundle, pin = _package(tmp_path, disabled=True, exit_code=9)
    result = _run(bundle, pin)
    assert result['outcome'] == 'off'
    assert result['view_json'] == ''


@WINDOWS
@pytest.mark.parametrize('fault', ['manifest_pin', 'config_pin', 'session_pin', 'missing_session'])
def test_unverified_package_fails_closed_before_reader(tmp_path, fault):
    bundle, pin = _package(tmp_path)
    if fault == 'manifest_pin':
        pin = '0' * 64
    elif fault == 'config_pin':
        (bundle / 'Get-WdMindReadConfig.ps1').write_text('throw "synthetic"', encoding='ascii')
    elif fault == 'session_pin':
        (bundle / 'tools-bootstrap/tools/wd_bridge_mind_session.py').write_text('# changed', encoding='ascii')
    else:
        (bundle / 'tools-bootstrap/tools/wd_bridge_mind_session.py').unlink()
    result = _run(bundle, pin)
    assert result['outcome'] == 'refused'
    assert result['view_json'] == ''


@WINDOWS
@pytest.mark.parametrize('fault', ['schema', 'authority', 'scope', 'lane', 'stale', 'extra', 'shape'])
def test_untrusted_or_stale_reader_result_is_withheld(tmp_path, fault):
    view = _view()
    if fault in {'schema', 'authority', 'scope', 'lane'}:
        view[fault] = 'synthetic-wrong'
    elif fault == 'stale':
        view['freshness']['age_seconds'] = 901
    elif fault == 'extra':
        view['raw_capture'] = 'synthetic-private-never-deliver'
    else:
        view = ['synthetic-private-never-deliver']
    bundle, pin = _package(tmp_path, view=view)
    result = _run(bundle, pin)
    assert result['outcome'] == 'refused'
    assert result['view_json'] == ''
    assert 'synthetic-private-never-deliver' not in json.dumps(result)


@WINDOWS
def test_validated_status_projection_is_labelled_and_digest_bound(tmp_path):
    bundle, pin = _package(tmp_path)
    result = _run(bundle, pin)
    assert result['outcome'] == 'success'
    assert json.loads(result['view_json']) == _view()
    assert result['view_sha256'] == hashlib.sha256(result['view_json'].encode('utf-8')).hexdigest()
    assert result['authority'] == 'none'


@WINDOWS
def test_nonzero_reader_exit_never_delivers_partial_output(tmp_path):
    bundle, pin = _package(tmp_path, exit_code=2)
    result = _run(bundle, pin)
    assert result['outcome'] == 'refused'
    assert result['view_json'] == ''


@WINDOWS
def test_output_is_bounded_before_json_parsing(tmp_path):
    bundle, pin = _package(tmp_path, view='x' * 70000)
    result = _run(bundle, pin)
    assert result['outcome'] != 'success'
    assert result['view_json'] == ''


@WINDOWS
def test_untrusted_inherited_mind_environment_is_not_forwarded(tmp_path):
    bundle, pin = _package(tmp_path, check_scrub=True)
    assert _run(bundle, pin, inherited=True)['outcome'] == 'success'


@WINDOWS
def test_timeout_is_unknown_without_retry_or_success(tmp_path):
    bundle, pin = _package(tmp_path, delay=3)
    result = _run(bundle, pin, timeout=1)
    assert result['outcome'] == 'unknown'
    assert result['view_json'] == ''
    assert result['reason'] == 'timeout'


@WINDOWS
@pytest.mark.parametrize('location', ['binding', 'pointer', 'identity', 'source', 'lanes', 'prs', 'source.window', 'lane.claim'])
def test_nested_private_fields_cannot_reach_model_context(tmp_path, location):
    view = _view()
    if location == 'source.window':
        target = view['source']['window']
    elif location == 'lane.claim':
        claim = dict(task_id='synthetic', lease_expires_utc='UNKNOWN', expired='UNKNOWN')
        view['lanes']['codex-lead-1'] = dict(observed_in_window=True, identity_verification='roster_bound',
                                            last_event_utc='2026-11-12T18:00:00Z', observation_age_seconds=1, claims=[claim])
        target = claim
    else:
        target = view[location]
    target['raw_message'] = 'synthetic-private-never-deliver'
    bundle, pin = _package(tmp_path, view=view)
    result = _run(bundle, pin)
    assert result['outcome'] == 'refused'
    assert result['view_json'] == ''
    assert 'synthetic-private-never-deliver' not in json.dumps(result)


@WINDOWS
@pytest.mark.parametrize('relative', ['tools/wd_bridge_mind_lane_read.py', 'tools/bridge_compact_view.py',
                                      'waggledance/core/bridge_log_reader.py'])
def test_changed_imported_module_is_refused_before_execution(tmp_path, relative):
    bundle, _ = _package(tmp_path)
    dependency = bundle / 'tools-bootstrap' / relative
    dependency.parent.mkdir(parents=True, exist_ok=True)
    dependency.write_bytes(b'# inert imported dependency pin\n')
    wrapper = bundle / 'Invoke-WdBridgePython.ps1'
    guard = r'''if ($args -ccontains '-VerifyPackage') {
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'deployment-manifest.json') -Raw|ConvertFrom-Json
foreach($pin in $manifest.files.PSObject.Properties) {
if((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot $pin.Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $pin.Value){exit 2}
}
}
'''
    wrapper.write_bytes(guard.encode('ascii') + wrapper.read_bytes())
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['Invoke-WdBridgePython.ps1'] = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    manifest['files']['tools-bootstrap/' + relative] = hashlib.sha256(dependency.read_bytes()).hexdigest()
    manifest_path.write_bytes(json.dumps(manifest, separators=(',', ':')).encode('ascii'))
    pin = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    dependency.write_bytes(b'# changed inert imported module\n')
    result = _run(bundle, pin)
    assert result['outcome'] == 'refused'
    assert result['view_json'] == ''


@WINDOWS
@pytest.mark.parametrize('case', ['traversal', 'ancestor_junction', 'audit_file', 'target_directory', 'valid'])
def test_receipt_path_is_verified_before_any_creation_or_write(tmp_path, case):
    bundle, pin = _package(tmp_path)
    worktree = tmp_path / 'owned-worktree'
    setup = cleanup = ''
    if case == 'traversal':
        worktree.mkdir()
        worktree = worktree / '..' / 'owned-sibling'
    elif case == 'ancestor_junction':
        # Keep this link entirely outside pytest's automatic recursive basetemp
        # cleanup. On interruption its UUID root is retained, never auto-traversed.
        audit = ROOT / '.codex-audit'
        assert audit.is_dir() and audit.resolve(strict=True) == audit.absolute()
        owned = audit / ('direct-junction-fixture-' + uuid.uuid4().hex)
        assert owned.parent == audit and not owned.exists()
        owned.mkdir()
        physical = owned / 'physical'
        physical.mkdir()
        (physical / 'worktree').mkdir()
        junction = owned / 'link'
        verify = r'''
$link=Get-Item -LiteralPath LINK -Force -ErrorAction Stop
if(($link.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -or $link.LinkType -cne 'Junction' -or
   @($link.Target).Count -ne 1 -or
   -not [IO.Path]::GetFullPath([string]$link.Target[0]).Equals([IO.Path]::GetFullPath(TARGET),[StringComparison]::OrdinalIgnoreCase)) {
throw 'owned_junction_mismatch'
}
'''.replace('LINK', _literal(junction)).replace('TARGET', _literal(physical))
        setup = '[void](New-Item -ItemType Junction -Path ' + _literal(junction) + ' -Target ' + _literal(physical) + ')\n' + verify
        cleanup = 'if(Test-Path -LiteralPath ' + _literal(junction) + ') {\n' + verify + '\n' + (
            '[IO.Directory]::Delete(' + _literal(junction) + ',$false)\n'
            'if(Test-Path -LiteralPath ' + _literal(junction) + "){throw 'owned_junction_cleanup_unconfirmed'}\n}"
        )
        worktree = junction / 'worktree'
    else:
        worktree.mkdir()
        if case == 'audit_file':
            (worktree / '.codex-audit').write_bytes(b'owned-file')
        elif case == 'target_directory':
            (worktree / '.codex-audit' / 'wd-mind-startup-view.json').mkdir(parents=True)
    result = _run(bundle, pin, worktree=worktree, setup=setup, cleanup=cleanup)
    if case == 'valid':
        assert result['outcome'] == 'success' and result['record_written'] is True
        receipt = json.loads((worktree / '.codex-audit' / 'wd-mind-startup-view.json').read_bytes())
        assert 'view_json' not in receipt
        assert receipt['view_sha256'] == result['view_sha256']
    else:
        assert result['outcome'] != 'success'
        assert result['record_written'] is False
        assert result['view_json'] == ''


@WINDOWS
@pytest.mark.parametrize('location', ['view', 'binding'])
def test_discarded_duplicate_fields_never_reach_context(tmp_path, location):
    # JSON decoders keep the last duplicate value; validating that object does
    # not make the original, unnormalized text safe to forward to a model.
    raw = json.dumps(_view(), separators=(',', ':'))
    if location == 'view':
        raw = '{"schema":"synthetic-private-duplicate-never-deliver",' + raw[1:]
    else:
        raw = raw.replace('"origin":', '"origin":"synthetic-private-duplicate-never-deliver","origin":', 1)
    bundle, _ = _package(tmp_path)
    wrapper = bundle / 'Invoke-WdBridgePython.ps1'
    wrapper.write_bytes(('[Console]::Out.Write(' + _literal(raw) + ');exit 0').encode('ascii'))
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files']['Invoke-WdBridgePython.ps1'] = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    manifest_path.write_bytes(json.dumps(manifest, separators=(',', ':')).encode('ascii'))
    pin = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    result = _run(bundle, pin)
    assert 'synthetic-private-duplicate-never-deliver' not in json.dumps(result)
    if result['outcome'] == 'success':
        assert json.loads(result['view_json']) == _view()
