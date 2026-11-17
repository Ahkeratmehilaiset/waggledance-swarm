"""Default-off synthetic localhost IPC demonstration; NOT a production guardian.

Only fixed fake resources and synthetic reference hashes are supported. There
is no peer authentication, process-wide reservation, crash-persistent HOLD,
real source verification, launcher integration, or native ownership proof.
No request, result, or raw Mind text is written to disk by this module.
"""
import hashlib
import json
import math
import socket
import struct
import threading
import time

from tools.wd_mind_startup_guardian import Guardian,Resources
from tools.wd_mind_guardian_host_prototype import Request,SyntheticHost,SyntheticClient,_valid

_LIMIT=4096

def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')

def _send(sock,value):
    raw=_canonical(value)
    if not 0<len(raw)<=_LIMIT:raise ValueError('frame')
    sock.sendall(struct.pack('!I',len(raw))+raw)

def _exact(sock,count):
    out=bytearray()
    while len(out)<count:
        data=sock.recv(count-len(out))
        if not data:raise ValueError('eof')
        out.extend(data)
    return bytes(out)

def _recv(sock):
    size=struct.unpack('!I',_exact(sock,4))[0]
    if not 0<size<=_LIMIT:raise ValueError('frame')
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate')
            result[key]=value
        return result
    raw=_exact(sock,size)
    value=json.loads(raw.decode('ascii'),object_pairs_hook=unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('constant')))
    if type(value) is not dict or _canonical(value)!=raw:raise ValueError('canonical')
    return value

def _request(request,deadline):
    if not _valid(request) or type(deadline) not in (float,int) or not math.isfinite(deadline):raise ValueError('request')
    return {'schema':'wd.synthetic-guardian-request.v1','lane':request.lane,'session':request.session,
            'nonce':request.nonce,'manifest_sha256':request.manifest_sha256,'deadline':deadline}

def _validate_response(response,request_digest,request):
    keys={'schema','request_sha256','lane','session','nonce','manifest_sha256','state','source_refs'}
    if type(response) is not dict or set(response)!=keys:raise ValueError('response')
    if (response['schema']!='wd.synthetic-guardian-response.v1' or response['request_sha256']!=request_digest
            or response['lane']!=request.lane or response['session']!=request.session
            or response['nonce']!=request.nonce or response['manifest_sha256']!=request.manifest_sha256):raise ValueError('binding')
    refs=response['source_refs']
    if (response['state'] not in ('CANDIDATES_UNTRUSTED','WITHOUT_MIND') or type(refs) is not list
            or len(refs)>16 or any(type(x) is not str or len(x)!=64 or any(c not in '0123456789abcdef' for c in x) for x in refs)
            or (response['state']=='WITHOUT_MIND' and refs)
            or (response['state']=='CANDIDATES_UNTRUSTED' and not refs)):raise ValueError('result')
    return {'state':response['state'],'source_refs':list(refs)}

class Ticket:
    def __init__(self,deadline,permit=None):
        self.deadline=deadline;self._permit=permit or (lambda ticket:(lambda:None))
        self._lock=threading.Lock();self._state='PENDING';self._value=None
        self._event=threading.Event()
    def finish(self,value):
        if not self._lock.acquire(False):return False
        release=None
        try:
            if self._state=='PENDING' and time.monotonic()<self.deadline:
                release=self._permit(self)
            accepted=release is not None
            if accepted:self._state='READY';self._value=value
            else:self._state='EXPIRED';self._value=None
        finally:
            if release is not None:release()
            self._lock.release()
        self._event.set();return accepted
    def wait(self):
        remaining=max(0,self.deadline-time.monotonic())
        if remaining:self._event.wait(remaining)
        if not self._lock.acquire(False):return {'state':'WITHOUT_MIND','source_refs':[]}
        release=None
        try:
            if self._state=='READY' and time.monotonic()<self.deadline:
                release=self._permit(self)
            if release is not None:
                value=self._value;self._value=None;self._state='CONSUMED';return value
            self._value=None;self._state='EXPIRED'
            return {'state':'WITHOUT_MIND','source_refs':[]}
        finally:
            if release is not None:release()
            self._lock.release()

