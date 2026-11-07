"""Synthetic manifest and helper bytes only; no helper execution or live bundle."""
import hashlib
import json
import os
import dataclasses
import importlib.util
import sys
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
    runtime = tmp_path/'runtime'
    runtime_tools = runtime/'assets'/'code'/'tools'; runtime_tools.mkdir(parents=True)
    runtime_peer = runtime_tools/'wd_q4_secure_transport.py'; runtime_peer.write_bytes(peer.read_bytes())
    runtime_helper = runtime_tools/'wd_q4_staged_fixed_backend.py'; runtime_helper.write_bytes(helper.read_bytes())
    entries = [{'path':'assets/code/tools/'+p.name,'sha256':sha(p.read_bytes()),'size':p.stat().st_size}
               for p in (runtime_peer,runtime_helper)]
    runtime_raw = json.dumps({'schema':'wd.q4-runtime-manifest.v1','generation':'b'*40,'revision':'c'*40,
                              'files':entries},sort_keys=True,separators=(',',':')).encode()
    runtime_manifest = tmp_path/'runtime-manifest.json'; runtime_manifest.write_bytes(runtime_raw)
    plan.update(runtime_root=str(runtime),runtime_manifest_path=str(runtime_manifest),
                runtime_manifest_sha256=sha(runtime_raw),runtime_generation='b'*40,runtime_revision='c'*40)
    return root, peer, helper, plan


def test_pinned_backend_is_read_but_not_executed(staged):
    _, peer, helper, plan = staged
    assert fixed._read_staged_backend(str(peer), plan) == helper.read_bytes()


@pytest.fixture
def staged_runtime(staged, tmp_path):
    root, peer, helper, plan = staged
    tools = Path(plan['runtime_root'])/'assets'/'code'/'tools'
    runtime_peer = tools/'wd_q4_secure_transport.py'
    runtime_helper = tools/'wd_q4_staged_fixed_backend.py'
    manifest = Path(plan['runtime_manifest_path'])
    return root, peer, helper, plan, runtime_peer, runtime_helper, manifest


def test_runtime_peer_and_bundle_peer_use_the_same_pinned_backend(staged_runtime):
    _, bundle_peer, helper, plan, runtime_peer, _, _ = staged_runtime
    assert fixed._read_staged_backend(str(bundle_peer), plan) == helper.read_bytes()
    assert fixed._read_staged_backend(str(runtime_peer), plan) == helper.read_bytes()


@pytest.mark.parametrize('source', ['bundle','runtime'])
@pytest.mark.parametrize('damage', ['runtime_peer','runtime_helper','runtime_manifest_pin','generation','revision',
                                   'missing_peer_entry','missing_helper_entry','duplicate_entry','entry_hash','entry_size'])
def test_both_peer_routes_refuse_unbound_runtime_bytes(staged_runtime, source, damage):
    _, bundle_peer, _, plan, runtime_peer, runtime_helper, manifest = staged_runtime
    if damage in ('runtime_peer', 'runtime_helper'):
        path = runtime_peer if damage == 'runtime_peer' else runtime_helper
        path.write_bytes(path.read_bytes()+b' changed')
    elif damage == 'runtime_manifest_pin':
        plan['runtime_manifest_sha256'] = '0'*64
    elif damage in ('generation','revision'):
        plan['runtime_'+damage] = 'd'*40
    else:
        value = json.loads(manifest.read_bytes())
        if damage == 'missing_peer_entry': value['files'].pop(0)
        elif damage == 'missing_helper_entry': value['files'].pop(1)
        elif damage == 'duplicate_entry': value['files'].append(dict(value['files'][0]))
        elif damage == 'entry_hash': value['files'][0]['sha256'] = '0'*64
        else: value['files'][0]['size'] += 1
        raw = json.dumps(value,sort_keys=True,separators=(',',':')).encode()
        manifest.write_bytes(raw); plan['runtime_manifest_sha256'] = sha(raw)
    with pytest.raises(fixed._Refused):
        fixed._read_staged_backend(str(bundle_peer if source == 'bundle' else runtime_peer), plan)


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
    public=json.dumps({'schema':'wd.q4-public-wrapper-plan.v3','config':{'timeout':900}}).encode()
    plan.update(public_plan_path=str(root/'public-plan.json'),public_plan_sha256=sha(public))
    command=backend._wrapper_command(plan,r'C:\Windows',public)
    assert command[:5]==[r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
                          '-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass']
    assert command[5:7]==['-File',str(root/'Invoke-WdQ4Query.ps1')]
    assert '-Public' in command and '-Command' not in command and '-EncodedCommand' not in command
    assert command[command.index('-TimeoutSeconds')+1]=='900'
    assert all('synthetic-secret-query' not in item for item in command)


