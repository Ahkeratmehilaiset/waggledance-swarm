"""Synthetic checkpoint -> fixed bundle -> bound observed log position."""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from dataclasses import replace
from pathlib import Path
import pytest
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools import test_wd_q4_genesis_bootstrap as genesis_fixture
from tests.tools.test_wd_q4_continuation_adopt import actual_continuation
from tests.tools import test_wd_q4_continuation_adopt as adopt_fixture
from tools.wd_q4_roster_from_fleet import build_roster_snapshot
from tests.tools.test_wd_q4_canonical_ref_verifier import case, _prefix_args


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
        'generation_path':args['generation_path'],'max_prefix_bytes':1024*1024*1024}


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
    args['max_lag_bytes']=4096
    assert d.observe(**args)['status']=='BOUNDED_BY_LOG_POSITION'
    with path.open('ab') as stream: stream.write(b'{"partial":')
    assert d.observe(**args)=={'status':'UNKNOWN'}

def test_current_pointer_change_does_not_replace_pinned_bundle_watermark(snapshot_case,monkeypatch):
    from tools import wd_q4_snapshot_position_diagnostic as d
    from tools import wd_q4_snapshot_lag as lag
    original=lag.check_snapshot_position
    def move_pointer(**kw):
        out=original(**kw)
        (Path(snapshot_case['checkpoint_root'])/'q4-latest.json').write_bytes(b'{}')
        return out
    monkeypatch.setattr(lag,'check_snapshot_position',move_pointer)
    assert d.observe(**snapshot_case)=={'status':'BOUNDED_BY_LOG_POSITION','freshness':'UNKNOWN',
                                        'coverage':'UNKNOWN','source_authentication':'UNKNOWN'}

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
    from tools import wd_q4_secure_transport as transport
    wrong=snapshot_arguments(args); parts=transport._fixed_bundle_decode(wrong['bundle_bytes'])
    pointer['watermark']=pointer['cursor_end']
    parts['pointer_bytes']=json.dumps(pointer,sort_keys=True,separators=(',',':')).encode()
    wrong['expected_pointer_sha256']=sha(parts['pointer_bytes'])
    wrong['bundle_bytes']=transport.fixed_snapshot_bundle(**parts)
    wrong['expected_bundle_sha256']=sha(wrong['bundle_bytes'])
    assert d.observe(**wrong)=={'status':'UNKNOWN'}

@pytest.mark.parametrize('filename',['descriptor.json','memory.json','projection.json','build_receipt.json'])
def test_bundle_active_artifact_must_match_pointer_even_with_new_bundle_hash(snapshot_case,monkeypatch,filename):
    from tools import wd_q4_snapshot_position_diagnostic as d
    from tools import wd_q4_secure_transport as transport
    from tools import wd_q4_snapshot_lag as lag
    args=dict(snapshot_case); parts=transport._fixed_bundle_decode(args['bundle_bytes'])
    parts['active_artifact_bytes'][filename]+=b' '
    args['bundle_bytes']=transport.fixed_snapshot_bundle(**parts); args['expected_bundle_sha256']=sha(args['bundle_bytes'])
    monkeypatch.setattr(lag,'check_snapshot_position',lambda **kw: pytest.fail('unbound artifact reached source'))
    assert d.observe(**args)=={'status':'UNKNOWN'}

@pytest.mark.parametrize('argument,value',[('max_lag_bytes',1),('max_lag_bytes',64),
                                             ('max_prefix_bytes',65536),('max_lag_bytes',True)])
def test_arbitrary_probe_thresholds_are_refused_before_reading_source(snapshot_case,monkeypatch,argument,value):
    from tools import wd_q4_snapshot_position_diagnostic as d
    from tools import wd_q4_snapshot_lag as lag
    args=dict(snapshot_case); args[argument]=value
    monkeypatch.setattr(lag,'check_snapshot_position',lambda **kw: pytest.fail('arbitrary probe reached source'))
    assert d.observe(**args)=={'status':'UNKNOWN'}

def fake_bundle_args(case):
    """All pointer and artifact bytes are synthetic; no FAISS dependency."""
    from sandbox.mind.step2 import shared_index_pointer as pointer
    from tools import wd_q4_secure_transport as transport
    event,setup=case; source=_prefix_args(setup([event])); cursor=asdict(source['end_cursor'])
    start=dict(cursor,offset=0)
    active={name:b'{}' for name in pointer.ARTIFACTS.values()}
    value={'schema':pointer.PREFIX_SCHEMA,'generation':'synthetic',
           'artifacts':{key:sha(active[name]) for key,name in pointer.ARTIFACTS.items()},
           'policy_sha256':source['policy_sha256'],'roster_sha256':source['roster_sha256'],
           'anchor_sha256':'a'*64,'cursor_start':start,'cursor_end':cursor,'watermark':cursor,
           'empty_advance':None,'previous':None,'previous_pointer_sha256':None,
           'prefix_start_sha256':sha(b''),'prefix_watermark_sha256':source['expected_prefix_sha256']}
    raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    bundle=transport.fixed_snapshot_bundle(pointer_bytes=raw,policy_bytes=source['policy_bytes'],
        roster_bytes=source['roster_bytes'],public_policy_bytes=source['public_policy_bytes'],
        active_artifact_bytes=active,history_bytes={},cell_snapshots={})
    return {'enabled':True,'expected_pointer_sha256':sha(raw),'bundle_bytes':bundle,
            'expected_bundle_sha256':sha(bundle),'policy_sha256':source['policy_sha256'],
            'roster_sha256':source['roster_sha256'],'public_policy_sha256':source['public_policy_sha256'],
            'log_path':source['source_path'],'generation_path':source['generation_path'],
            'max_prefix_bytes':1024*1024*1024}

def test_snapshot_observation_in_python_without_faiss(case):
    import base64
    args=fake_bundle_args(case)
    args['bundle_bytes']=base64.b64encode(args['bundle_bytes']).decode('ascii')
    code='''import base64,json,sys\nroot=sys.argv[1];sys.path.insert(0,root)\nfrom tools import wd_q4_snapshot_position_diagnostic as d\na=json.load(sys.stdin);a["bundle_bytes"]=base64.b64decode(a["bundle_bytes"])\nr=d.observe(**a)\nassert "faiss" not in sys.modules\nprint(json.dumps(r,sort_keys=True))\n'''
    env={k:v for k,v in os.environ.items() if not k.startswith(('WD_','AGENT_BRIDGE_','PYTHON'))}
    proc=subprocess.run([sys.executable,'-I','-S','-B','-c',code,str(Path.cwd())],
        input=json.dumps(args),text=True,capture_output=True,env=env,timeout=30)
    assert proc.returncode==0,proc.stderr
    assert json.loads(proc.stdout)=={'status':'BOUNDED_BY_LOG_POSITION','freshness':'UNKNOWN',
                                     'coverage':'UNKNOWN','source_authentication':'UNKNOWN'}
