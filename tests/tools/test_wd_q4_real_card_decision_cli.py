"""Synthetic local Git and log only; no bridge or installed runtime."""
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from tests.tools.test_wd_q4_real_card_generation import real_fixture
from tests.tools.test_wd_q4_real_card_decision_prepare import arguments
from waggledance.core.bridge_log_reader import BridgeCursor


@pytest.fixture
def tmp_path(tmp_path_factory):
    """Keep the CLI and its inherited synthetic Git/source fixture under audit."""
    root = tmp_path_factory.mktemp('real-card-decision-cli') / '.codex-audit'
    root.mkdir()
    return root


def sha(raw):return hashlib.sha256(raw).hexdigest()


def fixture(real_fixture,tmp_path):
    from tools import wd_q4_external_generation_anchor as anchor
    args=arguments(real_fixture);old=args['end_cursor']
    Path(args['generation_path']).unlink()  # synthetic fixture only
    cursor=BridgeCursor(old.offset,old.file_identity,None)
    anchor_dir=tmp_path/'anchor';anchor_dir.mkdir()
    pin=anchor.prepare(enabled=True,source_path=args['source_path'],original_cursor=cursor,
                       expected_prefix_sha256=args['expected_prefix_sha256'],output_root=str(anchor_dir))
    inputs={}
    for name in ('policy','roster','card_policy','public_policy','vocabulary','path_allowlist','anchors'):
        raw=args[name+'_bytes'];path=tmp_path/(name+'.json');path.write_bytes(raw)
        inputs[name]={'path':str(path),'sha256':sha(raw)}
    output=tmp_path/'decision-out';output.mkdir()
    request={'schema':'wd.q4-real-card-decision-request.v1','mode':'real','source_mode':'synthetic_fixture',
             'inputs':inputs,'expected_card_module_sha256':args['expected_card_module_sha256'],
             'source_path':args['source_path'],'cursor':{'offset':cursor.offset,'file_identity':cursor.file_identity,
             'generation':None},'prefix_sha256':args['expected_prefix_sha256'],
             'anchor':{'root':str(anchor_dir),'generation_sha256':pin['generation_sha256'],
                       'binding_sha256':pin['binding_sha256']},
             'git':{'executable':args['git_executable'],'sha256':args['git_executable_sha256'],
                    'repository':args['git_repository']}}
    raw=json.dumps(request,sort_keys=True,separators=(',',':')).encode()
    path=tmp_path/'request.json';path.write_bytes(raw)
    return request,path,sha(raw),output,args


def test_default_off_no_input_access():
    mod=importlib.import_module('tools.wd_q4_real_card_decision_cli')
    class Untouchable:
        def __getattribute__(self,_):raise AssertionError('OFF touched input')
    assert mod.run(enabled=False,request_path=Untouchable())=={'status':'OFF'}


def test_real_decision_file_roundtrip(real_fixture,tmp_path):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_cli')
    request,path,pin,out,args=fixture(real_fixture,tmp_path)
    result=mod.run(enabled=True,request_path=str(path),request_sha256=pin,output_root=str(out))
    raw=(out/'card-decision.json').read_bytes();value=json.loads(raw)
    assert result['status']=='WRITTEN' and result['decision_sha256']==sha(raw)
    assert value['synthetic'] is False and value['source_prefix']['sha256']==request['prefix_sha256']
    assert result['source_authentication']==result['freshness']=='UNKNOWN'
    assert str(tmp_path) not in json.dumps(result)


def test_cli_process_writes_synthetic_fixture(real_fixture,tmp_path):
    _,path,pin,out,_=fixture(real_fixture,tmp_path)
    result=subprocess.run([sys.executable,'-B','-m','tools.wd_q4_real_card_decision_cli',
        '--enabled','--request',str(path),'--request-sha256',pin,'--output-root',str(out)],
        cwd=Path(__file__).resolve().parents[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    assert result.returncode==0 and not result.stderr
    assert json.loads(result.stdout)['status']=='WRITTEN' and (out/'card-decision.json').is_file()
    assert str(tmp_path).encode() not in result.stdout


@pytest.mark.parametrize('fault',['request_pin','input_pin','anchor_pin','extra_key','output_nonempty'])
def test_bad_input_refused_without_output(real_fixture,tmp_path,fault):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_cli')
    request,path,pin,out,_=fixture(real_fixture,tmp_path)
    if fault=='input_pin':request['inputs']['policy']['sha256']='0'*64
    if fault=='anchor_pin':request['anchor']['binding_sha256']='0'*64
    if fault=='extra_key':request['notes']='private-canary'
    if fault=='output_nonempty':(out/'existing').write_bytes(b'unchanged')
    raw=json.dumps(request,sort_keys=True,separators=(',',':')).encode();path.write_bytes(raw)
    with pytest.raises(mod.Refused):
        mod.run(enabled=True,request_path=str(path),request_sha256=('0'*64 if fault=='request_pin' else sha(raw)),output_root=str(out))
    assert not (out/'card-decision.json').exists()


@pytest.mark.parametrize('fault',['request_changed','rotated'])
def test_change_during_prepare_refused_before_write(real_fixture,tmp_path,monkeypatch,fault):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_cli')
    from tools import wd_q4_real_card_decision_prepare as prepare
    request,path,pin,out,args=fixture(real_fixture,tmp_path);original=prepare.prepare
    def mutate(**kwargs):
        result=original(**kwargs)
        if fault=='request_changed':path.write_bytes(b'changed')
        else:
            source=Path(args['source_path']);raw=source.read_bytes()
            source.rename(tmp_path/'old.jsonl');source.write_bytes(raw)
        return result
    monkeypatch.setattr(prepare,'prepare',mutate)
    with pytest.raises(mod.Refused):mod.run(enabled=True,request_path=str(path),request_sha256=pin,output_root=str(out))
    assert not (out/'card-decision.json').exists()


def test_partial_write_is_unknown_and_preserved(real_fixture,tmp_path,monkeypatch):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_cli')
    _,path,pin,out,_=fixture(real_fixture,tmp_path)
    def partial(target,raw):target.write_bytes(raw[:5]);raise OSError()
    monkeypatch.setattr(mod,'_write_new',partial)
    with pytest.raises(mod.Unknown):mod.run(enabled=True,request_path=str(path),request_sha256=pin,output_root=str(out))
    assert (out/'card-decision.json').read_bytes()!=b''


@pytest.mark.parametrize('fault',['request_changed','rotated','interrupt'])
def test_uncertainty_after_write_keeps_file(real_fixture,tmp_path,monkeypatch,fault):
    mod=importlib.import_module('tools.wd_q4_real_card_decision_cli')
    _,path,pin,out,args=fixture(real_fixture,tmp_path);original=mod._write_new
    def mutate(target,raw):
        original(target,raw)
        if fault=='request_changed':path.write_bytes(b'changed')
        if fault=='rotated':
            source=Path(args['source_path']);data=source.read_bytes()
            source.rename(tmp_path/'old-after-write.jsonl');source.write_bytes(data)
        if fault=='interrupt':raise KeyboardInterrupt()
    monkeypatch.setattr(mod,'_write_new',mutate)
    with pytest.raises(mod.Interrupted if fault=='interrupt' else mod.Unknown):
        mod.run(enabled=True,request_path=str(path),request_sha256=pin,output_root=str(out))
    assert (out/'card-decision.json').is_file()
