# SPDX-License-Identifier: BUSL-1.1
"""Explicit inert pages; no filesystem, live bridge, gate or provider action."""
from copy import deepcopy

import pytest

from tools import wd_bridge_mind as core
from tools import wd_bridge_mind_rebuild as rebuild
from tools.bridge_compact_view import event_id

AS_OF = '2026-10-13T04:00:00Z'
IDENTITY = 'fixture-1'


def row(i, **changes):
    event = dict(agent='codex-tools-1', agent_uuid='7a8af68d-20bc-4598-9953-23c5dd98b102',
                 session_id='s1', run_id='r1', type='message', status='head_updated', task_id='task/1',
                 ts_utc='2026-10-13T03:59:00Z', payload={'pr':1900,'head':str(i)*40})
    event.update(changes)
    return {'ref':event_id(event), 'event':event}


def page(start, end, rows, eof=30, **source_changes):
    source = dict(log_identity=IDENTITY, generation=None, cursor_start=start, cursor_end=end,
                  event_count=len(rows), coverage='complete')
    source.update(source_changes)
    return dict(envelopes=rows, source=source, frozen_eof=eof)


def begin(eof=30, **kw):
    return rebuild.begin(log_identity=IDENTITY, generation=None, frozen_eof=eof, as_of_utc=AS_OF, **kw)


def test_three_page_frozen_eof_positive_and_no_premature_current_snapshot():
    # MUT_PREMATURE_COMPLETE: a complete PAGE is not a complete frozen log prefix.
    rows = [row(1), row(2), row(3)]
    state = begin()
    assert rebuild.receipt(state)['complete'] is False
    for index, item in enumerate(rows):
        state = rebuild.accept_page(state, page(index*10, (index+1)*10, [item]))
        receipt = rebuild.receipt(state)
        if index < 2:
            assert receipt['complete'] is False and receipt['current'] is False
            assert receipt['snapshot'] is None and receipt['source']['accepted_cursor'] == (index+1)*10
    receipt = rebuild.receipt(state)
    assert receipt['complete'] is True and receipt['current'] is True
    expected = core.full_rebuild(rows, source=page(0,30,rows)['source'], as_of_utc=AS_OF)
    assert core.canonical_bytes(receipt['snapshot']) == core.canonical_bytes(core.snapshot(expected, as_of_utc=AS_OF))
    assert core.state_bytes(state.core_state) == core.state_bytes(expected)
    assert receipt['authority']=='none' and receipt['source']['coverage']=='declared_complete_frozen_prefix'


def test_duplicate_and_late_events_keep_exact_full_rebuild_state_and_bytes():
    a, b = row(1), row(2, ts_utc='2026-10-13T03:58:00Z')
    rows = [a,a,b]
    state = begin()
    for index,item in enumerate(rows):
        state = rebuild.accept_page(state, page(index*10,(index+1)*10,[item]))
    expected = core.full_rebuild(rows,source=page(0,30,rows)['source'],as_of_utc=AS_OF)
    assert core.state_bytes(state.core_state)==core.state_bytes(expected)
    assert core.canonical_bytes(rebuild.receipt(state)['snapshot'])==core.canonical_bytes(core.snapshot(expected,as_of_utc=AS_OF))
    assert rebuild.receipt(state)['snapshot']['source']['duplicate_count']==1


@pytest.mark.parametrize('bad',[
    page(20,30,[row(2)]), page(0,10,[row(2)]), page(10,20,[row(2)],log_identity='changed-log-private-value'),
    page(10,20,[row(2)],generation='changed-generation-private-value'), page(10,20,[row(2)],eof=40),
    page(10,40,[row(2)]), page(10,20,[row(2)],event_count=2),
    page(10,20,[row(2)],coverage='partial'), page(10,20,[dict(row(2),ref='f'*64)]),
    page(10,20,[row(2,message='PRIVATE'+'_MARKER')]),
    page(10,20,[row(2,message='\\u0050RIVATE_MARKER')]),
    page(10,20,[dict(row(2),event={**row(2)['event'],'payload':{'nan':float('nan')}})]),
    page(10,20,[row(2, ts_utc='2026-10-13T04:01:00Z')]),
    page(10,10,[]),
])
def test_gap_rotation_partial_or_rejected_page_retains_good_cursor_and_caller_state(bad):
    previous = core.full_rebuild([row(9)],source=page(0,10,[row(9)])['source'],as_of_utc=AS_OF)
    previous_bytes = core.state_bytes(previous)
    state = rebuild.accept_page(begin(previous_good=previous),page(0,10,[row(1)]))
    good_bytes = core.state_bytes(state.core_state)
    refused = rebuild.accept_page(state,deepcopy(bad))
    receipt = rebuild.receipt(refused)
    assert receipt['complete'] is receipt['current'] is False and receipt['snapshot'] is None
    assert receipt['blocked']['active'] is True and receipt['blocked']['offset']==10
    assert receipt['source']['accepted_cursor']==10
    assert core.state_bytes(refused.core_state)==good_bytes
    assert core.state_bytes(refused.previous_good)==core.state_bytes(previous)==previous_bytes
    rendered=core.canonical_bytes(receipt).decode()
    assert 'PRIVATE'+'_MARKER' not in rendered
    assert 'changed-log-private-value' not in rendered and 'changed-generation-private-value' not in rendered
    assert rebuild.receipt(state)['blocked'] is None  # Caller state never mutated by refusal.


def test_empty_eof_explicit_final_page_and_partial_last_page_positive_retry():
    empty=begin(eof=0)
    assert rebuild.receipt(empty)['complete'] is False
    finished=rebuild.accept_page(empty,page(0,0,[],eof=0))
    assert rebuild.receipt(finished)['complete'] is True
    assert rebuild.receipt(finished)['snapshot']['source']['event_count']==0
    repeated=rebuild.accept_page(finished,page(0,0,[],eof=0))
    assert core.state_bytes(repeated.core_state)==core.state_bytes(finished.core_state)
    state=rebuild.accept_page(begin(),page(0,10,[row(1)]))
    state=rebuild.accept_page(state,page(10,20,[row(2)]))
    bad=rebuild.accept_page(state,page(20,30,[row(3)],coverage='partial'))
    assert rebuild.receipt(bad)['complete'] is False
    assert rebuild.receipt(bad)['source']['accepted_cursor']==20
    repaired=rebuild.accept_page(bad,page(20,30,[row(3)]))
    assert rebuild.receipt(repaired)['complete'] is True


def test_page_bounds_and_invalid_shape_block_without_any_current_output():
    for policy, bad in [(dict(max_page_events=1),page(0,10,[row(1),row(2)])),
                        (dict(max_page_bytes=10),page(0,10,[row(1)])), ({},None), ({},{'envelopes':[]})]:
        state=begin(**policy)
        result=rebuild.receipt(rebuild.accept_page(state,bad))
        assert result['blocked'] and result['source']['accepted_cursor']==0
        assert result['complete'] is False and result['snapshot'] is None


def test_initial_policy_is_explicit_finite_and_fail_closed():
    for change in ({'frozen_eof':True},{'frozen_eof':-1},{'frozen_eof':2**63},
                   {'log_identity':'UNKNOWN'},{'generation':'UNKNOWN'}, {'as_of_utc':None},
                   {'max_page_events':100001},{'max_page_bytes':64*1024*1024+1}):
        kwargs=dict(log_identity=IDENTITY,generation=None,frozen_eof=30,as_of_utc=AS_OF)
        kwargs.update(change)
        with pytest.raises(rebuild.RebuildError):rebuild.begin(**kwargs)
