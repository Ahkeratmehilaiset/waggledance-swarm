"""Parent-owned recovery reservation. Never removes the job marker itself.

The owner handle remains in this Python process only. The supervisor may later
reconcile its exact marker after the Job exits; uncertain states stay pending.
This is cooperative coordination, not hostile same-user authentication.
"""
from contextlib import ExitStack, contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from sandbox.mind.step2 import prefix_private_state as private_state
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.reader_slot import reader_slot


class OwnerUnknown(RuntimeError): pass
_KEYS=frozenset({'manifest_sha256','config_sha256','runtime_sha256','host_sha256','receipt_sha256',
                 'tuple_sha256','baseline_sha256','interpreter_sha256','launcher_sha256','commit'})
_HEX=re.compile(r'^[0-9a-f]{64}$'); _COMMIT=re.compile(r'^[0-9a-f]{40}$')
_NAME='q4-adopt-recovery-job.pending.json'


def _valid_pins(pins):
    return type(pins) is dict and set(pins)==_KEYS and all(
        type(value) is str and (_COMMIT if key=='commit' else _HEX).fullmatch(value)
        for key,value in pins.items())


def _kernel():
    if os.name!='nt': raise OwnerUnknown('owner_unknown')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True); handle=ctypes.c_void_p; dword=wintypes.DWORD
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,dword,dword,handle,dword,dword,handle]; kernel.CreateFileW.restype=handle
    kernel.GetFileInformationByHandleEx.argtypes=[handle,ctypes.c_int,handle,dword]; kernel.GetFileInformationByHandleEx.restype=wintypes.BOOL
    kernel.ReadFile.argtypes=[handle,handle,dword,ctypes.POINTER(dword),handle]; kernel.ReadFile.restype=wintypes.BOOL
    kernel.WriteFile.argtypes=[handle,handle,dword,ctypes.POINTER(dword),handle]; kernel.WriteFile.restype=wintypes.BOOL
    kernel.FlushFileBuffers.argtypes=[handle]; kernel.FlushFileBuffers.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[handle]; kernel.CloseHandle.restype=wintypes.BOOL
    return kernel


class _FileId(ctypes.Structure):
    _fields_=[('volume',ctypes.c_uint64),('identifier',ctypes.c_ubyte*16)]


def _identity(kernel,handle):
    result=_FileId()
    if not kernel.GetFileInformationByHandleEx(handle,18,ctypes.byref(result),ctypes.sizeof(result)):
        raise OwnerUnknown('owner_unknown')
    return format(result.volume,'016x')+bytes(result.identifier).hex()


class _Owner:
    __slots__=('_handle','_pid','_path','_private','_shared','_pins','_bytes','_id','_attempt')
    def __init__(self,handle,path,private,shared,pins,raw,identity,attempt):
        self._handle=handle; self._pid=os.getpid(); self._path=path; self._private=private; self._shared=shared
        self._pins=dict(pins); self._bytes=raw; self._id=identity; self._attempt=attempt
    def verify_locked(self,private,shared,pins):
        try:
            if not self._handle or os.getpid()!=self._pid or Path(private)!=self._private or Path(shared)!=self._shared or pins!=self._pins:
                return False
            kernel=_kernel(); checkpoint._secure(self._path)
            if _identity(kernel,self._handle)!=self._id: return False
            other=kernel.CreateFileW(str(self._path),0x80000000,7,None,3,0x80,None)
            if other in (None,ctypes.c_void_p(-1).value): return False
            try:
                if _identity(kernel,other)!=self._id: return False
                buffer=ctypes.create_string_buffer(65537); count=wintypes.DWORD()
                if not kernel.ReadFile(other,buffer,65537,ctypes.byref(count),None) or count.value>65536: return False
                return buffer.raw[:count.value]==self._bytes
            finally: kernel.CloseHandle(other)
        except BaseException: return False
    def descriptor(self):
        return {'attempt':self._attempt,'marker_id':self._id,'marker_sha256':hashlib.sha256(self._bytes).hexdigest()}
    def close(self):
        if self._handle:
            _kernel().CloseHandle(self._handle); self._handle=None


@contextmanager
def reserve(private_root,shared_root,pins):
    """Create one owner marker under writer locks; preserve it on every exit."""
    owner=None; handle=None; created=False
    try:
        if not _valid_pins(pins): raise ValueError()
        private=checkpoint._secure(private_root); shared=checkpoint._secure(shared_root)
        if not private.is_dir() or not shared.is_dir(): raise ValueError()
        with ExitStack() as slots:
            for name in ('q4-continuation-orchestrator.lock','q4-live-update.lock','q4-cutoff-owner.lock'):
                slots.enter_context(reader_slot(checkpoint._secure(private/name,missing_leaf=True),timeout_seconds=.2))
            with private_state.locked_state(private,shared,enabled=True):
                with private_state._checkpoint_lock(shared,.2):
                    competing=(private/'prefix-private-state.pending',private/'.wd-q4-status-prefix-runner.pending',
                        shared/'.wd-q4-status-prefix-runner.pending',private/'q4-cutoff.pending.json',
                        private/'q4-continuation-orchestrator.pending.json',private/'q4-continuation-orchestrator-job.pending.json',
                        private/'q4-live-update.pending.json',shared/'q4-prefix-fence.json')
                    if any(os.path.lexists(path) for path in competing): raise OwnerUnknown('owner_unknown')
                    path=checkpoint._secure(private/_NAME,missing_leaf=True); kernel=_kernel()
                    handle=kernel.CreateFileW(str(path),0xC0010000,1,None,1,0x80000080,None)
                    if handle in (None,ctypes.c_void_p(-1).value): handle=None; raise OwnerUnknown('owner_unknown')
                    created=True
                    attempt=secrets.token_hex(32)
                    raw=json.dumps({'schema':'wd.q4-parent-owned-recovery.v1','attempt':attempt,**pins},sort_keys=True,separators=(',',':')).encode('ascii')
                    data=ctypes.create_string_buffer(raw); count=wintypes.DWORD()
                    if not kernel.WriteFile(handle,data,len(raw),ctypes.byref(count),None) or count.value!=len(raw) or not kernel.FlushFileBuffers(handle):
                        raise OwnerUnknown('owner_unknown')
                    owner=_Owner(handle,path,private,shared,pins,raw,_identity(kernel,handle),attempt); handle=None
        yield owner
    except OwnerUnknown:
        raise
    except BaseException:
        raise OwnerUnknown('owner_unknown') from None
    finally:
        if owner is not None: owner.close()
        if handle: _kernel().CloseHandle(handle)
