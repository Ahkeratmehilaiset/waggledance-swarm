"""Windows single-file share-denying hash check; not runtime authentication.

The caller supplies an independently trusted digest and must explicitly close
the returned handle. No ACL, parent-directory, DLL, imports, mutable mappings,
filesystem provenance, source authentication or process isolation is proved.
Only direct HarddiskVolume DOS mappings are accepted. Final DOS path matching
is exact and case-sensitive, so equivalent aliases or casing may refuse.
A BaseException during CloseHandle makes cleanup unknown and is replaced by
AttestationCleanupUnknown after quarantine; no OS-close success is claimed.
A KeyboardInterrupt elsewhere propagates if cleanup is confirmed successful.
Arbitrary asynchronous interruption between Python bytecode instructions is
not guaranteed to leave handle state or quarantine bookkeeping consistent.
"""
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
import re
import threading

_HARD_LIMIT = 1024 * 1024 * 1024
_INVALID = C.c_void_p(-1).value
_QUARANTINED = []  # Retain uncertain handles; never blindly close a potentially reused handle value.
_STATE_LOCK = threading.RLock()  # Admission, held-handle close and quarantine are serialized in-process.


class AttestationRefused(ValueError):
    """Fixed error with no pathname or input reflected."""


class AttestationCleanupUnknown(AttestationRefused):
    """Close was not confirmed; lease retains the handle for explicit handling."""

    def __init__(self, lease):
        super().__init__("attestation_cleanup_unknown")
        self.lease = lease


class _Info(C.Structure):
    _fields_ = [("attributes", W.DWORD), ("creation", W.FILETIME), ("access", W.FILETIME),
                ("write", W.FILETIME), ("volume", W.DWORD), ("size_high", W.DWORD),
                ("size_low", W.DWORD), ("links", W.DWORD), ("index_high", W.DWORD),
                ("index_low", W.DWORD)]


def _api():
    k = C.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = (W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE)
    k.CreateFileW.restype = W.HANDLE
    k.GetFileType.argtypes, k.GetFileType.restype = (W.HANDLE,), W.DWORD
    k.GetFileInformationByHandle.argtypes = (W.HANDLE, C.POINTER(_Info))
    k.GetFileInformationByHandle.restype = W.BOOL
    k.ReadFile.argtypes = (W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p)
    k.ReadFile.restype = W.BOOL
    k.CloseHandle.argtypes, k.CloseHandle.restype = (W.HANDLE,), W.BOOL
    k.GetDriveTypeW.argtypes, k.GetDriveTypeW.restype = (W.LPCWSTR,), W.UINT
    k.QueryDosDeviceW.argtypes, k.QueryDosDeviceW.restype = (W.LPCWSTR, W.LPWSTR, W.DWORD), W.DWORD
    k.GetFinalPathNameByHandleW.argtypes = (W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD)
    k.GetFinalPathNameByHandleW.restype = W.DWORD
    return k


def _verify_fixed_drive(k, path):
    if k.GetDriveTypeW(path[:3]) != 3:  # DRIVE_FIXED alone does not reject SUBST.
        raise AttestationRefused("attestation_refused")
    target = C.create_unicode_buffer(32768)
    length = k.QueryDosDeviceW(path[:2], target, len(target))
    if not length or length >= len(target) or re.fullmatch(r"\\Device\\HarddiskVolume[0-9]+", target.value) is None:
        raise AttestationRefused("attestation_refused")


def _verify_final_path(k, handle, path):
    final = C.create_unicode_buffer(32768)
    length = k.GetFinalPathNameByHandleW(handle, final, len(final), 0)  # VOLUME_NAME_DOS
    if not length or length >= len(final) or final.value != "\\\\?\\" + path:
        raise AttestationRefused("attestation_refused")


class HeldFile:
    """A read-only lease; its caller must own it exclusively while in use.

    Concurrent close calls are serialized, but another thread may close a
    lease while its context body is running. No path or content is returned.
    """
    __slots__ = ("_kernel", "_handle", "_unknown", "sha256", "size")

    def __init__(self, kernel, handle, digest, size):
        self._kernel, self._handle, self.sha256, self.size = kernel, handle, digest, size
        self._unknown = False

    def _quarantine(self):
        with _STATE_LOCK:
            if not self._unknown:
                self._unknown = True
                _QUARANTINED.append(self)

    def close(self):
        with _STATE_LOCK:
            if self._unknown:
                raise AttestationCleanupUnknown(self)
            if self._handle is not None:
                closed = False
                try:
                    closed = bool(self._kernel.CloseHandle(self._handle))
                except BaseException:
                    pass
                if not closed:
                    self._quarantine()
                    raise AttestationCleanupUnknown(self) from None
                self._handle = None

    def __enter__(self):
        with _STATE_LOCK:
            if self._unknown:
                raise AttestationCleanupUnknown(self)
            if self._handle is None:
                raise AttestationRefused("attestation_closed")
            return self

    def __exit__(self, *_):
        self.close()


