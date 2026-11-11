"""Five-lane observer tests using only synthetic in-memory sources."""
import copy

import pytest

from tools.wd_q4_startup_receipt_assessment import LANES

OTHER = tuple(lane for lane in LANES if lane != 'codex-tools-1')
GEN = 'a'*40
DESC = 'b'*64
MANIFEST = 'c'*64
HELPER = 'e'*64
THREAD = '12345678-1234-1234-1234-123456789abc'
NOW = '2026-11-11T10:00:05Z'


def sample():
    runs = {lane: 'fleet-run' for lane in OTHER}
    uuids = {lane: f'11111111-1111-4111-8111-{idx:012d}' for idx, lane in enumerate(OTHER)}
    plan = dict(expected_runs=runs, expected_agent_uuids=uuids, expected_generation=GEN,
                expected_thread_id=THREAD, expected_descriptor_sha256=DESC,
                expected_manifest_sha256=MANIFEST, expected_helper_sha256=HELPER,
                launch_started_utc='2026-11-11T10:00:00Z', deadline_utc='2026-11-11T10:01:00Z',
                expected_tools_run_id='wd-lane-codex-tools-1-20261111T100001123Z-123')
    tools_run = 'wd-lane-codex-tools-1-20261111T100001123Z-123'
    receipts = {}
    for lane in LANES:
        receipts[lane] = dict(schema='wd.mind-startup-read.v1', lane=lane,
            run_id=tools_run if lane == 'codex-tools-1' else runs[lane], ts_utc='2026-11-11T10:00:02Z',
            expected_manifest_sha256=MANIFEST, descriptor_sha256=DESC, helper_sha256=HELPER,
            helper_env='verified', helper_status='EXITED', cleanup='CONFIRMED',
            reason='content_delivered_unknown_freshness' if lane == 'codex-tools-1' else 'content_delivered',
            route='default_caller_cli', outcome='success', exit_code=0, inner_status='ANSWER',
            result_count=1, answer_sha256='f'*64, call_id='f'*32, delivered_items=1,
            delivered_chars=100, delivered_sha256='d'*64, refs=['d'*64])
    handshakes = {lane: dict(schema_version=1, status='bridge_bootstrapped', agent=lane,
        agent_uuid=uuids[lane], run_id=runs[lane], session_id=runs[lane], pid=1000+idx,
        bundle_generation=GEN, created_at_utc='2026-11-11T10:00:04.0000000Z')
        for idx, lane in enumerate(OTHER)}
    processes = {1000+idx: dict(process_id=1000+idx, created_utc='2026-11-11T10:00:01.000000Z')
                 for idx, lane in enumerate(OTHER)}
    processes.update({123: dict(process_id=123, created_utc='2026-11-11T10:00:01.123456Z'),
        456: dict(process_id=456, parent_process_id=123, name='codex.exe',
                  created_utc='2026-11-11T10:00:03.234567Z')})
    ready = dict(schema='wd.tools-consumer-ready.v3', status='terminal_ready',
        readiness_scope='native_cli_only', conversation_surface='native_terminal', agent='codex-tools-1',
        generation=GEN, run_id=tools_run, session_id=tools_run, thread_id=THREAD, pid=123,
        native_pid=456, native_parent_pid=123, process_start_utc='2026-11-11T10:00:01.1234567Z',
        native_process_start_utc='2026-11-11T10:00:03.2345678Z', ready_at_utc='2026-11-11T10:00:04Z',
        q4_shared_descriptor_sha256=DESC, task_completion_verified=False)
    return dict(plan=plan, receipts=receipts, handshakes=handshakes, processes=processes, ready=ready)


def run(data, *, enabled=True, clock=lambda: NOW):
    from tools.wd_q4_startup_observer import observe_startup
    return observe_startup(enabled=enabled, plan=data['plan'],
        read_receipt=lambda lane: copy.deepcopy(data['receipts'].get(lane)),
        read_handshake=lambda lane: copy.deepcopy(data['handshakes'].get(lane)),
        read_tools_readiness=lambda: copy.deepcopy(data['ready']),
        read_process=lambda pid: copy.deepcopy(data['processes'].get(pid)), utc_now=clock)


def test_default_off_never_invokes_sources():
    from tools.wd_q4_startup_observer import observe_startup
    def forbidden(*args): raise AssertionError('must remain off')
    out = observe_startup(read_receipt=forbidden, read_handshake=forbidden,
                          read_tools_readiness=forbidden, read_process=forbidden, utc_now=forbidden)
    assert out['status'] == 'OFF' and out['authority_effect'] == 'none'


def test_non_boolean_enabled_never_reads_sources():
    from tools.wd_q4_startup_observer import observe_startup
    def forbidden(*args): raise AssertionError('not enabled')
    result = observe_startup(enabled=1, read_receipt=forbidden, read_handshake=forbidden,
        read_tools_readiness=forbidden, read_process=forbidden, utc_now=forbidden)
    assert result['status'] == 'HOLD'


def test_five_synthetic_observations_match_but_are_not_authenticated():
    result = run(sample())
    assert result['status'] == 'PARTIAL_OBSERVATIONS_MATCH'
    assert result['receipt_decision'] == 'RECEIPTS_OK'
    assert result['tools_binding'] == 'BOUND'
    assert result['authority_effect'] == 'none'
    assert not result['observations_authenticated']
    assert not result['clock_authenticated']
    assert not result['process_liveness_verified']
    assert not result['rollback_performed']
    assert not result['wall_clock_deadline_enforced']
    assert not result['full_handshake_verified']
    assert not result['session_delivery_verified']
    assert not result['production_decision_supported']
    assert result['receipt_observed_at_utc'] == {lane: NOW for lane in LANES}
    assert result['handshake_observed_at_utc'] == {lane: NOW for lane in OTHER}
    assert result['process_observed_at_utc'] == {**{lane: NOW for lane in LANES}, 'codex-tools-1-native': NOW}
    assert result['tools_readiness_observed_at_utc'] == NOW


