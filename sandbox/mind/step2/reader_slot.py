"""A bounded, process-shared, single-reader resource slot.

All cooperating readers must supply the same persistent lock-file path in a
trusted, shared directory. Never delete, replace, or rename that lock file
while readers might be running: locks apply to the file, not the path. This is
resource coordination, not authentication or protection from hostile users.
"""

from contextlib import contextmanager
import errno
import math
import os
import stat
import time

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ReaderBusy(RuntimeError):
    """The shared reader slot was still held when the deadline expired."""

    def __init__(self):
        super().__init__("reader_busy")


def _try_lock(fd):
    try:
        if os.name == "nt":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
            return False
        if getattr(exc, "winerror", None) in (33, 36):
            return False
        raise


def _unlock(fd):
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def reader_slot(lock_path, *, timeout_seconds):
    """Acquire one OS-released file lock, or raise ReaderBusy in <= 30 s.

    Filesystem errors are not converted to "reader_busy"; the caller must
    refuse the read on any error. The bound is for contention, and cannot bound
    a stalled filesystem call. The file is deliberately kept after release.
    """
    timeout = float(timeout_seconds)
    if not math.isfinite(timeout) or not 0 <= timeout <= 30:
        raise ValueError("timeout_seconds must be finite and between 0 and 30")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0)
    fd = os.open(os.fspath(lock_path), flags, 0o600)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("reader slot must be a regular file")
        deadline = time.monotonic() + timeout
        while True:
            if _try_lock(fd):
                locked = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReaderBusy()
            time.sleep(min(0.05, remaining))
        yield
    finally:
        try:
            if locked:
                _unlock(fd)
        finally:
            os.close(fd)
