import json
import subprocess
from pathlib import Path

SCRIPT=Path('ops/windows/reboot/Invoke-WdQ4AdoptRecovery.ps1').resolve()

def test_default_off():
    p=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT)],capture_output=True,text=True,timeout=20)
    assert p.returncode==0 and json.loads(p.stdout)=={'status':'OFF'} and not p.stderr

def test_bad_pins_refuse_without_job():
    p=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run'],capture_output=True,text=True,timeout=20)
    assert p.returncode==2 and json.loads(p.stdout)=={'status':'REFUSED'} and not p.stderr

import hashlib
import importlib.util
import os
import shutil
import sys
import time
import pytest
from tests.tools.test_wd_q4_adopt_recovery_job_e2e import prepared, launch, _available
from tools import wd_q4_owned_recovery_parent, wd_q4_owned_recovery_reader

@pytest.mark.skipif(not _available(os.environ,sys.platform),reason='explicit synthetic Windows native-runtime opt-in required')
def test_real_job_finishes_proven_early_refusal(prepared):
    case,runtime,args=prepared
    import base64
    tup=json.loads(args['genesis_tuple_bytes'])
    route=json.loads(base64.b64decode(tup['route']['bytes_b64']))
    private=Path(route['private_state_root'])
    baseline=Path(args['baseline_path']); baseline.write_bytes(baseline.read_bytes()+b' ')
    result=launch(case,runtime,args)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()
    receipt,=private.glob('q4-adopt-recovery-job-result-*.json')
    assert json.loads(receipt.read_bytes())['job_cleanup']=='confirmed'

@pytest.mark.skipif(not _available(os.environ,sys.platform),reason='explicit synthetic Windows native-runtime opt-in required')
def test_stdout_error_after_real_f3_mutation_is_unknown(tmp_path,monkeypatch,capsys):
    from tests.tools.test_wd_q4_continuation_adopt_recovery import paths
    from tools import wd_q4_continuation_adopt as adopt
    original_copy=shutil.copyfile
    def patched_copy(src,dst,*args,**kwargs):
        result=original_copy(src,dst,*args,**kwargs)
        if Path(src).name=='wd_q4_continuation_adopt_recovery_bootstrap.py':
            path=Path(dst); source=path.read_text(encoding='utf-8')
            needle="print(json.dumps(answer, sort_keys=True, separators=(',', ':')))"
            assert source.count(needle)==1
            source=source.replace(needle,'_synthetic_emit(answer)')
            source=source.replace("if __name__ == '__main__':", "def _synthetic_emit(_answer):\n    raise OSError('synthetic stdout error')\n\nif __name__ == '__main__':")
            path.write_text(source,encoding='utf-8')
        return result
    with monkeypatch.context() as scoped:
        scoped.setattr(shutil,'copyfile',patched_copy)
        case,runtime,args=prepared.__wrapped__(tmp_path,monkeypatch,capsys)
    private,generation=paths(args); original=adopt.update._publish
    def interrupted(*a,**kw):
        original(*a,**kw)
        raise KeyboardInterrupt()
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',interrupted)
        with pytest.raises(adopt.AdoptInterrupted): adopt.run_once(**args)
    adoption=private/'q4-continuation-adopt.pending.json'
    assert adoption.exists() and generation.is_dir()
    result=launch(case,runtime,args)
    assert not adoption.exists(), 'the real F3 mutation must happen before the synthetic error'
    assert (private/'q4-adopt-recovery-job.pending.json').exists()
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}

_spec=importlib.util.spec_from_file_location('continuation_job_fixture',Path(__file__).with_name('test_wd_q4_continuation_runtime_bootstrap.py'))
_existing=importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_existing)
candidate=_existing.candidate
job=_existing.job

