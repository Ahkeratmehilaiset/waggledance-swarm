"""Synthetic manifest and helper bytes only; no helper execution or live bundle."""
import hashlib
import json
import os
import dataclasses
from pathlib import Path

import pytest

from tools import wd_q4_secure_transport as fixed
from tests.tools.test_wd_q4_real_card_snapshot_e2e import real_fixture, real_snapshot, rt

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows bundle paths')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def staged(tmp_path):
    root = tmp_path/'bundle'
    tools = root/'tools-bootstrap'/'tools'
    tools.mkdir(parents=True)
    peer = tools/'wd_q4_secure_transport.py'
    helper = tools/'wd_q4_staged_fixed_backend.py'
    wrapper = root/'Invoke-WdQ4Query.ps1'
    peer.write_bytes(b'synthetic peer')
    helper.write_bytes(b'synthetic backend; never execute')
    wrapper.write_bytes(b'synthetic wrapper; never execute')
    manifest = {'schema_version':1,'source_commit':'a'*40,'files':{
        'tools-bootstrap/tools/wd_q4_secure_transport.py':sha(peer.read_bytes()),
        'tools-bootstrap/tools/wd_q4_staged_fixed_backend.py':sha(helper.read_bytes()),
        'Invoke-WdQ4Query.ps1':sha(wrapper.read_bytes())}}
    raw = json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    (root/'deployment-manifest.json').write_bytes(raw)
    plan = {'bundle_root':str(root),'bundle_commit':'a'*40,'deployment_manifest_sha256':sha(raw),
            'peer_code_sha256':sha(peer.read_bytes()),'backend_sha256':sha(helper.read_bytes()),
            'wrapper_path':str(wrapper),'wrapper_sha256':sha(wrapper.read_bytes())}
    return root, peer, helper, plan


def test_pinned_backend_is_read_but_not_executed(staged):
    _, peer, helper, plan = staged
    assert fixed._read_staged_backend(str(peer), plan) == helper.read_bytes()


@pytest.mark.parametrize('damage', ['helper','peer','manifest','wrapper','backend_pin','foreign_peer'])
def test_staged_backend_refuses_unpinned_or_foreign_source(staged,tmp_path,damage):
    root, peer, helper, plan = staged
    if damage in ('helper','peer','manifest','wrapper'):
        paths = {'helper':helper,'peer':peer,'manifest':root/'deployment-manifest.json',
                 'wrapper':root/'Invoke-WdQ4Query.ps1'}
        with paths[damage].open('ab') as stream:
            stream.write(b'changed')
    elif damage == 'backend_pin':
        plan['backend_sha256'] = '0'*64
    else:
        peer = tmp_path/'wd_q4_secure_transport.py'
        peer.write_bytes(b'synthetic peer')
    with pytest.raises(fixed._Refused):
        fixed._read_staged_backend(str(peer), plan)


def test_fixed_wrapper_command_has_no_query_or_arbitrary_code(staged):
    from tools import wd_q4_staged_fixed_backend as backend
    root, _, _, plan = staged
    plan.update(public_plan_path=str(root/'public-plan.json'),public_plan_sha256='a'*64)
    command=backend._wrapper_command(plan,r'C:\Windows')
    assert command[:5]==[r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                          '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass']
    assert command[5:7]==['-File',str(root/'Invoke-WdQ4Query.ps1')]
    assert '-Public' in command and '-Command' not in command and '-EncodedCommand' not in command
    assert all('synthetic-secret-query' not in item for item in command)


