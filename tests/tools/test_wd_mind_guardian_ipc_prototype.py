"""Only opt-in local synthetic host processes; never terminate or kill."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import pytest
from tools import wd_mind_guardian_ipc_prototype as ipc
from tools.wd_mind_guardian_host_prototype import Request

LANE='codex-lead-1';SESSION='b'*64;NONCE='c'*64;MANIFEST='a'*64

def request():return Request(LANE,SESSION,NONCE,MANIFEST)

def eventually(predicate,timeout=8):
    limit=time.monotonic()+timeout
    while time.monotonic()<limit:
        if predicate():return True
        threading.Event().wait(.01)
    return False

# Retain any uncertain process handle; pytest crash persistence remains UNKNOWN.
_UNCERTAIN=[]
@pytest.fixture
def host_factory():
    hosts=[]
    def spawn(mode):
        root=Path(__file__).resolve().parents[2]
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        proc=subprocess.Popen([sys.executable,'-u','-m','tools.wd_mind_guardian_ipc_prototype',
                               '--enable-synthetic','--test-mode',mode],cwd=root,stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=flags)
        hosts.append(proc)
        ready=[];done=threading.Event()
        def read_ready():
            try:ready.append(proc.stdout.readline(64))
            finally:done.set()
        threading.Thread(target=read_ready,daemon=True).start()
        assert done.wait(10) and ready and ready[0].startswith(b'READY ')
        port=int(ready[0].split()[1]);assert 1<=port<=65535
        return proc,('127.0.0.1',port)
    yield spawn
    for proc in hosts:
        if proc.stdin is not None and not proc.stdin.closed:proc.stdin.close()
    for proc in hosts:
        if not eventually(lambda:proc.poll() is not None):
            _UNCERTAIN.append(proc)
            pytest.fail('synthetic host remains owned/HOLD; no forced cleanup')
        assert proc.returncode==0
        if proc.stdout is not None:proc.stdout.close()

@pytest.fixture
def client_factory():
    clients=[]
    def create(address):
        client=ipc.SyntheticIpcClient(enabled=True,address=address)
        assert client.start()=='READY';clients.append(client);return client
    yield create
    for client in clients:
        assert eventually(lambda:client.request_stop() in ('HOLD','STOPPED'))
    for client in clients:assert eventually(lambda:client.status()['state']=='STOPPED')


def test_default_off_never_connects():
    client=ipc.SyntheticIpcClient()
    assert client.start()=='OFF'
    assert client.submit(request(),1)==(None,'OFF')


def test_separate_host_process_returns_bound_untrusted_references(host_factory,client_factory):
    proc,address=host_factory('normal');client=client_factory(address)
    ticket,reason=client.submit(request(),2);assert reason=='PENDING'
    answer=ticket.wait()
    assert answer=={'state':'CANDIDATES_UNTRUSTED','source_refs':['d'*64]}
    assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}
    assert proc.poll() is None


def test_slow_host_times_out_without_duplicate_and_exits_cooperatively(host_factory,client_factory):
    proc,address=host_factory('blocked');client=client_factory(address)
    ticket,reason=client.submit(request(),.1);assert reason=='PENDING'
    assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}
    assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}
    # The remote query can still be running: the client must not claim clean ownership.
    assert eventually(lambda:client.status()['state']=='HOLD')
    assert client.submit(Request(LANE,SESSION,'e'*64,MANIFEST),1)==(None,'BUSY')
    assert proc.poll() is None
    proc.stdin.write(b'R');proc.stdin.flush()
    assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}


def test_response_binding_rejects_each_wrong_field():
    req=request();message=ipc._request(req,100.0);digest=hashlib.sha256(ipc._canonical(message)).hexdigest()
    result={'schema':'wd.synthetic-guardian-response.v1','request_sha256':digest,'lane':req.lane,
            'session':req.session,'nonce':req.nonce,'manifest_sha256':req.manifest_sha256,
            'state':'CANDIDATES_UNTRUSTED','source_refs':['d'*64]}
    for key in ('schema','request_sha256','lane','session','nonce','manifest_sha256'):
        changed=dict(result);changed[key]='e'*64
        with pytest.raises(ValueError):ipc._validate_response(changed,digest,req)
    assert ipc._validate_response(result,digest,req)['state']=='CANDIDATES_UNTRUSTED'


def test_exact_deadline_and_oversized_or_extra_fields_refuse():
    ticket=ipc.Ticket(time.monotonic()-1)
    assert not ticket.finish({'state':'CANDIDATES_UNTRUSTED','source_refs':['d'*64]})
    assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}
    req=request();result={'schema':'wd.synthetic-guardian-response.v1','request_sha256':'d'*64,
        'lane':req.lane,'session':req.session,'nonce':req.nonce,'manifest_sha256':req.manifest_sha256,
        'state':'CANDIDATES_UNTRUSTED','source_refs':['d'*64]*17}
    with pytest.raises(ValueError):ipc._validate_response(result,'d'*64,req)
    result['source_refs']=[];result['raw_text']='synthetic text must refuse'
    with pytest.raises(ValueError):ipc._validate_response(result,'d'*64,req)


def test_client_stop_revokes_ready_but_unconsumed_response(host_factory,client_factory):
    _,address=host_factory('normal');client=client_factory(address)
    ticket,reason=client.submit(request(),2);assert reason=='PENDING'
    assert ticket._event.wait(3)
    assert eventually(lambda:client.request_stop() in ('HOLD','STOPPED'))
    assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}


def test_client_wait_returns_while_connect_is_blocked_without_new_worker(monkeypatch):
    entered,release=threading.Event(),threading.Event()
    class BlockedSocket:
        def settimeout(self,value):pass
        def connect(self,address):
            entered.set();assert release.wait(5);raise OSError('synthetic')
        def close(self):pass
    monkeypatch.setattr(ipc.socket,'socket',lambda *args:BlockedSocket())
    client=ipc.SyntheticIpcClient(enabled=True,address=('127.0.0.1',12345))
    assert client.start()=='READY'
    try:
        ticket,reason=client.submit(request(),.1);assert reason=='PENDING'
        assert entered.wait(3)
        before=time.monotonic()
        assert ticket.wait()=={'state':'WITHOUT_MIND','source_refs':[]}
        assert time.monotonic()-before<2
        assert client.submit(Request(LANE,SESSION,'e'*64,MANIFEST),1)==(None,'BUSY')
        assert client.status()['remote_ownership']=='UNKNOWN'
    finally:
        assert eventually(lambda:client.request_stop() in ('HOLD','STOPPED'))
        release.set();assert eventually(lambda:client.status()['state']=='STOPPED')


@pytest.mark.parametrize('operation',['finish','wait'])
def test_deadline_rechecked_after_permission_gate(monkeypatch,operation):
    class FakeTime:
        def __init__(self):self.value=1.0
        def monotonic(self):return self.value
    clock=FakeTime();monkeypatch.setattr(ipc,'time',clock)
    entered,release=threading.Event(),threading.Event();out=[];leases=[]
    def gate(ticket):
        entered.set();assert release.wait(5)
        return lambda:leases.append('released')
    ticket=ipc.Ticket(2.0,gate)
    value={'state':'CANDIDATES_UNTRUSTED','source_refs':['d'*64]}
    if operation=='wait':
        ticket._state='READY';ticket._value=value;ticket._event.set()
    thread=threading.Thread(target=lambda:out.append(ticket.finish(value) if operation=='finish' else ticket.wait()))
    thread.start()
    try:
        assert entered.wait(3)
        clock.value=2.0
    finally:
        release.set();thread.join(3)
    assert not thread.is_alive() and leases==['released']
    if operation=='finish':assert out==[False]
    else:assert out==[{'state':'WITHOUT_MIND','source_refs':[]}]
    assert ticket._state=='EXPIRED' and ticket._value is None
