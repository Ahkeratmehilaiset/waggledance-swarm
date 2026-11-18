"""Synthetic Windows AF_PIPE and actual owner/prewarm/resolver budget integration.

No model or live data. Synthetic checkout/pin readers and worker are fixtures;
this is not installed-bundle, native worker, caller-authentication or launch proof.
"""
import io, json, math, os, secrets, threading, time
from pathlib import Path
from multiprocessing.connection import Client
import pytest

import importlib.util, sys
_fixture_path = Path(__file__).with_name('test_wd_mind_owner_source_budget.py')
_fixture_spec = importlib.util.spec_from_file_location('budget_pipe_owner_fixtures', _fixture_path)
_fixture_module = importlib.util.module_from_spec(_fixture_spec)
sys.modules[_fixture_spec.name] = _fixture_module
_fixture_spec.loader.exec_module(_fixture_module)
synthetic_checkout_pins = _fixture_module.synthetic_checkout_pins
flat_consumer_case = _fixture_module.flat_consumer_case
source_fixtures = _fixture_module.source_fixtures
_instrument_source_streams = _fixture_module._instrument_source_streams
_fixtures = _fixture_module._fixtures
_full_fixtures = _fixture_module._full_fixtures
from tools import wd_mind_full_runtime as runtime
from tools import wd_mind_absent_generation_prefix_readonly as prefix
from tools import wd_mind_content_query_readonly as reader
from tools import wd_bridge_mind_session as session
from tools import wd_mind_runtime_process as process


def _isolate_synthetic_pin(original, monkeypatch):
    # A fresh synthetic pin isolates the OS rendezvous across parallel runs.
    # It is not a deployed-manifest pin or authentication of a pipe peer.
    pin = secrets.token_hex(32)
    assert pin != 'b' * 64
    assert pin != os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH', '').lower()
    monkeypatch.setattr(original, '_pin', pin)
    monkeypatch.setattr(original._gate, '_manifest_pin', pin)
    return pin


def _bind_synthetic_owner(original, snapshot, binding, factory):
    binding._PrivateBinding__snapshot = snapshot
    binding._PrivateBinding__bundle = Path(original._root)
    binding._PrivateBinding__manifest = original._pin
    binding._PrivateBinding__policy_pin = reader._sha(snapshot['identifier_policy_binding'][0])
    factory._bundle, factory._manifest = str(original._root), original._pin


def _synthetic_routes(original, owner):
    name = process.local_candidate_pipe_name(manifest_sha256=original._pin)
    return {'enabled':True, 'owner':owner, 'pipe_name':name}, (original._root, original._pin)


def _assert_synthetic_sources(tmp_path, snapshot):
    source = Path(snapshot['messages']['source_path'])
    sidecar = Path(snapshot['messages']['generation_path'])
    assert tmp_path.resolve() in source.resolve().parents and tmp_path.resolve() in sidecar.resolve().parents
    return source, sidecar


def test_synthetic_pin_routes_are_consistent_without_pipe(synthetic_checkout_pins, flat_consumer_case,
                                                           tmp_path, monkeypatch):
    _, original, _, snapshot, _, _ = flat_consumer_case
    pin = _isolate_synthetic_pin(original, monkeypatch)
    source, sidecar = _assert_synthetic_sources(tmp_path, snapshot)
    _, _, binding, factory, launches = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    _bind_synthetic_owner(original, snapshot, binding, factory)
    owner = object()
    service_args, client_args = _synthetic_routes(original, owner)
    assert original._pin == original._gate._manifest_pin == binding._PrivateBinding__manifest == factory._manifest == pin
    assert binding._PrivateBinding__snapshot is snapshot and binding._PrivateBinding__bundle == Path(original._root)
    assert binding._PrivateBinding__policy_pin == reader._sha(snapshot['identifier_policy_binding'][0])
    assert factory._bundle == str(original._root) and client_args == (original._root, pin)
    assert service_args == {'enabled':True, 'owner':owner,
                            'pipe_name':process.local_candidate_pipe_name(manifest_sha256=pin)}
    assert source != sidecar and launches == []

