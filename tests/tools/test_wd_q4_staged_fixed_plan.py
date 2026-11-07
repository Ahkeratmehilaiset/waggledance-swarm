"""The staged C2 wire plan is closed and cannot launch during this slice."""
import os
from pathlib import Path

import pytest

from tools import wd_q4_secure_transport as fixed
from tools import wd_q4_fixed_plan_producer as producer
from tests.tools.test_wd_q4_fixed_plan_producer import kwargs as base_args

pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows fixed-peer paths')


@pytest.fixture
def plan():
    return {'schema':'wd.q4-fixed-plan.v3','backend':'staged-card-wrapper-v1',
            **{key:'a'*64 for key in ('interpreter_sha256','peer_code_sha256','runtime_manifest_sha256',
                'bundle_sha256','verifier_sha256','expected_pointer_sha256','policy_sha256',
                'roster_sha256','expected_public_policy_sha256','expected_source_sha256',
                'expected_encoder_sha256','card_decision_sha256','card_policy_sha256',
                'wrapper_sha256','deployment_manifest_sha256','public_plan_sha256','host_sha256','backend_sha256')},
            'bundle_bytes':100, 'runtime_root':r'C:\fixture\runtime',
            'runtime_manifest_path':r'C:\fixture\runtime-manifest.json',
            'runtime_generation':'a'*40,'runtime_revision':'b'*40,'approved_tasks':['synthetic-task'],
            'bundle_root':r'C:\fixture\bundle','bundle_commit':'c'*40,
            'wrapper_path':r'C:\fixture\bundle\Invoke-WdQ4Query.ps1',
            'public_plan_path':r'C:\fixture\public-plan.json'}


def test_plan_is_closed_and_no_code_argument(plan):
    assert fixed._fixed_plan(fixed._canonical(plan)) == plan
    with pytest.raises(fixed._Refused):
        fixed._fixed_plan(fixed._canonical({**plan, 'code':'arbitrary'}))


@pytest.mark.parametrize('key,value', [
    ('card_decision_sha256', 'x'*64), ('card_policy_sha256', 'a'*63),
    ('host_sha256', 'A'*64), ('bundle_commit', 'c'*39),
    ('wrapper_path', r'C:\foreign\Invoke-WdQ4Query.ps1'),
    ('public_plan_path', 'relative.json'),
    ('bundle_root', r'C:\fixture\bundle\..\bundle'),
    ('public_plan_path', r'C:\fixture\public.json:stream'),
    ('public_plan_path', r'\\server\share\public.json'),
])
def test_invalid_pin_or_path_is_refused(plan, key, value):
    plan[key] = value
    with pytest.raises(fixed._Refused):
        fixed._fixed_plan(fixed._canonical(plan))


def test_parsed_new_schema_cannot_fall_through_to_old_backend(plan):
    raw = fixed._canonical(plan)
    with pytest.raises(fixed._Refused):
        fixed._run_fixed_peer_impl(None,None,None,raw,None,None,None,b'{}',None,1,1024,True)


def test_fixed_card_bundle_is_explicit_closed_and_roundtrips():
    names = ('pointer','policy','roster','public_policy','card_decision','card_policy',
             'memory','projection','descriptor')
    data = {name: ('synthetic-' + name).encode() for name in names}
    assert fixed.fixed_card_bundle(**data) == {'status':'OFF'}
    raw = fixed.fixed_card_bundle(enabled=True, **data)
    assert type(raw) is bytes and fixed._fixed_card_bundle_decode(raw) == data
    with pytest.raises(fixed._Refused):
        fixed._fixed_card_bundle_decode(raw+b' ')


def test_fixed_card_bundle_refuses_missing_extra_or_empty():
    names = ('pointer','policy','roster','public_policy','card_decision','card_policy',
             'memory','projection','descriptor')
    data = {name: b'synthetic' for name in names}
    with pytest.raises((TypeError, fixed._Refused)):
        fixed.fixed_card_bundle(enabled=True, **{k:v for k,v in data.items() if k != 'pointer'})
    with pytest.raises((TypeError, fixed._Refused)):
        fixed.fixed_card_bundle(enabled=True, **{**data,'extra':b'x'})
    with pytest.raises(fixed._Refused):
        fixed.fixed_card_bundle(enabled=True, **{**data,'card_policy':b''})


