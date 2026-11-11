"""Default-off owned-child launch observation; not a fleet launch controller.

Only a fixed synthetic Python child may be started. Its own retained process
handle supplies PID, creation FILETIME and image path. The child is asked to
exit naturally by closing its stdin; it is never terminated by this helper.
The monotonic deadline bounds the explicit wait and gates success, but cannot
interrupt a blocked process creation, kernel call, or pipe/handle close.
No authentication, UAC, agent startup, peer channel, rollback or deployment
is established. A timed-out child can remain alive and cleanup is UNKNOWN.
"""
import os
import subprocess
import sys
import time

from tools.wd_startup_process_identity import _image_key, _new_api, _windows

_SYNTHETIC_CHILD_CODE = 'import sys; sys.stdin.buffer.read(1)'
_CREATE_NO_WINDOW = 0x08000000


def _controller_executable():
    return sys.executable


def run_synthetic_controller(*, enabled=False, deadline_seconds=5):
    """Observe one fixed child via the controller-owned process handle."""
    out = {'status': 'OFF' if enabled is False else 'HOLD', 'authority_effect': 'none',
           'pid': None, 'creation_filetime': None, 'image_path': None,
           'child_cleanup_confirmed': False, 'handle_release_confirmed': False,
           'anchor_authenticated': False, 'image_bytes_verified': False,
           'peer_channel_verified': False, 'uac_verified': False,
           'lane_startup_verified': False, 'rollback_performed': False,
           'hard_wall_clock_deadline_enforced': False}
    if enabled is not True:
        return out
    if (type(deadline_seconds) not in (int, float) or not 0 < deadline_seconds <= 30
            or not _windows()):
        return out

    process = None
    deadline = None
    observed = None
    exit_code = None
    try:
        image = _image_key(_controller_executable())
        # This location is a bounded input, not an authenticated OS identity.
        system_root = _image_key(os.environ.get('SystemRoot'))
        deadline = time.monotonic() + deadline_seconds
        process = subprocess.Popen(
            [image, '-I', '-S', '-B', '-c', _SYNTHETIC_CHILD_CODE],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False, close_fds=True, cwd=system_root,
            env={'SystemRoot': system_root, 'WINDIR': system_root},
            creationflags=_CREATE_NO_WINDOW)
        if time.monotonic() >= deadline:
            raise TimeoutError('spawn exceeded observation deadline')
        api = _new_api()
        # Popen owns this handle from creation until wait/close; no PID reopen.
        handle = int(process._handle)
        if (not handle or type(process.pid) is not int or not 0 < process.pid < 2**32
                or api.alive(handle) is not True):
            raise ValueError('unavailable process')
        first = api.identity(handle)
        second = api.identity(handle)
        if (type(first) is not tuple or len(first) != 3 or type(second) is not tuple or len(second) != 3
                or type(second[0]) is not int or type(second[1]) is not int or first != second
                or type(first[0]) is not int or first[0] != process.pid
                or type(first[1]) is not int or not 0 < first[1] < 2**64
                or _image_key(first[2]) != image or api.alive(handle) is not True
                or time.monotonic() >= deadline):
            raise ValueError('unusable process observation')
        observed = first
    except Exception:
        observed = None
    finally:
        if process is not None:
            try:
                process.stdin.close()  # EOF requests this specific synthetic child to exit.
            except Exception:
                observed = None
            try:
                remaining = max(0.0, deadline - time.monotonic()) if deadline is not None else 0.0
                exit_code = process.wait(timeout=remaining)
                if type(exit_code) is not int:
                    raise ValueError('invalid child exit status')
                out['child_cleanup_confirmed'] = True
            except Exception:
                observed = None
            try:
                process._handle.Close()
                out['handle_release_confirmed'] = True
            except Exception:
                observed = None
    try:
        if (observed is not None and type(exit_code) is int and exit_code == 0
                and deadline is not None and time.monotonic() <= deadline):
            out.update(status='SYNTHETIC_CHILD_OBSERVED_AND_EXITED', pid=observed[0],
                       creation_filetime=observed[1], image_path=observed[2])
    except Exception:
        pass  # A failed final time observation cannot establish success.
    return out
