"""Synthetic local Git and bridge fixture only; no live inputs."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tests.tools.test_wd_q4_real_card_generation import real_fixture


def sha(raw): return hashlib.sha256(raw).hexdigest()


def setup_request(real_fixture, tmp_path):
    args, out, _, _ = real_fixture
    inputs = {}
    for name in ('decision','vocabulary','path_allowlist','policy','roster','card_policy'):
        data = args[name + '_bytes']
        path = tmp_path / (name + '.json'); path.write_bytes(data)
        inputs[name] = {'path':str(path), 'sha256':sha(data)}
    generation_path = Path(args['generation_path'])
    cursor = args['end_cursor']
    exe = Path(args['real_git_executable'])
    req = {'schema':'wd.q4-real-card-snapshot-request.v1','source_mode':'synthetic_fixture',
           'inputs':inputs,'source_path':args['source_path'],
           'source_generation':{'path':str(generation_path),'sha256':sha(generation_path.read_bytes())},
           'cursor':{'offset':cursor.offset,'file_identity':cursor.file_identity,'generation':cursor.generation},
           'now_utc':args['now_utc'],'output_root':str(out),'generation_name':'candidate',
           'git':{'executable':str(exe),'sha256':sha(exe.read_bytes()),
                  'repository':args['real_git_repository'],'anchors':args['real_approved_anchors']},
           'encoder':{},'trusted_model_root':str(tmp_path)}
    raw=json.dumps(req,sort_keys=True,separators=(',',':')).encode()
    path=tmp_path/'request.json';path.write_bytes(raw)
    return req, path, sha(raw), args


def test_off_and_cli_no_args_do_no_io():
    mod=importlib.import_module('tools.wd_q4_real_card_snapshot_cli')
    class Untouchable:
        def __getattribute__(self,_):raise AssertionError('OFF input touched')
    assert mod.run(enabled=False,request_path=Untouchable())=={'status':'OFF'}
    proc=subprocess.run([sys.executable,'-B','-m','tools.wd_q4_real_card_snapshot_cli'],
                        cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=15)
    assert proc.returncode==0 and json.loads(proc.stdout)=={'status':'OFF'}


@pytest.mark.parametrize('mode',['synthetic_fixture','operator_authorized_source'])
def test_synthetic_fixture_calls_actual_c2_producer(real_fixture,tmp_path,monkeypatch,mode):
    pytest.importorskip('faiss')
    mod=importlib.import_module('tools.wd_q4_real_card_snapshot_cli')
    from tools import wd_q4_real_card_snapshot_producer as producer
    req,path,pin,args=setup_request(real_fixture,tmp_path)
    req['source_mode']=mode
    raw=json.dumps(req,sort_keys=True,separators=(',',':')).encode();path.write_bytes(raw);pin=sha(raw)
    monkeypatch.setattr(producer,'_load_pinned_e5',lambda **_: (args['encode'],args['identity'],args['dimension'],lambda:True))
    result=mod.run(enabled=True,request_path=str(path),request_sha256=pin)
    assert result['status']=='VALIDATED_STAGED' and result['source_mode']==mode
    assert result['publisher_authentication']==result['source_authentication']=='UNKNOWN'
    assert sha((Path(args['output_root'])/'card-snapshot-candidate.json').read_bytes())==result['pointer_sha256']
    assert str(tmp_path) not in json.dumps(result)


def test_bad_cli_request_reports_fixed_token_without_path_or_canary(tmp_path):
    path=tmp_path/'sensitive-synthetic-canary.json';path.write_bytes(b'{}')
    proc=subprocess.run([sys.executable,'-B','-m','tools.wd_q4_real_card_snapshot_cli',
        '--enabled','--request',str(path),'--request-sha256',sha(path.read_bytes())],
        cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=15)
    assert proc.returncode==2 and not proc.stdout and json.loads(proc.stderr)=={'status':'REFUSED'}


@pytest.mark.parametrize('fault',['request_pin','input_pin','generation_pin','git_pin','source_mode','extra_key','source_escape'])
def test_bad_request_refused_without_producer(real_fixture,tmp_path,monkeypatch,fault):
    mod=importlib.import_module('tools.wd_q4_real_card_snapshot_cli')
    from tools import wd_q4_real_card_snapshot_producer as producer
    req,path,pin,args=setup_request(real_fixture,tmp_path)
    calls=[];monkeypatch.setattr(producer,'produce',lambda **_: calls.append(True))
    if fault=='input_pin':req['inputs']['policy']['sha256']='0'*64
    if fault=='generation_pin':req['source_generation']['sha256']='0'*64
    if fault=='git_pin':req['git']['sha256']='0'*64
    if fault=='source_mode':req['source_mode']='automatic'
    if fault=='extra_key':req['secret']='synthetic-canary'
    if fault=='source_escape':req['source_path']=str(Path(args['real_git_executable']))
    raw=json.dumps(req,sort_keys=True,separators=(',',':')).encode();path.write_bytes(raw)
    with pytest.raises(mod.Refused):mod.run(enabled=True,request_path=str(path),request_sha256=('0'*64 if fault=='request_pin' else sha(raw)))
    assert calls==[] and not (Path(args['output_root'])/'candidate').exists()


def test_post_call_input_change_is_unknown(real_fixture,tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_real_card_snapshot_cli')
    from tools import wd_q4_real_card_snapshot_producer as producer
    req,path,pin,_=setup_request(real_fixture,tmp_path)
    def changed(**_kwargs):
        Path(req['inputs']['policy']['path']).write_bytes(b'changed')
        return {'status':'VALIDATED_STAGED'}
    monkeypatch.setattr(producer,'produce',changed)
    with pytest.raises(mod.Unknown):mod.run(enabled=True,request_path=str(path),request_sha256=pin)
