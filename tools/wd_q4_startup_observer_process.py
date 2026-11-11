"""Default-off owned subprocess for *synthetic* startup snapshots only.

The worker does not access runtime paths, invoke live adapters, deploy, run
agent commands or roll back. Fixture data travels in a process argument and is visible to local
process inspection; never pass real or secret data. Timeout bounds child wait
and cleanup attempts, not process creation
or an operating-system failure. The executable and source are not authenticated;
this is not a production watchdog or a security boundary against the same user.
"""

import base64
import json
import math
import os
import subprocess
import sys


_MAX = 16 * 1024
_WORKER_FLAG = '--synthetic-observer-worker'


def run_synthetic_observation(*, enabled=False, snapshot=None, timeout_seconds=10):
    """Run one in-memory fixture in an owned process; never authorizes action."""
    result = dict(status='OFF' if enabled is False else 'HOLD', worker_status='UNKNOWN',
                  authority_effect='none', production_ready=False, live_sources_verified=False,
                  rollback_performed=False, wall_clock_deadline_enforced=False,
                  owned_child_reaped=None)
    if enabled is False or enabled is not True:
        return result
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0.05 <= timeout_seconds <= 60 or type(snapshot) is not dict):
        return result
    try:
        payload = json.dumps(snapshot, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
        if len(payload) > _MAX:
            return result
    except (TypeError, ValueError, OverflowError, RecursionError):
        return result
    child = None
    try:
        # A bounded synthetic-only argument avoids an unbounded Windows stdin
        # write when a child never reads. It is visible in the process list.
        # -I/-S excludes user site code; the worker opens no runtime paths.
        encoded = base64.b64encode(payload).decode('ascii')
        child = subprocess.Popen([sys.executable, '-I', '-S', os.path.abspath(__file__), _WORKER_FLAG, encoded],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            exit_code = child.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            result['worker_status'] = 'TIMEOUT'
            # Only the handle returned by our own Popen is signalled; never a PID
            # from the fixture, a process name, or an existing agent.
            try:
                child.kill()
                child.wait(timeout=2)
                result['owned_child_reaped'] = child.poll() is not None
            except (OSError, subprocess.TimeoutExpired):
                result['owned_child_reaped'] = False
            return result
        result['owned_child_reaped'] = child.poll() is not None
        if exit_code == 0:
            result['worker_status'] = 'PARTIAL_OBSERVATIONS_MATCH'
        elif exit_code == 4:
            result['worker_status'] = 'HOLD'
    except (OSError, ValueError, UnicodeError, TypeError, subprocess.SubprocessError):
        return result
    return result


def _worker(encoded):
    # The worker intentionally exposes only preloaded synthetic dictionaries.
    try:
        if len(encoded) > ((_MAX + 2) // 3) * 4:
            return 2
        raw = base64.b64decode(encoded.encode('ascii'), validate=True)
        if not raw or len(raw) > _MAX:
            return 2
        fixture = json.loads(raw.decode('ascii'))
        if type(fixture) is not dict or set(fixture) != {'plan', 'receipts', 'handshakes', 'processes', 'ready', 'clock_utc'}:
            return 2
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, root)
        from tools.wd_q4_startup_observer import observe_startup
        report = observe_startup(enabled=True, plan=fixture['plan'],
            read_receipt=lambda lane: fixture['receipts'].get(lane),
            read_handshake=lambda lane: fixture['handshakes'].get(lane),
            read_tools_readiness=lambda: fixture['ready'],
            read_process=lambda pid: fixture['processes'].get(str(pid)),
            utc_now=lambda: fixture['clock_utc'])
        status = report.get('status')
        if status not in ('PARTIAL_OBSERVATIONS_MATCH', 'HOLD'):
            return 2
        return 0 if status == 'PARTIAL_OBSERVATIONS_MATCH' else 4
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        return 2


if __name__ == '__main__':
    sys.exit(_worker(sys.argv[2]) if len(sys.argv) == 3 and sys.argv[1] == _WORKER_FLAG else 2)
