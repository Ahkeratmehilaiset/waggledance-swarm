"""Default-off subprocess experiment for the read-only startup observer.

This runs only supplied in-memory fixtures. It has no live runtime adapters,
rollback capability, authenticated clock or production authority. Python's
subprocess timeout does not bound operating-system process creation or cleanup.
"""

import json
import os
from pathlib import Path
import subprocess
import sys


# The child may only import the local pure observer and use supplied values.
# No caller-supplied code, paths, commands or environment are accepted.
_CHILD = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
from tools.wd_q4_startup_observer import observe_startup
d = json.load(sys.stdin)
if d['stall']:
    time.sleep(30)
r = observe_startup(enabled=True, plan=d['plan'],
    read_receipt=lambda lane: d['receipts'].get(lane),
    read_handshake=lambda lane: d['handshakes'].get(lane),
    read_tools_readiness=lambda: d['ready'],
    read_process=lambda pid: d['processes'].get(str(pid)),
    utc_now=lambda: d['now'])
json.dump(r, sys.stdout, separators=(',', ':'))
"""


def observe_synthetic_process(*, enabled=False, fixture=None, timeout_seconds=2):
    """Try one synthetic observation in a child; return OFF, HOLD or partial evidence.

    A timeout terminates the direct synthetic child via subprocess.run; it is
    not evidence of a bounded live process tree, a real receipt, or rollback.
    """
    result = {
        'status': 'OFF' if enabled is False else 'HOLD',
        'reason': 'disabled' if enabled is False else 'invalid_input',
        'production_decision_supported': False,
        'rollback_performed': False,
        'wall_clock_deadline_enforced': False,
        'observation': None,
    }
    if enabled is not True:
        return result
    if (type(timeout_seconds) not in (int, float) or not 0.05 <= timeout_seconds <= 10
            or type(fixture) is not dict
            or set(fixture) != {'plan', 'receipts', 'handshakes', 'processes', 'ready', 'now', 'stall'}
            or type(fixture['stall']) is not bool):
        return result
    try:
        data = json.dumps(fixture, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(data) > 65536:
            return result
    except (TypeError, ValueError, RecursionError):
        return result

    # Windows requires SystemRoot to start system libraries. Nothing else from
    # the parent environment (including Python paths and credentials) passes.
    env = {}
    if os.name == 'nt' and os.environ.get('SystemRoot'):
        env['SystemRoot'] = os.environ['SystemRoot']
    root = str(Path(__file__).resolve().parent.parent)
    try:
        completed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', _CHILD, root],
            input=data, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout_seconds, check=False, env=env)
        if completed.returncode != 0 or len(completed.stdout) > 65536:
            result['reason'] = 'child_failed'
            return result
        observation = json.loads(completed.stdout)
        if type(observation) is not dict or observation.get('status') != 'PARTIAL_OBSERVATIONS_MATCH':
            result['reason'] = 'observation_hold'
            return result
        result.update(status='SYNTHETIC_PARTIAL', reason='observations_match', observation=observation)
        return result
    except subprocess.TimeoutExpired:
        result['reason'] = 'child_timeout'
    except (OSError, ValueError, UnicodeError):
        result['reason'] = 'child_failed'
    return result
