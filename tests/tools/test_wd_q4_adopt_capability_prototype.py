"""Real Windows handle/Job capability proof on synthetic audit paths only."""
import hashlib
import json
import secrets
from pathlib import Path
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
SCRIPT=ROOT/'ops/windows/reboot/Invoke-WdQ4AdoptCapabilityPrototype.ps1'
PROBE=ROOT/'tools/wd_q4_adopt_capability_probe.py'
pytestmark=pytest.mark.skipif(sys.platform!='win32', reason='Windows native handles and Job')

def make_manifest(path):
    sha=lambda value: hashlib.sha256(value).hexdigest()
    attempt=secrets.token_hex(32)
    marker=('wd.synthetic-adopt-marker:'+attempt+'\n').encode('ascii')
    value={'schema':'wd.synthetic-adopt-capability.v1','python_sha256':sha(Path(sys.executable).read_bytes()),
           'probe_sha256':sha(PROBE.read_bytes()),'attempt':attempt,'marker_sha256':sha(marker)}
    path.write_text(json.dumps(value),encoding='ascii')

def test_prototype_default_off():
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT)],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==0 and json.loads(result.stdout)=={'status':'OFF'}

@pytest.mark.parametrize('scenario,expected',[('good','CAPABILITY_VALID'),('missing','REFUSED'),
    ('closed','REFUSED'),('foreign','REFUSED'),('notjob','REFUSED'),('readonly','REFUSED')])
def test_synthetic_real_windows_capability(tmp_path,scenario,expected):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    make_manifest(manifest)
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(tmp_path),'-Scenario',scenario,'-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256',sha(manifest)],capture_output=True,text=True,timeout=30)
    assert result.returncode==(0 if expected=='CAPABILITY_VALID' else 2), result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':expected}

def test_unbound_manifest_refuses_before_marker_creation(tmp_path):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    make_manifest(manifest)
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
    result=subprocess.run([sys.executable,'-I','-S','-B',str(PROBE),'--child','0',str(tmp_path/'none'),str(tmp_path/'manifest'),'0'*64,'once'],
                          capture_output=True,text=True,timeout=15)
    assert result.returncode==2 and result.stdout=='' and result.stderr==''

@pytest.mark.parametrize('scenario',['unsafejob','deletecap'])
def test_unsafe_job_or_delete_capable_marker_is_refused(tmp_path,scenario):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    make_manifest(manifest)
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(tmp_path),'-Scenario',scenario,'-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256',sha(manifest)],capture_output=True,text=True,timeout=30)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}


def test_parent_death_terminates_job_child(tmp_path):
    import ctypes
    import time
    from ctypes import wintypes
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    make_manifest(manifest)
    cmd=['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run','-AuditRoot',str(tmp_path),
       '-Scenario','killparent','-Python',sys.executable,'-PythonSha256',sha(sys.executable),'-ProbeSha256',sha(PROBE),
       '-Manifest',str(manifest),'-ManifestSha256',sha(manifest)]
    parent=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    child=None
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]; kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]; kernel.WaitForSingleObject.restype=wintypes.DWORD
    kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]; kernel.TerminateProcess.restype=wintypes.BOOL
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]; kernel.CloseHandle.restype=wintypes.BOOL
    try:
        ready=tmp_path/'synthetic-child-ready.json'; deadline=time.monotonic()+20
        while not ready.exists() and time.monotonic()<deadline:
            if parent.poll() is not None: pytest.fail('prototype launcher exited before child was ready')
            time.sleep(.05)
        assert ready.exists()
        pid=json.loads(ready.read_text())['pid']
        child=kernel.OpenProcess(0x00100001,False,pid)
        assert child
        parent.kill(); parent.communicate(timeout=10)
        assert kernel.WaitForSingleObject(child,10000)==0
    finally:
        if parent.poll() is None: parent.kill(); parent.communicate(timeout=10)
        if child:
            if kernel.WaitForSingleObject(child,0)!=0:
                kernel.TerminateProcess(child,125); kernel.WaitForSingleObject(child,2000)
            kernel.CloseHandle(child)

@pytest.mark.parametrize('scenario,expected',[('mutate','REFUSED'),('grandchild','CAPABILITY_VALID')])
def test_capability_rechecked_and_not_inherited(tmp_path,scenario,expected):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'
    make_manifest(manifest)
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(tmp_path),'-Scenario',scenario,'-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256',sha(manifest)],capture_output=True,text=True,timeout=30)
    assert result.returncode==(0 if expected=='CAPABILITY_VALID' else 2), result.stdout+result.stderr
    assert json.loads(result.stdout)=={'status':expected}

@pytest.mark.parametrize('scenario',['breakaway','silentbreakaway','wrongattempt'])
def test_unsafe_job_flags_and_wrong_attempt_are_refused(tmp_path,scenario):
    sha=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest()
    manifest=tmp_path/'manifest.json'; make_manifest(manifest)
    result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-File',str(SCRIPT),'-Run',
      '-AuditRoot',str(tmp_path),'-Scenario',scenario,'-Python',sys.executable,'-PythonSha256',sha(sys.executable),
      '-ProbeSha256',sha(PROBE),'-Manifest',str(manifest),'-ManifestSha256',sha(manifest)],capture_output=True,text=True,timeout=30)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
