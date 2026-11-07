"""The staged C2 wire plan is closed and cannot launch during this slice."""
import os

import pytest

from tools import wd_q4_secure_transport as fixed

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
