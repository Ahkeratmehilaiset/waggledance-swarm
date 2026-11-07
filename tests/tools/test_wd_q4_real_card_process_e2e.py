"""Opt-in synthetic actual resolved-child OS process, with an in-process Q4 launcher.

Parent validates C2 before starting the child; this test is not a production
C2-to-child integration. The child uses real resolved-child/Q4/FAISS code and
fabricated data, but a synthetic encoder and a replaced INNER runtime-launcher.
It does not exercise E5, Windows Job containment, live data or installed code.
"""
import base64
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

if os.name != 'nt' or os.environ.get('WD_Q4_CARD_CHILD_E2E') != '1':
    pytest.skip('explicit opt-in and Windows are required for this subprocess test', allow_module_level=True)

from sandbox.mind.step2 import shared_card_checkpoint

_spec = importlib.util.spec_from_file_location('synthetic_snapshot_fixture',
    Path(__file__).with_name('test_wd_q4_real_card_snapshot_e2e.py'))
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)
real_fixture = fixture_module.real_fixture
real_snapshot = fixture_module.real_snapshot
rt = fixture_module.rt
ROOT = Path(__file__).resolve().parents[2]

_CHILD = r'''
import base64,datetime,hashlib,io,json,os,re,sys
sys.path.insert(0,sys.argv[2]);sys.path.insert(0,sys.argv[1])
import numpy as np
from sandbox.mind.step2 import runtime_launcher,wd_q4_private_read as reader
from tools import wd_q4_agent_query as q4,wd_q4_resolved_child as child
data=json.load(sys.stdin)
dec=lambda key:base64.b64decode(data[key],validate=True)
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
calls=[]
def local_launcher(*,query,**kwargs):
 calls.append('in_process_private_reader')
 q=json.loads(query)
 value=reader._private_read_locked(data['directory'],data['descriptor'],data['descriptor_sha'],
       q['situation'],q['time'],r'C:\fake\synthetic-model',data['revision'])
 return runtime_launcher._validate_output(json.dumps(value).encode(),0,data['descriptor_sha'])
q4._invoke_launcher=local_launcher
config=q4.VerifiedQ4Config(**data['config']);out=io.StringIO()
code=child.run_resolved(config,io.BytesIO(dec('query')),out,
 policy_bytes=dec('policy'),policy_sha256=data['policy_sha'],
 roster_bytes=dec('roster'),roster_sha256=data['roster_sha'],
 memory_bytes=dec('memory'),memory_sha256=data['memory_sha'],
 projection_bytes=dec('projection'),projection_sha256=data['projection_sha'],
 card_decision_bytes=dec('decision'),card_decision_sha256=data['decision_sha'])
print(json.dumps({'pid':os.getpid(),'code':code,'calls':calls,'result':json.loads(out.getvalue())},sort_keys=True))
'''


@pytest.mark.skipif(os.name != 'nt' or os.environ.get('WD_Q4_CARD_CHILD_E2E') != '1',
                    reason='explicit opt-in and Windows are required for this subprocess test')
@pytest.mark.parametrize('bad_decision', [False, True], ids=['answer', 'wrong-decision-pin'])
def test_c2_then_actual_resolved_process(real_snapshot, bad_decision):
    python = Path(os.environ['WD_Q4_CARD_TEST_PYTHON'])
    site = Path(os.environ['WD_Q4_CARD_TEST_SITE'])
    expected_python_sha = os.environ['WD_Q4_CARD_TEST_PYTHON_SHA256']
    assert python.is_absolute() and python.is_file() and site.is_absolute() and site.is_dir()
    assert hashlib.sha256(python.read_bytes()).hexdigest() == expected_python_sha
    directory, built, _, snapshot, projection, args = real_snapshot
    assert shared_card_checkpoint.validate_card_snapshot(enabled=True, generation_dir=directory, **snapshot) == {
        'status': 'VALIDATED_STAGED', 'completeness': 'UNKNOWN'}
    memory = (directory / 'memory.json').read_bytes()
    projection_bytes = (directory / 'projection.json').read_bytes()
    encode = lambda value: base64.b64encode(value).decode('ascii')
    request = json.dumps({'situation': 'merged green pr 2046 genesis', 'time': '2026-11-02T00:00:00Z'}).encode()
    wire = {'directory': str(directory), 'descriptor': str(directory / 'descriptor.json'),
            'descriptor_sha': built['descriptor_sha256'], 'revision': rt.fx.IDENTITY['revision'],
            'config': dataclasses.asdict(rt.config(directory, built['descriptor_sha256'])),
            'query': encode(request), 'policy': encode(args['policy_bytes']), 'policy_sha': args['policy_sha256'],
            'roster': encode(args['roster_bytes']), 'roster_sha': args['roster_sha256'],
            'memory': encode(memory), 'memory_sha': built['memory_sha256'],
            'projection': encode(projection_bytes), 'projection_sha': hashlib.sha256(projection_bytes).hexdigest(),
            'decision': encode(args['decision_bytes']),
            'decision_sha': '0' * 64 if bad_decision else args['decision_sha256']}
    env = {name: os.environ[name] for name in ('SYSTEMROOT', 'WINDIR') if name in os.environ}
    proc = subprocess.Popen([str(python), '-I', '-S', '-B', '-c', _CHILD, str(ROOT), str(site)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT), env=env,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        stdout, stderr = proc.communicate(json.dumps(wire).encode(), timeout=90)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail('synthetic resolved child timed out')
    assert proc.returncode == 0, stderr.decode('utf-8', 'replace')
    result = json.loads(stdout)
    assert result['pid'] == proc.pid and proc.pid != os.getpid()
    if bad_decision:
        assert result['code'] == 2 and result['result']['status'] == 'REFUSED' and result['calls'] == []
    else:
        assert result['code'] == 0 and result['result']['status'] == 'ANSWER'
        assert result['calls'] == ['in_process_private_reader']
        assert any(item['stored_text'] == projection['records'][0]['stored_text'] for item in result['result']['results'])
