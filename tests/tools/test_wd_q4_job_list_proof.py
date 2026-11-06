import ctypes as c
from ctypes import wintypes as w
import dis, os, pathlib, subprocess, sys
import pytest
pytest.importorskip('_winapi')
root=pathlib.Path(__file__).resolve().parents[2]
from tools import wd_q4_secure_transport as transport

class STARTUPINFO(c.Structure):
    _fields_=[('cb',w.DWORD),('reserved',w.LPWSTR),('desktop',w.LPWSTR),('title',w.LPWSTR),('x',w.DWORD),('y',w.DWORD),('xsize',w.DWORD),('ysize',w.DWORD),('xcount',w.DWORD),('ycount',w.DWORD),('fill',w.DWORD),('flags',w.DWORD),('show',w.WORD),('reserved2size',w.WORD),('reserved2',c.c_void_p),('std_in',w.HANDLE),('std_out',w.HANDLE),('std_err',w.HANDLE)]
class STARTUPINFOEX(c.Structure):
    _fields_=[('startup',STARTUPINFO),('attributes',c.c_void_p)]
class PROCESS_INFORMATION(c.Structure):
    _fields_=[('process',w.HANDLE),('thread',w.HANDLE),('pid',w.DWORD),('tid',w.DWORD)]

k,_=transport._api()
k.InitializeProcThreadAttributeList.argtypes=[c.c_void_p,w.DWORD,w.DWORD,c.POINTER(c.c_size_t)]
k.InitializeProcThreadAttributeList.restype=w.BOOL
k.UpdateProcThreadAttribute.argtypes=[c.c_void_p,w.DWORD,c.c_size_t,c.c_void_p,c.c_size_t,c.c_void_p,c.c_void_p]
k.UpdateProcThreadAttribute.restype=w.BOOL
k.DeleteProcThreadAttributeList.argtypes=[c.c_void_p]
k.DeleteProcThreadAttributeList.restype=None
k.CreateProcessW.argtypes=[w.LPCWSTR,w.LPWSTR,c.c_void_p,c.c_void_p,w.BOOL,w.DWORD,c.c_void_p,w.LPCWSTR,c.POINTER(STARTUPINFOEX),c.POINTER(PROCESS_INFORMATION)]
k.CreateProcessW.restype=w.BOOL
k.IsProcessInJob.argtypes=[w.HANDLE,w.HANDLE,c.POINTER(w.BOOL)]
k.IsProcessInJob.restype=w.BOOL

def check(result,stage):
    if not result: raise OSError(c.get_last_error(),stage)

def run_case(mode):
    job=k.CreateJobObjectW(None,None)
    if not job: raise OSError(c.get_last_error(),'CreateJobObjectW')
    attrs=None
    attr_initialized=False
    pi=PROCESS_INFORMATION()
    injected=[]
    try:
        limits=transport._Limits()
        limits.basic.flags=0x2000
        check(k.SetInformationJobObject(job,9,c.byref(limits),c.sizeof(limits)),'SetInformationJobObject')
        size=c.c_size_t()
        k.InitializeProcThreadAttributeList(None,1,0,c.byref(size))
        assert size.value>0
        storage=c.create_string_buffer(size.value)
        attrs=c.cast(storage,c.c_void_p)
        check(k.InitializeProcThreadAttributeList(attrs,1,0,c.byref(size)),'InitializeProcThreadAttributeList')
        attr_initialized=True
        handles=(w.HANDLE*1)(job)
        check(k.UpdateProcThreadAttribute(attrs,0,0x0002000D,c.cast(handles,c.c_void_p),c.sizeof(handles),None,None),'UpdateProcThreadAttribute JOB_LIST')
        startup=STARTUPINFOEX()
        startup.startup.cb=c.sizeof(startup)
        startup.attributes=attrs.value
        cmd=c.create_unicode_buffer(subprocess.list2cmdline([sys.executable,'-c','pass']))
        # Trace only our own call and interrupt after CreateProcessW returns but
        # before the Python return value is stored. pi remains caller-owned.
        def spawn():
            ok=k.CreateProcessW(sys.executable,cmd,None,None,False,0x08080404,None,str(root),c.byref(startup),c.byref(pi))
            return ok
        offset=next(i.offset for i in dis.get_instructions(spawn) if i.opname=='STORE_FAST' and i.argval=='ok')
        previous=sys.gettrace()
        def trace(frame,event,arg):
            if frame.f_code is spawn.__code__:
                frame.f_trace_opcodes=True
                if event=='opcode' and frame.f_lasti==offset:
                    injected.append(True)
                    sys.settrace(None)
                    raise RuntimeError('synthetic interruption after native call')
            return trace
        try:
            sys.settrace(trace)
            spawn()
            raise AssertionError('injection did not happen')
        except RuntimeError as exc:
            assert str(exc)=='synthetic interruption after native call'
        finally:
            sys.settrace(previous)
        assert injected==[True] and pi.process and pi.thread
        in_job=w.BOOL()
        check(k.IsProcessInJob(pi.process,job,c.byref(in_job)),'IsProcessInJob')
        assert in_job.value
        info=transport._JobAccounting()
        check(k.QueryInformationJobObject(job,1,c.byref(info),c.sizeof(info),None),'QueryInformationJobObject')
        assert info.active_processes==1
        if mode=='terminate':
            check(k.TerminateJobObject(job,124),'TerminateJobObject')
        else:
            check(k.CloseHandle(job),'CloseHandle job')
            job=None
        result=k.WaitForSingleObject(pi.process,5000)
        assert result==0, f'child did not exit: {result}'
        if job:
            check(k.QueryInformationJobObject(job,1,c.byref(info),c.sizeof(info),None),'QueryInformationJobObject after')
            assert info.active_processes==0
        print(f'{mode}: injected_after_native_return=true child_in_job=true active_before=1 process_signalled=true')
    finally:
        # Only the job and the handles created by this case are touched.
        if job:
            k.TerminateJobObject(job,125)
            k.CloseHandle(job)
        child_cleanup_ok = True
        if pi.process:
            result=k.WaitForSingleObject(pi.process,5000)
            if result!=0:
                k.TerminateProcess(pi.process,126)
                result=k.WaitForSingleObject(pi.process,5000)
            child_cleanup_ok = result == 0
            k.CloseHandle(pi.process)
        if pi.thread: k.CloseHandle(pi.thread)
        if attr_initialized: k.DeleteProcThreadAttributeList(attrs)
        assert child_cleanup_ok, 'own synthetic child cleanup unconfirmed'

@pytest.mark.parametrize('mode', ['terminate', 'kill_on_close'])
def test_job_list_child_cleanup_after_native_return_interruption(mode):
    run_case(mode)

