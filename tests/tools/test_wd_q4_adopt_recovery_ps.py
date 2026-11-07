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
import time
import pytest

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


def test_unknown_child_keeps_marker_and_never_retries(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    cli=job['bundle']/'tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'
    cli.write_text("def main(args):\n print('{\"status\":\"UNKNOWN\"}')\n return 3\n",encoding='ascii')
    repin_changed(job,['tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'])
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
        text=text.replace('$pendingStream.Write($pendingBytes,0,$pendingBytes.Length)', "throw 'synthetic crash before write'")
    else:
        text=text.replace('$pendingStream.Position=0', "throw 'synthetic crash after receipt'")
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
    cli=job['bundle']/'tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'
    cli.write_text("def main(args):\n import time\n time.sleep(20)\n print('{\"status\":\"NOT_ADOPTED\"}')\n return 0\n",encoding='ascii')
    repin_changed(job,['tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'])
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
    cli=job['bundle']/'tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'
    cli.write_text("def main(args):\n import msvcrt\n"
        + " p=open("+repr(str(private/'prefix-private-state.lock'))+",'r+b'); p.seek(0); msvcrt.locking(p.fileno(),msvcrt.LK_NBLCK,1)\n"
        + " q=open("+repr(str(checkpoint/'q4-checkpoint.lock'))+",'r+b'); q.seek(0); msvcrt.locking(q.fileno(),msvcrt.LK_NBLCK,1)\n"
        + " q.seek(0); msvcrt.locking(q.fileno(),msvcrt.LK_UNLCK,1); q.close()\n"
        + " p.seek(0); msvcrt.locking(p.fileno(),msvcrt.LK_UNLCK,1); p.close()\n"
        + " print('{\"status\":\"NOT_ADOPTED\"}'); return 0\n",encoding='ascii')
    repin_changed(job,['tools-bootstrap/tools/wd_q4_continuation_adopt_recovery_cli.py'])
    result=run_job(job,scratch)
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'NOT_ADOPTED'}
