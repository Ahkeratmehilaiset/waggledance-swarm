"""Synthetic pinned bootstrap checks; no installed bundle or real model."""
import hashlib
import ctypes
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAME = 'wd_q4_card_orchestrator_runtime_bootstrap.py'
CLI = 'wd_q4_card_audit_orchestrator.py'
HELPER = 'wd_q4_live_update_bootstrap.py'
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows C: drive only')


def sha(value): return hashlib.sha256(value).hexdigest()
def raw(value): return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def test_off_without_reads(monkeypatch, capsys):
    module = importlib.import_module('tools.wd_q4_card_orchestrator_runtime_bootstrap')
    monkeypatch.setattr(module, '_read', lambda *_: pytest.fail('OFF read'))
    assert module.main([]) == 0
    assert json.loads(capsys.readouterr().out) == {'status':'OFF'}


def test_bridge_definition_has_one_fixed_orchestrator_entrypoint():
    definition=json.loads((ROOT/'ops/windows/reboot/bridge-code-files.json').read_bytes())
    relative='tools/wd_q4_card_orchestrator_runtime_bootstrap.py'
    assert definition['python_files'].count(relative)==1
    assert list(definition['python_entrypoints'].values()).count(relative)==1
    assert definition['python_entrypoints']['q4_card_orchestrator_runtime']==relative


@pytest.fixture
def case(tmp_path):
    from sandbox.mind.step2 import runtime_builder
    bundle = tmp_path / 'bundle'; code = bundle / 'tools-bootstrap'
    (code / 'tools').mkdir(parents=True); (code / 'sandbox/mind/step2').mkdir(parents=True)
    for name in (NAME, HELPER): shutil.copyfile(ROOT / 'tools' / name, code / 'tools' / name)
    (code / 'tools/__init__.py').write_text('', encoding='ascii')
    verifier = code / 'sandbox/mind/step2/runtime_manifest.py'
    shutil.copyfile(ROOT / 'sandbox/mind/step2/runtime_manifest.py', verifier)
    runtime = tmp_path / 'runtime'
    for item in ('assets/code','site','model'): (runtime / item).mkdir(parents=True)
    (runtime / 'assets/code/stub').write_bytes(b'code')
    (runtime / 'site/synthetic_dependency.py').write_text('VALUE=42\n', encoding='ascii')
    (runtime / 'model/stub').write_bytes(b'model')
    runtime_manifest = tmp_path / 'runtime.json'
    built = runtime_builder.build_runtime_manifest(runtime, runtime_manifest, generation='a'*40, revision='b'*40)
    gen = tmp_path / 'generation'; (gen / 'stage').mkdir(parents=True)
    pointer = gen / 'stage/candidate-pointer.json'; pointer.write_bytes(b'synthetic-pointer')
    template = tmp_path / 'snapshot-template.json'
    template.write_bytes(raw({'schema':'wd.q4-real-card-snapshot-request.v1',
                              'trusted_model_root':str(runtime / 'model')}))
    request = tmp_path / 'request.json'
    request.write_bytes(raw({'schema':'wd.q4-card-real-candidate-request.v1',
        'snapshot_template':{'path':str(template),'sha256':sha(template.read_bytes())},'generation_root':str(gen),
        'decision':{}, 'finish':{}, 'output_root':str(tmp_path/'audit')}))
    cli = code / 'tools' / CLI
    cli.write_text('import json\ndef main(args):\n import synthetic_dependency\n assert synthetic_dependency.VALUE == 42\n'
        ' print(json.dumps({"status":"PREPARED","pointer_path":'+repr(str(pointer))+',"pointer_sha256":'+repr(sha(pointer.read_bytes()))+'},sort_keys=True,separators=(",",":")))\n return 0\n', encoding='ascii')
    files = {'tools-bootstrap/' + p.relative_to(code).as_posix():sha(p.read_bytes()).upper()
             for p in code.rglob('*') if p.is_file()}
    manifest = bundle / 'deployment-manifest.json'
    manifest.write_bytes(raw({'schema_version':1,'source_commit':'c'*40,'files':files}))
    spec = tmp_path / 'runtime-spec.json'
    spec.write_bytes(raw({'schema':'wd.q4-card-orchestrator-runtime.v1','bundle_root':str(bundle),
        'manifest_sha256':sha(manifest.read_bytes()),'interpreter_path':sys.executable,
        'interpreter_sha256':sha(Path(sys.executable).read_bytes()),'runtime_root':str(runtime),
        'runtime_manifest_path':str(runtime_manifest),'runtime_manifest_sha256':built['manifest_sha256'],
        'runtime_generation':'a'*40,'runtime_revision':'b'*40,'verifier_sha256':sha(verifier.read_bytes())}))
    return {'bundle':bundle,'code':code,'runtime':runtime,'manifest':manifest,'spec':spec,'request':request,
            'template':template,'bootstrap':code/'tools'/NAME,'pointer':pointer}


