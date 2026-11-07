"""Five synthetic caller identities; never evidence of real agents or E5 use.

The shared descriptor and all lane state roots are test fixtures. The adapter
is a recorder, so native peer execution and cleanup remain unobserved. The
separate real-E5 smoke must use its own audited tuple and process ownership.
"""
import json
import hashlib

from tests.tools.test_wd_q4_lane_caller_cli import case
from tests.tools import test_wd_q4_lane_caller_cli as fixture


def classify(lane,code,stdout):
    """Private, synthetic receipt; never treats a refusal as cleanup proof."""
    if lane not in fixture.LANES or type(code) is not int or type(stdout) is not bytes or len(stdout)>65536:
        return {'result':'UNKNOWN'}
    result='UNKNOWN'
    if code == 2:
        result='REFUSED_OR_UNAVAILABLE'
    elif code == 0:
        try:
            def unique(pairs):
                out={}
                for key,value in pairs:
                    if key in out:
                        raise ValueError()
                    out[key]=value
                return out
            doc=json.loads(stdout.decode('utf-8'),object_pairs_hook=unique)
            if type(doc) is dict and doc.get('schema')=='wd.q4-lane-coverage-envelope.v1':
                if (set(doc)!={'schema','coverage','freshness','result'}
                        or doc['coverage']!='UNKNOWN' or doc['freshness']!='UNKNOWN'):
                    raise ValueError()
                doc=doc['result']
            if (type(doc) is dict and doc.get('status')=='VALIDATED' and doc.get('lane')==lane
                    and doc.get('inner_status') in ('ANSWER','ABSTAIN')
                    and doc.get('lane_binding')=='DECLARED_CONSISTENT'
                    and doc.get('lane_authentication')=='UNKNOWN' and doc.get('cleanup')=='chain_confirmed'):
                result='SYNTHETIC_VALIDATED'
        except Exception:
            pass
    return {'lane':lane,'result':result,'exit_code':code,'stdout_sha256':hashlib.sha256(stdout).hexdigest(),
            'native_peer':'UNKNOWN','real_agent_use':'UNKNOWN','cleanup_evidence':'SYNTHETIC_ONLY'}


def test_closed_receipt_classification():
    output = json.dumps({'status':'VALIDATED','lane':'codex-lead-1','inner_status':'ABSTAIN',
                         'lane_binding':'DECLARED_CONSISTENT','lane_authentication':'UNKNOWN',
                         'cleanup':'chain_confirmed'}).encode()
    assert classify('codex-lead-1',0,output)['result'] == 'SYNTHETIC_VALIDATED'
    assert classify('codex-lead-1',2,b'')['result'] == 'REFUSED_OR_UNAVAILABLE'
    assert classify('codex-lead-1',3,b'')['result'] == 'UNKNOWN'
    assert classify('codex-lead-1',0,b'invalid')['result'] == 'UNKNOWN'
    assert classify('fable-5',0,output)['result'] == 'UNKNOWN'
    assert classify('codex-lead-1',0,b'{"status":"VALIDATED","status":"VALIDATED"}')['result'] == 'UNKNOWN'
    envelope = {'schema':'wd.q4-lane-coverage-envelope.v1','coverage':'UNKNOWN','freshness':'UNKNOWN',
                'result':json.loads(output)}
    assert classify('codex-lead-1',0,json.dumps(envelope).encode())['result'] == 'SYNTHETIC_VALIDATED'
    envelope['freshness']='PASS'
    assert classify('codex-lead-1',0,json.dumps(envelope).encode())['result'] == 'UNKNOWN'


def test_five_distinct_synthetic_lanes(case,monkeypatch,capsys):
    case['outcome']['validated'] = True
    fleet=json.loads(case['files']['wd-fleet.json'].read_bytes())
    roots=[]
    receipts=[]
    for lane in fixture.LANES:
        monkeypatch.setenv('AGENT_BRIDGE_AGENT',lane)
        monkeypatch.setenv('AGENT_BRIDGE_AGENT_UUID',fixture.UUIDS[lane])
        monkeypatch.chdir(case['worktrees'][lane])
        code=fixture.invoke(case,monkeypatch)
        observed=capsys.readouterr()
        assert observed.err == ''
        receipt=classify(lane,code,observed.out.encode())
        assert receipt['result']=='SYNTHETIC_VALIDATED'
        assert fixture.QUERY_CANARY not in json.dumps(receipt)
        assert fixture.TOKEN_CANARY not in json.dumps(receipt)
        assert not (case['files']['descriptor.json'].parent/'live-bridge').exists()
        root=fixture.Path(fleet['runtime_root'])/'shared'/'.codex-audit'/lane/'q4-caller-state'
        assert root.is_dir() and not (root/(lane+'.pending.json')).exists()
        roots.append(root.resolve())
        receipts.append(receipt)
    assert len(set(roots))==len(set(case['worktrees'].values()))==len(receipts)==len(case['calls'])==5
    assert {row['lane'] for row in receipts}==set(fixture.LANES)


def test_unknown_lane_is_not_retried_and_other_guard_stays_separate(case,monkeypatch,capsys):
    fleet=json.loads(case['files']['wd-fleet.json'].read_bytes())
    first,second='fable-5','codex-lead-1'
    monkeypatch.setenv('AGENT_BRIDGE_AGENT',first)
    monkeypatch.setenv('AGENT_BRIDGE_AGENT_UUID',fixture.UUIDS[first])
    monkeypatch.chdir(case['worktrees'][first])
    case['outcome']['exception']=case['adapter'].CallerCleanupUnknown('synthetic unknown')
    code=fixture.invoke(case,monkeypatch)
    out=capsys.readouterr()
    assert code==3 and classify(first,code,out.out.encode())['result']=='UNKNOWN'
    root=fixture.Path(fleet['runtime_root'])/'shared'/'.codex-audit'
    pending=root/first/'q4-caller-state'/(first+'.pending.json')
    before=pending.read_bytes()
    case['outcome'].clear()
    case['outcome']['validated']=True
    monkeypatch.setenv('AGENT_BRIDGE_AGENT',second)
    monkeypatch.setenv('AGENT_BRIDGE_AGENT_UUID',fixture.UUIDS[second])
    monkeypatch.chdir(case['worktrees'][second])
    code=fixture.invoke(case,monkeypatch)
    out=capsys.readouterr()
    assert classify(second,code,out.out.encode())['result']=='SYNTHETIC_VALIDATED'
    assert pending.read_bytes()==before and len(case['calls'])==2
    assert not (root/second/'q4-caller-state'/(second+'.pending.json')).exists()
