"""Synthetic stdin/control protocol tests; not a generic process guardian."""
import importlib.util
import os
from pathlib import Path
import pytest

pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows synthetic handle fixture')
_spec=importlib.util.spec_from_file_location('mind_synthetic_owner_fixture',Path(__file__).with_name('test_wd_mind_cooperative_harness.py'))
fixture=importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture)
if os.name=='nt':
    import msvcrt, subprocess, sys, threading

_PAYLOAD=b'synthetic-raw-control-v1'
class RawInputSyntheticOwner(fixture.SyntheticOwner):
    """Fixed synthetic stdin payload; separate inherited pipe carries EOF."""
    def start_with_payload(self,payload):
        if type(payload) is not bytes or len(payload)>128: raise ValueError('stdin_limit')
        if self.process is not None: raise RuntimeError('already_started')
        source=fixture._CHILD.replace('grandchild_code = ',
            "raw = sys.stdin.buffer.read(129)\nif raw != b'synthetic-raw-control-v1': raise SystemExit(12)\ngrandchild_code = ",1)
        read_fd,write_fd=os.pipe(); handle=msvcrt.get_osfhandle(read_fd)
        os.set_handle_inheritable(handle,True)
        info=subprocess.STARTUPINFO(); info.lpAttributeList={'handle_list':[handle]}
        try:
            self.process=subprocess.Popen([sys.executable,'-u','-c',source,str(handle),'quiet'],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,
                startupinfo=info,creationflags=subprocess.CREATE_NO_WINDOW)
            self.control=write_fd; write_fd=None
        finally:
            os.set_handle_inheritable(handle,False); os.close(read_fd)
            if write_fd is not None: os.close(write_fd)
        for index,stream in enumerate((self.process.stdout,self.process.stderr)):
            thread=threading.Thread(target=self._drain,args=(index,stream),daemon=True)
            self.readers.append(thread); thread.start()
        self.process.stdin.write(payload)
        self.process.stdin.close()

def test_raw_stdin_eof_is_not_control_eof():
    owner=RawInputSyntheticOwner(fixture.FakeClock(),1024)
    try:
        owner.start_with_payload(_PAYLOAD)
        assert owner.await_ready(20)
        assert owner.process.stdin.closed
        assert owner.process_alive() and owner.registered_descendant_is_owned()
        assert owner.control is not None
        owner.request_exit()
        assert owner.await_stopped(20)=={'state':'STOPPED','owned':False}
        assert owner.exit_codes==(0,0) and owner.registry_empty()
    finally:
        owner.request_exit()
        owner.await_stopped(20)

def test_oversize_stdin_refuses_before_spawn():
    owner=RawInputSyntheticOwner(fixture.FakeClock(),1024)
    try:
        with pytest.raises(ValueError,match='stdin_limit'): owner.start_with_payload(b'x'*129)
        assert owner.process is None
    finally:
        assert owner.await_stopped(0.1)=={'state':'STOPPED','owned':False}

def test_unknown_descendant_remains_hold_after_parent_exits():
    class ExitedSyntheticParent:
        def poll(self): return 0
    owner=RawInputSyntheticOwner(fixture.FakeClock(),1024)
    try:
        owner.process=ExitedSyntheticParent()
        assert owner.descendant is None
        assert owner.await_stopped(0.05)=={'state':'HOLD','owned':True}
        assert owner in fixture._OWNERS
    finally:
        # This case is a test double, with no OS process or handle.
        owner.process=None
        assert owner.await_stopped(0.1)=={'state':'STOPPED','owned':False}
