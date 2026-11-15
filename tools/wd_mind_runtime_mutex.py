# SPDX-License-Identifier: BUSL-1.1
"""Windows named reader mutex shim; never opens or creates a lock file.

The verified runtime bootstrap configures the exact manifest generation before
installing this module as the legacy reader-slot import. The name is fixed by
that generation, so all cooperating children of one generation share a mutex.
This is coordination, not sender authentication, cross-generation exclusion,
or protection from a hostile same-user process or pre-created mutex.
"""

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import math
import os
from pathlib import Path
import re

_GENERATION = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_LOCK_SENTINEL = Path(r"C:\Python\project2\.codex-audit\wd-mind-runtime\q4-reader.lock")
_PREFIX = "Global\\WdMindQ4Reader-"
_generation = None


class ReaderBusy(RuntimeError):
    def __init__(self):
        super().__init__("reader_busy")


def configure_generation(generation):
    global _generation
    if type(generation) is not str or not _GENERATION.fullmatch(generation):
        raise ValueError("mutex_generation_invalid")
    if _generation is not None and _generation != generation:
        raise ValueError("mutex_generation_changed")
    _generation = generation


def _kernel():
    if os.name != "nt":
        raise OSError("windows_mutex_required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = (wintypes.HANDLE,)
    api.ReleaseMutex.restype = wintypes.BOOL
    api.CloseHandle.argtypes = (wintypes.HANDLE,)
    api.CloseHandle.restype = wintypes.BOOL
    return api


@contextmanager
def reader_slot(lock_path, *, timeout_seconds):
    """Acquire the generation-wide mutex with a <=30s wait and no file I/O."""
    if _generation is None or type(timeout_seconds) not in (int, float):
        raise ValueError("mutex_not_configured")
    if not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 30:
        raise ValueError("mutex_timeout_invalid")
    path = Path(lock_path)
    if (not path.is_absolute() or ".." in path.parts or
            os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(_LOCK_SENTINEL))):
        raise ValueError("mutex_sentinel_invalid")
    api = _kernel()
    handle = api.CreateMutexW(None, False, _PREFIX + _generation)
    if not handle:
        raise OSError("mutex_create_failed")
    owned = False
    try:
        status = api.WaitForSingleObject(handle, int(math.ceil(timeout_seconds * 1000)))
        if status == 0x102:  # WAIT_TIMEOUT
            raise ReaderBusy()
        if status == 0x80:  # WAIT_ABANDONED: acquired, but consistency is unknown.
            owned = True
            raise OSError("mutex_abandoned")
        if status != 0:  # WAIT_OBJECT_0
            raise OSError("mutex_wait_failed")
        owned = True
        yield
    finally:
        try:
            if owned and not api.ReleaseMutex(handle):
                raise OSError("mutex_release_failed")
        finally:
            api.CloseHandle(handle)
