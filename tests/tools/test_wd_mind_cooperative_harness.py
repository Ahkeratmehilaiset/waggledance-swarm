"""Synthetic-only no-kill owner harness: its child programs are fixed fixtures."""
import os, time
import pytest
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows synthetic handle fixture')

def test_fake_deadline_retains_entire_tree_then_cooperative_close():
    clock=FakeClock()
    owner=SyntheticOwner(clock, limit=1024)
    try:
        owner.start()
        assert owner.await_ready(20) and owner.registered_descendant_is_owned()
        clock.advance(31)
        assert owner.deadline(30)=={'state':'HOLD','owned':True}
        assert owner.process_alive() and owner.registered_descendant_is_owned()
        owner.request_exit()
        assert owner.await_stopped(20) == {'state':'STOPPED','owned':False}
        assert owner.registry_empty()
        assert owner.exit_codes==(0,0)
    finally:
        owner.request_exit()
        owner.await_stopped(20)

def test_output_limit_refuses_and_still_closes_cooperatively():
    owner=SyntheticOwner(FakeClock(), limit=128)
    try:
        owner.start(noisy=True)
        assert owner.await_ready(20)
        assert owner.output_overflow.wait(20)
        assert owner.captured_bytes()<=256
        assert owner.deadline(0)=={'state':'HOLD','owned':True}
        owner.request_exit()
        assert owner.await_stopped(20)=={'state':'STOPPED','owned':False}
        assert owner.registry_empty()
        assert owner.exit_codes==(0,0)
    finally:
        owner.request_exit()
        owner.await_stopped(20)
def test_keyboard_interrupt_requests_cooperative_exit():
    owner=SyntheticOwner(FakeClock(),limit=1024)
    try:
        owner.start()
        assert owner.await_ready(20)
        try:
            raise KeyboardInterrupt()
        finally:
            owner.request_exit()
    except KeyboardInterrupt:
        pass
    finally:
        owner.request_exit()
        assert owner.await_stopped(20)=={'state':'STOPPED','owned':False}
    assert owner.exit_codes==(0,0)
    assert owner.registry_empty()

import ctypes, subprocess, sys, threading
if os.name == 'nt':
    import msvcrt
from ctypes import wintypes

class FakeClock:
    def __init__(self): self.value=0
    def advance(self, seconds): self.value+=seconds

_CHILD = r'''import msvcrt, os, subprocess, sys, threading, time
# Fixed synthetic grandchild; it has an independent cooperative lifetime bound.
grandchild_code = "import sys,threading; e=threading.Event(); threading.Thread(target=lambda:(sys.stdin.buffer.read(),e.set()),daemon=True).start(); e.wait(90)"
grandchild = subprocess.Popen([sys.executable,'-u','-c',grandchild_code], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
print('READY',grandchild.pid,flush=True)
if sys.argv[2]=='noisy':
    os.write(1,b'x'*4096)
    os.write(2,b'y'*4096)
control=msvcrt.open_osfhandle(int(sys.argv[1]),os.O_RDONLY)
event=threading.Event()
def reader():
    try:
        while os.read(control,256): pass
    finally: event.set()
threading.Thread(target=reader,daemon=True).start()
event.wait(90)
grandchild.stdin.close()
end=time.monotonic()+20
while grandchild.poll() is None and time.monotonic()<end: time.sleep(.01)
# No kill or unbounded wait even if the descendant is uncooperative.
if grandchild.poll() is None: sys.exit(2)
'''

class _ProcessEntry(ctypes.Structure):
    _fields_=[('dwSize',wintypes.DWORD),('cntUsage',wintypes.DWORD),('th32ProcessID',wintypes.DWORD),
              ('th32DefaultHeapID',ctypes.c_size_t),('th32ModuleID',wintypes.DWORD),('cntThreads',wintypes.DWORD),
              ('th32ParentProcessID',wintypes.DWORD),('pcPriClassBase',ctypes.c_long),('dwFlags',wintypes.DWORD),
              ('szExeFile',wintypes.WCHAR*260)]
if os.name == 'nt':
    _kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    _kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
    _kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
    _kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(_ProcessEntry)]
    _kernel.Process32FirstW.restype=wintypes.BOOL
    _kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(_ProcessEntry)]
    _kernel.Process32NextW.restype=wintypes.BOOL
    _kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    _kernel.OpenProcess.restype=wintypes.HANDLE
    _kernel.CloseHandle.argtypes=[wintypes.HANDLE]; _kernel.CloseHandle.restype=wintypes.BOOL
    _kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    _kernel.WaitForSingleObject.restype=wintypes.DWORD
    _kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    _kernel.GetExitCodeProcess.restype=wintypes.BOOL
else:
    _kernel=None

def _is_direct_child(pid,parent):
    snapshot=_kernel.CreateToolhelp32Snapshot(2,0)
    if snapshot==wintypes.HANDLE(-1).value: return False
    try:
        entry=_ProcessEntry(); entry.dwSize=ctypes.sizeof(entry)
        ok=_kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            if entry.th32ProcessID==pid: return entry.th32ParentProcessID==parent
            ok=_kernel.Process32NextW(snapshot,ctypes.byref(entry))
        return False
    finally: _kernel.CloseHandle(snapshot)

