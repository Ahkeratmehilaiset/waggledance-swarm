"""RED: staged C2 admission with a real cooperative guard, synthetic data only.

This does not prove wrapper execution, Job cleanup, or guard completion: those
must be added with the real staged transport rather than simulated here.
"""
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
from sandbox.mind.step2 import shared_card_checkpoint
from tests.tools.test_wd_q4_bound_lane_transport import case, create
from tests.tools.test_wd_q4_owner_plumbing import _synthetic_guard
from tests.tools.test_wd_q4_real_card_snapshot_e2e import real_fixture, real_snapshot, rt
from tools import wd_q4_bound_lane_transport as bound, wd_q4_secure_transport as fixed
from tools.wd_q4_public_query import _encoder_digest


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def canonical_card_snapshot(real_snapshot, tmp_path):
    """Rebuild the fabricated cards with the actual fleet-to-roster contract."""
    from tools import wd_q4_card_generation, wd_q4_roster_from_fleet
    _, _, decision, _, _, args = real_snapshot
    fleet = json.loads(args['roster_bytes'])
    for item in [*fleet['lanes'], fleet['tools_supervisor']]:
        item['worktree'] = str(tmp_path)
    fleet['runtime_root'] = str(tmp_path/'fleet-runtime')
    fleet['q4_read'] = {'enabled': False}
    fleet_bytes = fixed._canonical(fleet)
    roster, roster_sha = wd_q4_roster_from_fleet.build_roster_snapshot(fleet_bytes, _sha(fleet_bytes), enabled=True)
    decision = dict(decision, roster_sha256=roster_sha)
    decision_bytes = fixed._canonical(decision)
    output_root = tmp_path/'canonical-card-output'; output_root.mkdir()
    args = dict(args, roster_bytes=roster, roster_sha256=roster_sha,
                decision_bytes=decision_bytes, decision_sha256=_sha(decision_bytes), output_root=output_root)
    built = wd_q4_card_generation.prepare_card_generation(**args)
    directory = output_root/args['generation_name']
    artifacts = {str(p.relative_to(directory)).replace('\\','/'): _sha(p.read_bytes())
                 for p in directory.rglob('*') if p.is_file()}
    pointer = fixed._canonical({'schema':'wd.q4-card-snapshot.v1','generation':args['generation_name'],
        'artifacts':artifacts,'decision_sha256':args['decision_sha256'],'policy_sha256':args['policy_sha256'],
        'roster_sha256':roster_sha,'source_prefix':decision['source_prefix']})
    snapshot = dict(pointer_bytes=pointer, expected_pointer_sha256=_sha(pointer), decision_bytes=decision_bytes,
        decision_sha256=_sha(decision_bytes), policy_bytes=args['policy_bytes'], policy_sha256=args['policy_sha256'],
        roster_bytes=roster, roster_sha256=roster_sha, card_policy_bytes=args['card_policy_bytes'],
        source_path=args['source_path'], generation_path=args['generation_path'], end_cursor=args['end_cursor'])
    return (directory, built, decision, snapshot, None, args), fleet_bytes


@pytest.mark.skipif(os.name != 'nt', reason='Windows system-host attestation')
def test_staged_system_host_canonical_spelling_keeps_strict_handle_lease(monkeypatch):
    from tools.wd_q4_fixed_parent_attestation import attest_file
    monkeypatch.setenv('SYSTEMROOT', os.environ['SYSTEMROOT'].upper())
    path = fixed._staged_system_host()
    with attest_file(path, _sha(Path(path).read_bytes()), max_bytes=128*1024*1024, enabled=True):
        pass


@pytest.mark.skipif(os.name != 'nt', reason='Windows system-host canonicalization')
def test_staged_system_host_refuses_different_resolved_path(monkeypatch):
    monkeypatch.setattr(fixed, '_canonical_windows_long_path', lambda _path: r'C:\Windows\not-the-system-host.exe')
    with pytest.raises(fixed._Refused):
        fixed._staged_system_host()


