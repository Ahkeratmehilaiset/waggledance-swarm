"""Only in-process synthetic guardian adapters; never starts OS children."""
import threading
import time
import pytest
from tools.wd_mind_startup_guardian import Guardian, Resources
from tools.wd_mind_guardian_host_prototype import Request, SyntheticHost, SyntheticClient, _Exchange

MANIFEST='a'*64;SESSION='b'*64;NONCE='c'*64;LANE='codex-lead-1'
class Adapter:
    def __init__(self,log,name):self.log,self.name=log,name
    def start(self):self.log.append(('start',self.name));return {'state':'READY','owned':True}
    def status(self):return {'state':'READY','owned':True}
    def close(self):self.log.append(('close',self.name));return {'state':'STOPPED','owned':False}

def result(ticket):
    return {'schema':'wd.mind-guardian-candidates.v1','lane':ticket.lane,'nonce':ticket.nonce,
            'manifest_sha256':ticket.manifest_sha256,'query_id':ticket.query_id,
            'status':'CANDIDATES','source_refs':['d'*64]}

def request():return Request(LANE,SESSION,NONCE,MANIFEST)

def eventually(predicate,timeout=5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return True
        threading.Event().wait(.005)
    return False

def setup(query):
    log=[];resources=Resources(*(Adapter(log,name) for name in ('owner','probe','pipe')))
    guardian=Guardian(enabled=True,manifest_sha256=MANIFEST,resources=resources,query=query)
    host=SyntheticHost(enabled=True,guardian=guardian);host.start()
    assert eventually(lambda:host.status()['state']=='READY')
    return host,SyntheticClient(host),log

def stop(host):
    assert eventually(lambda:host.request_stop() in ('HOLD','STOPPED'))
    assert eventually(lambda:host.status()['state']=='STOPPED')


def test_off_is_inert_and_default():
    host=SyntheticHost();assert host.start()=='OFF'
    exchange,reason=SyntheticClient(host).submit(request(),.1)
    assert exchange is None and reason=='OFF'


def test_candidate_arrives_once_and_is_explicitly_untrusted():
    host,client,log=setup(lambda resources,ticket:result(ticket))
    try:
        exchange,reason=client.submit(request(),1)
        assert reason=='PENDING'
        answer=client.wait(exchange)
        assert answer['state']=='CANDIDATES_UNTRUSTED'
        assert answer['result']['source_refs']==['d'*64]
        assert exchange.read()['state']=='WITHOUT_MIND'
    finally:stop(host)
    assert [n for action,n in log if action=='close']==['pipe','probe','owner']


def test_blocked_submission_times_out_and_occupies_single_slot(monkeypatch):
    host,client,_=setup(lambda resources,ticket:result(ticket))
    entered,release=threading.Event(),threading.Event()
    original=host.guardian.submit
    def blocked(**kwargs):
        entered.set();assert release.wait(5);return original(**kwargs)
    monkeypatch.setattr(host.guardian,'submit',blocked)
    try:
        exchange,reason=client.submit(request(),.1);assert reason=='PENDING'
        assert entered.wait(3)
        assert client.wait(exchange)['state']=='WITHOUT_MIND'
        assert client.submit(Request(LANE,SESSION,'e'*64,MANIFEST),1)==(None,'BUSY')
        assert host.request_stop()=='HOLD'
        assert host.status()['ownership']=='UNKNOWN'
    finally:
        release.set();assert eventually(lambda:host.status()['state']=='STOPPED')
    assert exchange.read()['state']=='WITHOUT_MIND'


def test_result_exactly_at_deadline_is_discarded():
    clock=[1.0];exchange=_Exchange(request(),2.0,lambda:clock[0])
    clock[0]=2.0
    assert not exchange.complete({'schema':'synthetic'})
    assert exchange.read()=={'state':'WITHOUT_MIND','reason':'EXPIRED'}


def test_bad_binding_and_oversized_inputs_fail_without_query():
    calls=[];host,client,_=setup(lambda resources,ticket:(calls.append(ticket),result(ticket))[1])
    try:
        assert client.submit(Request([],SESSION,NONCE,MANIFEST),1)==(None,'INVALID')
        assert client.submit(Request(LANE,SESSION,'c'*100000,MANIFEST),1)==(None,'INVALID')
        assert client.submit(request(),float('nan'))==(None,'INVALID')
        assert calls==[]
    finally:stop(host)


def test_wrong_manifest_and_forged_reference_result_are_refused():
    def bad(resources,ticket):
        answer=result(ticket);answer['source_refs']=['not-a-hash'];return answer
    host,client,_=setup(bad)
    try:
        exchange,reason=client.submit(Request(LANE,SESSION,NONCE,'e'*64),1)
        assert reason=='PENDING'
        assert client.wait(exchange)['state']=='WITHOUT_MIND'
        assert eventually(lambda:host.status()['state']=='READY')
        exchange,reason=client.submit(request(),1);assert reason=='PENDING'
        assert client.wait(exchange)['state']=='WITHOUT_MIND'
    finally:stop(host)


def test_blocked_adapter_start_keeps_client_independent_and_shutdown_held():
    entered,release=threading.Event(),threading.Event();log=[]
    class Blocked(Adapter):
        def start(self):
            entered.set();assert release.wait(5)
            return super().start()
    resources=Resources(Blocked(log,'owner'),Adapter(log,'probe'),Adapter(log,'pipe'))
    guardian=Guardian(enabled=True,manifest_sha256=MANIFEST,resources=resources,
                      query=lambda resources,ticket:result(ticket))
    host=SyntheticHost(enabled=True,guardian=guardian);client=SyntheticClient(host);host.start()
    try:
        assert entered.wait(3)
        assert client.submit(request(),.1)==(None,'BUSY')
        assert host.request_stop()=='HOLD'
        assert host.status()['ownership']=='UNKNOWN'
    finally:
        release.set();assert eventually(lambda:host.status()['state']=='STOPPED')


def test_late_query_result_is_discarded_before_slot_reuse():
    entered,release=threading.Event(),threading.Event();calls=[]
    def query(resources,ticket):
        calls.append(ticket.nonce);entered.set()
        assert release.wait(5)
        return result(ticket)
    host,client,_=setup(query)
    try:
        exchange,reason=client.submit(request(),.1);assert reason=='PENDING'
        assert entered.wait(3)
        assert client.wait(exchange)['state']=='WITHOUT_MIND'
        assert client.submit(Request(LANE,SESSION,'e'*64,MANIFEST),1)==(None,'BUSY')
        release.set()
        assert eventually(lambda:host.status()['state']=='READY')
        assert exchange.read()['state']=='WITHOUT_MIND'
        newer,reason=client.submit(Request(LANE,SESSION,'e'*64,MANIFEST),1)
        assert reason=='PENDING'
        response=client.wait(newer)
        assert response['state']=='CANDIDATES_UNTRUSTED', response
        assert calls==[NONCE,'e'*64]
    finally:
        release.set();stop(host)


def test_publisher_releases_exchange_lock_before_waking_client():
    observed=[]
    class CheckingEvent:
        def __init__(self,exchange):self.exchange=exchange
        def set(self):
            acquired=self.exchange._lock.acquire(False);observed.append(acquired)
            if acquired:self.exchange._lock.release()
    accepted=_Exchange(request(),2.0,lambda:1.0);accepted.event=CheckingEvent(accepted)
    assert accepted.complete({'schema':'synthetic'})
    expired=_Exchange(request(),2.0,lambda:1.0);expired.event=CheckingEvent(expired)
    assert expired.expire()
    assert observed==[True,True]


def test_stop_expiration_discards_ready_but_unconsumed_result():
    exchange=_Exchange(request(),2.0,lambda:1.0)
    assert exchange.complete({'schema':'synthetic'})
    assert exchange.expire()
    assert exchange.read()=={'state':'WITHOUT_MIND','reason':'EXPIRED'}


def _unconsumed_ready(host,client):
    exchange,reason=client.submit(request(),1)
    assert reason=='PENDING' and exchange.event.wait(3)
    assert exchange._state=='READY'
    assert eventually(lambda:host.status()['state']=='READY')
    return exchange


def test_detach_invalidates_a_ready_result_even_after_slot_release():
    host,client,_=setup(lambda resources,ticket:result(ticket))
    try:
        exchange=_unconsumed_ready(host,client)
        assert host.detach(lane=LANE,session=SESSION)
        assert exchange.read()['state']=='WITHOUT_MIND'
    finally:stop(host)


def test_session_replacement_invalidates_old_ready_result():
    host,client,_=setup(lambda resources,ticket:result(ticket))
    try:
        exchange=_unconsumed_ready(host,client)
        assert host.attach(lane=LANE,session='f'*64)
        assert exchange.read()['state']=='WITHOUT_MIND'
    finally:stop(host)


def test_stop_invalidates_ready_result_after_active_slot_released():
    host,client,_=setup(lambda resources,ticket:result(ticket))
    try:
        exchange=_unconsumed_ready(host,client)
        host.request_stop()
        assert exchange.read()['state']=='WITHOUT_MIND'
    finally:stop(host)


@pytest.mark.parametrize('action',['stop','detach','replace'])
def test_invalidation_survives_exchange_lock_contention(action):
    host,client,_=setup(lambda resources,ticket:result(ticket))
    try:
        exchange=_unconsumed_ready(host,client)
        assert exchange._lock.acquire(False)
        try:
            if action=='stop':
                assert host.request_stop()=='HOLD'
            elif action=='detach':
                assert host.detach(lane=LANE,session=SESSION)
            else:
                assert host.attach(lane=LANE,session='f'*64)
        finally:exchange._lock.release()
        assert exchange.read()['state']=='WITHOUT_MIND'
    finally:stop(host)


def test_host_lock_contention_fails_closed_without_waiting():
    host,client,_=setup(lambda resources,ticket:result(ticket))
    try:
        exchange=_unconsumed_ready(host,client)
        assert host._lock.acquire(False)
        try:assert exchange.read()['state']=='WITHOUT_MIND'
        finally:host._lock.release()
    finally:stop(host)


@pytest.mark.parametrize('action',['stop','detach','replace'])
def test_consumption_and_session_invalidation_share_atomic_gate(monkeypatch,action):
    host,client,_=setup(lambda resources,ticket:result(ticket))
    checked,release=threading.Event(),threading.Event();out=[]
    original=host._permitted
    def gated(exchange):
        lease=original(exchange)
        if threading.current_thread().name=='synthetic-result-reader' and lease:
            checked.set();assert release.wait(5)
        return lease
    monkeypatch.setattr(host,'_permitted',gated)
    try:
        exchange=_unconsumed_ready(host,client)
        reader=threading.Thread(target=lambda:out.append(exchange.read()),name='synthetic-result-reader')
        reader.start();assert checked.wait(3)
        try:
            if action=='stop':during=host.request_stop()
            elif action=='detach':during=host.detach(lane=LANE,session=SESSION)
            else:during=host.attach(lane=LANE,session='f'*64)
        finally:
            release.set();reader.join(3)
        assert not reader.is_alive()
        assert during==('BUSY' if action=='stop' else False)
        assert out[0]['state']=='CANDIDATES_UNTRUSTED'
        if action=='stop':assert host.request_stop()=='HOLD'
        elif action=='detach':assert host.detach(lane=LANE,session=SESSION)
        else:assert host.attach(lane=LANE,session='f'*64)
    finally:
        release.set();stop(host)


def test_exchange_complete_rechecks_deadline_after_permission_gate():
    now=[1.0];entered,release=threading.Event(),threading.Event();out=[];leases=[]
    def permit(exchange):
        entered.set();assert release.wait(5)
        return lambda:leases.append('released')
    exchange=_Exchange(request(),2.0,lambda:now[0],permit)
    publisher=threading.Thread(target=lambda:out.append(exchange.complete({'schema':'synthetic'})))
    publisher.start()
    try:
        assert entered.wait(3)
        now[0]=2.0
    finally:
        release.set();publisher.join(3)
    assert not publisher.is_alive() and leases==['released']
    assert out==[False] and exchange._state=='EXPIRED' and exchange._result is None
