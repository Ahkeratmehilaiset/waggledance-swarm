"""Synthetic-only, closed provenance diagnostics over independently pinned bytes."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import pytest
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_projection as projection
from tests.tools.test_wd_q4_canonical_ref_verifier import case, _prefix_args


def sha(raw): return hashlib.sha256(raw).hexdigest()

def arguments(case, events):
    event, setup=case
    base=_prefix_args(setup(events))
    tasks, roster=capture.strict_pins(base['policy_bytes'],base['policy_sha256'],base['roster_bytes'],base['roster_sha256'])
    projected=projection.project_events([event],approved_tasks=tasks,roster=roster,exclude_review_signal=True)
    raw=json.dumps(projected,sort_keys=True,indent=1).encode('utf-8')
    record=projected['records'][0]
    return {key:value for key,value in base.items() if key!='record'} | {
        'projection_bytes':raw, 'expected_projection_sha256':sha(raw),
        'ref_sha256':sha(record['provenance']['event_ref'].encode()),
        'doc_id_sha256':sha(record['record_id'].encode())}


def test_default_off_never_reads_inputs():
    from tools import wd_q4_provenance_diagnostic as d
    assert d.diagnose(projection_bytes=object(),source_path=object())=={'status':'OFF'}

def test_pinned_match_is_closed_and_does_not_leak_private_material(case):
    from tools import wd_q4_provenance_diagnostic as d
    event,_=case
    out=d.diagnose(**arguments(case,[event]))
    assert out=={'status':'MATCH_WITHIN_PREFIX'}
    assert 'private-canary' not in json.dumps(out)

@pytest.mark.parametrize('fault',['projection_hash','projection_tamper','ref','doc','prefix','policy',
                                  'public_policy','roster','foreign_cursor','missing_source','missing_generation'])
def test_mismatch_is_unknown_without_leaking_inputs(case,fault):
    from tools import wd_q4_provenance_diagnostic as d
    event,_=case; args=arguments(case,[event])
    if fault=='projection_hash': args['expected_projection_sha256']='f'*64
    elif fault=='projection_tamper': args['projection_bytes']+=b' '
    elif fault=='ref': args['ref_sha256']='f'*64
    elif fault=='doc': args['doc_id_sha256']='f'*64
    elif fault=='prefix': args['expected_prefix_sha256']='f'*64
    elif fault=='policy': args['policy_sha256']='f'*64
    elif fault=='public_policy': args['public_policy_sha256']='f'*64
    elif fault=='roster': args['roster_sha256']='f'*64
    elif fault=='foreign_cursor': args['end_cursor']=replace(args['end_cursor'],generation='foreign-generation')
    elif fault=='missing_source': Path(args['source_path']).unlink()
    else: Path(args['generation_path']).unlink()
    assert d.diagnose(**args)=={'status':'UNKNOWN'}

def test_absent_or_ambiguous_source_is_unknown(case):
    from tools import wd_q4_provenance_diagnostic as d
    event,_=case
    assert d.diagnose(**arguments(case,[dict(event,status='done')]))=={'status':'UNKNOWN'}
    other=dict(event,ts_utc='2026-11-06T18:05:43Z')
    assert d.diagnose(**arguments(case,[event,other]))=={'status':'UNKNOWN'}

def test_invalid_projection_pin_does_not_open_source(case,monkeypatch):
    from tools import wd_q4_provenance_diagnostic as d
    from tools import wd_q4_canonical_ref_verifier as verifier
    event,_=case; args=arguments(case,[event]); args['expected_projection_sha256']='0'*64
    monkeypatch.setattr(verifier,'verify_within_prefix',lambda **kw: pytest.fail('source reader reached'))
    assert d.diagnose(**args)=={'status':'UNKNOWN'}

def test_partial_tail_is_unknown_even_when_prefix_hash_matches(case):
    from tools import wd_q4_provenance_diagnostic as d
    event,_=case; args=arguments(case,[event]); source=Path(args['source_path'])
    source.write_bytes(source.read_bytes()+b'{"partial":')
    args['end_cursor']=replace(args['end_cursor'],offset=source.stat().st_size)
    args['expected_prefix_sha256']=sha(source.read_bytes())
    assert d.diagnose(**args)=={'status':'UNKNOWN'}

def test_private_source_records_never_appear_in_result(case):
    from tools import wd_q4_provenance_diagnostic as d
    event,_=case
    private=dict(event,message='synthetic-private-canary',paths=['C:\\synthetic-private'])
    result=d.diagnose(**arguments(case,[private,event]))
    assert result=={'status':'MATCH_WITHIN_PREFIX'}
    assert 'private' not in json.dumps(result)