def test_real_c2_staged_plan_refuses_status_transport_without_clearing_guard(case, real_snapshot, tmp_path):
    directory, built, decision, snapshot, _projection, args = real_snapshot
    assert shared_card_checkpoint.validate_card_snapshot(enabled=True, generation_dir=directory, **snapshot) == {
        'status': 'VALIDATED_STAGED', 'completeness': 'UNKNOWN'}
    descriptor = json.loads((directory / 'descriptor.json').read_bytes())
    plan_path = tmp_path / 'staged-plan.json'
    plan = json.loads(open(case['public_plan_path'], 'rb').read())
    plan.update(schema='wd.q4-public-wrapper-plan.v3', root=str(directory.parent),
        config={'descriptor_sha256': built['descriptor_sha256']},
        source_anchor_sha256=decision['source_prefix']['sha256'],
        encoder_sha256=_encoder_digest(descriptor['encoder']),
        pointer_sha256=snapshot['expected_pointer_sha256'], policy_sha256=args['policy_sha256'],
        roster_sha256=args['roster_sha256'], card_decision_sha256=args['decision_sha256'],
        card_policy_sha256=_sha(args['card_policy_bytes']),
        source_path=str(args['source_path']),generation_path=str(args['generation_path']),
        end_cursor=dataclasses.asdict(args['end_cursor']))
    for key, raw in (('pointer', snapshot['pointer_bytes']), ('policy', args['policy_bytes']),
                     ('roster', args['roster_bytes']), ('card_decision', args['decision_bytes']),
                     ('card_policy', args['card_policy_bytes'])):
        path=tmp_path/(key+'.json')
        path.write_bytes(raw)
        plan[key+'_path']=str(path)
    plan_path.write_bytes(json.dumps(plan,sort_keys=True,separators=(',',':')).encode('ascii'))
    case['public_plan_path']=str(plan_path)
    case['expected_public_plan_sha256']=_sha(plan_path.read_bytes())
    guard, pending = _synthetic_guard(tmp_path/'guard-fixture','codex-lead-1')
    context=guard._owner_context()
    before=pending.read_bytes()
    case['_owner_guard']=guard
    case['_owner_context']=context
    # The pre-existing fixed plan in case is a different, status-only snapshot.
    # A valid implementation must replace this contract, never ignore the
    # mismatch. The eventual real-Job integration requires its own test.
    public = {k:v for k,v in case.items() if not k.startswith('_')}
    public.update(_owner_guard=guard,_owner_context=context)
    with pytest.raises(bound.BoundLaneTransportRefused):
        bound.make_bound_transport(**public)
    assert pending.read_bytes()==before


def test_guarded_staged_transport_has_an_explicit_separate_factory():
    # A new, fixed C2-aware route is required. The existing status-only factory
    # must not treat a mismatched fixed snapshot as the card snapshot.
    assert callable(getattr(bound, 'make_guarded_staged_transport', None)), (
        'No guarded C2 Windows-Job transport exists yet')


