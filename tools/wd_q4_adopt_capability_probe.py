"""Synthetic Windows capability probe; never authorizes production recovery.

A matching file ID, granted access and Job membership demonstrate only this
local test invocation. Same-user handle duplication and escaped children are
outside this cooperative proof.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def _valid(marker_number, marker_path, manifest_path, expected_manifest_sha, mode='once'):
    if os.name!='nt' or not re.fullmatch(r'[0-9a-f]{64}',expected_manifest_sha):
        return False
    marker_number=int(marker_number)
    if not 0 < marker_number < 2**64: return False
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    ntdll=ctypes.WinDLL('ntdll')
    handle=ctypes.c_void_p; dword=wintypes.DWORD
    kernel.GetCurrentProcess.restype=handle
    kernel.GetFileType.argtypes=[handle]; kernel.GetFileType.restype=dword
    kernel.SetHandleInformation.argtypes=[handle,dword,dword]; kernel.SetHandleInformation.restype=wintypes.BOOL
    kernel.IsProcessInJob.argtypes=[handle,handle,ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype=wintypes.BOOL
    kernel.OpenJobObjectW.argtypes=[dword,wintypes.BOOL,wintypes.LPCWSTR]; kernel.OpenJobObjectW.restype=handle
    kernel.QueryInformationJobObject.argtypes=[handle,ctypes.c_int,handle,dword,ctypes.POINTER(dword)]
    kernel.QueryInformationJobObject.restype=wintypes.BOOL
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,dword,dword,handle,dword,dword,handle]
    kernel.CreateFileW.restype=handle
    kernel.GetFileInformationByHandleEx.argtypes=[handle,ctypes.c_int,handle,dword]
    kernel.GetFileInformationByHandleEx.restype=wintypes.BOOL
    kernel.ReadFile.argtypes=[handle,handle,dword,ctypes.POINTER(dword),handle]
    kernel.ReadFile.restype=wintypes.BOOL
    kernel.GetFileSizeEx.argtypes=[handle,ctypes.POINTER(ctypes.c_int64)]; kernel.GetFileSizeEx.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[handle]; kernel.CloseHandle.restype=wintypes.BOOL
    ntdll.NtQueryObject.argtypes=[handle,ctypes.c_int,handle,dword,ctypes.POINTER(dword)]
    ntdll.NtQueryObject.restype=ctypes.c_long
    marker=handle(marker_number)
    class FileId(ctypes.Structure):
        _fields_=[('volume',ctypes.c_uint64),('identifier',ctypes.c_ubyte*16)]
    if kernel.GetFileType(marker)!=1: return False
    # Do not pass the marker capability to a later child process.
    if not kernel.SetHandleInformation(marker,1,0): return False
    info=(ctypes.c_ubyte*56)(); returned=dword()
    status=ntdll.NtQueryObject(marker,0,info,ctypes.sizeof(info),ctypes.byref(returned))
    if status!=0: return False
    granted=int.from_bytes(bytes(info[4:8]),'little')
    if not (granted & 0x2) or (granted & 0x10000): return False  # WRITE_DATA required, DELETE forbidden
    in_job=wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(),None,ctypes.byref(in_job)) or not in_job.value: return False
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',dword),
                  ('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',dword),
                  ('affinity',ctypes.c_size_t),('priority',dword),('scheduling',dword)]
    class Io(ctypes.Structure):
        _fields_=[(str(i),ctypes.c_uint64) for i in range(6)]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',Io),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    limits=Extended(); size=dword()
    # NULL queries the immediate Job without inheriting a keep-alive Job handle.
    if not kernel.QueryInformationJobObject(None,9,ctypes.byref(limits),ctypes.sizeof(limits),ctypes.byref(size)):
        return False
    if not (limits.basic.flags & 0x2000) or (limits.basic.flags & (0x800|0x1000)): return False
    original=FileId()
    if not kernel.GetFileInformationByHandleEx(marker,18,ctypes.byref(original),ctypes.sizeof(original)): return False
    length=ctypes.c_int64()
    if not kernel.GetFileSizeEx(marker,ctypes.byref(length)) or length.value!=0: return False
    path=Path(marker_path)
    if not path.is_absolute() or path.drive.upper()!='C:' or '..' in path.parts: return False
    for parent in (path,*path.parents):
        info_stat=os.lstat(parent)
        if getattr(info_stat,'st_file_attributes',0)&0x400: return False
    manifest=Path(manifest_path)
    if not manifest.is_absolute() or not os.path.samefile(manifest.parent,path.parent): return False
    manifest_stat=os.lstat(manifest)
    if getattr(manifest_stat,'st_file_attributes',0)&0x400 or manifest_stat.st_size>65536: return False
    with manifest.open('rb') as stream:
        before=os.fstat(stream.fileno()); raw=stream.read(65537); after=os.fstat(stream.fileno())
    if (len(raw)!=manifest_stat.st_size or not os.path.samestat(manifest_stat,before) or
            not os.path.samestat(before,after) or not os.path.samestat(after,os.lstat(manifest)) or
            hashlib.sha256(raw).hexdigest()!=expected_manifest_sha): return False
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result: raise ValueError('duplicate')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=unique)
    if (type(value) is not dict or set(value)!={'schema','python_sha256','probe_sha256','attempt','audit_anchor'} or
            value['schema']!='wd.synthetic-adopt-capability.v1' or
            not re.fullmatch(r'[0-9a-f]{64}',value['attempt']) or
            value['python_sha256']!=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest() or
            value['probe_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()): return False
    anchor=Path(value['audit_anchor'])
    if not anchor.is_absolute() or anchor.drive.upper()!='C:' or '..' in anchor.parts: return False
    if os.path.commonpath((os.path.normcase(str(path)),os.path.normcase(str(anchor)))) != os.path.normcase(str(anchor)):
        return False
    for parent in (anchor,*anchor.parents):
        if getattr(os.lstat(parent),'st_file_attributes',0)&0x400: return False
    cap_id=format(original.volume,'016x')+bytes(original.identifier).hex()
    expected_marker=('wd.synthetic-adopt-marker:'+value['attempt']+':'+cap_id+'\n').encode('ascii')
    other=kernel.CreateFileW(str(path),0x80000000,7,None,3,0x80,None)
    if other in (None,ctypes.c_void_p(-1).value): return False
    try:
        observed=FileId()
        if not kernel.GetFileInformationByHandleEx(other,18,ctypes.byref(observed),ctypes.sizeof(observed)): return False
        buffer=ctypes.create_string_buffer(4097); count=dword()
        if not kernel.ReadFile(other,buffer,4097,ctypes.byref(count),None) or count.value>4096: return False
        if buffer.raw[:count.value]!=expected_marker: return False
    finally:
        kernel.CloseHandle(other)
    # Open the attempt-derived named Job only briefly. This QUERY handle itself
    # can delay KILL_ON_JOB_CLOSE if the host dies during the query.
    job=kernel.OpenJobObjectW(0x4,False,'Local\\wd-q4-synthetic-'+value['attempt'])
    if not job: return False
    try:
        member=wintypes.BOOL()
        if not kernel.IsProcessInJob(kernel.GetCurrentProcess(),job,ctypes.byref(member)) or not member.value:
            return False
        if mode=='holdjob':
            ready=path.parent/'synthetic-child-holdjob.json'
            with ready.open('x',encoding='ascii') as stream:
                json.dump({'pid':os.getpid()},stream); stream.flush(); os.fsync(stream.fileno())
            time.sleep(30)
        return True
    finally:
        kernel.CloseHandle(job)


def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    if not args:
        print('{"status":"OFF"}')
        return 0
    if len(args)==2 and args[0]=='--grandchild' and sys.flags.isolated and sys.flags.no_site:
        try:
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.GetFileType.argtypes=[ctypes.c_void_p]; kernel.GetFileType.restype=wintypes.DWORD
            return 2 if kernel.GetFileType(ctypes.c_void_p(int(args[1])))==1 else 0
        except BaseException:
            return 0
    if (len(args)!=6 or args[0]!='--child' or args[5] not in ('once','linger','mutate','grandchild','markerprotect','holdjob') or
            not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)):
        return 2
    try:
        if not _valid(*args[1:5],mode=args[5]): return 2
        if args[5]=='markerprotect':
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p]
            kernel.CreateFileW.restype=ctypes.c_void_p
            opened=kernel.CreateFileW(args[2],0x40000000,7,None,3,0x80,None)
            if opened not in (None,ctypes.c_void_p(-1).value):
                kernel.CloseHandle(ctypes.c_void_p(opened)); return 2
        if args[5]=='mutate':
            kernel=ctypes.WinDLL('kernel32',use_last_error=True)
            kernel.SetFilePointerEx.argtypes=[ctypes.c_void_p,ctypes.c_int64,ctypes.c_void_p,wintypes.DWORD]
            kernel.WriteFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.c_void_p]
            count=wintypes.DWORD(); data=ctypes.create_string_buffer(b'X')
            if not kernel.SetFilePointerEx(ctypes.c_void_p(int(args[1])),0,None,0): return 2
            if not kernel.WriteFile(ctypes.c_void_p(int(args[1])),data,1,ctypes.byref(count),None) or count.value!=1: return 2
        if args[5]=='grandchild':
            result=subprocess.run([sys.executable,'-I','-S','-B',__file__,'--grandchild',args[1]],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=False,timeout=10)
            if result.returncode!=0: return 2
        # A persistent marker or manifest edit between initial validation and
        # this decision is refused. No production recovery lock is claimed.
        if not _valid(*args[1:5]): return 2
        if args[5]=='linger':
            ready=Path(args[2]).parent/'synthetic-child-ready.json'
            with ready.open('x',encoding='ascii') as stream:
                json.dump({'pid':os.getpid()},stream); stream.flush(); os.fsync(stream.fileno())
            time.sleep(30)
        return 0
    except BaseException:
        return 2


if __name__=='__main__':
    raise SystemExit(main())