@pytest.mark.skipif(os.name != 'nt', reason='requires Windows AF_PIPE')
@pytest.mark.parametrize('gate_on', [False, True])
def test_synthetic_owner_real_pipe_selected_resolver_budget(
        synthetic_checkout_pins, flat_consumer_case, tmp_path, monkeypatch, gate_on):
    package, original, _, old, clock, _ = flat_consumer_case
    _isolate_synthetic_pin(original, monkeypatch)
    _fixtures._located_candidates(flat_consumer_case, monkeypatch)  # synthetic pin/control fixtures
    snapshot = dict(old, messages=dict(old['messages']))
    source, sidecar = _assert_synthetic_sources(tmp_path, snapshot)
    # This is a synthetic test-owned source; derive text and time directly
    # from its rows independently of the resolver and the pipe candidate list.
    fixture_events = {reader._sha(reader._canonical(row)): (row['message'], row['ts_utc'])
                      for row in (json.loads(line) for line in source.read_bytes().splitlines())}
    cursor = snapshot['messages']['end_cursor']
    snapshot['messages']['end_cursor'] = prefix.bridge.BridgeCursor(cursor.offset, cursor.file_identity, None)
    sidecar.unlink()
    snapshot['messages']['generation_path'] = str(source.with_name('events.generation.json'))
    decision = json.loads(snapshot['decision_bytes'])
    decision['source_prefix']['generation'] = None
    snapshot['decision_bytes'] = reader._canonical(decision)
    snapshot['decision_sha256'] = reader._sha(snapshot['decision_bytes'])
    inputs = {k:v for k,v in snapshot.items() if k not in ('projection_bytes','projection_sha256')}
    snapshot['projection_bytes'] = reader._serialized(reader.rederive_complete_message_projection(**inputs))
    snapshot['projection_sha256'] = reader._sha(snapshot['projection_bytes'])
    fleet = b'synthetic fleet bytes'
    controls = (str(source), snapshot['messages']['generation_path'], reader._sha(fleet),
                snapshot['roster_bytes'], snapshot['messages']['policy_bytes'])
    monkeypatch.setattr(package, '_consumer_bundle_controls', lambda *_: controls)
    monkeypatch.setattr(runtime, '_bundle_inputs', lambda *_: (fleet, controls[3], controls[4]))
    monkeypatch.setattr(reader.dt, 'datetime', clock)
    gate = original._gate
    flat = json.loads(gate._flat_raw)
    flat['projection_sha256'] = snapshot['projection_sha256']
    flat_bytes = reader._canonical(flat)
    _, adapter_module, binding, factory, launches = _full_fixtures._single_owner_synthetic(monkeypatch, tmp_path)
    _bind_synthetic_owner(original, snapshot, binding, factory)
    monkeypatch.setattr(runtime._PrivateBinding, 'snapshot_for_query', lambda _: snapshot)
    monkeypatch.setattr(runtime._SingleManifestOwner, '_check_policy_anchor', lambda _: None)
    monkeypatch.setattr(runtime, '_STARTUP_OWNER_SOURCE_BUDGET_ENABLED', gate_on)
    class Adapter:
        def query(self, _text): return [1.0] + [0.0]*383
    monkeypatch.setattr(adapter_module, 'create_single_flat_adapter', lambda **_: Adapter())
    def build(handler):
        handler._memory = runtime._PrivateFlatMemory(binding, snapshot, flat_bytes, gate._ids, gate._vectors, gate._encoder)
        handler._frozen_flat = (flat_bytes, gate._ids, gate._vectors, gate._encoder)
        return {'schema':'wd.mind-flat-owner-build.v1','status':'BUILT_IN_MEMORY','authority':'none'}
    monkeypatch.setattr(runtime._FlatRequestHandler, 'build', build)
    policy = snapshot['identifier_policy_binding'][0]
    monkeypatch.setattr(session, '_selected_pin_read', lambda *_: policy)
    clients=[]
    create_resolver=session.create_selected_row_resolver
    def capture_resolver(**kwargs):
        result=create_resolver(**kwargs); clients.append(result); return result
    monkeypatch.setattr(session, 'create_selected_row_resolver', capture_resolver)
    replies=[]
    validate_reply=process._local_candidate_reply
    def capture_reply(value, request):
        result=validate_reply(value, request)
        replies.append((dict(value),dict(request),threading.current_thread().name))
        return result
    monkeypatch.setattr(process, '_local_candidate_reply', capture_reply)
    # Capture physical thread identities before invoking Thread.start. Even
    # after session removes its pending record, these references remain held.
    tracked_threads, queries = [], []
    tracked_names = {'mind-e5-launch', 'mind-local-candidates', 'mind-local-wake',
                     'mind-local-selected-client', 'mind-local-query'}
    original_start = threading.Thread.start
    def tracked_start(thread):
        if thread.name in tracked_names:
            # The target's own completion event proves Thread._bootstrap has
            # entered run(); ident alone can be set before _started is set.
            if type(thread) is not threading.Thread or not callable(thread._target):
                raise RuntimeError('unsupported tracked thread')
            target, target_done = thread._target, threading.Event()
            tracked_threads.append((thread, target_done))
            def run_tracked(*args, **kwargs):
                try:
                    return target(*args, **kwargs)
                finally:
                    target_done.set()
            thread._target = run_tracked
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, 'start', tracked_start)
    create_query = process.create_local_candidate_query
    def capture_query(**kwargs):
        query = create_query(**kwargs)
        queries.append(query)
        return query
    monkeypatch.setattr(process, 'create_local_candidate_query', capture_query)
    owner = runtime.create_single_manifest_owner(enabled=True, binding=binding, lane='codex-lead-1',
                                                 worker_factory=factory, shutdown_timeout=0)
    reads = _instrument_source_streams(monkeypatch, source, tagger=lambda:threading.current_thread().name)
    budgets=[]
    orig_init=prefix._SourceReadBudget.__init__
    def budget_init(self, limit):
        orig_init(self, limit)
        budgets.append((threading.current_thread().name,self,limit))
    monkeypatch.setattr(prefix._SourceReadBudget, '__init__', budget_init)
    service = None
    pending_before = {id(item) for item in session._LOCAL_CANDIDATE_PENDING}
    try:
        assert owner.start()['state'] == 'READY' and launches == [True]
        if gate_on:
            prewarm=sum(n for _,n,tag in reads if tag == 'MainThread')
            assert prewarm > 0 and budgets[0][2] == 2*1024**3
            assert budgets[0][1]._remaining == budgets[0][2]-prewarm
        service_args, client_args = _synthetic_routes(original, owner)
        name = service_args['pipe_name']
        assert original._pin.lower() != os.environ.get('WD_REBOOT_EXPECTED_MANIFEST_HASH','').lower()
        service=process.create_local_candidate_service(**service_args)
        service.start()
        # Malformed requests actually reach AF_PIPE; reject without dropping service.
        for bad in ({'nonce':'bad'}, {'lane':'not-a-lane'}):
            request={'schema':'wd.mind-pipe-request.v2','query_id':'startup_work_overview',
                     'nonce':'1'*64,'lane':'codex-lead-1','scope':'shared'}
            request.update(bad)
            c=Client(address=name,family='AF_PIPE',authkey=None)
            try:
                c.send_bytes(reader._canonical(request))
                try:
                    assert c.poll(5), 'malformed request did not close promptly'
                    with pytest.raises((EOFError,OSError)):
                        c.recv_bytes()
                except BrokenPipeError:
                    pass  # Windows can report closure directly from poll.
            finally: c.close()
        args=['--mind-local-candidate-read','--policy-path','synthetic-policy',
              '--policy-sha256',reader._sha(policy),'--approval-reference-sha256','c'*64,
              '--lane','codex-lead-1','--timeout-seconds','90']
        out, err=io.BytesIO(),io.BytesIO()
        rc=session._local_candidate_entry(args,out,err,*client_args)
        owner_read=sum(n for _,n,tag in reads if tag=='mind-local-candidates')
        client_read=sum(n for _,n,tag in reads if tag=='mind-local-selected-client')
        if gate_on:
            assert rc == 0, (rc,err.getvalue())
            result=json.loads(out.getvalue())
            assert result['authority']=='none' and result['sender_authentication']=='UNKNOWN'
            assert result['results']
            client_replies = [(frame, request) for frame, request, thread in replies
                              if thread == 'mind-local-query']
            assert len(client_replies) == 1
            frame, request = client_replies[0]
            wire_refs = {item['source_event_sha256'] for item in frame['candidates']}
            for row in result['results']:
                ref = row['source_event_sha256']
                assert ref in wire_refs and ref in fixture_events
                source_text, source_time = fixture_events[ref]
                assert row['text'] == source_text and row['source_agent'] == 'codex-lead-1'
                assert type(row['event_age_seconds_upper_bound']) is int
                assert row['event_age_seconds_upper_bound'] >= 0
                floor = reader._utc(row['event_time_utc_floor'])
                assert floor == reader._utc(source_time).replace(microsecond=0)
                expected_age = math.ceil((clock.now(reader.dt.timezone.utc) - floor).total_seconds())
                assert row['event_age_seconds_upper_bound'] >= expected_age >= 0
            assert owner_read>0 and client_read>0 and len(clients)==1
            assert client_read <= 1024**3-clients[0]._source_budget._remaining
            for invalid in ({'nonce':'2'*64}, {'lane':'claude-rco-1'}):
                tampered=dict(frame, **invalid)
                with pytest.raises(ValueError): validate_reply(tampered, request)
            owned=[(b,limit) for tag,b,limit in budgets if tag=='mind-local-candidates']
            assert len(owned)==1 and owned[0][1]==1024**3
            assert owner_read == owned[0][1]-owned[0][0]._remaining
        else:
            assert rc != 0 and not out.getvalue() and owner_read == 0
        print('synthetic_counts gate_on=',gate_on,'owner_bytes=',owner_read,'client_bytes=',client_read)
    finally:
        # Hold each created object across close uncertainty; never abandon a
        # possibly-started service/owner, including a start() exception.
        delayed = None
        def hold_pause():
            nonlocal delayed
            try:
                time.sleep(0.1)
            except BaseException as exc:
                if delayed is None: delayed = exc
        if service is not None:
            while True:
                try:
                    state = service.close(timeout=1)
                    if isinstance(state, dict) and state.get('state') == 'STOPPED':
                        break
                except BaseException as exc:
                    if delayed is None: delayed = exc
                hold_pause()
        while True:
            try:
                state = owner.close()
                if (isinstance(state, dict) and state.get('state') == 'STOPPED'
                        and state.get('owned') is False):
                    break
            except BaseException as exc:
                if delayed is None: delayed = exc
            hold_pause()
        # Wait for every physical thread including a session query whose
        # pending-list entry was removed when only its done event was set.
        # A start entered without an observed identity stays HOLD.
        while True:
            try:
                def target_done_and_joined(thread, target_done):
                    if not target_done.is_set() or thread.ident is None:
                        return False
                    # Never join an uncertain, never-entered start. Once the
                    # target ran, join(0) plus is_alive verifies native exit.
                    thread.join(timeout=0)
                    return not thread.is_alive()
                all_threads_stopped = all(target_done_and_joined(thread, target_done)
                                          for thread, target_done in tracked_threads)
                all_queries_accounted = all(query._thread is None or
                    (query._done.is_set() and any(thread is query._thread
                        and target_done_and_joined(thread, target_done)
                        for thread, target_done in tracked_threads)) for query in queries)
                if all_threads_stopped and all_queries_accounted:
                    break
            except BaseException as exc:
                if delayed is None: delayed = exc
            hold_pause()
        # Pending records may already have been removed by session; only
        # remove retained records after all physical threads have stopped.
        while True:
            try:
                pending = [item for item in session._LOCAL_CANDIDATE_PENDING
                           if id(item) not in pending_before]
                for item in pending:
                    worker, state = item
                    if worker.ident is None or worker.is_alive():
                        break
                    query = state.get('query')
                    if query is not None and query not in queries:
                        break
                    if item in session._LOCAL_CANDIDATE_PENDING:
                        session._LOCAL_CANDIDATE_PENDING.remove(item)
                if not any(id(item) not in pending_before for item in session._LOCAL_CANDIDATE_PENDING):
                    break
            except BaseException as exc:
                if delayed is None: delayed = exc
            hold_pause()
        if delayed is not None:
            raise delayed
