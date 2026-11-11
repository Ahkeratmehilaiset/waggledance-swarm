"""Synthetic Tools readiness/startup binding; no filesystem or process IO."""
import copy

import pytest


def sample():
    run = 'wd-lane-codex-tools-1-20261111T100001123Z-123'
    ready = dict(schema='wd.tools-consumer-ready.v3', status='terminal_ready',
        readiness_scope='native_cli_only', conversation_surface='native_terminal',
        agent='codex-tools-1', generation='a'*40, run_id=run, session_id=run,
        thread_id='12345678-1234-1234-1234-123456789abc', pid=123,
        native_pid=456, native_parent_pid=123, process_start_utc='2026-11-11T10:00:01.1234567Z',
        native_process_start_utc='2026-11-11T10:00:03.2345678Z',
        ready_at_utc='2026-11-11T10:00:04.0000000Z',
        q4_shared_descriptor_sha256='b'*64, task_completion_verified=False)
    receipt = dict(schema='wd.mind-startup-read.v1', lane='codex-tools-1', run_id=run,
        ts_utc='2026-11-11T10:00:02Z', expected_manifest_sha256='c'*64, descriptor_sha256='b'*64)
    return dict(readiness=ready, receipt=receipt, expected_generation='a'*40,
        expected_thread_id=ready['thread_id'], expected_descriptor_sha256='b'*64,
        expected_manifest_sha256='c'*64, launch_started_utc='2026-11-11T10:00:00Z',
        observed_utc='2026-11-11T10:00:05Z',
        consumer=dict(process_id=123, created_utc='2026-11-11T10:00:01.123456Z'),
        native=dict(process_id=456, parent_process_id=123, name='codex.exe',
                    created_utc='2026-11-11T10:00:03.234567Z'))


def bind(**kwargs):
    from tools.wd_q4_tools_startup_binding import bind_tools_startup
    return bind_tools_startup(**kwargs)


def test_real_shape_links_startup_to_observed_tools_process_without_authority():
    i = sample()
    before = copy.deepcopy(i)
    result = bind(**i)
    assert result['status'] == 'BOUND'
    assert result['run_id'] == i['readiness']['run_id']
    assert result['authority_effect'] == 'none'
    assert result['observations_authenticated'] is False
    assert result['receipt_content_verified'] is False
    assert result['independent_liveness_verified'] is False
    assert i == before


@pytest.mark.parametrize('fault', [
    'stale_receipt', 'wrong_lane', 'wrong_schema', 'wrong_generation', 'wrong_thread',
    'wrong_descriptor', 'wrong_manifest', 'wrong_session', 'wrong_pid', 'wrong_native_pid',
    'wrong_native_parent', 'wrong_native_name', 'old_consumer', 'old_native', 'wrong_consumer_start',
    'wrong_native_start', 'future_ready', 'receipt_before_consumer', 'receipt_after_ready',
    'bool_pid', 'completed_claim', 'bad_run', 'wrong_readiness_scope', 'missing_process', 'bad_time',
    'consumer_100ns_mismatch', 'native_100ns_mismatch', 'insufficient_precision', 'receipt_after_native',
])
def test_unmatched_or_stale_evidence_never_binds(fault):
    i = sample(); r = i['readiness']; s = i['receipt']
    if fault == 'stale_receipt': s['run_id'] = 'previous'
    if fault == 'wrong_lane': s['lane'] = 'fable-5'
    if fault == 'wrong_schema': r['schema'] = 'wd.tools-consumer-ready.v1'
    if fault == 'wrong_generation': r['generation'] = 'd'*40
    if fault == 'wrong_thread': r['thread_id'] = 'ffffffff-ffff-ffff-ffff-ffffffffffff'
    if fault == 'wrong_descriptor': r['q4_shared_descriptor_sha256'] = '0'*64
    if fault == 'wrong_manifest': s['expected_manifest_sha256'] = '0'*64
    if fault == 'wrong_session': r['session_id'] = 'previous'
    if fault == 'wrong_pid': i['consumer']['process_id'] = 999
    if fault == 'wrong_native_pid': i['native']['process_id'] = 999
    if fault == 'wrong_native_parent': i['native']['parent_process_id'] = 999
    if fault == 'wrong_native_name': i['native']['name'] = 'other.exe'
    if fault == 'old_consumer': i['launch_started_utc'] = '2026-11-11T10:00:02Z'
    if fault == 'old_native': r['native_process_start_utc'] = '2026-11-11T10:00:00Z'
    if fault == 'wrong_consumer_start': i['consumer']['created_utc'] = '2026-11-11T10:00:01.123457Z'
    if fault == 'wrong_native_start': i['native']['created_utc'] = '2026-11-11T10:00:03.234568Z'
    if fault == 'future_ready': r['ready_at_utc'] = '2026-11-11T10:00:06Z'
    if fault == 'receipt_before_consumer': s['ts_utc'] = '2026-11-11T10:00:00Z'
    if fault == 'receipt_after_ready': s['ts_utc'] = '2026-11-11T10:00:05Z'
    if fault == 'bool_pid': r['pid'] = True
    if fault == 'completed_claim': r['task_completion_verified'] = True
    if fault == 'bad_run': r['run_id'] = s['run_id'] = r['session_id'] = '../foreign'
    if fault == 'wrong_readiness_scope': r['readiness_scope'] = 'full'
    if fault == 'missing_process': i['consumer'] = None
    if fault == 'bad_time': i['observed_utc'] = 'invalid'
    if fault == 'consumer_100ns_mismatch': i['consumer']['created_utc'] = '2026-11-11T10:00:01.1234568Z'
    if fault == 'native_100ns_mismatch': i['native']['created_utc'] = '2026-11-11T10:00:03.2345679Z'
    if fault == 'insufficient_precision': i['consumer']['created_utc'] = '2026-11-11T10:00:01Z'
    if fault == 'receipt_after_native': s['ts_utc'] = '2026-11-11T10:00:04Z'
    result = bind(**i)
    assert result['status'] == 'UNKNOWN' and result['run_id'] is None
    assert result['authority_effect'] == 'none'


