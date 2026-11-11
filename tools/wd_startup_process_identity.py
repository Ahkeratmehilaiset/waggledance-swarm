"""Default-off process instance observation using a retained Windows handle.

The expected PID, exact creation FILETIME and image path must come from a
separate caller-controlled source. A match is an instant observation only; it
does not authenticate that source, verify executable bytes, prove agent
context, confer rollback authority or establish future process liveness.
This helper never starts, stops, or changes a process and performs no file I/O.
"""
import re
import sys

_DRIVE_PATH = re.compile(r'^[A-Za-z]:\\[^\\]+(?:\\[^\\]+)*\Z')


def _windows():
    return sys.platform == 'win32'


def _image_key(value):
    if type(value) is not str or not (1 <= len(value) <= 32767):
        raise ValueError('invalid image path')
    if any(ord(char) < 32 or ord(char) >= 127 or char in '<>"|?*/' for char in value):
        raise ValueError('invalid image path')
    text = value
    if not _DRIVE_PATH.fullmatch(text) or ':' in text[2:]:
        raise ValueError('invalid image path')
    if any(part in ('.', '..') or part.endswith(('.', ' ')) for part in text[3:].split('\\')):
        raise ValueError('invalid image path')
    # A Windows directory can be case-sensitive: do not fold or normalize.
    return text


class _WindowsApi:
    def __init__(self):
        import ctypes
        from ctypes import wintypes

        class FileTime(ctypes.Structure):
            _fields_ = [('low', wintypes.DWORD), ('high', wintypes.DWORD)]

        self.ctypes, self.wintypes, self.filetime = ctypes, wintypes, FileTime
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.GetProcessId.argtypes = [wintypes.HANDLE]
        self.kernel.GetProcessId.restype = wintypes.DWORD
        self.kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(FileTime)] * 4
        self.kernel.GetProcessTimes.restype = wintypes.BOOL
        self.kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                            wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL

    def open(self, pid):
        handle = self.kernel.OpenProcess(0x1000 | 0x00100000, False, pid)
        if not handle:
            raise OSError('process handle unavailable')
        return handle

    def alive(self, handle):
        code = self.kernel.WaitForSingleObject(handle, 0)
        if code == 0x102: return True  # WAIT_TIMEOUT: not signaled at this instant.
        if code == 0: return False
        raise OSError('process wait unavailable')

    def identity(self, handle):
        c = self.ctypes
        pid = self.kernel.GetProcessId(handle)
        if not pid: raise OSError('process id unavailable')
        created, exited, kernel, user = (self.filetime() for _ in range(4))
        if not self.kernel.GetProcessTimes(handle, c.byref(created), c.byref(exited),
                                           c.byref(kernel), c.byref(user)):
            raise OSError('process creation unavailable')
        length = self.wintypes.DWORD(32768)
        image = c.create_unicode_buffer(length.value)
        if not self.kernel.QueryFullProcessImageNameW(handle, 0, image, c.byref(length)):
            raise OSError('process image unavailable')
        return int(pid), (int(created.high) << 32) | int(created.low), image.value

    def close(self, handle):
        if not self.kernel.CloseHandle(handle):
            raise OSError('process handle close failed')


def _new_api():
    return _WindowsApi()


def inspect_process_instance(*, enabled=False, expected_pid=None, expected_creation_filetime=None,
                             expected_image_path=None):
    """Compare a supplied tuple twice on one process handle; no authority effect."""
    out = {'status': 'OFF' if enabled is False else 'HOLD', 'authority_effect': 'none',
           'instance_tuple_matches': False, 'live_at_last_probe': False,
           'anchor_authenticated': False, 'image_bytes_verified': False,
           'full_startup_verified': False, 'future_liveness_verified': False,
           'rollback_performed': False}
    if enabled is not True:
        return out
    try:
        if not _windows() or type(expected_pid) is not int or not 0 < expected_pid < 2**32:
            return out
        if type(expected_creation_filetime) is not int or not 0 < expected_creation_filetime < 2**64:
            return out
        image = _image_key(expected_image_path)
        api = _new_api()
        handle = api.open(expected_pid)
        try:
            if not api.alive(handle): return out
            for _ in range(2):
                pid, created, path = api.identity(handle)
                if type(pid) is not int or type(created) is not int or (pid, created) != (
                        expected_pid, expected_creation_filetime) or _image_key(path) != image:
                    return out
            if not api.alive(handle): return out
        finally:
            api.close(handle)
        out['status'] = 'PROCESS_INSTANCE_OBSERVED'
        out['instance_tuple_matches'] = True
        out['live_at_last_probe'] = True
    except Exception:
        return out
    return out