class SyntheticIpcClient:
    """One prestarted pump, one request at a time, no per-request threads."""
    def __init__(self,*,enabled=False,address=None):
        if type(enabled) is not bool:raise ValueError('enabled')
        if enabled and (type(address) is not tuple or len(address)!=2 or address[0]!='127.0.0.1'
                        or type(address[1]) is not int or not 1<=address[1]<=65535):raise ValueError('address')
        self.enabled,self.address=enabled,address
        self._lock=threading.Lock();self._wake=threading.Event();self._stop=threading.Event()
        self._active=None;self._latest=None;self._thread=None;self._state='OFF' if not enabled else 'NEW'
    def start(self):
        # Must happen before a launcher's bounded request path.
        if not self.enabled:return 'OFF'
        if not self._lock.acquire(False):return 'HOLD'
        try:
            if self._thread is not None:return self._state
            self._thread=threading.Thread(target=self._run,name='synthetic-ipc-client',daemon=True)
            self._state='READY'
            try:self._thread.start()
            except BaseException:self._state='HOLD'
            return self._state
        finally:self._lock.release()
    def submit(self,request,budget):
        started=time.monotonic()
        if not self.enabled:return None,'OFF'
        if not _valid(request) or type(budget) not in (int,float) or not math.isfinite(budget) or not 0<budget<=30:return None,'INVALID'
        if not self._lock.acquire(False):return None,'BUSY'
        try:
            if self._state!='READY' or self._active is not None or self._stop.is_set():return None,'BUSY'
            ticket=Ticket(started+budget,self._permit);self._latest=ticket;self._active=(request,ticket)
            self._state='BUSY';self._wake.set();return ticket,'PENDING'
        finally:self._lock.release()
    def _permit(self,ticket):
        if self._stop.is_set() or not self._lock.acquire(False):return None
        if not self._stop.is_set() and self._latest is ticket:return self._lock.release
        self._lock.release();return None
    def request_stop(self):
        if not self._lock.acquire(False):return 'BUSY'
        try:
            self._stop.set();self._wake.set()
            return 'STOPPED' if self._state=='STOPPED' else 'HOLD'
        finally:self._lock.release()
    def status(self):return {'state':self._state,'remote_ownership':'UNKNOWN',
                             'crash_ownership':'UNKNOWN','peer_authentication':'UNKNOWN'}
    def _run(self):
        try:
            while not self._stop.is_set():
                self._wake.wait(.01);self._wake.clear()
                with self._lock:active=self._active
                if active is None:continue
                request,ticket=active
                held=False
                try:
                    message=_request(request,ticket.deadline)
                    digest=hashlib.sha256(_canonical(message)).hexdigest()
                    if time.monotonic()>=ticket.deadline:raise ValueError('expired')
                    # Any potentially blocking connection or I/O lives only here.
                    sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
                    try:
                        sock.settimeout(3);sock.connect(self.address);_send(sock,message)
                        reply=_recv(sock);value=_validate_response(reply,digest,request)
                    finally:sock.close()
                    ticket.finish(value)
                    held=value['state']!='CANDIDATES_UNTRUSTED'
                except BaseException:
                    held=True;ticket.finish({'state':'WITHOUT_MIND','source_refs':[]})
                with self._lock:self._active=None
                if held:
                    self._state='HOLD'
                    # A transport failure cannot prove the remote owner stopped.
                    while not self._stop.is_set():self._wake.wait(.05);self._wake.clear()
                elif not self._stop.is_set():self._state='READY'
        finally:
            self._state='STOPPED'

class _SyntheticAdapter:
    def start(self):return {'state':'READY','owned':True}
    def status(self):return {'state':'READY','owned':True}
    def close(self):return {'state':'STOPPED','owned':False}

def serve_synthetic(mode,control):
    """Explicit opt-in fake host, in a separate test-owned process only."""
    if mode not in ('normal','blocked'):raise ValueError('mode')
    release=threading.Event();stop=threading.Event();closed=threading.Event()
    def read_control():
        try:
            while True:
                byte=control.read(1)
                if not byte:break
                if byte==b'R':release.set()
        finally:stop.set();release.set();closed.set()
    threading.Thread(target=read_control,name='synthetic-host-control',daemon=True).start()
    def query(resources,ticket):
        if mode=='blocked':release.wait(60)
        return {'schema':'wd.mind-guardian-candidates.v1','lane':ticket.lane,'nonce':ticket.nonce,
                'manifest_sha256':ticket.manifest_sha256,'query_id':ticket.query_id,
                'status':'CANDIDATES','source_refs':['d'*64]}
    adapters=Resources(*(_SyntheticAdapter() for _ in range(3)))
    guardian=Guardian(enabled=True,manifest_sha256='a'*64,resources=adapters,query=query)
    host=SyntheticHost(enabled=True,guardian=guardian);host.start()
    while host.status()['state']!='READY':
        if stop.is_set() or host.status()['state']=='HOLD':raise RuntimeError('host_not_ready')
        time.sleep(.005)
    listen=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
    listen.bind(('127.0.0.1',0));listen.listen(1);listen.settimeout(.05)
    print('READY '+str(listen.getsockname()[1]),flush=True)
    try:
        while not stop.is_set():
            try:connection,_=listen.accept()
            except socket.timeout:continue
            try:
                connection.settimeout(3);message=_recv(connection)
                keys={'schema','lane','session','nonce','manifest_sha256','deadline'}
                if set(message)!=keys or message['schema']!='wd.synthetic-guardian-request.v1':raise ValueError('request')
                request=Request(message['lane'],message['session'],message['nonce'],message['manifest_sha256'])
                now=time.monotonic();deadline=message['deadline']
                if not _valid(request) or type(deadline) not in (int,float) or not math.isfinite(deadline):raise ValueError('request')
                remaining=deadline-now
                if remaining<=0 or remaining>30:raise ValueError('deadline')
                exchange,reason=SyntheticClient(host).submit(request,remaining)
                output=({'state':'WITHOUT_MIND'} if exchange is None else SyntheticClient(host).wait(exchange))
                refs=output.get('result',{}).get('source_refs',[]) if output.get('state')=='CANDIDATES_UNTRUSTED' else []
                reply={'schema':'wd.synthetic-guardian-response.v1','request_sha256':hashlib.sha256(_canonical(message)).hexdigest(),
                       'lane':request.lane,'session':request.session,'nonce':request.nonce,
                       'manifest_sha256':request.manifest_sha256,'state':'CANDIDATES_UNTRUSTED' if refs else 'WITHOUT_MIND',
                       'source_refs':refs}
                _send(connection,reply)
            except (OSError,ValueError,KeyError,TypeError):pass
            finally:connection.close()
    finally:
        listen.close();stop.set();release.set()
        # Retain the host until cooperative adapters report STOPPED; never kill.
        while host.request_stop()!='STOPPED':time.sleep(.01)
    return 0

if __name__=='__main__':
    import sys
    if len(sys.argv)!=4 or sys.argv[1:3]!=['--enable-synthetic','--test-mode']:
        raise SystemExit(2)
    raise SystemExit(serve_synthetic(sys.argv[3],sys.stdin.buffer))