def _open_and_hash(path, expected_sha256, max_bytes):
    if (os.name != "nt" or type(path) is not str or not re.match(r"^[A-Za-z]:\\", path)
            or "\x00" in path or type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or type(max_bytes) is not int or not 1 <= max_bytes <= _HARD_LIMIT):
        raise AttestationRefused("attestation_refused")
    # Reject dot/parent/alternate-stream path forms. Parent identity and
    # ancestor reparse points are NOT verified by this single-file helper.
    components = path[3:].split("\\")
    if any(not part or part in (".", "..") or ":" in part or "/" in part for part in components):
        raise AttestationRefused("attestation_refused")
    k = _api()
    _verify_fixed_drive(k, path)
    # GENERIC_READ, FILE_SHARE_READ only, OPEN_EXISTING, OPEN_REPARSE_POINT.
    handle = k.CreateFileW(path, 0x80000000, 0x1, None, 3, 0x00200000, None)
    if handle is None or handle == _INVALID:
        raise AttestationRefused("attestation_refused")
    transferred = False
    try:
        _verify_final_path(k, handle, path)
        info = _Info()
        if (k.GetFileType(handle) != 1 or not k.GetFileInformationByHandle(handle, C.byref(info))
                or info.attributes & (0x10 | 0x400)):
            raise AttestationRefused("attestation_refused")
        size = (int(info.size_high) << 32) | int(info.size_low)
        if size > max_bytes:
            raise AttestationRefused("attestation_refused")
        digest = hashlib.sha256()
        buffer = C.create_string_buffer(min(1024 * 1024, max_bytes + 1))
        total = 0
        while True:
            count = W.DWORD()
            request_size = min(len(buffer), max_bytes - total + 1)
            if not k.ReadFile(handle, buffer, request_size, C.byref(count), None):
                raise AttestationRefused("attestation_refused")
            if not count.value:
                break
            total += count.value
            if total > max_bytes:
                raise AttestationRefused("attestation_refused")
            digest.update(buffer.raw[:count.value])
        actual = digest.hexdigest()
        if total != size or actual != expected_sha256:
            raise AttestationRefused("attestation_refused")
        result = HeldFile(k, handle, actual, total)
        transferred = True
        return result
    finally:
        if not transferred:
            closed = False
            try:
                closed = bool(k.CloseHandle(handle))
            except BaseException:
                pass
            if not closed:
                # Do not lose ownership of an OS handle whose closure is unknown.
                lease = HeldFile(k, handle, None, None)
                lease._quarantine()
                raise AttestationCleanupUnknown(lease) from None


def attest_file(path, expected_sha256, *, max_bytes, enabled=False):
    """Fail closed on one file; this does not establish process identity."""
    lease = None
    try:
        with _STATE_LOCK:
            if enabled is True and not _QUARANTINED:
                return _open_and_hash(path, expected_sha256, max_bytes)
    except AttestationCleanupUnknown as exc:
        lease = exc.lease
    except Exception:
        pass
    del path, expected_sha256, max_bytes, enabled
    if lease is not None:
        raise AttestationCleanupUnknown(lease) from None
    raise AttestationRefused("attestation_refused") from None


