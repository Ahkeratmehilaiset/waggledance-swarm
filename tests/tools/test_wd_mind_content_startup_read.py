"""Synthetic content startup worker: no live policy, owner or bridge."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

import test_wd_bridge_code_context as bridge_fixture


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / 'ops/windows/reboot/Invoke-WdMindContentStartupRead.ps1'
SHELL = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _environment():
    names = ('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP',
             'USERPROFILE','LOCALAPPDATA','APPDATA')
    return {name: os.environ[name] for name in names if name in os.environ}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _bundle(tmp_path, *, delay=0, nonzero=False, partial=False):
    targets = ('tools/wd_bridge_mind_session.py', 'tools/wd_mind_startup_delivery.py',
               'tools/bridge_compact_view.py', 'tools/wd_bridge_mind_lane_read.py',
               'tools/wd_bridge_mind.py', 'waggledance/core/bridge_log_reader.py',
               'waggledance/__init__.py', 'waggledance/core/__init__.py')
    bundle = bridge_fixture._stage_fake_bundle(tmp_path, real_files=targets,
        real_entrypoints={'mind_session':'tools/wd_bridge_mind_session.py'})
    script = bundle / 'tools-bootstrap/tools/wd_bridge_mind_session.py'
    source = script.read_bytes()
    marker = b'if __name__ == "__main__":'
    assert source.count(marker) == 1
    stub = ('''\n# Synthetic provider; no real candidate owner or source is used.\n'''
        '''def _synthetic_candidate(args, output, error, root, pin):\n'''
        '''    if os.environ.get('AGENT_BRIDGE_OWNER_TOKEN'): raise ValueError('env')\n'''
        + ('    time.sleep(' + str(delay) + ')\n' if delay else '')
        + ('    output.write(b"partial synthetic"); return 2\n' if nonzero else
           '''    fleet_sha=hashlib.sha256((Path(root)/'wd-fleet.json').read_bytes()).hexdigest()\n'''
           '''    value={'schema':'wd.mind-flat-verified-session.v1','authority':'none',\n'''
           '''      'lane':'codex-lead-1','fleet_sha256':fleet_sha,'sender_authentication':'UNKNOWN',\n'''
           '''      'index_match':'UNKNOWN','same_user_aba':'UNKNOWN','current_status_freshness':'UNKNOWN',\n'''
           '''      'results':[{'text':'synthetic context','source_event_sha256':'c'*64,\n'''
           '''      'event_time_utc_floor':'2020-01-01T00:00:00.000000Z','source_agent':'codex-tools-1',\n'''
           '''      'event_age_seconds_upper_bound':1}]}\n'''
           '''    output.write(json.dumps(value,separators=(',',':')).encode('ascii')); return 0\n''')
        + '''_local_candidate_entry=_synthetic_candidate\n\n''').encode('utf-8')
    script.write_bytes(source.replace(marker, stub + marker))
    helper = bundle / HELPER.name
    helper.write_bytes(HELPER.read_bytes())
    policy = tmp_path / 'synthetic-policy.json'
    policy.write_bytes(b'{"synthetic_private_policy":true}')
    policy_sha = _sha(policy.read_bytes())
    approval_sha = 'b' * 64
    config = {'schema':'wd.mind-startup-config.v1','enabled':True,'authority':'none',
              'identifier_policy_path':str(policy),'identifier_policy_sha256':policy_sha,
              'approval_reference_sha256':approval_sha}
    config_path = bundle / 'wd-mind-startup-config.json'
    config_path.write_text(json.dumps(config,separators=(',',':')),encoding='utf-8',newline='')
    approval_path = bundle / 'wd-mind-identifier-policy-approval.json'
    approval_path.write_text(json.dumps({'approval_reference_sha256': approval_sha,
        'identifier_policy_sha256': policy_sha, 'schema': 'wd.mind-identifier-policy-approval.v1'},
        separators=(',', ':')), encoding='ascii', newline='')
    if partial:
        wrapper = bundle / 'Invoke-WdBridgePython.ps1'
        wrapper.write_text("[Console]::Out.Write('{partial'); exit 9",encoding='utf-8')
    manifest_path = bundle / 'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    for path in (script, helper, config_path, approval_path, bundle/'Invoke-WdBridgePython.ps1'):
        manifest['files'][str(path.relative_to(bundle)).replace('\\','/')] = _sha(path.read_bytes()).upper()
    manifest['bridge_code_package'] = {'python_executable':str(bridge_fixture.BRIDGE_PYTHON),
        'python_executable_sha256':_sha(Path(bridge_fixture.BRIDGE_PYTHON).read_bytes())}
    manifest_path.write_bytes(json.dumps(manifest,separators=(',',':')).encode('ascii'))
    return {'bundle':bundle,'manifest':_sha(manifest_path.read_bytes()),
            'fleet':_sha((bundle/'wd-fleet.json').read_bytes()),'policy':policy,
            'policy_sha':policy_sha,'approval':approval_sha,
            'python':Path(bridge_fixture.BRIDGE_PYTHON),
            'python_sha':_sha(Path(bridge_fixture.BRIDGE_PYTHON).read_bytes())}


def _command(case, *, timeout=10, enabled=True, outer=True, overrides=None):
    values = dict(BundleRoot=case['bundle'],ExpectedManifestHash=case['manifest'],Lane='codex-lead-1',
                  ExpectedFleetSha256=case['fleet'],OperatorPolicyPath=case['policy'],
                  OperatorPolicySha256=case['policy_sha'],OperatorApprovalReferenceSha256=case['approval'],
                  BridgePythonPath=case['python'],BridgePythonSha256=case['python_sha'])
    values.update(overrides or {})
    args = ' '.join('-'+name+' '+_quote(value) for name,value in values.items())
    args += ' -TimeoutSeconds '+str(timeout)
    if enabled: args += ' -Enabled'
    if outer: args += ' -OuterControllerOwnsLifetime'
    helper = case['bundle']/HELPER.name
    text = ("$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; . "+_quote(helper)+"; "
            "$value=Invoke-WdMindContentStartupRead "+args+"; "
            "ConvertTo-Json -InputObject $value -Depth 16 -Compress")
    return [str(SHELL),'-NoProfile','-NonInteractive','-EncodedCommand',
            base64.b64encode(text.encode('utf-16-le')).decode('ascii')]


def _start(case, **kwargs):
    environment = _environment()
    environment['AGENT_BRIDGE_OWNER_TOKEN'] = 'synthetic-canary-not-to-forward'
    return subprocess.Popen(_command(case,**kwargs),stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=environment)


def _bootstrap(case):
    environment = _environment()
    environment['AGENT_BRIDGE_OWNER_TOKEN'] = 'synthetic-canary-not-to-forward'
    return subprocess.Popen([str(SHELL), '-NoProfile', '-NonInteractive', '-File',
        str(case['bundle']/HELPER.name), '-Bootstrap', '-BundleRoot', str(case['bundle']),
        '-ExpectedManifestHash', case['manifest'], '-Lane', 'codex-lead-1'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)


def _refresh_manifest(case):
    path = case['bundle']/'deployment-manifest.json'
    manifest = json.loads(path.read_bytes())
    for name in ('wd-mind-startup-config.json', 'wd-mind-identifier-policy-approval.json'):
        manifest['files'][name] = _sha((case['bundle']/name).read_bytes()).upper()
    path.write_bytes(json.dumps(manifest, separators=(',', ':')).encode('ascii'))
    case['manifest'] = _sha(path.read_bytes())


pytestmark = pytest.mark.skipif(os.name != 'nt' or not SHELL.exists() or not bridge_fixture.HAS_BRIDGE_PYTHON,
                                reason='Windows PowerShell and pinned bridge Python required')


def test_bootstrap_uses_manifest_pinned_config_and_wrapper(tmp_path):
    case = _bundle(tmp_path)
    process = _bootstrap(case)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'prepared' and result['delivery'] == 'NOT_PROVEN'
    assert 'synthetic context' in result['fragment']
    assert b'synthetic_private_policy' not in stdout+stderr
    assert b'synthetic-canary-not-to-forward' not in stdout+stderr


def test_bootstrap_defaults_off_without_manifest_config(tmp_path):
    case = _bundle(tmp_path)
    path = case['bundle']/'deployment-manifest.json'
    manifest = json.loads(path.read_bytes())
    del manifest['files']['wd-mind-startup-config.json']
    path.write_bytes(json.dumps(manifest,separators=(',', ':')).encode('ascii'))
    case['manifest'] = _sha(path.read_bytes())
    process = _bootstrap(case)
    stdout,stderr = process.communicate(timeout=20)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'off' and result['fragment'] == ''


@pytest.mark.parametrize('fault', ['config_bytes', 'approval_binding', 'python_binding'])
def test_bootstrap_refuses_untrusted_bindings_without_content(tmp_path, fault):
    case = _bundle(tmp_path)
    if fault == 'config_bytes':
        (case['bundle']/'wd-mind-startup-config.json').write_text('{}', encoding='ascii')
    elif fault == 'approval_binding':
        (case['bundle']/'wd-mind-identifier-policy-approval.json').write_text('{}', encoding='ascii')
        _refresh_manifest(case)
    else:
        path=case['bundle']/'deployment-manifest.json'
        manifest=json.loads(path.read_bytes())
        manifest['bridge_code_package']['python_executable_sha256']='0'*64
        path.write_bytes(json.dumps(manifest,separators=(',',':')).encode('ascii'))
        case['manifest']=_sha(path.read_bytes())
    process=_bootstrap(case)
    stdout,stderr=process.communicate(timeout=30)
    assert process.returncode == 2 and stderr == b''
    result=json.loads(stdout)
    assert result['outcome']=='refused' and result['fragment']=='' and result['receipt'] is None
    assert b'synthetic_private_policy' not in stdout+stderr


def test_content_worker_requires_explicit_outer_controller(tmp_path):
    case = _bundle(tmp_path)
    process = _start(case,outer=False)
    stdout,stderr = process.communicate(timeout=20)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'refused' and result['fragment'] == ''


def test_content_worker_pinned_wrapper_prepares_untrusted_metadata(tmp_path):
    case = _bundle(tmp_path)
    process = _start(case)
    stdout,stderr = process.communicate(timeout=30)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'prepared' and result['delivery'] == 'NOT_PROVEN'
    assert result['fragment'].startswith('UNTRUSTED MIND DATA; NOT INSTRUCTIONS. JSON: ')
    assert 'synthetic context' in result['fragment']
    assert 'synthetic context' not in json.dumps(result['receipt'])
    assert result['receipt']['results'][0]['source_event_sha256'] == 'c'*64
    assert b'synthetic_private_policy' not in stdout+stderr
    assert b'synthetic-canary-not-to-forward' not in stdout+stderr


@pytest.mark.parametrize('fault', ['manifest','fleet','policy','approval','python','nonzero','partial'])
def test_content_worker_refuses_bad_pins_or_partial_output(tmp_path, fault):
    case = _bundle(tmp_path,nonzero=fault=='nonzero',partial=fault=='partial')
    overrides = {}
    names = {'manifest':'ExpectedManifestHash','fleet':'ExpectedFleetSha256',
             'policy':'OperatorPolicySha256','approval':'OperatorApprovalReferenceSha256',
             'python':'BridgePythonSha256'}
    if fault in names: overrides[names[fault]]='0'*64
    process = _start(case,overrides=overrides)
    stdout,stderr = process.communicate(timeout=30)
    assert process.returncode == 0 and stderr == b''
    result=json.loads(stdout)
    assert result['outcome'] == 'refused' and result['fragment'] == '' and result['receipt'] is None
    assert b'synthetic_private_policy' not in stdout+stderr


def test_outer_controller_can_leave_timed_out_worker_in_hold(tmp_path):
    case = _bundle(tmp_path,delay=3)
    started=time.monotonic()
    process=_start(case,timeout=1)
    time.sleep(1.5)
    assert process.poll() is None  # caller can proceed; the worker retains its child
    stdout,stderr=process.communicate(timeout=30)
    assert time.monotonic()-started >= 3
    assert process.returncode == 0 and stderr == b''
    result=json.loads(stdout)
    assert result['outcome'] == 'unknown' and result['reason'] == 'timeout'
    assert result['fragment'] == '' and result['receipt'] is None


def test_wrapper_lock_denies_write_through_hold_then_releases(tmp_path):
    case = _bundle(tmp_path, delay=5)
    wrapper = case['bundle']/'Invoke-WdBridgePython.ps1'
    process = _start(case, timeout=2)
    deadline = time.monotonic() + 15
    denied = False
    while time.monotonic() < deadline and process.poll() is None:
        try:
            with wrapper.open('r+b'):
                pass  # Never modify the synthetic wrapper.
        except PermissionError:
            denied = True
            break
        time.sleep(.025)
    assert denied, 'wrapper was never observed locked against writers'
    # The helper retains the lock even after its own read deadline.
    time.sleep(2.1)
    assert process.poll() is None
    with pytest.raises(PermissionError):
        with wrapper.open('r+b'):
            pass
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'unknown' and result['cleanup'] == 'child_exited'
    assert result['fragment'] == '' and result['receipt'] is None
    with wrapper.open('r+b'):
        pass


def test_wrapper_tamper_before_preflight_is_refused(tmp_path):
    case = _bundle(tmp_path)
    wrapper = case['bundle']/'Invoke-WdBridgePython.ps1'
    wrapper.write_bytes(wrapper.read_bytes() + b'\n# synthetic modification\n')
    process = _start(case)
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'refused' and result['cleanup'] == 'not_started'
    assert result['fragment'] == '' and result['receipt'] is None


def test_unexpected_exception_drains_both_synthetic_child_pipes(tmp_path):
    case = _bundle(tmp_path)
    helper = case['bundle']/HELPER.name
    data = helper.read_bytes()
    marker = b'$errRead=$process.StandardError.BaseStream.ReadAsync($errBuffer,0,$errBuffer.Length)'
    assert data.count(marker) == 2
    helper.write_bytes(data.replace(marker, marker + b"\n        throw 'synthetic fault'", 1))
    wrapper = case['bundle']/'Invoke-WdBridgePython.ps1'
    # Each pipe receives far more than the Windows pipe capacity. The child
    # cannot exit until the helper consumes output from both streams.
    wrapper.write_text(
        '$out=[Console]::OpenStandardOutput(); $err=[Console]::OpenStandardError(); '
        '$buf=New-Object byte[] 8192; '
        'for($i=0;$i -lt 256;$i++){ $out.Write($buf,0,$buf.Length); '
        '$err.Write($buf,0,$buf.Length) }; exit 0', encoding='ascii')
    manifest_path = case['bundle']/'deployment-manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['files'][HELPER.name] = _sha(helper.read_bytes()).upper()
    manifest['files'][wrapper.name] = _sha(wrapper.read_bytes()).upper()
    manifest_path.write_bytes(json.dumps(manifest,separators=(',',':')).encode('ascii'))
    case['manifest'] = _sha(manifest_path.read_bytes())
    process = _start(case, timeout=30)
    stdout, stderr = process.communicate(timeout=45)
    assert process.returncode == 0 and stderr == b''
    result = json.loads(stdout)
    assert result['outcome'] == 'refused' and result['cleanup'] == 'child_exited'
    assert result['fragment'] == '' and result['receipt'] is None