def creation_filetime(pid):
    class FileTime(ctypes.Structure):
        _fields_=[('low',ctypes.c_uint32),('high',ctypes.c_uint32)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]; kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.GetProcessTimes.argtypes=[ctypes.c_void_p,*([ctypes.POINTER(FileTime)]*4)]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x1000,False,pid); assert handle
    try:
        created,exited,system,user=FileTime(),FileTime(),FileTime(),FileTime()
        assert kernel.GetProcessTimes(handle,ctypes.byref(created),ctypes.byref(exited),ctypes.byref(system),ctypes.byref(user))
        return (created.high<<32)|created.low
    finally: kernel.CloseHandle(handle)


def issue_token(case, *, wrong_creation=False):
    root=case['request'].parent/'scratch'; root.mkdir(exist_ok=True)
    nonce=secrets.token_hex(32); path=root/('wd-q4-card-'+nonce[:32]+'.json')
    doc={'schema':'wd.q4-card-launch-token.v1','launcher_pid':os.getpid(),
         'launcher_creation_filetime':creation_filetime(os.getpid())+(1 if wrong_creation else 0),
         'scratch_root':str(root),'nonce':nonce,'manifest_sha256':sha(case['manifest'].read_bytes()),
         'runtime_sha256':sha(case['spec'].read_bytes()),'request_sha256':sha(case['request'].read_bytes())}
    path.write_bytes(raw(doc))
    return str(path),sha(path.read_bytes())


def execute(case, *, flags=('-I','-S','-B'), pin=None, extra_env=None, with_token=True, token=None):
    manifest_pin = sha(case['manifest'].read_bytes())
    env = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=manifest_pin)
    env.pop('WD_Q4_CARD_JOB_TOKEN_PATH',None); env.pop('WD_Q4_CARD_JOB_TOKEN_SHA256',None)
    if with_token:
        token=token or issue_token(case)
        env.update(WD_Q4_CARD_JOB_TOKEN_PATH=token[0],WD_Q4_CARD_JOB_TOKEN_SHA256=token[1])
    if extra_env: env.update(extra_env)
    return subprocess.run([sys.executable,*flags,str(case['bootstrap']),'--enable','--request',str(case['request']),
        '--request-sha256',pin or sha(case['request'].read_bytes()),'--manifest-sha256',manifest_pin,
        '--runtime',str(case['spec']),'--runtime-sha256',sha(case['spec'].read_bytes())],
        env=env,capture_output=True,text=True,timeout=60)


def repin_bundle(case):
    code = case['code']
    files = {'tools-bootstrap/' + p.relative_to(code).as_posix():sha(p.read_bytes()).upper()
             for p in code.rglob('*') if p.is_file()}
    case['manifest'].write_bytes(raw({'schema_version':1,'source_commit':'c'*40,'files':files}))
    spec = json.loads(case['spec'].read_bytes())
    spec['manifest_sha256'] = sha(case['manifest'].read_bytes())
    case['spec'].write_bytes(raw(spec))


def test_isolated_pinned_runtime_reaches_only_fixed_cli(case):
    result = execute(case)
    assert result.returncode == 0 and result.stderr == ''
    assert json.loads(result.stdout) == {'status':'PREPARED','pointer_path':str(case['pointer']),
                                         'pointer_sha256':sha(case['pointer'].read_bytes())}


def test_wrapper_style_parent_reaches_isolated_child_without_foreign_pythonpath(case, tmp_path):
    marker = tmp_path/'foreign-imported'
    (tmp_path/'hashlib.py').write_text('open('+repr(str(marker))+',"w").write("bad")\n',encoding='ascii')
    result = execute(case,flags=('-S','-B'),extra_env={'PYTHONPATH':str(tmp_path)})
    assert result.returncode == 0 and json.loads(result.stdout)['status'] == 'PREPARED'
    assert not marker.exists()


