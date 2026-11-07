"""Opt-in synthetic Windows Job -> real staged child/C2/Q4/FAISS.

The outer runtime verifier, inner launcher and encoder are synthetic shims.
This is not a test of E5, deployment-manifest verification, installed code,
live bridge data, or authenticated publishers. All files are local fixtures.
"""
import base64
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

if os.name != 'nt' or os.environ.get('WD_Q4_STAGED_JOB_E2E') != '1':
    pytest.skip('Windows and explicit WD_Q4_STAGED_JOB_E2E=1 required', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('staged_fixture',
    Path(__file__).with_name('test_wd_q4_real_card_snapshot_e2e.py'))
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
real_fixture = _fixture.real_fixture
real_snapshot = _fixture.real_snapshot
rt = _fixture.rt


def sha(value):
    return hashlib.sha256(value).hexdigest()


def write(path, value):
    path.write_bytes(value)
    return str(path), sha(value)


def test_staged_job_runs_real_c2_and_q4_and_refuses_changed_projection(real_snapshot, tmp_path):
    python = Path(os.environ['WD_Q4_TEST_PYTHON'])
    site = Path(os.environ['WD_Q4_TEST_SITE'])
    pin = os.environ['WD_Q4_TEST_PYTHON_SHA256']
    assert python.is_absolute() and python.is_file() and site.is_absolute() and site.is_dir()
    assert sha(python.read_bytes()) == pin
    host = shutil.which('powershell.exe')
    assert host
    directory, built, decision, snapshot, projection, args = real_snapshot
    code = tmp_path / 'synthetic-code'
    tools = code / 'tools'
    tools.mkdir(parents=True)
    (tools / '__init__.py').write_text('__path__.append(' + repr(str(ROOT / 'tools')) + ')\n', encoding='utf-8')
    shim = '''\
import datetime,hashlib,json,re,sys
sys.path.insert(0, SITE)
sys.path.insert(1, ROOT)
import numpy as np
from sandbox.mind.step2 import runtime_launcher,wd_q4_private_read as reader
from tools import wd_q4_agent_query as q4,wd_q4_public_child as child
class Encoder:
 def query(self,text):
  vector=[0.0]*64
  for token in re.findall(r'[a-z0-9]{2,}',text.lower()):
   vector[int(hashlib.sha256(token.encode()).hexdigest(),16)%64]+=1.0
  norm=sum(x*x for x in vector)**0.5 or 1.0
  return np.array([x/norm for x in vector],dtype=np.float32)
reader.ENCODER_FACTORY=lambda **kwargs:Encoder()
reader.bind_runtime_encoder=lambda *args,**kwargs:None
q4._utc_now=lambda:datetime.datetime(2026,11,3,tzinfo=datetime.timezone.utc)
def local_launcher(*,query,**kwargs):
 q=json.loads(query)
 value=reader._private_read_locked(DIRECTORY,DESCRIPTOR,DESCRIPTOR_SHA,q['situation'],q['time'],r'C:\\fake\\synthetic-model',REVISION)
 return runtime_launcher._validate_output(json.dumps(value).encode(),0,DESCRIPTOR_SHA)
q4._invoke_launcher=local_launcher
def bootstrap_public_imports(**kwargs):
 return {'status':'MODULES_IMPORTED','child':child}
'''
    constants = {'SITE': str(site), 'ROOT': str(ROOT), 'DIRECTORY': str(directory),
                 'DESCRIPTOR': str(directory / 'descriptor.json'), 'DESCRIPTOR_SHA': built['descriptor_sha256'],
                 'REVISION': rt.fx.IDENTITY['revision']}
    shim = '\n'.join(k + '=' + repr(v) for k, v in constants.items()) + '\n' + shim
    outer = tools / 'wd_q4_public_outer.py'
    outer.write_text(shim, encoding='utf-8')
    scratch = tmp_path / 'scratch'
    scratch.mkdir()
    cfg = dataclasses.asdict(rt.config(directory, built['descriptor_sha256']))
    cfg.update(interpreter=str(python), interpreter_sha256=pin, scratch_root=str(scratch), timeout=90)
    files = {}
    for name, raw in [('policy', args['policy_bytes']), ('roster', args['roster_bytes']),
                      ('public_policy', b'{}'), ('card_decision', args['decision_bytes']),
                      ('card_policy', args['card_policy_bytes']), ('pointer', snapshot['pointer_bytes'])]:
        files[name] = write(tmp_path / (name + '.json'), raw)
    from tools.wd_q4_public_query import _encoder_digest
    descriptor = json.loads((directory / 'descriptor.json').read_bytes())
    plan = {'schema':'wd.q4-public-wrapper-plan.v3','root':str(directory.parent), 'config':cfg,
            'source_anchor_sha256':decision['source_prefix']['sha256'],
            'encoder_sha256':_encoder_digest(descriptor['encoder']),
            'source_path':str(args['source_path']),'generation_path':str(args['generation_path']),
            'end_cursor':dataclasses.asdict(args['end_cursor'])}
    for name, (path, digest) in files.items():
        plan[name + '_path'], plan[name + '_sha256'] = path, digest
    plan_path, plan_sha = write(tmp_path / 'plan.json', json.dumps(plan, separators=(',', ':')).encode())
    cfg_path, _ = write(tmp_path / 'cfg.json', json.dumps(cfg).encode())
    query_path, _ = write(tmp_path / 'query.json', json.dumps({'situation':'merged green pr 2046 genesis',
                                             'time':'2026-11-02T00:00:00Z'}).encode())
    helper = ROOT / 'ops/windows/reboot/WdQ4ProcessJob.ps1'
    wrapper = ROOT / 'ops/windows/reboot/Invoke-WdQ4Query.ps1'
    # The three declared pins are synthetic fixture inputs, not a runtime manifest.
    pins = {str(p.relative_to(ROOT)).replace('\\','/'): sha(p.read_bytes()) for p in
            (ROOT/'tools/wd_q4_public_child.py', ROOT/'tools/wd_q4_public_query.py')}
    pins['tools/wd_q4_public_outer.py'] = sha(outer.read_bytes())
    inputs = {'plan':plan_path,'plan_sha':plan_sha,'code':str(code),'cfg':cfg_path,'query':query_path,
              'helper':str(helper),'wrapper':str(wrapper),'pins':pins,
              'projection':str(directory / 'projection.json')}
    input_path, _ = write(tmp_path / 'inputs.json', json.dumps(inputs).encode())
    literal = str(input_path).replace("'", "''")
    script = r'''$ErrorActionPreference='Stop'
$i=Get-Content -Raw -LiteralPath '__INPUT__' | ConvertFrom-Json
. $i.wrapper
$helper=[IO.File]::ReadAllBytes($i.helper)
$hash=[Security.Cryptography.SHA256]::Create()
try {$helperSha=[BitConverter]::ToString($hash.ComputeHash($helper)).Replace('-','')} finally {$hash.Dispose()}
$config=Get-Content -Raw -LiteralPath $i.cfg | ConvertFrom-Json
$pins=@{}; foreach($p in $i.pins.PSObject.Properties) {$pins[$p.Name]=$p.Value}
$base=@{Enabled=$true;CodeRoot=$i.code;Config=$config;JobHelperBytes=$helper;JobHelperSha256=$helperSha;PublicPins=$pins}
$plan=Get-WdQ4PublicPlan -BasePlan $base -Path $i.plan -ExpectedSha256 $i.plan_sha
$query=[IO.File]::ReadAllBytes($i.query)
$good=Invoke-WdQ4PublicTransport -Plan $plan -Query $query
[IO.File]::AppendAllText($i.projection,' ')
$bad=try {$null=Invoke-WdQ4PublicTransport -Plan $plan -Query $query;'accepted'} catch {'refused'}
@{good=$good;changed_projection=$bad} | ConvertTo-Json -Depth 12 -Compress
'''.replace('__INPUT__', literal)
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([host, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=210)
    assert result.returncode == 0, (result.stdout, result.stderr)
    value = json.loads(result.stdout)
    assert value['good']['status'] == 'ANSWER'
    assert value['changed_projection'] == 'refused'
    assert all(value['good'][key] == 'UNKNOWN' for key in
               ('publisher_authentication','source_filter_attestation','freshness','same_user_aba','completeness'))
    assert any(row['stored_text'] == projection['records'][0]['stored_text'] for row in value['good']['results'])
