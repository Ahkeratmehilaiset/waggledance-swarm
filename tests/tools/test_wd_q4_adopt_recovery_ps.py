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

def run_job(item,scratch):
    host=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    cmd=['powershell','-NoProfile','-NonInteractive','-File',str(item['bundle']/'Invoke-WdQ4AdoptRecovery.ps1'),'-Run',
         '-BundleRoot',str(item['bundle']),'-ExpectedCommit','c'*40,
         '-ExpectedManifestSha256',item['manifest_pin'],'-ConfigPath',str(item['config']),
         '-ExpectedConfigSha256',item['config_pin'],'-RuntimePath',str(item['spec']),
         '-ExpectedRuntimeSha256',item['spec_pin'],'-ScratchRoot',str(scratch),
         '-ExpectedHostSha256',hashlib.sha256(host.read_bytes()).hexdigest(),'-TimeoutSeconds','30']
    return subprocess.run(cmd,text=True,capture_output=True,timeout=50)

def test_owned_job_invokes_pinned_synthetic_recovery_cli(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    result=run_job(job,scratch)
    assert result.returncode==0, result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':'NOT_ADOPTED'}

def test_job_rejects_changed_receipt(job):
    prep_job(job); scratch=job['bundle'].parent/'scratch'; scratch.mkdir()
    (job['config'].parent/'receipt.json').write_bytes(b'{"changed":true}')
    result=run_job(job,scratch)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
