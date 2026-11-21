"""Own synthetic test children through deadline and drain uncertainty."""
import math
import subprocess
import time

def _hold_ambiguous_spawn():
    # No process handle was returned: existence/ownership remains unknown.
    while True:
        try: print('HOLD: synthetic launch ownership uncertain', flush=True)
        except BaseException: pass
        try: time.sleep(10)
        except BaseException: pass

def _drain_held(process, problem):
    while True:
        try:
            process.communicate(timeout=10)
            return problem
        except BaseException as error:
            # Do not swallow a cancellation arriving while awaiting EOF.
            if isinstance(error, (KeyboardInterrupt, SystemExit)) and not isinstance(problem, (KeyboardInterrupt, SystemExit)):
                problem = error
            try: print('HOLD: synthetic child remains owned', flush=True)
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)) and not isinstance(problem, (KeyboardInterrupt, SystemExit)):
                    problem = error
            try: time.sleep(1)
            except BaseException as error:
                if isinstance(error, (KeyboardInterrupt, SystemExit)) and not isinstance(problem, (KeyboardInterrupt, SystemExit)):
                    problem = error

def run_owned(argv, *, timeout, **kwargs):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('bad synthetic timeout')
    data = kwargs.pop('input', None)
    kwargs.pop('capture_output', None)
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(argv, stdin=subprocess.PIPE if data is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except BaseException:
        # An interrupted or failed spawn has no trustworthy returned handle.
        # Never claim it was absent or continue as though ownership was settled.
        _hold_ambiguous_spawn()
        raise AssertionError('unreachable ambiguous spawn')
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        stdout, stderr = process.communicate(input=data, timeout=remaining)
    except BaseException as error:
        # Retain owned process until exit and both pipes reach EOF; never kill.
        pending = _drain_held(process, error)
        if isinstance(pending, subprocess.TimeoutExpired):
            raise AssertionError('synthetic child deadline exceeded; late output discarded') from None
        raise pending
    if time.monotonic() >= deadline:
        raise AssertionError('synthetic child deadline exceeded; late output discarded')
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