@pytest.mark.parametrize('flags',[('-S','-B'),('-I','-S','-B')])
def test_direct_wrapper_without_job_token_refuses(case,flags):
    marker=case['code']/'tools'/'synthetic-entry-marker'
    cli=case['code']/'tools'/CLI
    cli.write_text('def main(args):\n open('+repr(str(marker))+',"w").write("entered")\n return 0\n',encoding='ascii')
    repin_bundle(case)
    result=execute(case,flags=flags,with_token=False)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not marker.exists()


def test_job_token_is_bound_to_live_launcher_and_consumed_once(case):
    wrong=issue_token(case,wrong_creation=True)
    refused=execute(case,flags=('-S','-B'),token=wrong)
    assert refused.returncode==2 and json.loads(refused.stdout)=={'status':'REFUSED'}
    token=issue_token(case)
    first=execute(case,flags=('-S','-B'),token=token)
    second=execute(case,flags=('-S','-B'),token=token)
    assert first.returncode==0 and json.loads(first.stdout)['status']=='PREPARED'
    assert second.returncode==2 and json.loads(second.stdout)=={'status':'REFUSED'}
    assert Path(token[0]+'.used').is_file()


def test_concurrent_reuse_has_only_one_consumer(case):
    token=issue_token(case)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(execute,case,flags=('-S','-B'),token=token) for _ in range(2)]
        results=[f.result() for f in futures]
    assert sorted(item.returncode for item in results)==[0,2]


def test_stdlib_precedes_packaged_top_level_module(case):
    (case['code']/'fractions.py').write_text("raise RuntimeError('shadowed stdlib')\n", encoding='ascii')
    cli = case['code']/'tools'/CLI
    body = cli.read_text(encoding='ascii').replace(
        ' import synthetic_dependency\n',
        ' import fractions\n assert fractions.Fraction(1, 2).numerator == 1\n import synthetic_dependency\n')
    cli.write_text(body, encoding='ascii')
    repin_bundle(case)
    result = execute(case)
    assert result.returncode == 0 and result.stderr == ''
    assert json.loads(result.stdout)['status'] == 'PREPARED'


@pytest.mark.parametrize('fault',['no_isolation','bad_request_pin','changed_template','unlisted_file','bad_site',
                                   'changed_pointer','model_mismatch','wrong_runtime_schema','interpreter_pin'])
def test_refuses_or_unknown_on_unbound_inputs(case, fault):
    options = {}
    if fault == 'no_isolation': options['flags'] = ('-S',)
    if fault == 'bad_request_pin': options['pin'] = '0'*64
    if fault == 'changed_template': case['template'].write_bytes(b'{}')
    if fault == 'unlisted_file': (case['code']/'tools/extra.py').write_text('pass')
    if fault == 'bad_site': (case['runtime']/'site/synthetic_dependency.py').write_text('VALUE=7')
    if fault == 'changed_pointer': case['pointer'].write_bytes(b'tampered')
    if fault == 'model_mismatch':
        snapshot = json.loads(case['template'].read_bytes())
        snapshot['trusted_model_root'] = str(case['runtime']/'site')
        case['template'].write_bytes(raw(snapshot))
        req = json.loads(case['request'].read_bytes())
        req['snapshot_template']['sha256'] = sha(case['template'].read_bytes())
        case['request'].write_bytes(raw(req))
    if fault in ('wrong_runtime_schema','interpreter_pin'):
        spec = json.loads(case['spec'].read_bytes())
        if fault == 'wrong_runtime_schema': spec['schema'] = 'wd.q4-genesis-runtime.v1'
        if fault == 'interpreter_pin': spec['interpreter_sha256'] = '0'*64
        case['spec'].write_bytes(raw(spec))
    result = execute(case,**options)
    assert result.returncode in (2,3) and json.loads(result.stdout)['status'] in ('REFUSED','UNKNOWN')


@pytest.mark.parametrize('body', [
    'print("synthetic-private-canary"); return 0',
    'raise RuntimeError("synthetic-private-canary")',
    'print("synthetic-private-canary"); return 3',
])
def test_unvalidated_or_interrupted_cli_result_is_unknown_without_leak(case, body):
    cli = case['code']/'tools'/CLI
    cli.write_text('def main(args):\n '+body+'\n', encoding='ascii')
    repin_bundle(case)
    result = execute(case)
    assert result.returncode == 3 and json.loads(result.stdout) == {'status':'UNKNOWN'}
    assert 'synthetic-private-canary' not in result.stdout + result.stderr
