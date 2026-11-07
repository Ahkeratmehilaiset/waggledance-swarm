"""Real-format, fabricated cards require the exact C2-verified route decision."""
import dataclasses
import hashlib
import json

import pytest

from tests.tools.test_wd_q4_staged_guarded_transport_e2e import canonical_card_snapshot, real_fixture, real_snapshot
from tools import wd_q4_lane_query as lane


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def candidate(canonical_card_snapshot, tmp_path):
    (directory, _built, decision, snapshot, _, args), _fleet = canonical_card_snapshot
    public = json.dumps({'schema':'wd.mind-public-status-policy.v1',
                         'public_tasks':['mind-public-knowledge-v1'],'public_types':['status']}).encode()
    data = {'pointer':snapshot['pointer_bytes'], 'policy':args['policy_bytes'], 'roster':args['roster_bytes'],
            'public_policy':public, 'card_decision':args['decision_bytes'], 'card_policy':args['card_policy_bytes']}
    plan = {'root':str(directory.parent), 'source_path':args['source_path'],
            'generation_path':args['generation_path'], 'end_cursor':dataclasses.asdict(args['end_cursor']),
            'source_anchor_sha256':decision['source_prefix']['sha256']}
    for key, raw in data.items():
        path=tmp_path/('lane-'+key+'.json'); path.write_bytes(raw)
        plan[key+'_path']=str(path); plan[key+'_sha256']=sha(raw)
    projection=(directory/'projection.json').read_bytes()
    binding={key:plan[key] for key in ('pointer_sha256','policy_sha256','roster_sha256',
        'public_policy_sha256','card_decision_sha256','card_policy_sha256','source_anchor_sha256')}
    binding['projection_sha256']=sha(projection)
    return plan, binding, projection, data


def test_real_projection_rederived_only_after_c2_validates_decision(candidate):
    plan, binding, projection, _ = candidate
    decision=lane._checked_staged_card_decision(plan,binding,projection)
    records=lane._card_records(projection,binding['projection_sha256'],{'mind-public-knowledge-v1'},
                               _real_decision=decision)
    assert records


@pytest.mark.parametrize('fault',['decision_pin','changed_decision','changed_source','projection_pin'])
def test_c2_rejects_changed_or_wrong_decision_and_projection(candidate, fault):
    plan, binding, projection, _ = candidate
    if fault=='decision_pin': binding['card_decision_sha256']='0'*64
    if fault=='changed_decision':
        from pathlib import Path
        path=Path(plan['card_decision_path']); path.write_bytes(path.read_bytes()+b' ')
    if fault=='changed_source':
        from pathlib import Path
        path=Path(plan['source_path']); raw=path.read_bytes(); path.write_bytes(b' '+raw[1:])
    if fault=='projection_pin': binding['projection_sha256']='0'*64
    with pytest.raises(Exception):
        lane._checked_staged_card_decision(plan,binding,projection)