def test_process_timestamps_both_at_100ns_precision_match():
    i = sample()
    i['consumer']['created_utc'] = i['readiness']['process_start_utc']
    i['native']['created_utc'] = i['readiness']['native_process_start_utc']
    assert bind(**i)['status'] == 'BOUND'


def test_100ns_launch_interval_boundary_does_not_truncate():
    i = sample()
    i['launch_started_utc'] = '2026-11-11T10:00:01.1234568Z'
    assert bind(**i)['status'] == 'UNKNOWN'


def test_no_file_or_process_io(monkeypatch):
    from tools.wd_q4_tools_startup_binding import bind_tools_startup
    import builtins, subprocess
    i = sample()
    def forbidden(*args, **kwargs): raise AssertionError('I/O not allowed')
    monkeypatch.setattr(builtins, 'open', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    assert bind_tools_startup(**i)['status'] == 'BOUND'


def test_bound_tools_id_can_be_supplied_to_actual_five_lane_receipt_assessor():
    from tools.wd_q4_startup_receipt_assessment import assess_startup_receipts, LANES
    i = sample()
    common = dict(helper_sha256='e'*64, helper_env='verified', helper_status='EXITED', cleanup='CONFIRMED',
        route='default_caller_cli', outcome='success', exit_code=0, inner_status='ANSWER', result_count=1,
        answer_sha256='f'*64, call_id='f'*32, delivered_items=1, delivered_chars=100,
        delivered_sha256='c'*64, refs=['d'*64])
    i['receipt'].update(common, reason='content_delivered_unknown_freshness')
    result = bind(**i)
    assert result['status'] == 'BOUND' and result['receipt_content_verified'] is False
    runs = {lane: 'fleet-run' for lane in LANES}
    runs['codex-tools-1'] = result['run_id']
    records = {lane: dict(i['receipt'], lane=lane, run_id=runs[lane],
                   reason='content_delivered_unknown_freshness' if lane == 'codex-tools-1' else 'content_delivered')
               for lane in LANES}
    observed = {lane: '2026-11-11T10:00:05Z' for lane in LANES}
    assessment = assess_startup_receipts(expected_runs=runs, expected_manifest_sha256='c'*64,
        expected_descriptor_sha256='b'*64, expected_helper_sha256='e'*64, records=records,
        observed_at_utc=observed, started_at_utc='2026-11-11T10:00:00Z',
        deadline_utc='2026-11-11T10:01:00Z', now_utc='2026-11-11T10:00:05Z')
    assert assessment['decision'] == 'RECEIPTS_OK'
    records['codex-tools-1']['outcome'] = 'refused'
    assert assess_startup_receipts(expected_runs=runs, expected_manifest_sha256='c'*64,
        expected_descriptor_sha256='b'*64, expected_helper_sha256='e'*64, records=records,
        observed_at_utc=observed, started_at_utc='2026-11-11T10:00:00Z',
        deadline_utc='2026-11-11T10:01:00Z', now_utc='2026-11-11T10:00:05Z')['decision'] == 'ROLLBACK_REQUIRED'