def prep_job(job):
    bundle=job['bundle']; code=bundle/'tools-bootstrap/tools'
    bootstrap=code/'wd_q4_continuation_adopt_recovery_bootstrap.py'
    shutil.copyfile(Path('tools')/bootstrap.name, bootstrap)
    cli=code/'wd_q4_continuation_adopt_recovery_cli.py'
    cli.write_text("def main(args):\n print('{\"status\":\"NOT_ADOPTED\"}')\n return 0\n",encoding='ascii')
    # Synthetic parent retains the real Windows reservation/lock code; only
    # F3 itself is replaced here. The separate opt-in test runs real F3.
    from tools import wd_q4_owned_recovery_parent, wd_q4_owned_recovery_reader
    project=Path.cwd().resolve(); code_root=bundle/'tools-bootstrap'
    for module in list(sys.modules.values()):
        filename=getattr(module,'__file__',None)
        if not filename: continue
        try: relative=Path(filename).resolve().relative_to(project)
        except ValueError: continue
        if relative.suffix!='.py' or relative.parts[0] not in ('tools','sandbox','core','waggledance'): continue
        target=code_root/relative; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(project/relative,target)
    owner=code/'wd_q4_owned_recovery_parent.py'
    with owner.open('a',encoding='utf-8') as stream:
        stream.write('''\n\ndef run_owned(*, genesis_tuple_bytes, owner_pins, **kwargs):
    import base64
    tup=json.loads(genesis_tuple_bytes)
    route=json.loads(base64.b64decode(tup['route']['bytes_b64']))
    with reserve(Path(route['private_state_root']), Path(route['checkpoint_root']), owner_pins) as owner:
        # SYNTHETIC_BEHAVIOR
        return {'status':'NOT_ADOPTED', **owner.descriptor()}
''')
    base=job['config'].parent/'baseline.json'; base.write_bytes(b'{}')
    receipt=job['config'].parent/'receipt.json'; receipt.write_bytes(b'{}')
    old=json.loads(job['config'].read_bytes())
    old.update(schema='wd.q4-adopt-recovery-cli.v1',baseline_path=str(base),
       baseline_sha256=_existing.sha(base.read_bytes()),receipt_path=str(receipt),
       receipt_sha256=_existing.sha(receipt.read_bytes()))
    old.pop('policy_path'); old.pop('public_policy_path')
    job['config'].write_bytes(_existing.encode(old))
    shutil.copyfile(SCRIPT, bundle/'Invoke-WdQ4AdoptRecovery.ps1')
    _existing.repin(job)
    manifest=json.loads(job['manifest'].read_bytes())
    manifest['files']['Invoke-WdQ4AdoptRecovery.ps1']=_existing.sha((bundle/'Invoke-WdQ4AdoptRecovery.ps1').read_bytes()).upper()
    job['manifest'].write_bytes(_existing.encode(manifest)); job['manifest_pin']=_existing.sha(job['manifest'].read_bytes())
    for name,pin in (('config','config_pin'),('spec','spec_pin')):
        data=json.loads(job[name].read_bytes()); data['manifest_sha256']=job['manifest_pin']
        job[name].write_bytes(_existing.encode(data)); job[pin]=_existing.sha(job[name].read_bytes())
    return job

def command(item,scratch):
    host=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    cmd=['powershell','-NoProfile','-NonInteractive','-File',str(item['bundle']/'Invoke-WdQ4AdoptRecovery.ps1'),'-Run',
         '-BundleRoot',str(item['bundle']),'-ExpectedCommit','c'*40,
         '-ExpectedManifestSha256',item['manifest_pin'],'-ConfigPath',str(item['config']),
         '-ExpectedConfigSha256',item['config_pin'],'-RuntimePath',str(item['spec']),
         '-ExpectedRuntimeSha256',item['spec_pin'],'-ScratchRoot',str(scratch),
         '-ExpectedHostSha256',hashlib.sha256(host.read_bytes()).hexdigest(),'-TimeoutSeconds','30']
    return cmd

def run_job(item,scratch):
    return subprocess.run(command(item,scratch),text=True,capture_output=True,timeout=50)

