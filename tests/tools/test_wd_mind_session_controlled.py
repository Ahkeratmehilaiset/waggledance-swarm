"""Synthetic controlled protocol with the real selected-row resolver.

The source rows and policy are synthetic. Bundle/control readers and candidate
pipe are test doubles, so this does not prove the installed owner or transport.
"""
import hashlib
import io
import json
from pathlib import Path
import threading
import time
import pytest

import test_wd_mind_session_content as fixtures
from tools import wd_bridge_mind_session as session
from tools import wd_mind_runtime_process as transport
from tools import wd_mind_content_query_readonly as reader

source_fixtures=fixtures.source_fixtures
_base_flat_consumer_case=fixtures.flat_consumer_case

@pytest.fixture
def flat_consumer_case(monkeypatch, request):
    # Synthetic checkout files can have CRLF bytes. Pin those actual bytes
    # before requesting the source-gate fixture; production pins are unchanged.
    root=Path(reader.__file__).resolve().parents[1]
    assert root == Path(__file__).resolve().parents[2]
    pins={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
          for name in reader._REQUIRED_CODE}
    monkeypatch.setattr(reader,'_REQUIRED_CODE',pins)
    return request.getfixturevalue('_base_flat_consumer_case')

@pytest.mark.parametrize('fault',['none','missing_go','wrong_go','extra_go','selected','append','policy','short_write','none_write'])
def test_controlled_go_precedes_exactly_one_real_resolution(flat_consumer_case,monkeypatch,tmp_path,fault):
    _,frame,snapshot,_clock=fixtures._located_candidates(flat_consumer_case,monkeypatch)
    _package,owner,_proposals,_snapshot,_clock,_response=flat_consumer_case
    source=Path(snapshot['messages']['source_path'])
    own=[]
    with source.open('rb') as stream:
        for item in frame['candidates']:
            stream.seek(item['source_offset'])
            if json.loads(stream.read(item['source_length']))['agent']=='codex-lead-1': own.append(item)
    assert own
    frame['candidates'],frame['scope']=own[:1],'own'
    policy=tmp_path/'synthetic-policy.json'
    policy.write_bytes(snapshot['identifier_policy_binding'][0])
    sha=hashlib.sha256(policy.read_bytes()).hexdigest()
    nonce='1'*64
    monkeypatch.setattr(session.secrets,'token_hex',lambda _:nonce)
    query=transport._LocalCandidateQuery('synthetic',{})
    monkeypatch.setattr(query,'start',lambda:None)
    def wait(*,timeout):
        response=dict(frame,nonce=nonce)
        return {'status':'CANDIDATES','authority':'none','sender_authentication':'UNKNOWN',
                'source_verification':'UNVERIFIED','candidate_frame':transport._local_candidate_encode(response)}
    monkeypatch.setattr(query,'wait',wait)
    monkeypatch.setattr(transport,'create_local_candidate_query',lambda **_:query)
    counts=[]
    original=session._SelectedRowResolver.resolve
    def counted(self,**kwargs):
        counts.append('resolve')
        return original(self,**kwargs)
    monkeypatch.setattr(session._SelectedRowResolver,'resolve',counted)
    class Control:
        def __init__(self): self._bytes=io.BytesIO()
        def read(self,n): return self._bytes.read(n)
    control=Control()
    class Output:
        def __init__(self): self.data=bytearray();self.ready=[];self.lock=threading.Lock()
        def write(self,raw):
            if raw.endswith(b'\n') and not self.ready:
                value=json.loads(raw)
                assert value=={'schema':'wd.mind-controlled-read-ready.v1','authority':'none','nonce':nonce}
                assert counts==[]
                self.ready.append(value)
                if fault=='selected':
                    data=source.read_bytes();entry=own[0];start=entry['source_offset'];end=start+entry['source_length']
                    changed=data[start:end].replace(b'network',b'Network',1)
                    assert changed!=data[start:end] and len(changed)==end-start
                    source.write_bytes(data[:start]+changed+data[end:])
                if fault=='append':
                    data=source.read_bytes();entry=own[0]
                    with source.open('ab') as stream: stream.write(data[entry['source_offset']:entry['source_offset']+entry['source_length']])
                if fault=='policy': policy.write_bytes(b'changed')
                go={'missing_go':b'','wrong_go':b'G'+b'f'*64,'extra_go':b'G'+nonce.encode()+b'X'}.get(fault,b'G'+nonce.encode())
                control._bytes=io.BytesIO(go)
            with self.lock:
                if self.ready and not raw.endswith(b'\n') and fault=='short_write':
                    self.data.extend(raw[:1])
                    return 1
                if self.ready and not raw.endswith(b'\n') and fault=='none_write':
                    return None
                self.data.extend(raw)
            return len(raw)
        def flush(self): pass
    out=Output();err=io.BytesIO()
    argv=['--mind-local-candidate-read-controlled','--policy-path',str(policy),'--policy-sha256',sha,
          '--approval-reference-sha256','c'*64,'--lane','codex-lead-1','--timeout-seconds','90']
    code=session._local_candidate_entry(argv,out,err,str(owner._root),owner._pin,control=control)
    assert len(out.ready)==1
    ready,separator,raw=bytes(out.data).partition(b'\n')
    assert separator
    if fault in ('none','append'):
        assert code==0 and err.getvalue()==b'' and counts==['resolve'] and 0<len(raw)<=16384
        assert json.loads(raw)['results'][0]['source_event_sha256']==own[0]['source_event_sha256']
    elif fault in ('short_write','none_write'):
        assert code!=0 and b'mind_session_content_refused' in err.getvalue()
        assert raw==(b'{' if fault=='short_write' else b'') and counts==['resolve']
    else:
        assert code!=0 and not raw and b'mind_session_content_refused' in err.getvalue()
        assert counts==([] if fault in ('missing_go','wrong_go','extra_go') else ['resolve'])

