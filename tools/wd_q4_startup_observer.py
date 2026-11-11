"""Default-off, single-pass startup observation over injected read-only ports.

This is not a resident watchdog and has no real runtime adapter. The caller
owns all source and clock authenticity; even matching observations do not prove
agent activity, installation or permission to restore. No mutation port exists.
The adapter call may block indefinitely; a separate bounded observer is needed.
"""

import re

from tools.wd_q4_startup_receipt_assessment import LANES, assess_startup_receipts
from tools.wd_q4_tools_startup_binding import bind_tools_startup, _utc


_OTHER = tuple(lane for lane in LANES if lane != 'codex-tools-1')
_UUID = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z')
_RUN = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')


def _match(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _pid(value):
    return type(value) is int and 0 < value <= 2**31 - 1


def observe_startup(*, enabled=False, plan=None, read_receipt=None, read_handshake=None,
                    read_tools_readiness=None, read_process=None, utc_now=None):
    """Collect one bounded observation; all input adapters must be read-only.

    Clock is called by this observer immediately after each adapter returns.
    Missing/unusable evidence is HOLD. Five matching receipts still do not
    authenticate the clock, process facts, input paths, or manifest installation.
    """
    out = dict(status='OFF' if enabled is False else 'HOLD', authority_effect='none',
               receipt_decision='UNKNOWN', tools_binding='UNKNOWN', observations_authenticated=False,
               clock_authenticated=False, process_liveness_verified=False,
               manifest_install_verified=False, agent_context_verified=False, rollback_performed=False,
               wall_clock_deadline_enforced=False, full_handshake_verified=False,
               session_delivery_verified=False, production_decision_supported=False,
               receipt_observed_at_utc={}, handshake_observed_at_utc={},
               process_observed_at_utc={}, tools_readiness_observed_at_utc=None)
    if enabled is False:
        return out
    if enabled is not True:
        return out
    try:
        keys = {'expected_runs', 'expected_agent_uuids', 'expected_generation', 'expected_thread_id',
                'expected_descriptor_sha256', 'expected_manifest_sha256', 'expected_helper_sha256',
                'launch_started_utc', 'deadline_utc', 'expected_tools_run_id'}
        if type(plan) is not dict or set(plan) != keys:
            return out
        runs, uuids = plan['expected_runs'], plan['expected_agent_uuids']
        if not (type(runs) is dict and set(runs) == set(_OTHER)
                and type(uuids) is dict and set(uuids) == set(_OTHER)
                and all(_match(_RUN, x) for x in runs.values())
                and all(_match(_UUID, x) for x in uuids.values())
                and len(set(uuids.values())) == len(_OTHER)
                and _match(_UUID, plan['expected_thread_id'])
                and _match(_RUN, plan['expected_tools_run_id'])
                and _match(_COMMIT, plan['expected_generation'])
                and all(_match(_SHA, plan[k]) for k in ('expected_descriptor_sha256',
                    'expected_manifest_sha256', 'expected_helper_sha256'))):
            return out
        start, deadline = _utc(plan['launch_started_utc'])[0], _utc(plan['deadline_utc'])[0]
        if deadline <= start:
            return out
        last = start

        def observed(reader, *args):
            nonlocal last
            value = reader(*args)
            timestamp = utc_now()
            ticks = _utc(timestamp)[0]
            if not (last <= ticks <= deadline):
                raise ValueError('clock')
            last = ticks
            return value, timestamp

        receipts, receipt_times, pids = {}, {}, set()
        for lane in LANES:
            receipt, at = observed(read_receipt, lane)
            if type(receipt) is not dict:
                return out
            receipts[lane], receipt_times[lane] = receipt, at
            out['receipt_observed_at_utc'][lane] = at

        for lane in _OTHER:
            handshake, handshake_at = observed(read_handshake, lane)
            if type(handshake) is not dict:
                return out
            out['handshake_observed_at_utc'][lane] = handshake_at
            pid = handshake.get('pid')
            if not (type(handshake.get('schema_version')) is int and handshake['schema_version'] == 1
                    and handshake.get('status') == 'bridge_bootstrapped' and handshake.get('agent') == lane
                    and handshake.get('agent_uuid') == uuids[lane] and handshake.get('run_id') == runs[lane]
                    and handshake.get('session_id') == runs[lane]
                    and handshake.get('bundle_generation') == plan['expected_generation']
                    and _pid(pid) and pid not in pids):
                return out
            pids.add(pid)
            process, process_at = observed(read_process, pid)
            out['process_observed_at_utc'][lane] = process_at
            if not (type(process) is dict and _pid(process.get('process_id'))
                    and process['process_id'] == pid):
                return out
            created = _utc(process.get('created_utc'))[0]
            handshake_created = _utc(handshake.get('created_at_utc'))[0]
            receipt_at = _utc(receipts[lane].get('ts_utc'))[0]
            if not (start <= created <= handshake_created <= _utc(handshake_at)[0] <= _utc(process_at)[0]):
                return out
            # Receipt timestamp is truncated to whole seconds and the launcher
            # writes it before publishing the handshake, after process creation.
            if not (created // 10_000_000 * 10_000_000 <= receipt_at
                    <= handshake_created // 10_000_000 * 10_000_000):
                return out

        ready, ready_at = observed(read_tools_readiness)
        out['tools_readiness_observed_at_utc'] = ready_at
        if type(ready) is not dict or not (_pid(ready.get('pid')) and _pid(ready.get('native_pid'))):
            return out
        if ready['pid'] in pids or ready['native_pid'] in pids:
            return out
        consumer, consumer_at = observed(read_process, ready['pid'])
        native, tools_at = observed(read_process, ready['native_pid'])
        out['process_observed_at_utc']['codex-tools-1'] = consumer_at
        out['process_observed_at_utc']['codex-tools-1-native'] = tools_at
        binding = bind_tools_startup(readiness=ready, receipt=receipts['codex-tools-1'],
            expected_generation=plan['expected_generation'], expected_thread_id=plan['expected_thread_id'],
            expected_descriptor_sha256=plan['expected_descriptor_sha256'],
            expected_manifest_sha256=plan['expected_manifest_sha256'],
            launch_started_utc=plan['launch_started_utc'], observed_utc=tools_at,
            consumer=consumer, native=native)
        if binding['status'] != 'BOUND' or binding['run_id'] != plan['expected_tools_run_id']:
            return out
        out['tools_binding'] = 'BOUND'
        expected_runs = dict(runs, **{'codex-tools-1': binding['run_id']})
        verdict = assess_startup_receipts(expected_runs=expected_runs,
            expected_manifest_sha256=plan['expected_manifest_sha256'],
            expected_descriptor_sha256=plan['expected_descriptor_sha256'],
            expected_helper_sha256=plan['expected_helper_sha256'], records=receipts,
            observed_at_utc=receipt_times, started_at_utc=plan['launch_started_utc'],
            deadline_utc=plan['deadline_utc'], now_utc=tools_at)
        out['receipt_decision'] = verdict['decision']
        if verdict['decision'] == 'RECEIPTS_OK':
            out['status'] = 'PARTIAL_OBSERVATIONS_MATCH'
    except Exception:
        # An unavailable or malformed read-only source is unknown; never retry or restore.
        return out
    return out