def test_owned_job_invokes_pinned_synthetic_recovery_cli(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    result=run_job(job,scratch)
    assert result.returncode==0, result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':'NOT_ADOPTED'}
    private=job['bundle'].parent/'private'
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()
    receipts=list(private.glob('q4-adopt-recovery-job-result-*.json'))
    assert len(receipts)==1
    receipt=json.loads(receipts[0].read_bytes())
    assert receipt['status']=='NOT_ADOPTED' and receipt['job_cleanup']=='confirmed'
    assert receipt['config_sha256']==job['config_pin'] and receipt['manifest_sha256']==job['manifest_pin']

def test_job_rejects_changed_receipt(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    (job['config'].parent/'receipt.json').write_bytes(b'{"changed":true}')
    result=run_job(job,scratch)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}

def test_existing_attempt_marker_blocks_new_job(job):
    prep_job(job)
    scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    marker=job['bundle'].parent/'private'/'q4-adopt-recovery-job.pending.json'
    marker.write_bytes(b'synthetic foreign pending')
    result=run_job(job,scratch)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert marker.read_bytes()==b'synthetic foreign pending'


def repin_changed(job, names):
    manifest=json.loads(job['manifest'].read_bytes())
    for name in names:
        manifest['files'][name]=_existing.sha((job['bundle']/name).read_bytes()).upper()
    job['manifest'].write_bytes(_existing.encode(manifest)); job['manifest_pin']=_existing.sha(job['manifest'].read_bytes())
    for name,pin in (('config','config_pin'),('spec','spec_pin')):
        data=json.loads(job[name].read_bytes()); data['manifest_sha256']=job['manifest_pin']
        job[name].write_bytes(_existing.encode(data)); job[pin]=_existing.sha(job[name].read_bytes())


def parent_behavior(job, statement):
    parent=job['bundle']/'tools-bootstrap/tools/wd_q4_owned_recovery_parent.py'
    source=parent.read_text(encoding='utf-8')
    assert source.count('# SYNTHETIC_BEHAVIOR')==1
    parent.write_text(source.replace('# SYNTHETIC_BEHAVIOR',statement),encoding='utf-8')
    repin_changed(job,['tools-bootstrap/tools/wd_q4_owned_recovery_parent.py'])


@pytest.mark.parametrize('field',['marker_id','marker_sha256'])
def test_wrong_parent_marker_identity_or_digest_stays_unknown(job,field):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    value='0'*(48 if field=='marker_id' else 64)
    parent_behavior(job,"d=owner.descriptor(); d[%r]=%r; return {'status':'NOT_ADOPTED',**d}" % (field,value))
    result=run_job(job,scratch)
    private=job['bundle'].parent/'private'
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert (private/'q4-adopt-recovery-job.pending.json').is_file()
    assert not list(private.glob('q4-adopt-recovery-job-result-*.json'))

def test_confirmed_no_mutation_refusal_finishes_owned_marker(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    parent_behavior(job,"return {'status':'REFUSED', **owner.descriptor()}")
    result=run_job(job,scratch); private=job['bundle'].parent/'private'
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()
    receipt,=private.glob('q4-adopt-recovery-job-result-*.json')
    assert json.loads(receipt.read_bytes())['job_cleanup']=='confirmed'


def test_mismatched_worker_host_pin_refuses_before_parent_reservation(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    script=job['bundle']/'Invoke-WdQ4AdoptRecovery.ps1'; text=script.read_text(encoding='utf-8-sig')
    anchor="'-ExpectedHostSha256',$ExpectedHostSha256"
    assert text.count(anchor)==1
    text=text.replace(anchor,"'-ExpectedHostSha256',('f'*64)")
    script.write_text(text,encoding='utf-8'); repin_changed(job,['Invoke-WdQ4AdoptRecovery.ps1'])
    result=run_job(job,scratch); private=job['bundle'].parent/'private'
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()
    assert not list(private.glob('q4-adopt-recovery-job-result-*.json'))


def test_bootstrap_independently_checks_forwarded_host_before_reservation(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    worker=job['bundle']/'Invoke-WdQ4ContinuationWorker.ps1'; text=worker.read_text(encoding='utf-8-sig')
    anchor="$psi.Arguments+=' --host-sha256 ' + $ExpectedHostSha256"
    assert text.count(anchor)==1
    worker.write_text(text.replace(anchor,"$psi.Arguments+=' --host-sha256 ' + ('f'*64)"),encoding='utf-8')
    repin_changed(job,['Invoke-WdQ4ContinuationWorker.ps1'])
    result=run_job(job,scratch); private=job['bundle'].parent/'private'
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()


@pytest.mark.parametrize('corrupt',[False,True])
def test_finalization_reuses_exact_prior_receipt_without_starting_job(job,monkeypatch,corrupt):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    script=job['bundle']/'Invoke-WdQ4AdoptRecovery.ps1'; text=script.read_text(encoding='utf-8-sig')
    target='[WdQ4AdoptPendingV1]::DeleteOwned($pendingStream.SafeFileHandle)'
    assert text.count(target)==1 and text.count('    . $jobHelper')==1
    text=text.replace(target,"if ($env:WD_TEST_CRASH -eq '1') { throw 'synthetic' }; "+target)
    text=text.replace('    . $jobHelper',"    if ($env:WD_TEST_FORBID_JOB -eq '1') { throw 'forbidden retry' }\n    . $jobHelper")
    script.write_text(text,encoding='utf-8'); repin_changed(job,['Invoke-WdQ4AdoptRecovery.ps1'])
    monkeypatch.setenv('WD_TEST_CRASH','1')
    first=run_job(job,scratch)
    assert first.returncode==3 and json.loads(first.stdout)=={'status':'UNKNOWN'}
    private=job['bundle'].parent/'private'; marker=private/'q4-adopt-recovery-job.pending.json'
    before=marker.read_bytes(); receipt,=private.glob('q4-adopt-recovery-job-result-*.json')
    attempt=json.loads(receipt.read_bytes())['attempt']
    if corrupt: receipt.write_bytes(receipt.read_bytes()+b' ')
    monkeypatch.delenv('WD_TEST_CRASH'); monkeypatch.setenv('WD_TEST_FORBID_JOB','1')
    result=subprocess.run(command(job,scratch)+['-FinalizeAttempt',attempt],text=True,capture_output=True,timeout=50)
    if corrupt:
        assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
        assert marker.read_bytes()==before
    else:
        assert result.returncode==0 and json.loads(result.stdout)=={'status':'NOT_ADOPTED'}
        assert not marker.exists()


def test_unknown_child_keeps_marker_and_never_retries(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    parent_behavior(job,"raise RuntimeError('synthetic uncertain child')")
    first=run_job(job,scratch); assert first.returncode==3 and json.loads(first.stdout)=={'status':'UNKNOWN'}
    private=job['bundle'].parent/'private'; marker=private/'q4-adopt-recovery-job.pending.json'
    initial=marker.read_bytes(); assert json.loads(initial)['config_sha256']==job['config_pin']
    second=run_job(job,scratch); assert second.returncode==3 and json.loads(second.stdout)=={'status':'UNKNOWN'}
    assert marker.read_bytes()==initial and not list(private.glob('q4-adopt-recovery-job-result-*.json'))


@pytest.mark.parametrize('point',['before_marker_flush','after_receipt_flush'])
def test_crash_window_preserves_marker_for_new_launcher(job,point):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    script=job['bundle']/'Invoke-WdQ4AdoptRecovery.ps1'; text=script.read_text(encoding='utf-8-sig')
    if point=='before_marker_flush':
        parent=job['bundle']/'tools-bootstrap/tools/wd_q4_owned_recovery_parent.py'
        source=parent.read_text(encoding='utf-8')
        assert source.count('created=True')==1
        parent.write_text(source.replace('created=True','created=True; raise RuntimeError()'),encoding='utf-8')
        repin_changed(job,['tools-bootstrap/tools/wd_q4_owned_recovery_parent.py'])
    else:
        assert text.count('[WdQ4AdoptPendingV1]::DeleteOwned($pendingStream.SafeFileHandle)')==1
        text=text.replace('[WdQ4AdoptPendingV1]::DeleteOwned($pendingStream.SafeFileHandle)', "throw 'synthetic crash after receipt'")
    script.write_text(text,encoding='utf-8'); repin_changed(job,['Invoke-WdQ4AdoptRecovery.ps1'])
    first=run_job(job,scratch); assert first.returncode==3 and json.loads(first.stdout)=={'status':'UNKNOWN'}
    private=job['bundle'].parent/'private'; marker=private/'q4-adopt-recovery-job.pending.json'
    initial=marker.read_bytes(); assert initial==b'' if point=='before_marker_flush' else initial
    second=run_job(job,scratch); assert second.returncode==3 and json.loads(second.stdout)=={'status':'UNKNOWN'}
    assert marker.read_bytes()==initial
    receipts=list(private.glob('q4-adopt-recovery-job-result-*.json'))
    assert len(receipts)==(1 if point=='after_receipt_flush' else 0)


def test_concurrent_then_killed_launcher_never_restarts(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    parent_behavior(job,'import time; time.sleep(20)')
    marker=job['bundle'].parent/'private'/'q4-adopt-recovery-job.pending.json'
    first=subprocess.Popen(command(job,scratch),stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        deadline=time.monotonic()+20
        while not marker.exists() and time.monotonic()<deadline:
            if first.poll() is not None: pytest.fail('first launcher exited before reservation')
            time.sleep(.1)
        assert marker.exists()
        second=run_job(job,scratch)
        assert second.returncode==3 and json.loads(second.stdout)=={'status':'UNKNOWN'}
    finally:
        if first.poll() is None: first.kill()
        first.communicate(timeout=10)
    initial=marker.read_bytes()
    third=run_job(job,scratch)
    assert third.returncode==3 and json.loads(third.stdout)=={'status':'UNKNOWN'}
    assert marker.read_bytes()==initial

def test_private_root_cannot_overlap_the_bundle(job):
    import base64
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    tup=json.loads(job['tuple'].read_bytes())
    route=json.loads(base64.b64decode(tup['route']['bytes_b64']))
    route['private_state_root']=str(job['bundle'])
    raw=_existing.encode(route)
    tup['route']={'bytes_b64':base64.b64encode(raw).decode(),'sha256':_existing.sha(raw)}
    job['tuple'].write_bytes(_existing.encode(tup))
    config=json.loads(job['config'].read_bytes()); config['tuple_sha256']=_existing.sha(job['tuple'].read_bytes())
    job['config'].write_bytes(_existing.encode(config)); job['config_pin']=_existing.sha(job['config'].read_bytes())
    result=run_job(job,scratch)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not (job['bundle']/'q4-adopt-recovery-job.pending.json').exists()

def test_recovery_job_does_not_admit_while_private_lock_is_held(job):
    from sandbox.mind.step2.reader_slot import reader_slot
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    private=job['bundle'].parent/'private'; marker=private/'q4-adopt-recovery-job.pending.json'
    with reader_slot(private/'prefix-private-state.lock',timeout_seconds=1):
        result=run_job(job,scratch)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not marker.exists()

def test_recovery_job_refuses_foreign_runner_pending(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    private=job['bundle'].parent/'private'; foreign=private/'.wd-q4-status-prefix-runner.pending'
    foreign.write_bytes(b'foreign synthetic pending')
    result=run_job(job,scratch)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert foreign.read_bytes()==b'foreign synthetic pending'
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()

@pytest.mark.parametrize('where,name',[
    ('private','prefix-private-state.pending'),
    ('private','.wd-q4-status-prefix-runner.pending'),
    ('checkpoint','.wd-q4-status-prefix-runner.pending'),
    ('private','q4-cutoff.pending.json'),
    ('private','q4-continuation-orchestrator.pending.json'),
    ('private','q4-continuation-orchestrator-job.pending.json'),
    ('private','q4-live-update.pending.json'),
    ('checkpoint','q4-prefix-fence.json'),
])
def test_each_competing_reservation_is_preserved_and_blocks_job(job,where,name):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    root=job['bundle'].parent/where; other=root/name
    other.write_bytes(b'foreign synthetic reservation')
    result=run_job(job,scratch)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert other.read_bytes()==b'foreign synthetic reservation'
    assert not (job['bundle'].parent/'private'/'q4-adopt-recovery-job.pending.json').exists()


def test_recovery_job_does_not_admit_while_checkpoint_lock_is_held(job):
    from sandbox.mind.step2.reader_slot import reader_slot
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    checkpoint=job['bundle'].parent/'checkpoint'
    with reader_slot(checkpoint/'q4-checkpoint.lock',timeout_seconds=1):
        result=run_job(job,scratch)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not (job['bundle'].parent/'private'/'q4-adopt-recovery-job.pending.json').exists()


def test_admission_locks_are_released_before_the_child(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    private=job['bundle'].parent/'private'; checkpoint=job['bundle'].parent/'checkpoint'
    paths=[private/name for name in ('q4-continuation-orchestrator.lock','q4-live-update.lock',
          'q4-cutoff-owner.lock','prefix-private-state.lock')]+[checkpoint/'q4-checkpoint.lock']
    code="import msvcrt; slots=[]\n"
    code+="        for name in "+repr([str(path) for path in paths])+":\n            f=open(name,'r+b'); f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1); slots.append(f)\n"
    code+="        for f in reversed(slots):\n            f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1); f.close()"
    parent_behavior(job,code)
    result=run_job(job,scratch)
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'NOT_ADOPTED'}


def test_active_real_adoption_blocks_recovery_job(job,tmp_path,monkeypatch,capsys):
    import base64
    import threading
    from tests.tools import test_wd_q4_genesis_bootstrap as genesis_fixture
    from tests.tools import test_wd_q4_continuation_adopt as adopt_fixture
    from tools import wd_q4_continuation_adopt as adopt
    from tools import wd_q4_genesis_to_composer_cli as composer
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    case=tmp_path/'real-adoption'; case.mkdir()
    synthetic=genesis_fixture.synthetic.__wrapped__(case,monkeypatch)
    args=adopt_fixture.actual_continuation.__wrapped__(synthetic,monkeypatch,case,capsys)
    actual_route=composer._tuple_part(json.loads(args['genesis_tuple_bytes']),'route')
    private=Path(actual_route['private_state_root']); checkpoint=Path(actual_route['checkpoint_root'])
    tup=json.loads(job['tuple'].read_bytes()); route=json.loads(base64.b64decode(tup['route']['bytes_b64']))
    route.update(private_state_root=str(private),checkpoint_root=str(checkpoint))
    raw=_existing.encode(route); tup['route']={'bytes_b64':base64.b64encode(raw).decode(),'sha256':_existing.sha(raw)}
    job['tuple'].write_bytes(_existing.encode(tup))
    config=json.loads(job['config'].read_bytes()); config['tuple_sha256']=_existing.sha(job['tuple'].read_bytes())
    job['config'].write_bytes(_existing.encode(config)); job['config_pin']=_existing.sha(job['config'].read_bytes())
    entered=threading.Event(); release=threading.Event(); outcomes=[]
    def waiting_publish(*_a,**_kw):
        entered.set(); release.wait(40)
        raise KeyboardInterrupt()
    def run_adopt():
        try: adopt.run_once(**args)
        except BaseException as exc: outcomes.append(type(exc))
    with monkeypatch.context() as scoped:
        scoped.setattr(adopt.update,'_publish',waiting_publish)
        thread=threading.Thread(target=run_adopt); thread.start()
        try:
            assert entered.wait(20)
            assert (private/'q4-continuation-adopt.pending.json').exists()
            result=run_job(job,scratch)
        finally:
            release.set(); thread.join(10)
    assert not thread.is_alive() and outcomes==[adopt.AdoptInterrupted]
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()

@pytest.mark.parametrize('name',['q4-continuation-orchestrator.lock','q4-live-update.lock','q4-cutoff-owner.lock'])
def test_each_owner_lock_blocks_recovery_before_job_marker(job,name):
    from sandbox.mind.step2.reader_slot import reader_slot
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    private=job['bundle'].parent/'private'
    with reader_slot(private/name,timeout_seconds=1):
        result=run_job(job,scratch)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not (private/'q4-adopt-recovery-job.pending.json').exists()