def test_controlled_mode_requires_private_control_stream():
    argv=['--mind-local-candidate-read-controlled']+['x']*10
    out,err=io.BytesIO(),io.BytesIO()
    assert session._local_candidate_entry(argv,out,err,'x','x')!=0
    assert out.getvalue()==b''


def test_timeout_while_waiting_for_go_retains_owner_and_discards_late_content(monkeypatch):
    """Only owned in-process synthetic threads are used; no child or real pipe."""
    entered, release = threading.Event(), threading.Event()
    nonce='1'*64
    monkeypatch.setattr(session.secrets,'token_hex',lambda _:nonce)
    query=transport._LocalCandidateQuery('synthetic',{})
    monkeypatch.setattr(query,'start',lambda:None)
    monkeypatch.setattr(query,'wait',lambda **_: {'status':'CANDIDATES','authority':'none',
        'sender_authentication':'UNKNOWN','source_verification':'UNVERIFIED','candidate_frame':b'synthetic'})
    monkeypatch.setattr(transport,'create_local_candidate_query',lambda **_:query)
    monkeypatch.setattr(session,'_selected_pin_read',lambda *_:b'{}')
    called=[]
    class Resolver:
        def resolve(self,**kwargs):
            called.append(True)
            raise AssertionError('must not resolve after timeout')
    monkeypatch.setattr(session,'create_selected_row_resolver',lambda **_:Resolver())
    class Control:
        def read(self,n):
            entered.set()
            release.wait()
            return b'G'+nonce.encode() if n==65 else b''
    out,err=io.BytesIO(),io.BytesIO()
    argv=['--mind-local-candidate-read-controlled','--policy-path','synthetic','--policy-sha256','a'*64,
          '--approval-reference-sha256','b'*64,'--lane','codex-lead-1','--timeout-seconds','1']
    before={id(p) for p in session._LOCAL_CANDIDATE_PENDING}
    try:
        code=session._local_candidate_entry(argv,out,err,'synthetic','c'*64,control=Control())
        created=[p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]
        assert entered.is_set() and code==2 and len(created)==1
        worker,state=created[0]
        assert worker.is_alive() and state['abandoned'] and state['result'] is None
        ready,sep,content=out.getvalue().partition(b'\n')
        assert sep and json.loads(ready)['schema']=='wd.mind-controlled-read-ready.v1' and not content
    finally:
        created=[p for p in session._LOCAL_CANDIDATE_PENDING if id(p) not in before]
        interrupted=None
        for worker,state in created:
            while True:
                try:
                    release.set()
                    if state['worker_done'].is_set() and worker.ident is not None:
                        worker.join(1)
                        if not worker.is_alive(): break
                except BaseException as exc:
                    if interrupted is None: interrupted=exc
                try:
                    time.sleep(.01)
                except BaseException as exc:
                    if interrupted is None: interrupted=exc
        if interrupted is not None: raise interrupted
    assert called==[] and state['result'] is None
    assert not out.getvalue().partition(b'\n')[2]
    session._reap_local_candidate_pending()
    assert created[0] not in session._LOCAL_CANDIDATE_PENDING