@pytest.mark.parametrize('timeout',[0,1801,-1,True,'900',None,float('inf'),float('nan')])
def test_wrapper_refuses_unusable_public_plan_timeout(staged, timeout):
    from tools import wd_q4_staged_fixed_backend as backend
    root, _, _, plan=staged
    public=json.dumps({'schema':'wd.q4-public-wrapper-plan.v3','config':{'timeout':timeout}}).encode()
    plan.update(public_plan_path=str(root/'public-plan.json'),public_plan_sha256=sha(public))
    with pytest.raises(ValueError): backend._wrapper_command(plan,r'C:\Windows',public)


def test_wrapper_refuses_wrong_public_plan_pin_and_duplicate_timeout(staged):
    from tools import wd_q4_staged_fixed_backend as backend
    root, _, _, plan=staged
    public=b'{"schema":"wd.q4-public-wrapper-plan.v3","config":{"timeout":900}}'
    plan.update(public_plan_path=str(root/'public-plan.json'),public_plan_sha256='a'*64)
    with pytest.raises(ValueError): backend._wrapper_command(plan,r'C:\Windows',public)
    duplicate=b'{"schema":"wd.q4-public-wrapper-plan.v3","config":{"timeout":900,"timeout":2}}'
    plan['public_plan_sha256']=sha(duplicate)
    with pytest.raises(ValueError): backend._wrapper_command(plan,r'C:\Windows',duplicate)


@pytest.mark.parametrize('timeout',[1,30,900,1800,1.5])
def test_wrapper_uses_only_the_matching_pinned_timeout(staged, timeout):
    from tools import wd_q4_staged_fixed_backend as backend
    root, _, _, plan=staged
    public=json.dumps({'schema':'wd.q4-public-wrapper-plan.v3','config':{'timeout':timeout}}).encode()
    plan.update(public_plan_path=str(root/'public-plan.json'),public_plan_sha256=sha(public))
    command=backend._wrapper_command(plan,r'C:\Windows',public)
    assert command.count('-TimeoutSeconds')==1
    assert float(command[command.index('-TimeoutSeconds')+1])==timeout


def test_staged_backend_preparation_is_default_off_and_refuses_unbound_input():
    from tools import wd_q4_staged_fixed_backend as backend
    assert backend.prepare_staged_backend(None, None, None) == {'status':'OFF'}
    with pytest.raises(ValueError):
        backend.prepare_staged_backend(None, None, None, enabled=True)


