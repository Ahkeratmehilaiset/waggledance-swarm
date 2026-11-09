"""Synthetic request files and mocked publisher; no live bridge writes."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tests.tools.test_wd_q4_public_card_prepare import arguments


def sha(raw): return hashlib.sha256(raw).hexdigest()


def setup(tmp_path):
    inputs=arguments(); claim=inputs.pop('claim')
    specs={}
    for name in ('policy','public_policy','card_policy','roster','vocabulary','path_allowlist'):
        raw=inputs[name+'_bytes']; path=tmp_path/(name+'.json'); path.write_bytes(raw)
        specs[name]={'path':str(path),'sha256':sha(raw)}
    req={'schema':'wd.q4-public-card-publish-request.v1','claim':claim,'inputs':specs}
    raw=json.dumps(req,sort_keys=True,separators=(',',':')).encode()
    path=tmp_path/'request.json';path.write_bytes(raw)
    return req,path,sha(raw)


def success():
    return {'status':'WRITTEN','delivery':'canonical','event_ref_sha256':'b'*64,
            'truth':'UNVERIFIED','source_authentication':'UNKNOWN',
            'publisher_authentication':'UNKNOWN','clock_authentication':'UNKNOWN'}


def test_default_off_does_not_read_files_or_call_publisher(monkeypatch):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli')
    class Untouchable:
        def __getattribute__(self,_): raise AssertionError('OFF touched input')
    assert mod.run(request_path=Untouchable())=={'status':'OFF'}
    p=subprocess.run([sys.executable,'-B','-m','tools.wd_q4_public_card_publish_cli'],
        cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=15)
    assert p.returncode==0 and json.loads(p.stdout)=={'status':'OFF'}


def test_pinned_request_passes_exact_bytes_to_publisher(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli')
    req,path,pin=setup(tmp_path);calls=[]
    def publish(**kwargs): calls.append(kwargs);return success()
    monkeypatch.setattr(mod.publisher,'publish',publish)
    result=mod.run(enabled=True,request_path=str(path),request_sha256=pin)
    assert result==success() and len(calls)==1 and calls[0]['claim']==req['claim']
    for name,spec in req['inputs'].items():
        assert calls[0][name+'_bytes']==Path(spec['path']).read_bytes()
        assert calls[0][name+'_sha256']==spec['sha256']


@pytest.mark.parametrize('fault',['request_pin','input_pin','extra_key','missing_key','duplicate','relative'])
def test_bad_request_never_calls_publisher(tmp_path,monkeypatch,fault):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli')
    req,path,pin=setup(tmp_path)
    monkeypatch.setattr(mod.publisher,'publish',lambda **_:pytest.fail('publisher entered'))
    if fault=='input_pin':req['inputs']['policy']['sha256']='0'*64
    if fault=='extra_key':req['secret']='synthetic-secret'
    if fault=='missing_key':del req['inputs']['roster']
    if fault=='relative':req['inputs']['policy']['path']='policy.json'
    raw=json.dumps(req,sort_keys=True,separators=(',',':')).encode()
    if fault=='duplicate':raw=raw[:-1]+b',"schema":"wd.q4-public-card-publish-request.v1"}'
    path.write_bytes(raw)
    with pytest.raises(mod.Refused):mod.run(enabled=True,request_path=str(path),
        request_sha256='0'*64 if fault=='request_pin' else sha(raw))


@pytest.mark.parametrize('fault',['uncertain','interrupted','invalid_result','changed_request'])
def test_after_entry_unknown_has_no_retry_or_input_leak(tmp_path,monkeypatch,fault):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli');req,path,pin=setup(tmp_path);calls=[]
    def publish(**kwargs):
        calls.append(1)
        if fault=='uncertain':raise mod.publisher.Unknown('synthetic secret')
        if fault=='interrupted':raise mod.publisher.Interrupted('synthetic secret')
        if fault=='invalid_result':return {'status':'WRITTEN'}
        path.write_bytes(b'changed');return success()
    monkeypatch.setattr(mod.publisher,'publish',publish)
    with pytest.raises(mod.Unknown,match='^public_card_publish_cli_unknown$'):
        mod.run(enabled=True,request_path=str(path),request_sha256=pin)
    assert len(calls)==1


def test_known_preappend_refusal_is_refused(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli');_,path,pin=setup(tmp_path)
    def refuse(**kwargs): raise mod.publisher.Refused('synthetic secret')
    monkeypatch.setattr(mod.publisher,'publish',refuse)
    with pytest.raises(mod.Refused,match='^public_card_publish_cli_refused$'):
        mod.run(enabled=True,request_path=str(path),request_sha256=pin)


def test_queued_receipt_remains_queued(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli');_,path,pin=setup(tmp_path)
    queued={**success(),'status':'QUEUED','delivery':'queued','retained_wal_sha256':'c'*64}
    monkeypatch.setattr(mod.publisher,'publish',lambda **_:queued)
    assert mod.run(enabled=True,request_path=str(path),request_sha256=pin)==queued


def test_changed_policy_after_publication_is_unknown(tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_public_card_publish_cli');req,path,pin=setup(tmp_path);calls=[]
    def publish(**_):
        calls.append(1);Path(req['inputs']['roster']['path']).write_bytes(b'changed')
        return success()
    monkeypatch.setattr(mod.publisher,'publish',publish)
    with pytest.raises(mod.Unknown):mod.run(enabled=True,request_path=str(path),request_sha256=pin)
    assert calls==[1]
