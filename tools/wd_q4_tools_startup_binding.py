"""Bind Tools startup receipt to caller-observed readiness and process facts.

This is an opt-in, I/O-free evidence check. The caller must independently
obtain and authenticate all inputs and measure observation time. BOUND is not
an authenticated receipt, successful Mind read, liveness proof, or authority
to perform a rollback. No launcher currently calls this function.
"""

from datetime import datetime, timezone
import re


_RUN = re.compile(r'[A-Za-z0-9._:-]{1,128}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_COMMIT = re.compile(r'[0-9a-f]{40}\Z')
_THREAD = re.compile(r'[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z')
_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,7})?(?:Z|\+00:00)\Z')
_RECEIPT_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z')


def _matches(pattern, value):
    return type(value) is str and pattern.fullmatch(value) is not None


def _utc(value):
    if not _matches(_TIME, value):
        raise ValueError('timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo != timezone.utc:
        raise ValueError('timezone')
    fraction = value.split('.', 1)[1].split('Z')[0].split('+')[0] if '.' in value else ''
    hundred_ns = int(fraction[6]) if len(fraction) == 7 else 0
    seconds = ((parsed.toordinal() * 24 + parsed.hour) * 60 + parsed.minute) * 60 + parsed.second
    return (seconds * 10_000_000 + parsed.microsecond * 10 + hundred_ns, len(fraction))


def _same_process_start(recorded, observed):
    recorded_ticks, recorded_precision = _utc(recorded)
    observed_ticks, observed_precision = _utc(observed)
    if min(recorded_precision, observed_precision) >= 7:
        return recorded_ticks == observed_ticks
    if min(recorded_precision, observed_precision) < 6:
        return False
    return recorded_ticks // 10 == observed_ticks // 10


def _pid(value):
    return type(value) is int and 0 < value <= 2**31 - 1


def bind_tools_startup(*, readiness, receipt, expected_generation, expected_thread_id,
                       expected_descriptor_sha256, expected_manifest_sha256,
                       launch_started_utc, observed_utc, consumer, native):
    """Compare actual v3 readiness/receipt fields to supplied process observations.

    A newly launched Tools consumer must start at or after the launch anchor;
    already-running consumers remain UNKNOWN. Windows process starts are
    compared at their shared microsecond precision when an observation has
    only six fractional digits; when both have seven, all 100 ns are compared.
    """
    result = dict(status='UNKNOWN', run_id=None, authority_effect='none',
                  observations_authenticated=False, receipt_content_verified=False,
                  independent_liveness_verified=False)
    try:
        if not (type(readiness) is dict and type(receipt) is dict and
                type(consumer) is dict and type(native) is dict):
            return result
        if not (_matches(_COMMIT, expected_generation) and _matches(_THREAD, expected_thread_id)
                and _matches(_HASH, expected_descriptor_sha256) and _matches(_HASH, expected_manifest_sha256)):
            return result
        run = readiness.get('run_id')
        if not (_matches(_RUN, run) and readiness.get('session_id') == run and receipt.get('run_id') == run):
            return result
        expected = dict(schema='wd.tools-consumer-ready.v3', status='terminal_ready',
                        readiness_scope='native_cli_only', conversation_surface='native_terminal',
                        agent='codex-tools-1', generation=expected_generation,
                        thread_id=expected_thread_id, q4_shared_descriptor_sha256=expected_descriptor_sha256)
        if not all(readiness.get(key) == value for key, value in expected.items()):
            return result
        if readiness.get('task_completion_verified') is not False:
            return result
        if not (receipt.get('schema') == 'wd.mind-startup-read.v1'
                and receipt.get('lane') == 'codex-tools-1'
                and receipt.get('expected_manifest_sha256') == expected_manifest_sha256
                and receipt.get('descriptor_sha256') == expected_descriptor_sha256
                and _matches(_RECEIPT_TIME, receipt.get('ts_utc'))):
            return result
        pid, native_pid = readiness.get('pid'), readiness.get('native_pid')
        if not (_pid(pid) and _pid(native_pid) and pid != native_pid
                and _pid(readiness.get('native_parent_pid')) and readiness['native_parent_pid'] == pid
                and _pid(consumer.get('process_id')) and consumer['process_id'] == pid
                and _pid(native.get('process_id')) and native['process_id'] == native_pid
                and _pid(native.get('parent_process_id')) and native['parent_process_id'] == pid
                and type(native.get('name')) is str and native['name'].lower() == 'codex.exe'):
            return result
        started, seen = _utc(launch_started_utc)[0], _utc(observed_utc)[0]
        consumer_at = _utc(readiness.get('process_start_utc'))[0]
        native_at = _utc(readiness.get('native_process_start_utc'))[0]
        ready_at = _utc(readiness.get('ready_at_utc'))[0]
        receipt_at = _utc(receipt['ts_utc'])[0]
        if not (started <= consumer_at <= native_at <= ready_at <= seen
                and consumer_at // 10_000_000 * 10_000_000 <= receipt_at
                <= native_at // 10_000_000 * 10_000_000
                and _same_process_start(readiness['process_start_utc'], consumer.get('created_utc'))
                and _same_process_start(readiness['native_process_start_utc'], native.get('created_utc'))):
            return result
        result.update(status='BOUND', run_id=run)
    except (ValueError, TypeError, OverflowError):
        pass
    return result