@pytest.mark.parametrize('fault', ['missing_receipt','bad_receipt','missing_handshake','wrong_uuid',
    'wrong_generation','wrong_run','missing_process','old_process','future_handshake','missing_tools',
    'stale_tools','wrong_tools_process','bad_time','clock_backwards','bad_manifest','missing_anchor',
    'deadline_missed','duplicate_pid','bool_pid','later_receipt','future_observation'])
def test_missing_stale_or_untrusted_observations_hold(fault):
    d = sample(); lane = OTHER[0]; clock=lambda: NOW
    if fault == 'missing_receipt': del d['receipts'][lane]
    if fault == 'bad_receipt': d['receipts'][lane]['outcome'] = 'refused'
    if fault == 'missing_handshake': del d['handshakes'][lane]
    if fault == 'wrong_uuid': d['handshakes'][lane]['agent_uuid'] = d['plan']['expected_agent_uuids'][OTHER[1]]
    if fault == 'wrong_generation': d['handshakes'][lane]['bundle_generation'] = 'f'*40
    if fault == 'wrong_run': d['handshakes'][lane]['run_id'] = 'old'
    if fault == 'missing_process': del d['processes'][d['handshakes'][lane]['pid']]
    if fault == 'old_process': d['processes'][d['handshakes'][lane]['pid']]['created_utc'] = '2026-11-11T09:59:59.000000Z'
    if fault == 'future_handshake': d['handshakes'][lane]['created_at_utc'] = '2026-11-11T10:00:06Z'
    if fault == 'missing_tools': d['ready'] = None
    if fault == 'stale_tools': d['receipts']['codex-tools-1']['run_id'] = 'old'
    if fault == 'wrong_tools_process': d['processes'][456]['parent_process_id'] = 999
    if fault == 'bad_time': clock = lambda: 'invalid'
    if fault == 'clock_backwards':
        times = iter([NOW, '2026-11-11T10:00:04Z'])
        clock = lambda: next(times)
    if fault == 'bad_manifest': d['receipts'][lane]['expected_manifest_sha256'] = '0'*64
    if fault == 'missing_anchor': del d['plan']['expected_runs'][lane]
    if fault == 'deadline_missed': clock = lambda: '2026-11-11T10:01:00.0000001Z'
    if fault == 'duplicate_pid': d['handshakes'][OTHER[1]]['pid'] = d['handshakes'][lane]['pid']
    if fault == 'bool_pid': d['handshakes'][lane]['pid'] = True
    if fault == 'later_receipt': d['receipts'][lane]['ts_utc'] = '2026-11-11T10:00:06Z'
    if fault == 'future_observation': d['handshakes'][lane]['created_at_utc'] = '2026-11-11T10:00:05.0000001Z'
    result = run(d, clock=clock)
    assert result['status'] == 'HOLD', (fault, result)
    assert result['authority_effect'] == 'none' and not result['rollback_performed']


def test_source_exception_holds_and_never_performs_an_action():
    from tools.wd_q4_startup_observer import observe_startup
    def unavailable(_lane): raise OSError('unavailable')
    d = sample()
    result = observe_startup(enabled=True, plan=d['plan'], read_receipt=unavailable,
        read_handshake=lambda _: None, read_tools_readiness=lambda: None, read_process=lambda _: None,
        utc_now=lambda: NOW)
    assert result['status'] == 'HOLD' and not result['rollback_performed']


@pytest.mark.parametrize('lane', OTHER)
@pytest.mark.parametrize('boundary', ['before_process', 'after_handshake'])
def test_receipt_timestamp_must_belong_to_its_launcher_interval(lane, boundary):
    data = sample()
    if boundary == 'before_process':
        data['processes'][data['handshakes'][lane]['pid']]['created_utc'] = '2026-11-11T10:00:03.000000Z'
    else:
        data['handshakes'][lane]['created_at_utc'] = '2026-11-11T10:00:01.5000000Z'
    assert run(data)['status'] == 'HOLD'


@pytest.mark.parametrize('lane', OTHER)
def test_receipt_same_second_as_subsecond_process_and_handshake_is_admitted(lane):
    data = sample()
    data['processes'][data['handshakes'][lane]['pid']]['created_utc'] = '2026-11-11T10:00:02.100000Z'
    data['handshakes'][lane]['created_at_utc'] = '2026-11-11T10:00:02.9000000Z'
    assert run(data)['status'] == 'PARTIAL_OBSERVATIONS_MATCH'


def test_matching_forged_tools_ids_cannot_replace_independently_supplied_run():
    data = sample()
    del data['plan']['expected_tools_run_id']
    data['ready']['run_id'] = data['ready']['session_id'] = 'not-the-tools-generated-run'
    data['receipts']['codex-tools-1']['run_id'] = 'not-the-tools-generated-run'
    assert run(data)['status'] == 'HOLD'


def test_consistent_but_different_tools_run_is_not_independent_run():
    data = sample()
    data['ready']['run_id'] = data['ready']['session_id'] = 'not-the-tools-generated-run'
    data['receipts']['codex-tools-1']['run_id'] = 'not-the-tools-generated-run'
    result = run(data)
    assert result['status'] == 'HOLD'
    assert result['tools_binding'] == 'UNKNOWN'