_OWNERS=[]
class SyntheticOwner:
    """Owns only this fixed cooperative fixture, never arbitrary process trees.

    HOLD is in-process only: pytest crash/exit has no guardian. A nonresponsive
    child remains owned until this process ends; no force-cleanup is attempted.
    """
    def __init__(self,clock,limit):
        if type(limit) is not int or not 64<=limit<=4096: raise ValueError('limit')
        self.clock,self.limit,self.started=clock,limit,clock.value
        self.process=self.control=self.descendant=None
        self.buffers=[bytearray(),bytearray()]; self.readers=[]
        self.ready=threading.Event(); self.output_overflow=threading.Event(); self.failure=None; self.exit_codes=None
        _OWNERS.append(self)
    def _drain(self,index,stream):
        try:
            while True:
                chunk=os.read(stream.fileno(),1024)
                if not chunk: return
                current=self.buffers[index]; remaining=max(0,self.limit-len(current))
                current.extend(chunk[:remaining])
                if len(chunk)>remaining: self.output_overflow.set()
                if index==0 and not self.ready.is_set() and b'\n' in current:
                    line=bytes(current).split(b'\n',1)[0].rstrip(b'\r')
                    if not line.startswith(b'READY ') or not line[6:].isdigit():
                        self.failure='bad_ready'; self.ready.set(); continue
                    pid=int(line[6:]); handle=_kernel.OpenProcess(0x00101000,False,pid)
                    if not handle or not _is_direct_child(pid,self.process.pid):
                        if handle: _kernel.CloseHandle(handle)
                        self.failure='descendant_unknown'
                    else: self.descendant=handle
                    self.ready.set()
        except (OSError,ValueError): self.failure='pipe_unknown'; self.ready.set()
        finally: stream.close()
    def start(self,noisy=False):
        if self.process is not None: raise RuntimeError('already_started')
        read_fd,write_fd=os.pipe(); handle=msvcrt.get_osfhandle(read_fd)
        os.set_handle_inheritable(handle,True)
        si=subprocess.STARTUPINFO(); si.lpAttributeList={'handle_list':[handle]}
        try:
            self.process=subprocess.Popen([sys.executable,'-u','-',str(handle),'noisy' if noisy else 'quiet'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,
                startupinfo=si,creationflags=subprocess.CREATE_NO_WINDOW)
            self.control=write_fd; write_fd=None
        finally:
            os.set_handle_inheritable(handle,False); os.close(read_fd)
            if write_fd is not None: os.close(write_fd)
        for index,stream in enumerate((self.process.stdout,self.process.stderr)):
            thread=threading.Thread(target=self._drain,args=(index,stream),daemon=True)
            self.readers.append(thread); thread.start()
        payload=_CHILD.encode('utf-8')
        if len(payload)>8192: raise RuntimeError('input_limit')
        self.process.stdin.write(payload); self.process.stdin.close()
    def await_ready(self,timeout):
        return self.ready.wait(timeout) and self.failure is None and self.descendant is not None
    def process_alive(self): return self.process is not None and self.process.poll() is None
    def registered_descendant_is_owned(self):
        return self.descendant is not None and _kernel.WaitForSingleObject(self.descendant,0)==0x102
    def captured_bytes(self): return sum(map(len,self.buffers))
    def deadline(self,seconds):
        if self.clock.value-self.started>=seconds or self.output_overflow.is_set():
            return {'state':'HOLD','owned':self.process is not None or self.descendant is not None}
        return {'state':'ACTIVE','owned':True}
    def request_exit(self):
        if self.control is not None:
            try: os.close(self.control)
            except OSError: pass
            self.control=None
    def await_stopped(self,timeout):
        import time
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            child_done=self.process is None or self.process.poll() is not None
            descendant_done=(self.descendant is not None and _kernel.WaitForSingleObject(self.descendant,0)==0) or (self.process is None and self.descendant is None)
            if child_done and descendant_done and all(not t.is_alive() for t in self.readers):
                if self.descendant is not None:
                    code=wintypes.DWORD()
                    if not _kernel.GetExitCodeProcess(self.descendant,ctypes.byref(code)): return {'state':'HOLD','owned':True}
                    self.exit_codes=(self.process.returncode,code.value)
                    _kernel.CloseHandle(self.descendant); self.descendant=None
                self.process=None
                if self in _OWNERS: _OWNERS.remove(self)
                return {'state':'STOPPED','owned':False}
            time.sleep(.01)
        return {'state':'HOLD','owned':True}
    def registry_empty(self): return self.process is None and self.descendant is None and self not in _OWNERS

@pytest.fixture(scope='session',autouse=True)
def _close_cooperative_owners_at_session_end():
    yield
    for owner in tuple(_OWNERS): owner.request_exit()
    for owner in tuple(_OWNERS):
        if owner.await_stopped(5)['state']!='STOPPED':
            pytest.fail('synthetic child ownership remains HOLD; no force cleanup')