class _AttestationOwner:
    """Unwired, cooperative lifecycle for leases opened solely through this owner.

    The integration must supply call-bound owner callbacks, use this instance
    for *every* attestation in the call, seal admission with finish(), and call
    abort() on interruption or abandoned work. Ordinary AttestationRefused is
    conservatively UNKNOWN too: admission did not return a lease that this
    owner can account for. A completed callback says only that this owner's
    exact HeldFile objects reported successful close; it does
    not discover bypassed attest_file calls, authenticate a caller, or prove
    safety against monkeypatching, same-user tampering, or asynchronous Python
    interruption between bytecode instructions. Callbacks run without the
    global attestation lock; integration MUST use a call-bound registry whose
    unknown state is monotonic and refuses a racing completion, and must treat
    any callback failure as UNKNOWN. The enclosing call MUST independently mark
    its guard UNKNOWN in a finally block if it does not complete, even if this
    owner already appears uncertain: interruption can occur before an UNKNOWN
    callback runs. Do not consume a witness until the entire enclosing call,
    all owner lifecycles and all failure paths have finished; late abort cannot
    restore a guard already released. This class does not wire the guard or turn
    an unwitnessed real call into a successful one.
    """

    def __init__(self, *, on_complete, on_unknown):
        if not callable(on_complete) or not callable(on_unknown):
            raise AttestationRefused("attestation_refused")
        self._on_complete, self._on_unknown = on_complete, on_unknown
        self._open = set()
        self._opened = 0
        self._sealed = self._uncertain = self._completed = False
        self._completion_pending = False

    def _mark_unknown_locked(self):
        if not self._uncertain:
            self._uncertain = True
            return True
        return False

    def _reserve_completion_locked(self):
        if (self._sealed and not self._open and not self._uncertain
                and not self._completed and not self._completion_pending):
            self._completion_pending = True
            return True
        return False

    def _deliver_completion(self):
        try:
            self._on_complete()
            with _STATE_LOCK:
                self._completion_pending = False
                if self._uncertain:
                    raise AttestationRefused("attestation_refused")
                self._completed = True
        except BaseException:
            self.abort()
            raise

    def attest(self, path, expected_sha256, *, max_bytes, enabled=False):
        notify = False
        lease = None
        try:
            with _STATE_LOCK:
                if self._sealed or self._uncertain or self._completed:
                    raise AttestationRefused("attestation_refused")
                try:
                    lease = attest_file(path, expected_sha256, max_bytes=max_bytes, enabled=enabled)
                    if type(lease) is not HeldFile or lease._unknown or lease._handle is None:
                        raise AttestationRefused("attestation_refused")
                    wrapped = _OwnedAttestationLease(self, lease)
                    self._open.add(wrapped)
                    self._opened += 1
                    return wrapped
                except BaseException:
                    notify = self._mark_unknown_locked()
                    if type(lease) is HeldFile and lease._handle is not None and not lease._unknown:
                        lease.close()
                    raise
        except BaseException:
            if notify:
                self._on_unknown()
            raise

    def finish(self):
        try:
            with _STATE_LOCK:
                if self._sealed or self._uncertain or self._completed:
                    raise AttestationRefused("attestation_refused")
                self._sealed = True
                if not self._opened:
                    notify = self._mark_unknown_locked()
                    complete = False
                else:
                    notify = False
                    complete = self._reserve_completion_locked()
            if notify:
                self._on_unknown()
                raise AttestationRefused("attestation_refused")
            if complete:
                self._deliver_completion()
        except BaseException:
            self.abort()
            raise

    def abort(self):
        with _STATE_LOCK:
            notify = self._mark_unknown_locked()
        if notify:
            self._on_unknown()


class _OwnedAttestationLease:
    """The only lease object returned by an owner; raw handles stay private."""

    __slots__ = ("_owner", "_lease", "_closed")

    def __init__(self, owner, lease):
        self._owner, self._lease, self._closed = owner, lease, False

    @property
    def sha256(self):
        return self._lease.sha256

    @property
    def size(self):
        return self._lease.size

    def close(self):
        notify = False
        try:
            try:
                with _STATE_LOCK:
                    if self._closed:
                        return
                    try:
                        if self not in self._owner._open or self._lease._handle is None or self._lease._unknown:
                            raise AttestationRefused("attestation_refused")
                        self._lease.close()
                        if self._lease._handle is not None or self._lease._unknown:
                            raise AttestationRefused("attestation_refused")
                        self._closed = True
                        self._owner._open.remove(self)
                        complete = self._owner._reserve_completion_locked()
                    except BaseException:
                        notify = self._owner._mark_unknown_locked()
                        raise
            except BaseException:
                if notify:
                    self._owner._on_unknown()
                raise
            if complete:
                self._owner._deliver_completion()
        except BaseException:
            self._owner.abort()
            raise

    def __enter__(self):
        with _STATE_LOCK:
            if self._closed or self._lease._unknown or self._lease._handle is None:
                notify = self._owner._mark_unknown_locked()
                refused = True
            else:
                notify = refused = False
        if notify:
            self._owner._on_unknown()
        if refused:
            raise AttestationRefused("attestation_refused")
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            if error_type is not None:
                self._owner.abort()
        finally:
            self.close()