def test_guarded_parent_refuses_tampered_helper_before_launch(tmp_path, monkeypatch):
    """Actual guard and attestation; no subprocess is started by this negative test."""
    from tests.tools.test_wd_q4_owner_plumbing import _synthetic_guard
    from tests.tools.test_wd_q4_staged_fixed_plan import staged_args
    from tools import wd_q4_fixed_plan_producer as producer
    from tools.wd_q4_fixed_parent_attestation import _AttestationOwner
    root=tmp_path/'bundle'
    tools=root/'tools-bootstrap'/'tools';tools.mkdir(parents=True)
    verifier=root/'tools-bootstrap'/'sandbox'/'mind'/'step2'/'runtime_manifest.py'
    verifier.parent.mkdir(parents=True)
    source=Path(fixed.__file__)
    peer=tools/'wd_q4_secure_transport.py';peer.write_bytes(source.read_bytes())
    helper=tools/'wd_q4_staged_fixed_backend.py'
    helper.write_bytes((source.parent/'wd_q4_staged_fixed_backend.py').read_bytes())
    verifier.write_bytes((source.parents[1]/'sandbox/mind/step2/runtime_manifest.py').read_bytes())
    wrapper=root/'Invoke-WdQ4Query.ps1'
    wrapper.write_bytes((source.parents[1]/'ops/windows/reboot/Invoke-WdQ4Query.ps1').read_bytes())
    spec=importlib.util.spec_from_file_location('synthetic_fixed_parent',peer)
    candidate=importlib.util.module_from_spec(spec);spec.loader.exec_module(candidate)
    monkeypatch.setattr(candidate,'_run',lambda *a,**k:pytest.fail('launch reached'))
    public_path=tmp_path/'public-plan.json';public_path.write_bytes(b'{}')
    runtime_path=tmp_path/'runtime-manifest.json';runtime_path.write_bytes(b'{}')
    runtime_root=tmp_path/'runtime';runtime_root.mkdir()
    bundle=b'synthetic-c2'
    manifest={'schema_version':1,'source_commit':'a'*40,'files':{
        'tools-bootstrap/tools/wd_q4_secure_transport.py':sha(peer.read_bytes()),
        'tools-bootstrap/tools/wd_q4_staged_fixed_backend.py':sha(helper.read_bytes()),
        'Invoke-WdQ4Query.ps1':sha(wrapper.read_bytes())}}
    manifest_raw=json.dumps(manifest,sort_keys=True,separators=(',',':')).encode()
    (root/'deployment-manifest.json').write_bytes(manifest_raw)
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    values=staged_args()
    values.update(interpreter_sha256=sha(Path(sys.executable).read_bytes()),peer_code_sha256=sha(peer.read_bytes()),
        verifier_sha256=sha(verifier.read_bytes()),runtime_manifest_sha256=sha(runtime_path.read_bytes()),
        bundle_sha256=sha(bundle),bundle_bytes=len(bundle),runtime_root=str(runtime_root),
        runtime_manifest_path=str(runtime_path),bundle_root=str(root),bundle_commit='a'*40,
        wrapper_path=str(wrapper),wrapper_sha256=sha(wrapper.read_bytes()),
        deployment_manifest_sha256=sha(manifest_raw),public_plan_path=str(public_path),
        public_plan_sha256=sha(public_path.read_bytes()),host_sha256=sha(host.read_bytes()),
        backend_sha256=sha(helper.read_bytes()))
    plan=producer.build_staged_fixed_plan(enabled=True,**values)['plan_bytes']
    guard,marker=_synthetic_guard(tmp_path/'guard','codex-lead-1')
    before=marker.read_bytes();context=guard._owner_context()
    observed=[];original_attest=_AttestationOwner.attest
    def observe(owner,path,*args,**kwargs):
        observed.append(str(path))
        return original_attest(owner,path,*args,**kwargs)
    monkeypatch.setattr(_AttestationOwner,'attest',observe)
    with helper.open('ab') as stream:stream.write(b'\n# tampered synthetic copy\n')
    with pytest.raises(Exception):
        candidate._run_fixed_peer_impl(sys.executable,values['interpreter_sha256'],128*1024*1024,
            plan,sha(plan),bundle,runtime_path.read_bytes(),b'{}',str(tmp_path),30,32768,True,
            _owner_guard=guard,_owner_context=context)
    assert marker.read_bytes()==before
    assert str(helper) in observed


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