def test_actual_c2_tuple_admits_guarded_factory_without_launch(case, real_snapshot, tmp_path, monkeypatch):
    """C2 + real guard admission; intentionally no child launch or cleanup claim."""
    from tests.tools.test_wd_q4_staged_fixed_plan import staged_args
    from tools import wd_q4_fixed_plan_producer as producer
    directory,built,decision,snapshot,_,args=real_snapshot
    assert shared_card_checkpoint.validate_card_snapshot(enabled=True,generation_dir=directory,**snapshot)['status']=='VALIDATED_STAGED'
    descriptor=(directory/'descriptor.json').read_bytes()
    pointer=snapshot['pointer_bytes']
    data={'pointer':pointer,'policy':args['policy_bytes'],'roster':args['roster_bytes'],
          'public_policy':case['_parts']['public_policy_bytes'],'card_decision':args['decision_bytes'],
          'card_policy':args['card_policy_bytes'],'memory':(directory/'memory.json').read_bytes(),
          'projection':(directory/'projection.json').read_bytes(),'descriptor':descriptor}
    plan={'schema':'wd.q4-public-wrapper-plan.v3','root':str(directory.parent),
          'config':{'descriptor_sha256':built['descriptor_sha256']},
          'source_path':str(args['source_path']),'generation_path':str(args['generation_path']),
          'end_cursor':dataclasses.asdict(args['end_cursor']),
          'source_anchor_sha256':decision['source_prefix']['sha256'],
          'encoder_sha256':_encoder_digest(json.loads(descriptor)['encoder'])}
    for key in ('pointer','policy','roster','public_policy','card_decision','card_policy'):
        path=tmp_path/('staged-'+key+'.json');path.write_bytes(data[key])
        plan[key+'_path']=str(path);plan[key+'_sha256']=_sha(data[key])
    path=Path(case['public_plan_path']);path.write_bytes(fixed._canonical(plan))
    case['expected_public_plan_sha256']=_sha(path.read_bytes())
    root=Path(case['bundle_root']);wrapper=root/'Invoke-WdQ4Query.ps1'
    wrapper.write_bytes((Path(__file__).resolve().parents[2]/'ops/windows/reboot/Invoke-WdQ4Query.ps1').read_bytes())
    manifest={'schema_version':1,'source_commit':case['expected_commit'],'files':{wrapper.name:_sha(wrapper.read_bytes())}}
    raw_manifest=fixed._canonical(manifest);(root/'deployment-manifest.json').write_bytes(raw_manifest)
    case['expected_manifest_sha256']=_sha(raw_manifest);case['expected_wrapper_sha256']=_sha(wrapper.read_bytes())
    wire=fixed.fixed_card_bundle(enabled=True,**data)
    old=fixed._fixed_plan(case['fixed_plan_bytes'])
    values=staged_args()
    for key in values.keys() & old.keys():values[key]=old[key]
    values.update(bundle_sha256=_sha(wire),bundle_bytes=len(wire),expected_pointer_sha256=plan['pointer_sha256'],
       policy_sha256=plan['policy_sha256'],roster_sha256=plan['roster_sha256'],
       expected_public_policy_sha256=plan['public_policy_sha256'],expected_source_sha256=plan['source_anchor_sha256'],
       expected_encoder_sha256=plan['encoder_sha256'],card_decision_sha256=plan['card_decision_sha256'],
       card_policy_sha256=plan['card_policy_sha256'],bundle_root=str(root),bundle_commit=case['expected_commit'],
       wrapper_path=str(wrapper),wrapper_sha256=case['expected_wrapper_sha256'],
       deployment_manifest_sha256=case['expected_manifest_sha256'],public_plan_path=str(path),
       public_plan_sha256=case['expected_public_plan_sha256'])
    built_plan=producer.build_staged_fixed_plan(enabled=True,**values)
    case.update(fixed_plan_bytes=built_plan['plan_bytes'],expected_fixed_plan_sha256=built_plan['plan_sha256'],
                fixed_bundle_bytes=wire)
    guard,pending=_synthetic_guard(tmp_path/'guard-admission','codex-lead-1')
    context=guard._owner_context();before=pending.read_bytes()
    monkeypatch.setattr(fixed,'run_fixed_peer',lambda **_:pytest.fail('factory must not launch'))
    public={k:v for k,v in case.items() if not k.startswith('_')}
    with pytest.raises(bound.BoundLaneTransportRefused):
        bound.make_guarded_staged_transport(**public)
    transport=bound.make_guarded_staged_transport(**public,_owner_guard=guard,_owner_context=context)
    assert callable(transport) and pending.read_bytes()==before
    # Selection seam only: this legacy C2 fixture's roster is not the canonical
    # producer output, so roster and lane binding are substituted. No peer call
    # or owner completion is faked here; a full test needs a canonical roster.
    from tools import wd_q4_lane_session_client as session
    fleet=args['roster_bytes']
    pins={'lane':'codex-lead-1','expected_commit':case['expected_commit'],
          'expected_manifest_sha256':case['expected_manifest_sha256'],
          'expected_wrapper_sha256':case['expected_wrapper_sha256'],
          'expected_public_plan_sha256':case['expected_public_plan_sha256'],
          'expected_binding':{'roster_sha256':args['roster_sha256']}}
    monkeypatch.setattr(session.lane_binding,'resolve_lane_binding',lambda *a,**k:pins)
    monkeypatch.setattr(session.roster,'build_roster_snapshot',
                        lambda *a,**k:(data['roster'],args['roster_sha256']))
    observed=[]
    def observe_query(**kw):
        observed.append(kw)
        return {'status':'SYNTHETIC_SELECTION_ONLY'}
    monkeypatch.setattr(session.lane_query,'query_public',observe_query)
    result=session.query_session(lane='codex-lead-1',situation='synthetic',time=None,
        fleet_bytes=fleet,expected_fleet_sha256=_sha(fleet),binding_bytes=b'{}',expected_binding_sha256='a'*64,
        **{key:case[key] for key in ('bundle_root','public_plan_path','executable','expected_interpreter_sha256',
             'interpreter_max_bytes','fixed_plan_bytes','expected_fixed_plan_sha256','fixed_bundle_bytes',
             'runtime_manifest_bytes','cwd','timeout_seconds','max_output_bytes')},enabled=True,
        _owner_guard=guard,_owner_context=context)
    assert result=={'status':'SYNTHETIC_SELECTION_ONLY'} and len(observed)==1
    assert observed[0]['projection_bytes']==data['projection'] and observed[0]['policy_bytes']==data['policy']
    assert observed[0]['roster_bytes']==data['roster'] and pending.read_bytes()==before
    call_args=('-BundleRoot',str(root),'-ExpectedBundleCommit',case['expected_commit'],
        '-ExpectedManifestSha256',case['expected_manifest_sha256'],'-Public','-PublicPlanPath',str(path),
        '-ExpectedPublicPlanSha256',case['expected_public_plan_sha256'])
    with pytest.raises(bound.BoundLaneTransportRefused):
        transport(wrapper=str(wrapper),arguments=call_args+('-Other',),stdin_bytes=b'synthetic')
    path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(bound.BoundLaneTransportRefused):
        transport(wrapper=str(wrapper),arguments=call_args,stdin_bytes=b'synthetic')
    assert pending.read_bytes()==before