def staged_args():
    return {**base_args(), **{key:'a'*64 for key in ('card_decision_sha256','card_policy_sha256',
        'wrapper_sha256','deployment_manifest_sha256','public_plan_sha256','host_sha256','backend_sha256')},
        'bundle_root':r'C:\fixture\bundle','bundle_commit':'c'*40,
        'wrapper_path':r'C:\fixture\bundle\Invoke-WdQ4Query.ps1',
        'public_plan_path':r'C:\fixture\public-plan.json'}


def test_staged_producer_outputs_plan_accepted_by_fixed_parser():
    result=producer.build_staged_fixed_plan(enabled=True, **staged_args())
    plan=fixed._fixed_plan(result['plan_bytes'])
    assert plan['backend']=='staged-card-wrapper-v1'
    assert plan['card_policy_sha256']==staged_args()['card_policy_sha256']
    assert result['plan_sha256']==fixed._sha(result['plan_bytes'])


def test_staged_producer_is_disabled_by_default():
    with pytest.raises(producer.FixedPlanRefused, match='^disabled$'):
        producer.build_staged_fixed_plan(**staged_args())


def test_staged_child_validates_bundle_before_ack_or_query(plan, monkeypatch):
    """Protocol-order seam only; no real Job or owner completion claimed."""
    bundle=b'synthetic-c2-bundle'
    plan.update(bundle_sha256=fixed._sha(bundle),bundle_bytes=len(bundle))
    nonce='e'*32
    challenge=fixed._canonical({'schema':'wd.q4-fixed-challenge.v1','nonce':nonce})
    frames=[fixed._canonical(plan),challenge,bundle,b'synthetic-query']
    state={'validated':False,'ack':False}
    class Backend:
        def validate_bundle(self,raw):
            assert raw==bundle and not state['ack']
            state['validated']=True
        def __call__(self,_plan,_raw,_bundle,query):
            assert state['validated'] and state['ack'] and query==b'synthetic-query'
            return b'{}'
    monkeypatch.setattr(fixed,'_fixed_child_backend',lambda *_:Backend())
    def read(_limit):
        frame=frames.pop(0)
        if frame==b'synthetic-query':assert state['validated'] and state['ack']
        return frame
    def write(raw):
        doc=fixed.json.loads(raw)
        if doc['status']=='BUNDLE_VERIFIED':
            assert state['validated']
            state['ack']=True
    result=fixed._fixed_child_protocol(read,write,plan['peer_code_sha256'])
    assert fixed._fixed_unwrap(nonce,b'synthetic-query',result,1024)==b'{}'


def test_peer_limit_covers_exact_lf_and_crlf_checkout_bytes():
    source=Path(fixed.__file__).read_bytes().replace(b'\r\n',b'\n')
    assert len(source) <= fixed._PEER_CODE_LIMIT
    assert len(source.replace(b'\n',b'\r\n')) <= fixed._PEER_CODE_LIMIT
    assert 'source.read(%d)' % (fixed._PEER_CODE_LIMIT+1) in fixed._FIXED_BOOTSTRAP
    assert 'len(raw)>%d' % fixed._PEER_CODE_LIMIT in fixed._FIXED_BOOTSTRAP


@pytest.mark.parametrize('change', [
    {'backend_sha256':'A'*64}, {'wrapper_path':r'C:\foreign\Invoke-WdQ4Query.ps1'},
    {'public_plan_path':r'C:\fixture\public.json:stream'}, {'bundle_commit':'b'*39},
])
def test_staged_producer_fails_closed_on_wrong_pins_or_paths(change):
    with pytest.raises(producer.FixedPlanRefused):
        producer.build_staged_fixed_plan(enabled=True, **{**staged_args(),**change})
