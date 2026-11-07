"""Synthetic Windows capability probe; never authorizes production recovery.

A matching file ID, granted access and Job membership demonstrate only this
local test invocation. Same-user handle duplication and escaped children are
outside this cooperative proof.
"""
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import re
import sys


def _valid(marker_number, job_number, marker_path, expected_sha):
    if os.name!='nt' or not re.fullmatch(r'[0-9a-f]{64}',expected_sha):
        return False
    marker_number=int(marker_number); job_number=int(job_number)
    if not 0 < marker_number < 2**64 or not 0 < job_number < 2**64: return False
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    ntdll=ctypes.WinDLL('ntdll')
    handle=ctypes.c_void_p; dword=wintypes.DWORD
    kernel.GetCurrentProcess.restype=handle
    kernel.GetFileType.argtypes=[handle]; kernel.GetFileType.restype=dword
    kernel.IsProcessInJob.argtypes=[handle,handle,ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype=wintypes.BOOL
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,dword,dword,handle,dword,dword,handle]
    kernel.CreateFileW.restype=handle
    kernel.GetFileInformationByHandleEx.argtypes=[handle,ctypes.c_int,handle,dword]
    kernel.GetFileInformationByHandleEx.restype=wintypes.BOOL
    kernel.ReadFile.argtypes=[handle,handle,dword,ctypes.POINTER(dword),handle]
    kernel.ReadFile.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[handle]; kernel.CloseHandle.restype=wintypes.BOOL
    ntdll.NtQueryObject.argtypes=[handle,ctypes.c_int,handle,dword,ctypes.POINTER(dword)]
    ntdll.NtQueryObject.restype=ctypes.c_long
    marker=handle(marker_number); job=handle(job_number)
    class FileId(ctypes.Structure):
        _fields_=[('volume',ctypes.c_uint64),('identifier',ctypes.c_ubyte*16)]
    if kernel.GetFileType(marker)!=1: return False
    info=(ctypes.c_ubyte*56)(); returned=dword()
    status=ntdll.NtQueryObject(marker,0,info,ctypes.sizeof(info),ctypes.byref(returned))
    if status!=0: return False
    granted=int.from_bytes(bytes(info[4:8]),'little')
    if (granted & 0x10002)!=0x10002: return False  # DELETE and FILE_WRITE_DATA
    in_job=wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(),job,ctypes.byref(in_job)) or not in_job.value: return False
    original=FileId()
    if not kernel.GetFileInformationByHandleEx(marker,18,ctypes.byref(original),ctypes.sizeof(original)): return False
    path=Path(marker_path)
    if not path.is_absolute() or path.drive.upper()!='C:' or '..' in path.parts: return False
    audit=Path(r'C:\Python\project2\.codex-audit')
    if os.path.commonpath((os.path.normcase(str(path)),os.path.normcase(str(audit)))) != os.path.normcase(str(audit)):
        return False
    for parent in (path,*path.parents):
        info_stat=os.lstat(parent)
        if getattr(info_stat,'st_file_attributes',0)&0x400: return False
    other=kernel.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
    if other in (None,ctypes.c_void_p(-1).value): return False
    try:
        observed=FileId()
        if not kernel.GetFileInformationByHandleEx(other,18,ctypes.byref(observed),ctypes.sizeof(observed)): return False
        if original.volume!=observed.volume or bytes(original.identifier)!=bytes(observed.identifier): return False
        buffer=ctypes.create_string_buffer(4097); count=dword()
        if not kernel.ReadFile(other,buffer,4097,ctypes.byref(count),None) or count.value>4096: return False
        return hashlib.sha256(buffer.raw[:count.value]).hexdigest()==expected_sha
    finally:
        kernel.CloseHandle(other)


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    if len(args)!=5 or args[0]!='--child' or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode):
        return 2
    try:
        return 0 if _valid(*args[1:]) else 2
    except BaseException:
        return 2


if __name__=='__main__':
    raise SystemExit(main())