@pytest.mark.skipif(os.name != 'nt' or os.environ.get('WD_Q4_STAGED_JOB_E2E') != '1',
                    reason='Windows and explicit real-Job test opt-in required')
def test_actual_staged_job_positive_control_separate_from_guard(real_snapshot, tmp_path):
    """Real Job/C2 control; deliberately no claim of a guarded call."""
    path = Path(__file__).with_name('test_wd_q4_staged_card_job_e2e.py')
    spec = importlib.util.spec_from_file_location('staged_job_control', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.test_staged_job_runs_real_c2_and_q4_and_refuses_changed_projection(real_snapshot, tmp_path)


@pytest.mark.skipif(os.name != 'nt' or os.environ.get('WD_Q4_NESTED_JOB_E2E') != '1',
                    reason='Windows and explicit nested-Job test opt-in required')
def test_actual_nested_job_c2_query_and_real_guard_owners(canonical_card_snapshot, tmp_path, monkeypatch):
    """Synthetic files/encoder; actual fixed outer Job and staged wrapper inner Job.

    The public outer and inner model launcher are explicit synthetic shims. This
    tests transport/attestation owners only, not adapter completion or E5.
    """
    import shutil
    import sys
    from sandbox.mind.step2 import runtime_builder
    from tests.tools.test_wd_q4_staged_fixed_plan import staged_args
    from tools import wd_q4_fixed_plan_producer as producer
    root = Path(__file__).resolve().parents[2]
    site = Path(os.environ['WD_Q4_TEST_SITE'])
    python = Path(os.environ['WD_Q4_TEST_PYTHON'])
    assert python.is_file() and _sha(python.read_bytes()) == os.environ['WD_Q4_TEST_PYTHON_SHA256']
    assert os.path.normcase(str(python)) == os.path.normcase(sys.executable)
    runtime=tmp_path/'runtime';code=runtime/'assets'/'code';code.mkdir(parents=True)
    for relative in ('tools','waggledance','sandbox/mind','core'):
        source=root/relative
        if not source.is_dir():continue
        for original in source.rglob('*.py'):
            assert not original.is_symlink()
            dest=code/original.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(original,dest)
    runtime_site=runtime/'site';runtime_site.mkdir()
    for name in ('numpy','numpy.libs','faiss','faiss_cpu.libs','packaging'):
        original=site/name
        assert original.is_dir() and not original.is_symlink()
        for parent,dirs,files in os.walk(original,followlinks=False):
            for item in dirs+files:
                candidate=Path(parent)/item
                assert not candidate.is_symlink() and not (getattr(candidate.lstat(),'st_file_attributes',0)&0x400)
        shutil.copytree(original,runtime_site/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    model=runtime/'model';model.mkdir();(model/'synthetic-model.txt').write_bytes(b'not E5')
    runtime_manifest=tmp_path/'runtime-manifest.json'
    generated=runtime_builder.build_runtime_manifest(runtime,runtime_manifest,generation='f'*40,revision='a'*40)
    module_spec=importlib.util.spec_from_file_location('nested_wrapper_fixture',
        root/'tests/tools/test_wd_q4_agent_wrapper.py')
    module=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(module)
    bundle_dir=tmp_path/'bundle-fixture';bundle_dir.mkdir()
    bundle=module.bundle.__wrapped__(bundle_dir)
    package=json.loads((bundle['root']/'bridge-code-files.json').read_bytes())
    shim='''\
import datetime,hashlib,json,re,sys
sys.path.insert(0,SITE);sys.path.insert(1,ROOT)
import numpy as np
from sandbox.mind.step2 import runtime_launcher,wd_q4_private_read as reader
from tools import wd_q4_agent_query as q4,wd_q4_public_child as child
class Encoder:
 def query(self,text):
  v=[0.0]*64
  for t in re.findall(r'[a-z0-9]{2,}',text.lower()):v[int(hashlib.sha256(t.encode()).hexdigest(),16)%64]+=1
  n=sum(x*x for x in v)**0.5 or 1.0
  return np.array([x/n for x in v],dtype=np.float32)
reader.ENCODER_FACTORY=lambda **kw:Encoder()
reader.bind_runtime_encoder=lambda *a,**kw:None
q4._utc_now=lambda:datetime.datetime(2026,11,3,tzinfo=datetime.timezone.utc)
def launcher(*,query,**kw):
 q=json.loads(query)
 r=reader._private_read_locked(DIRECTORY,DESCRIPTOR,DIGEST,q['situation'],q['time'],r'C:\\fake\\synthetic','a'*40)
 return runtime_launcher._validate_output(json.dumps(r).encode(),0,DIGEST)
q4._invoke_launcher=launcher
def bootstrap_public_imports(**kw):return {'status':'MODULES_IMPORTED','child':child}
'''
    (directory,built,decision,snapshot,_,args), fleet_bytes = canonical_card_snapshot
    constants={'SITE':str(site),'ROOT':str(root),'DIRECTORY':str(directory),
               'DESCRIPTOR':str(directory/'descriptor.json'),'DIGEST':built['descriptor_sha256']}
    shim='\n'.join(k+'='+repr(v) for k,v in constants.items())+'\n'+shim
    source_files={
        'tools/__init__.py':('__path__.append('+repr(str(root/'tools'))+')\n').encode(),
        'tools/wd_q4_public_outer.py':shim.encode(),
        'tools/wd_q4_public_child.py':(root/'tools/wd_q4_public_child.py').read_bytes(),
        'tools/wd_q4_public_query.py':(root/'tools/wd_q4_public_query.py').read_bytes(),
        'tools/wd_q4_agent_query.py':(root/'tools/wd_q4_agent_query.py').read_bytes(),
        'tools/wd_q4_secure_transport.py':(root/'tools/wd_q4_secure_transport.py').read_bytes(),
        'tools/wd_q4_staged_fixed_backend.py':(root/'tools/wd_q4_staged_fixed_backend.py').read_bytes(),
        'sandbox/mind/step2/runtime_manifest.py':(root/'sandbox/mind/step2/runtime_manifest.py').read_bytes(),
    }
    for relative,raw in source_files.items():
        bundle['put']('tools-bootstrap/'+relative,raw)
        if relative not in package['python_files']:package['python_files'].append(relative)
    bundle['put']('bridge-code-files.json',json.dumps(package).encode())
    bundle['config']['code_manifest_sha256']=bundle['files']['bridge-code-files.json']
    bundle['config']['python_path']=str(python);bundle['config']['python_sha256']=_sha(python.read_bytes())
    bundle['put']('wd-fleet.json',fleet_bytes)
    manifest={'schema_version':1,'source_commit':module.COMMIT,'files':bundle['files'],
       'bridge_code_package':{'python_executable':str(python),'python_executable_sha256':_sha(python.read_bytes())}}
    manifest_raw=json.dumps(manifest).encode();(bundle['root']/'deployment-manifest.json').write_bytes(manifest_raw)
    manifest_sha=_sha(manifest_raw); code_root=bundle['root']/'tools-bootstrap'
    scratch=tmp_path/'scratch';scratch.mkdir();lock=tmp_path/'read.lock'
    config=dataclasses.asdict(rt.config(directory,built['descriptor_sha256']))
    config.pop('interpreter_identity')  # Not part of the wrapper's closed public config.
    config.update(interpreter=str(python),interpreter_sha256=_sha(python.read_bytes()),
        bootstrap=str(code_root/'sandbox/mind/step2/runtime_bootstrap.py'),
        bootstrap_sha256=bundle['files']['tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py'],
        verifier_sha256=_sha((code_root/'sandbox/mind/step2/runtime_manifest.py').read_bytes()),
        runtime_root=str(runtime),runtime_manifest=str(runtime_manifest),runtime_manifest_sha256=generated['manifest_sha256'],
        runtime_generation='f'*40,runtime_revision='a'*40,lock_file=str(lock),timeout=30,scratch_root=str(scratch))
    data={'pointer':snapshot['pointer_bytes'],'policy':args['policy_bytes'],'roster':args['roster_bytes'],
          'public_policy':fixed._canonical({'schema':'wd.mind-public-status-policy.v1',
              'public_tasks':['mind-public-knowledge-v1'],'public_types':['status']}),
          'card_decision':args['decision_bytes'],'card_policy':args['card_policy_bytes'],
          'memory':(directory/'memory.json').read_bytes(),'projection':(directory/'projection.json').read_bytes(),
          'descriptor':(directory/'descriptor.json').read_bytes()}
    public={'schema':'wd.q4-public-wrapper-plan.v3','root':str(directory.parent),'config':config,
            'source_path':str(args['source_path']),'generation_path':str(args['generation_path']),
            'end_cursor':dataclasses.asdict(args['end_cursor']),
            'source_anchor_sha256':decision['source_prefix']['sha256'],
            'encoder_sha256':_encoder_digest(json.loads(data['descriptor'])['encoder'])}
    for name in ('pointer','policy','roster','public_policy','card_decision','card_policy'):
        path=tmp_path/('nested-'+name+'.json');path.write_bytes(data[name])
        public[name+'_path']=str(path);public[name+'_sha256']=_sha(data[name])
    public_path=tmp_path/'nested-public-plan.json';public_path.write_bytes(fixed._canonical(public))
    fixed_bundle=fixed.fixed_card_bundle(enabled=True,**data)
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    values=staged_args();peer=code_root/'tools/wd_q4_secure_transport.py'
    values.update(interpreter_sha256=_sha(python.read_bytes()),peer_code_sha256=_sha(peer.read_bytes()),
        verifier_sha256=config['verifier_sha256'],runtime_manifest_sha256=generated['manifest_sha256'],
        bundle_sha256=_sha(fixed_bundle),bundle_bytes=len(fixed_bundle),runtime_root=str(runtime),
        runtime_manifest_path=str(runtime_manifest),runtime_generation='f'*40,runtime_revision='a'*40,
        expected_pointer_sha256=public['pointer_sha256'],policy_sha256=public['policy_sha256'],
        roster_sha256=public['roster_sha256'],expected_public_policy_sha256=public['public_policy_sha256'],
        expected_source_sha256=public['source_anchor_sha256'],expected_encoder_sha256=public['encoder_sha256'],
        approved_tasks=['mind-public-knowledge-v1'],card_decision_sha256=public['card_decision_sha256'],
        card_policy_sha256=public['card_policy_sha256'],bundle_root=str(bundle['root']),bundle_commit=module.COMMIT,
        wrapper_path=str(bundle['root']/'Invoke-WdQ4Query.ps1'),wrapper_sha256=_sha((bundle['root']/'Invoke-WdQ4Query.ps1').read_bytes()),
        deployment_manifest_sha256=manifest_sha,public_plan_path=str(public_path),public_plan_sha256=_sha(public_path.read_bytes()),
        host_sha256=_sha(host.read_bytes()),backend_sha256=_sha((code_root/'tools/wd_q4_staged_fixed_backend.py').read_bytes()))
    plan=producer.build_staged_fixed_plan(enabled=True,**values)
    spec=importlib.util.spec_from_file_location('nested_actual_fixed_parent',peer)
    actual=importlib.util.module_from_spec(spec);spec.loader.exec_module(actual)
    guard,marker=_synthetic_guard(tmp_path/'nested-guard','codex-lead-1');context=guard._owner_context()
    query=json.dumps({'situation':'merged green pr 2046 genesis','time':'2026-11-02T00:00:00Z'}).encode()
    result,exit_code=actual.run_fixed_peer(executable=str(python),expected_interpreter_sha256=_sha(python.read_bytes()),
        interpreter_max_bytes=128*1024*1024,plan_bytes=plan['plan_bytes'],expected_plan_sha256=plan['plan_sha256'],
        bundle_bytes=fixed_bundle,runtime_manifest_bytes=runtime_manifest.read_bytes(),query_bytes=query,cwd=str(scratch),
        timeout_seconds=180,max_output_bytes=32768,public_snapshot_enabled=True,_owner_guard=guard,_owner_context=context)
    answer=json.loads(result)
    assert exit_code==0 and answer['status']=='ANSWER'
    assert answer['freshness']=='UNKNOWN' and answer['completeness']=='UNKNOWN'
    context._require_done(('attestation','transport'))
    assert marker.is_file()  # adapter and outer owner were not exercised.

    # A fresh guard and separate call exercise the native Job timeout path.
    # The wrappers only observe actual OS operations; none simulates cleanup.
    timeout_guard, timeout_marker = _synthetic_guard(tmp_path/'timeout-guard','codex-lead-1')
    timeout_context = timeout_guard._owner_context()
    observations = []
    originals = {}
    for name in ('_create_process_in_job', '_wait_job_empty', '_wait_child_exit'):
        original = getattr(actual, name)
        originals[name] = original
        def observe(*args, _name=name, _original=original, **kwargs):
            outcome = _original(*args, **kwargs)
            observations.append((_name, outcome if _name != '_create_process_in_job' else True))
            return outcome
        setattr(actual, name, observe)
    try:
        with pytest.raises(TimeoutError):
            actual.run_fixed_peer(executable=str(python),expected_interpreter_sha256=_sha(python.read_bytes()),
                interpreter_max_bytes=128*1024*1024,plan_bytes=plan['plan_bytes'],expected_plan_sha256=plan['plan_sha256'],
                bundle_bytes=fixed_bundle,runtime_manifest_bytes=runtime_manifest.read_bytes(),query_bytes=query,cwd=str(scratch),
                timeout_seconds=2,max_output_bytes=32768,public_snapshot_enabled=True,
                _owner_guard=timeout_guard,_owner_context=timeout_context)
    finally:
        for name, original in originals.items():
            setattr(actual, name, original)
    assert ('_create_process_in_job', True) in observations
    assert ('_wait_job_empty', True) in observations and ('_wait_child_exit', True) in observations
    assert timeout_marker.is_file() and timeout_guard._registry._uncertain
    assert not actual._QUARANTINE

    # Assemble the full adapter/session contract from actual canonical fleet,
    # C2 and binding producers. Only the Python module-location seam is set to
    # the already pinned candidate peer; no session/owner result is simulated.
    import datetime
    from tests.tools import test_wd_q4_lane_caller_adapter as adapter_fixture
    from tools import wd_q4_lane_binding_producer, wd_q4_guard_completion, wd_q4_lane_caller_adapter
    wrapper_bytes=(bundle['root']/'Invoke-WdQ4Query.ps1').read_bytes()
    binding=wd_q4_lane_binding_producer.build_staged_lane_binding(enabled=True,expected_commit=module.COMMIT,
        manifest_bytes=manifest_raw,expected_manifest_sha256=manifest_sha,fleet_bytes=fleet_bytes,
        expected_fleet_sha256=_sha(fleet_bytes),wrapper_bytes=wrapper_bytes,expected_wrapper_sha256=_sha(wrapper_bytes),
        public_plan_bytes=public_path.read_bytes(),expected_public_plan_sha256=_sha(public_path.read_bytes()),
        pointer_bytes=data['pointer'],policy_bytes=data['policy'],roster_bytes=data['roster'],
        public_policy_bytes=data['public_policy'],card_decision_bytes=data['card_decision'],card_policy_bytes=data['card_policy'])
    expected_binding=json.loads(binding['binding_bytes'])['binding']
    inputs=dict(fleet_bytes=fleet_bytes,expected_fleet_sha256=_sha(fleet_bytes),binding_bytes=binding['binding_bytes'],
        expected_binding_sha256=binding['binding_sha256'],bundle_root=str(bundle['root']),public_plan_path=str(public_path),
        executable=str(python),expected_interpreter_sha256=_sha(python.read_bytes()),interpreter_max_bytes=128*1024*1024,
        fixed_plan_bytes=plan['plan_bytes'],expected_fixed_plan_sha256=plan['plan_sha256'],fixed_bundle_bytes=fixed_bundle,
        runtime_manifest_bytes=runtime_manifest.read_bytes(),cwd=str(scratch),timeout_seconds=180,max_output_bytes=32768)
    descriptor=adapter_fixture.descriptor_for(inputs,expected_binding=expected_binding)
    fleet_document=json.loads(fleet_bytes)
    agent=next(x for x in fleet_document['lanes'] if x['agent']=='codex-lead-1')
    identity=adapter_fixture.identity_for(descriptor,agent_uuid=agent['agent_uuid'],
        worktree=str(tmp_path),cwd=str(scratch))
    state=Path(fleet_document['runtime_root'])/'shared'/'.codex-audit'/'codex-lead-1'/'q4-caller-state'
    state.mkdir(parents=True)
    full_guard=wd_q4_guard_completion.acquire_guard(fleet_bytes=fleet_bytes,expected_fleet_sha256=_sha(fleet_bytes),
        lane='codex-lead-1',identity_sha256=_sha(identity),descriptor_sha256=_sha(descriptor))
    full_context=full_guard._owner_context()
    monkeypatch.chdir(scratch)
    monkeypatch.setattr(bound, 'fixed', actual)
    adapter_result=wd_q4_lane_caller_adapter.call_lane_session(enabled=True,identity_bytes=identity,
        expected_identity_sha256=_sha(identity),descriptor_bytes=descriptor,expected_descriptor_sha256=_sha(descriptor),
        situation='merged green pr 2046 genesis',time=datetime.datetime(2026,11,2,tzinfo=datetime.timezone.utc),
        inputs=inputs,_owner_guard=full_guard,_owner_context=full_context)
    assert adapter_result['status']=='VALIDATED' and adapter_result['inner_status']=='ANSWER', adapter_result
    full_context._require_done(('adapter','attestation','transport'))
    pending=state/'codex-lead-1.pending.json'
    assert pending.is_file()
    # This test is the cooperative outer owner, after the real inner owners.
    full_context._finish()
    full_guard.release(full_guard._registry._issue())
    assert not pending.exists()
