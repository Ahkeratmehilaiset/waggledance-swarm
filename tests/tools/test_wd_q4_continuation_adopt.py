"""Synthetic continuation -> publication; no live bridge or installed bundle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from tests.tools.test_wd_q4_genesis_bootstrap import synthetic
from tests.tools import test_wd_q4_genesis_bootstrap as base
from tests.tools import test_wd_q4_genesis_to_composer_cli as existing
from tools import wd_q4_genesis_to_composer_cli as initial
from tools import wd_q4_lane_caller_cli as caller
from tools import wd_q4_live_snapshot_publish as live
from tools import wd_q4_production_continuation as continuation


def enc(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


@pytest.fixture
def actual_continuation(synthetic,monkeypatch,tmp_path,capsys):
    pytest.importorskip('faiss')
    args,_=synthetic
    from tests.tools import test_wd_q4_staged_composer as staged_fixture
    original_synthetic=staged_fixture.synthetic
    def real_readable_fixture(path):
        request,source,target=original_synthetic(path)
        interpreter=Path(request['descriptor']['interpreter']); interpreter.write_bytes(b'synthetic-interpreter')
        manifest=Path(request['descriptor']['runtime_manifest_path']); manifest.write_bytes(b'{}')
        for name,pin in (('interpreter_sha256',caller._sha(interpreter.read_bytes())),
                         ('runtime_manifest_sha256',caller._sha(manifest.read_bytes()))):
            request['descriptor'][name]=pin; request['fixed_plan'][name]=pin
        return request,source,target
    monkeypatch.setattr(staged_fixture,'synthetic',real_readable_fixture)
    # The relay is real; the bounded wrapper is a synthetic query peer which
    # exercises the real selector/current-pointer checks without starting Q4.
    from tools import wd_q4_live_update_once as update
    original_encode=update._encode
    def encode_manifest(value):
        if (isinstance(value,dict) and value.get('schema_version')==1
                and isinstance(value.get('files'),dict) and 'wd-fleet.json' in value['files']):
            bundle=tmp_path/'compose-case'/'reboot-bundles'/('a'*40)
            wrapper=bundle/'Invoke-WdBridgePython.ps1'
            repo=str(Path(__file__).resolve().parents[2])
            code=("import json,os,sys;sys.path.insert(0,"+repr(repo)+");"
                  "sys.stdin.buffer.read();assert os.environ.get('WD_Q4_LIVE_MODE')=='1';"
                  "from tools import wd_q4_lane_caller_cli as c,wd_q4_live_snapshot_publish as l;"
                  "p=os.environ['WD_Q4_SHARED_DESCRIPTOR_PATH'];h=os.environ['WD_Q4_SHARED_DESCRIPTOR_SHA256'];"
                  "b=c._configuration(p,h);s=l.select(enabled=True,baseline_path=p,baseline_sha256=h,baseline=b,"
                  "manifest_sha256=os.environ['WD_REBOOT_EXPECTED_MANIFEST_HASH'],load_configuration=c._configuration);"
                  "d=s['configuration'];c._require_current_snapshot(d[0],d[4],d[5]);"
                  "print(json.dumps({'pointer_sha256':d[4]['pointer_sha256']}))")
            quote=lambda s: "'"+s.replace("'","''")+"'"
            wrapper.write_text("& "+quote(sys.executable)+" -c "+quote(code)+"\n$global:LASTEXITCODE=$LASTEXITCODE\n",encoding='utf-8')
            value={**value,'files':{**value['files'],'Invoke-WdBridgePython.ps1':caller._sha(wrapper.read_bytes())}}
        return original_encode(value)
    monkeypatch.setattr(update,'_encode',encode_manifest)
    captured=[]; emit=initial._emit
    def observe(result):
        if result.get('status')=='PREPARED': captured.append(dict(result))
        emit(result)
    monkeypatch.setattr(initial,'_emit',observe)
    existing.test_actual_genesis_receipt_to_five_lane_descriptor(synthetic,monkeypatch,tmp_path,capsys,'late_skip')
    assert len(captured)==1
    prepared=captured[0]; desc=json.loads(Path(prepared['descriptor_path']).read_bytes())
    manifest=Path(desc['bundle_root'])/'deployment-manifest.json'
    monkeypatch.setenv('WD_REBOOT_EXPECTED_MANIFEST_HASH',caller._sha(manifest.read_bytes()))
    baseline=caller._configuration(prepared['descriptor_path'],prepared['descriptor_sha256'])
    caller._require_current_snapshot(baseline[0],baseline[4],baseline[5])
    source=Path(args['source_path'])
    row=json.loads(base.f2_rows(args,['public'])[0]); row.update(ts_utc='2026-10-31T13:00:30Z',status='done')
    with source.open('ab') as stream: stream.write(base.raw(row)+b'\n')
    tuple_bytes=(Path(args['private_state_root'])/'q4-genesis-tuple-v1.json').read_bytes()
    report=continuation.continue_once(enabled=True,tuple_bytes=tuple_bytes,
        expected_tuple_sha256=caller._sha(tuple_bytes),expected_pointer_sha256=baseline[4]['pointer_sha256'],
        policy_bytes=args['policy_bytes'],public_policy_bytes=args['public_policy_bytes'],
        fleet_bytes=args['fleet_bytes'],expected_fleet_sha256=args['expected_fleet_sha256'],
        cutoff_utc='2026-10-31T13:02:00Z',previous_cutoff_utc='2026-10-31T13:00:00Z',
        watermark_enabled=True,expected_watermark_record_sha256=None)
    assert report['status']=='caught_up_to_cutoff'
    assert report['pointer_sha256']!=baseline[4]['pointer_sha256']
    keys=initial._RECEIPT_V3
    receipt=enc({key:report[key] for key in keys})
    with pytest.raises(ValueError): caller._require_current_snapshot(baseline[0],baseline[4],baseline[5])
    return {'enabled':True,'baseline_path':prepared['descriptor_path'],
            'baseline_sha256':prepared['descriptor_sha256'],'manifest_sha256':caller._sha(manifest.read_bytes()),
            'genesis_tuple_bytes':tuple_bytes,'genesis_tuple_sha256':caller._sha(tuple_bytes),
            'receipt_bytes':receipt,'receipt_sha256':caller._sha(receipt)}


def test_off_reads_nothing():
    from tools import wd_q4_continuation_adopt as adopt
    assert adopt.run_once()=={'status':'OFF'}


def test_actual_continuation_can_be_adopted_for_live_reader(actual_continuation):
    from tools import wd_q4_continuation_adopt as adopt
    args=actual_continuation
    out=adopt.run_once(**args)
    assert out['status']=='PUBLISHED'
    baseline=caller._configuration(args['baseline_path'],args['baseline_sha256'])
    selected=live.select(enabled=True,baseline_path=args['baseline_path'],baseline_sha256=args['baseline_sha256'],
        baseline=baseline,manifest_sha256=args['manifest_sha256'],load_configuration=caller._configuration)
    chosen=selected['configuration']
    assert set(chosen[0]['lanes'])==caller._LANES
    assert chosen[4]['pointer_sha256']==json.loads(args['receipt_bytes'])['pointer_sha256']
    caller._require_current_snapshot(chosen[0],chosen[4],chosen[5])
    prior,raw=adopt._prior_pointer(args['baseline_path'],args['baseline_sha256'],baseline,args['manifest_sha256'])
    assert prior==chosen[4]['pointer_sha256'] and raw is not None
    with pytest.raises(ValueError): caller._require_current_snapshot(baseline[0],baseline[4],baseline[5])
    host=shutil.which('powershell.exe')
    assert host
    env={key:value for key,value in os.environ.items() if not key.startswith(('WD_BRIDGE_','WD_Q4_','WD_REBOOT_'))}
    bundle=Path(baseline[0]['bundle_root'])
    env.update(WD_BRIDGE_BUNDLE_ROOT=str(bundle),WD_BRIDGE_PYTHON_WRAPPER=str(bundle/'Invoke-WdBridgePython.ps1'),
               WD_REBOOT_EXPECTED_MANIFEST_HASH=args['manifest_sha256'],
               WD_Q4_SHARED_DESCRIPTOR_PATH=args['baseline_path'],WD_Q4_SHARED_DESCRIPTOR_SHA256=args['baseline_sha256'])
    helper=Path(__file__).resolve().parents[2]/'ops/windows/reboot/Invoke-WdQ4LaneRecall.ps1'
    run=subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(helper),'-Live'],env=env,
        input=b'{"situation":"synthetic","time":"2026-11-01T12:00:00Z"}',capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr.decode(errors='replace')
    assert json.loads(run.stdout)['pointer_sha256']==json.loads(args['receipt_bytes'])['pointer_sha256']


def test_tampering_and_existing_pending_never_publish(actual_continuation):
    from tools import wd_q4_continuation_adopt as adopt
    args=actual_continuation
    route=initial._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    private=Path(route['private_state_root']); receipt=json.loads(args['receipt_bytes'])
    marker=private/'q4-continuation-adopt.pending.json'
    for bad in ({**receipt,'pointer_sha256':'0'*64},
                {**receipt,'watermark_record_sha256':'0'*64},
                {**receipt,'route_sha256':'0'*64},
                {**receipt,'runner_receipt_sha256':'0'*64},
                {**receipt,'status':'partial_ready_prefix'}):
        raw=enc(bad)
        with pytest.raises(adopt.AdoptRefused):
            adopt.run_once(**{**args,'receipt_bytes':raw,'receipt_sha256':caller._sha(raw)})
        assert not marker.exists()
    watermark=private/('q4-cutoff-record-'+receipt['watermark_record_sha256']+'.json')
    original=watermark.read_bytes(); watermark.write_bytes(original+b' ')
    with pytest.raises(adopt.AdoptRefused): adopt.run_once(**args)
    watermark.write_bytes(original)
    assert not marker.exists()
    competing=private/'q4-cutoff.pending.json'; competing.write_bytes(b'{}')
    with pytest.raises(adopt.AdoptOutcomeUnknown): adopt.run_once(**args)
    competing.unlink()
    assert not marker.exists()


def test_competing_owner_lock_prevents_adoption(actual_continuation):
    import threading
    from sandbox.mind.step2.reader_slot import reader_slot
    from tools import wd_q4_continuation_adopt as adopt
    args=actual_continuation
    route=initial._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    private=Path(route['private_state_root']); locked=threading.Event(); release=threading.Event()
    def owner():
        with reader_slot(private/'q4-cutoff-owner.lock',timeout_seconds=1):
            locked.set(); release.wait(5)
    thread=threading.Thread(target=owner); thread.start()
    try:
        assert locked.wait(2)
        with pytest.raises(adopt.AdoptRefused): adopt.run_once(**args)
        assert not (private/'q4-continuation-adopt.pending.json').exists()
    finally:
        release.set(); thread.join(5)
    assert not thread.is_alive()


def test_runner_marker_between_verification_and_admission_blocks_adoption(actual_continuation, monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    args = actual_continuation
    route = initial._tuple_part(json.loads(args['genesis_tuple_bytes']), 'route')
    private = Path(route['private_state_root'])
    foreign = private / adopt.runner._PENDING
    original = adopt._prior_pointer

    def arrive_after_verification(*pos, **kw):
        result = original(*pos, **kw)
        foreign.write_bytes(b'synthetic competing runner\n')
        return result

    monkeypatch.setattr(adopt, '_prior_pointer', arrive_after_verification)
    with pytest.raises(adopt.AdoptOutcomeUnknown):
        adopt.run_once(**args)
    assert foreign.read_bytes() == b'synthetic competing runner\n'
    assert not (private / 'q4-continuation-adopt.pending.json').exists()


@pytest.mark.parametrize('change', ['pointer', 'fence'])
def test_changed_pointer_or_fence_before_admission_is_refused(actual_continuation, monkeypatch, change):
    from tools import wd_q4_continuation_adopt as adopt
    args = actual_continuation
    route = initial._tuple_part(json.loads(args['genesis_tuple_bytes']), 'route')
    private = Path(route['private_state_root'])
    root = Path(route['checkpoint_root'])
    original = adopt._prior_pointer

    def change_after_verification(*pos, **kw):
        result = original(*pos, **kw)
        if change == 'pointer':
            (root / adopt.checkpoint.POINTER).write_bytes(b'{"changed":true}')
        else:
            (root / adopt.checkpoint.FENCE).write_bytes(b'synthetic pending fence')
        return result

    monkeypatch.setattr(adopt, '_prior_pointer', change_after_verification)
    with pytest.raises((adopt.AdoptRefused, adopt.AdoptOutcomeUnknown)):
        adopt.run_once(**args)
    assert not (private / 'q4-continuation-adopt.pending.json').exists()


def test_interruption_after_actual_publication_stays_unknown(actual_continuation,monkeypatch):
    from tools import wd_q4_continuation_adopt as adopt
    args=actual_continuation
    route=initial._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    private=Path(route['private_state_root']); original=adopt.update._publish
    def interrupt(*pos,**kw):
        original(*pos,**kw)
        raise KeyboardInterrupt()
    monkeypatch.setattr(adopt.update,'_publish',interrupt)
    with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    assert (private/'q4-continuation-adopt.pending.json').is_file()
    with pytest.raises(adopt.AdoptOutcomeUnknown): adopt.run_once(**args)




@pytest.mark.parametrize('fault',['none','tampered_route','tampered_watermark','competing','interrupt'])
def test_empty_advance_consumer_reproduction(actual_continuation,monkeypatch,fault):
    from tools import wd_q4_continuation_adopt as adopt
    args=actual_continuation
    first=adopt.run_once(**args)
    previous=json.loads(args['receipt_bytes'])
    route=initial._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    baseline=caller._configuration(args['baseline_path'],args['baseline_sha256'])
    public,_,_=live._plan(baseline[0],baseline[5])
    source=Path(route['source'])
    event=json.loads(base.f2_rows({'fleet_bytes':baseline[3]},['private'])[0])
    event['ts_utc']='2026-10-31T13:02:30Z'
    with source.open('ab') as stream: stream.write(base.raw(event)+b'\n')
    report=continuation.continue_once(enabled=True,tuple_bytes=args['genesis_tuple_bytes'],
        expected_tuple_sha256=args['genesis_tuple_sha256'],expected_pointer_sha256=previous['pointer_sha256'],
        policy_bytes=Path(public['policy_path']).read_bytes(),
        public_policy_bytes=Path(public['public_policy_path']).read_bytes(),
        fleet_bytes=baseline[3],expected_fleet_sha256=baseline[0]['fleet_sha256'],
        cutoff_utc='2026-10-31T13:04:00Z',previous_cutoff_utc=previous['cutoff_utc'],
        watermark_enabled=True,expected_watermark_record_sha256=previous['watermark_record_sha256'])
    private=Path(route['private_state_root'])
    runner,=[json.loads(p.read_bytes()) for p in private.glob('q4-status-result-*.json')
             if caller._sha(p.read_bytes())==report['runner_receipt_sha256']]
    assert runner['status']=='EMPTY_ADVANCED'
    assert report['status']=='partial_ready_prefix'
    with pytest.raises(live.LiveSnapshotRefused):
        live.select(enabled=True,baseline_path=args['baseline_path'],baseline_sha256=args['baseline_sha256'],
            baseline=baseline,manifest_sha256=args['manifest_sha256'],load_configuration=caller._configuration)
    raw=enc({key:report[key] for key in initial._RECEIPT_V3})
    marker=private/'q4-continuation-adopt.pending.json'
    if fault in ('tampered_route','tampered_watermark'):
        field='route_sha256' if fault=='tampered_route' else 'watermark_record_sha256'
        bad=json.loads(raw); bad[field]='0'*64; bad_raw=enc(bad)
        with pytest.raises(adopt.AdoptRefused):
            adopt.run_once(**{**args,'receipt_bytes':bad_raw,'receipt_sha256':caller._sha(bad_raw)})
        assert not marker.exists()
        return
    if fault=='competing':
        pending=private/'q4-cutoff.pending.json'; pending.write_bytes(b'{}')
        with pytest.raises(adopt.AdoptOutcomeUnknown):
            adopt.run_once(**{**args,'receipt_bytes':raw,'receipt_sha256':caller._sha(raw)})
        assert not marker.exists()
        return
    if fault=='interrupt':
        original=adopt.update._publish
        def interrupt(*pos,**kw):
            original(*pos,**kw)
            raise KeyboardInterrupt()
        monkeypatch.setattr(adopt.update,'_publish',interrupt)
        with pytest.raises(adopt.AdoptInterrupted):
            adopt.run_once(**{**args,'receipt_bytes':raw,'receipt_sha256':caller._sha(raw)})
        assert marker.exists()
        with pytest.raises(adopt.AdoptOutcomeUnknown):
            adopt.run_once(**{**args,'receipt_bytes':raw,'receipt_sha256':caller._sha(raw)})
        return
    second=adopt.run_once(**{**args,'receipt_bytes':raw,'receipt_sha256':caller._sha(raw)})
    assert second['status']=='PUBLISHED' and second['publication_sha256']!=first['publication_sha256']
    assert second['coverage']=='partial_ready_prefix' and second['freshness']=='UNKNOWN'
    selected=live.select(enabled=True,baseline_path=args['baseline_path'],baseline_sha256=args['baseline_sha256'],
        baseline=baseline,manifest_sha256=args['manifest_sha256'],load_configuration=caller._configuration)
    caller._require_current_snapshot(selected['configuration'][0],selected['configuration'][4],selected['configuration'][5])
    assert selected['configuration'][4]['pointer_sha256']==report['pointer_sha256']
    host=shutil.which('powershell.exe'); assert host
    env={key:value for key,value in os.environ.items() if not key.startswith(('WD_BRIDGE_','WD_Q4_','WD_REBOOT_'))}
    bundle=Path(baseline[0]['bundle_root'])
    env.update(WD_BRIDGE_BUNDLE_ROOT=str(bundle),WD_BRIDGE_PYTHON_WRAPPER=str(bundle/'Invoke-WdBridgePython.ps1'),
               WD_REBOOT_EXPECTED_MANIFEST_HASH=args['manifest_sha256'],
               WD_Q4_SHARED_DESCRIPTOR_PATH=args['baseline_path'],WD_Q4_SHARED_DESCRIPTOR_SHA256=args['baseline_sha256'])
    helper=Path(__file__).resolve().parents[2]/'ops/windows/reboot/Invoke-WdQ4LaneRecall.ps1'
    run=subprocess.run([host,'-NoProfile','-NonInteractive','-File',str(helper),'-Live'],env=env,
        input=b'{"situation":"synthetic","time":"2026-11-01T12:00:00Z"}',capture_output=True,timeout=30)
    assert run.returncode==0,run.stderr.decode(errors='replace')
    assert json.loads(run.stdout)['pointer_sha256']==report['pointer_sha256']


