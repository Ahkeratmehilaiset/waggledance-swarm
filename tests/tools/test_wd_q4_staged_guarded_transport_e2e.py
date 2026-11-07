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
from tests.tools.test_wd_q4_real_card_snapshot_e2e import real_fixture, real_snapshot
from tools import wd_q4_bound_lane_transport as bound, wd_q4_secure_transport as fixed
from tools.wd_q4_public_query import _encoder_digest


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