@pytest.mark.parametrize('damage', ['projection','source','pointer','card_policy','plan','bundle'])
def test_actual_c2_inputs_bind_independent_plan_before_dispatch(real_snapshot, tmp_path, damage):
    from tools import wd_q4_staged_fixed_backend as backend
    from tools.wd_q4_public_query import _encoder_digest
    directory, built, decision, snapshot, projection, args = real_snapshot
    data = {'pointer':snapshot['pointer_bytes'], 'policy':args['policy_bytes'], 'roster':args['roster_bytes'],
            'public_policy':b'{}', 'card_decision':args['decision_bytes'], 'card_policy':args['card_policy_bytes'],
            'memory':(directory/'memory.json').read_bytes(), 'projection':(directory/'projection.json').read_bytes(),
            'descriptor':(directory/'descriptor.json').read_bytes()}
    paths={}
    for key in ('pointer','policy','roster','public_policy','card_decision','card_policy'):
        path=tmp_path/(key+'.json');path.write_bytes(data[key]);paths[key]=str(path)
    descriptor=json.loads(data['descriptor'])
    public={'schema':'wd.q4-public-wrapper-plan.v3','root':str(directory.parent),
            'config':{'memory':str(directory),'descriptor':str(directory/'descriptor.json'),
                      'descriptor_sha256':built['descriptor_sha256'],'runtime_revision':descriptor['encoder']['revision']},
            'source_path':str(args['source_path']),'generation_path':str(args['generation_path']),
            'end_cursor':dataclasses.asdict(args['end_cursor']),
            'source_anchor_sha256':decision['source_prefix']['sha256'],
            'encoder_sha256':_encoder_digest(descriptor['encoder'])}
    for key,path in paths.items():
        public[key+'_path']=path;public[key+'_sha256']=sha(data[key])
    public_raw=json.dumps(public,sort_keys=True,separators=(',',':')).encode()
    public_path=tmp_path/'staged-public-plan.json';public_path.write_bytes(public_raw)
    bundle=fixed.fixed_card_bundle(enabled=True,**data)
    plan={'bundle_sha256':sha(bundle),'bundle_bytes':len(bundle),'public_plan_sha256':sha(public_raw),
          'public_plan_path':str(public_path),
          'expected_pointer_sha256':sha(data['pointer']),'policy_sha256':sha(data['policy']),
          'roster_sha256':sha(data['roster']),'expected_public_policy_sha256':sha(data['public_policy']),
          'card_decision_sha256':sha(data['card_decision']),'card_policy_sha256':sha(data['card_policy']),
          'expected_source_sha256':decision['source_prefix']['sha256'],
          'expected_encoder_sha256':_encoder_digest(descriptor['encoder'])}
    assert backend.validate_card_input(plan,bundle,public_raw) == {'status':'OFF'}
    result=backend.validate_card_input(plan,bundle,public_raw,enabled=True)
    assert result['status']=='VALIDATED_STAGED' and result['completeness']=='UNKNOWN'
    from sandbox.mind.step2 import shared_card_projection as cards
    when='2026-11-02T00:00:00Z'
    answer=cards.ask_shared_cards(directory,built['memory_sha256'],rt.fx.Encoder(),
        'merged green pr 2046 genesis',when,enabled=True,real_snapshot=snapshot)
    assert answer['status']=='ANSWER'
    results=[{key:item[key] for key in ('ref_sha256','doc_id_sha256','ref_kind','canonical_event','stored_text')}
             for item in answer['results']]
    reply={'schema':'wd.q4-card-snapshot-query.v1','status':'ANSWER','results':results,
           'binding':{'source_mode':'card-snapshot-v1','pointer_sha256':sha(data['pointer']),
             'source_anchor_sha256':plan['expected_source_sha256'],'encoder_sha256':plan['expected_encoder_sha256'],
             'policy_sha256':sha(data['policy']),'roster_sha256':sha(data['roster']),
             'card_decision_sha256':sha(data['card_decision']),'card_policy_sha256':sha(data['card_policy']),
             'descriptor_sha256':sha(data['descriptor']),'memory_sha256':sha(data['memory']),
             'projection_sha256':sha(data['projection'])},
           **{key:'UNKNOWN' for key in ('publisher_authentication','source_filter_attestation',
                                          'freshness','same_user_aba','completeness')}}
    query=json.dumps({'situation':'merged green pr 2046 genesis','time':when}).encode()
    wire=json.dumps(reply,sort_keys=True,separators=(',',':')).encode()
    assert backend.validate_card_result(plan,bundle,public_raw,query,wire,enabled=True)==wire
    bad={**reply,'results':[{**reply['results'][0],'stored_text':'invented'}]}
    with pytest.raises(ValueError):
        backend.validate_card_result(plan,bundle,public_raw,query,json.dumps(bad).encode(),enabled=True)
    if damage == 'projection':
        with (directory/'projection.json').open('ab') as stream:stream.write(b' ')
    elif damage == 'source':
        path=Path(args['source_path']);original=path.read_bytes();path.write_bytes(b'X'+original[1:])
    elif damage in ('pointer','card_policy'):
        with Path(paths[damage]).open('ab') as stream:stream.write(b' ')
    elif damage == 'plan':
        public_path.write_bytes(public_raw+b' ')
    else:
        bundle += b' '
    with pytest.raises(ValueError):backend.validate_card_input(plan,bundle,public_raw,enabled=True)
