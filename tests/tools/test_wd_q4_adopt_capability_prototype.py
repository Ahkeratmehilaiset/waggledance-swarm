"""Real Windows handle/Job capability proof on synthetic audit paths only."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'ops/windows/reboot/Invoke-WdQ4AdoptCapabilityPrototype.ps1'
PROBE=ROOT/'tools/wd_q4_adopt_capability_probe.py'
pytestmark=pytest.mark.skipif(sys.platform!='win32', reason='Windows native handles and Job')

def test_prototype_default_off():
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT)],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'OFF'}

@pytest.mark.parametrize('scenario,expected',[('good','CAPABILITY_VALID'),('missing','REFUSED'),
    ('closed','REFUSED'),('foreign','REFUSED'),('notjob','REFUSED'),('readonly','REFUSED')])
def test_synthetic_real_windows_capability(tmp_path,scenario,expected):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'schema':'wd.synthetic-adopt-capability.v1','python_sha256':sha(sys.executable),'probe_sha256':sha(PROBE)}),encoding='ascii')
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(tmp_path),'-Scenario',scenario,'-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256',sha(manifest)],capture_output=True,text=True,timeout=30)
    assert result.returncode==(0 if expected=='CAPABILITY_VALID' else 2), result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':expected}

def test_unbound_manifest_refuses_before_marker_creation(tmp_path):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'schema':'wd.synthetic-adopt-capability.v1','python_sha256':sha(sys.executable),'probe_sha256':sha(PROBE)}),encoding='ascii')
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(tmp_path),'-Scenario','good','-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256','0'*64],capture_output=True,text=True,timeout=15)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not (tmp_path/'synthetic-capability-marker.bin').exists()


def test_outside_audit_root_refuses_before_writes(tmp_path):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'; manifest.write_bytes(b'{}')
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(ROOT),'-Scenario','good','-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256',sha(manifest)],capture_output=True,text=True,timeout=15)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert not (ROOT/'synthetic-capability-marker.bin').exists()


def test_direct_probe_rejects_absent_handles(tmp_path):
    result=subprocess.run([sys.executable,'-I','-S','-B',str(PROBE),'--child','0','0',str(tmp_path/'none'),'0'*64],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==2 and result.stdout=='' and result.stderr==''
