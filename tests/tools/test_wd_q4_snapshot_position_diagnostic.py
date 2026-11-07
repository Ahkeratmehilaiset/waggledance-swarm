"""Synthetic checkpoint -> fixed bundle -> bound observed log position."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import pytest
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools import test_wd_q4_genesis_bootstrap as genesis_fixture
from tests.tools.test_wd_q4_continuation_adopt import actual_continuation
from tests.tools import test_wd_q4_continuation_adopt as adopt_fixture
from tools.wd_q4_roster_from_fleet import build_roster_snapshot


def sha(raw): return hashlib.sha256(raw).hexdigest()

@pytest.fixture
def snapshot_case(synthetic, monkeypatch):
    pytest.importorskip('faiss')
    genesis_fixture.test_actual_producer_checkpoint_and_partial_continuation(synthetic,monkeypatch)
    args,_=synthetic
    return snapshot_arguments(args)

def snapshot_arguments(args):
    from tools import wd_q4_genesis_snapshot_prepare as prepare
    root=Path(args['checkpoint_root']); pointer=(root/'q4-latest.json').read_bytes(); pin=sha(pointer)
    roster, roster_pin=build_roster_snapshot(args['fleet_bytes'],args['expected_fleet_sha256'],enabled=True)
    prepared=prepare.prepare_snapshot(enabled=True,checkpoint_root=str(root),expected_pointer_sha256=pin,
        policy_bytes=args['policy_bytes'],expected_policy_sha256=args['policy_sha256'],
        roster_bytes=roster,expected_roster_sha256=roster_pin,public_policy_bytes=args['public_policy_bytes'],
        expected_public_policy_sha256=args['public_policy_sha256'])
    return {'enabled':True,'checkpoint_root':str(root),'expected_pointer_sha256':pin,
        'bundle_bytes':prepared['snapshot_bytes'],'expected_bundle_sha256':prepared['snapshot_sha256'],
        'policy_sha256':args['policy_sha256'],'roster_sha256':roster_pin,
        'public_policy_sha256':args['public_policy_sha256'], 'log_path':args['source_path'],
        'generation_path':args['generation_path'],'max_prefix_bytes':65536}


def test_off_does_not_touch_inputs():
    from tools import wd_q4_snapshot_position_diagnostic as d
    assert d.observe(bundle_bytes=object(),log_path=object())=={'status':'OFF'}

def test_real_snapshot_and_log_end_are_bound_without_freshness_claim(snapshot_case):
    from tools import wd_q4_snapshot_position_diagnostic as d
    out=d.observe(**snapshot_case)
    assert out=={'status':'BOUNDED_BY_LOG_POSITION','freshness':'UNKNOWN',
                 'coverage':'UNKNOWN','source_authentication':'UNKNOWN'}
    assert snapshot_case['log_path'] not in json.dumps(out)

@pytest.mark.parametrize('field',['expected_pointer_sha256','expected_bundle_sha256','policy_sha256',
                                  'roster_sha256','public_policy_sha256'])
def test_wrong_external_pin_is_unknown_without_positive_observation(snapshot_case,field):
    from tools import wd_q4_snapshot_position_diagnostic as d
    args=dict(snapshot_case); args[field]='f'*64
    assert d.observe(**args)=={'status':'UNKNOWN'}

def test_source_append_and_partial_tail_never_prove_freshness(snapshot_case):
    from tools import wd_q4_snapshot_position_diagnostic as d
    args=dict(snapshot_case); path=Path(args['log_path'])
    with path.open('ab') as stream: stream.write(b'{"synthetic":true}\n')
    assert d.observe(**args)=={'status':'UNKNOWN'}
    args['max_lag_bytes']=64
    assert d.observe(**args)['status']=='BOUNDED_BY_LOG_POSITION'
    with path.open('ab') as stream: stream.write(b'{"partial":')
    assert d.observe(**args)=={'status':'UNKNOWN'}

def test_pointer_changed_during_observation_is_unknown(snapshot_case,monkeypatch):
    from tools import wd_q4_snapshot_position_diagnostic as d
    from tools import wd_q4_snapshot_lag as lag
    original=lag.check_snapshot_position
    def move_pointer(**kw):
        out=original(**kw)
        (Path(snapshot_case['checkpoint_root'])/'q4-latest.json').write_bytes(b'{}')
        return out
    monkeypatch.setattr(lag,'check_snapshot_position',move_pointer)
    assert d.observe(**snapshot_case)=={'status':'UNKNOWN'}

def test_other_source_with_identical_bytes_is_unknown(snapshot_case,tmp_path):
    from tools import wd_q4_snapshot_position_diagnostic as d
    args=dict(snapshot_case); source=Path(args['log_path']); other=tmp_path/'other.jsonl'
    other.write_bytes(source.read_bytes()); args['log_path']=str(other)
    assert d.observe(**args)=={'status':'UNKNOWN'}

def test_bundle_with_other_pointer_is_unknown_even_if_bundle_hash_is_correct(snapshot_case):
    from tools import wd_q4_snapshot_position_diagnostic as d
    from tools import wd_q4_secure_transport as transport
    args=dict(snapshot_case); parts=transport._fixed_bundle_decode(args['bundle_bytes'])
    parts['pointer_bytes']=b'{}'
    args['bundle_bytes']=transport.fixed_snapshot_bundle(**parts)
    args['expected_bundle_sha256']=sha(args['bundle_bytes'])
    assert d.observe(**args)=={'status':'UNKNOWN'}

def test_empty_advance_uses_watermark_without_claiming_coverage(synthetic,actual_continuation,monkeypatch):
    from tools import wd_q4_snapshot_position_diagnostic as d
    adopt_fixture.test_empty_advance_consumer_reproduction(actual_continuation,monkeypatch,'none')
    args,_=synthetic; root=Path(args['checkpoint_root'])
    pointer=json.loads((root/'q4-latest.json').read_bytes())
    assert pointer['empty_advance'] is not None and pointer['watermark'] != pointer['cursor_end']
    result=d.observe(**snapshot_arguments(args))
    assert result=={'status':'BOUNDED_BY_LOG_POSITION','freshness':'UNKNOWN',
                    'coverage':'UNKNOWN','source_authentication':'UNKNOWN'}
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
    from waggledance.core.bridge_log_reader import BridgeCursor
    real_accessor=checkpoint.read_verified_prefix_pointer
    def incorrect_cursor_end(*a,**kw):
        view, watermark=real_accessor(*a,**kw)
        return view,replace(watermark,cursor=BridgeCursor(**pointer['cursor_end']))
    monkeypatch.setattr(checkpoint,'read_verified_prefix_pointer',incorrect_cursor_end)
    assert d.observe(**snapshot_arguments(args))=={'status':'UNKNOWN'}
