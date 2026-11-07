"""Synthetic Windows Job entry tests; no deployment or live bridge."""
import hashlib
import ctypes
import base64
import time
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / 'ops/windows/reboot/Invoke-WdQ4CardOrchestratorJob.ps1'
JOB = ROOT / 'ops/windows/reboot/WdQ4ProcessJob.ps1'
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Job required')


def sha(data): return hashlib.sha256(data).hexdigest()
def raw(value): return json.dumps(value,sort_keys=True,separators=(',',':')).encode('ascii')


def test_off_without_inputs():
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(ENTRY)],
                            capture_output=True,text=True,timeout=20)
    assert result.returncode == 0 and json.loads(result.stdout) == {'status':'OFF'}


@pytest.fixture
def case(tmp_path):
    bundle=tmp_path/'bundle'; code=bundle/'tools-bootstrap'/'tools'; code.mkdir(parents=True)
    shutil.copyfile(ENTRY,bundle/ENTRY.name); shutil.copyfile(JOB,bundle/JOB.name)
    bootstrap=code/'wd_q4_card_orchestrator_runtime_bootstrap.py'
    generation=tmp_path/'generation'; (generation/'stage').mkdir(parents=True)
    pointer=generation/'stage'/'candidate-pointer.json'; pointer.write_bytes(b'synthetic')
    bootstrap.write_text('import json,sys,os\nassert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode\n'
        'assert "PYTHONPATH" not in os.environ\nassert os.environ["WD_REBOOT_EXPECTED_MANIFEST_HASH"] == sys.argv[sys.argv.index("--manifest-sha256")+1]\n'
        'print(json.dumps({"status":"PREPARED","pointer_path":'+repr(str(pointer))+
        ',"pointer_sha256":'+repr(sha(pointer.read_bytes()))+'},sort_keys=True,separators=(",",":")))\n',encoding='ascii')
    decision=tmp_path/'decision.json'; snapshot=tmp_path/'snapshot.json'; finish=tmp_path/'finish.json'
    bridge=tmp_path/'bridge'; bridge.mkdir()
    source=bridge/'source.jsonl'; source.write_text('synthetic\n',encoding='ascii')
    anchor=tmp_path/'anchor'; anchor.mkdir(); repo=tmp_path/'repo'; repo.mkdir()
    decision.write_bytes(raw({'schema':'wd.q4-real-card-decision-request.v1','source_mode':'operator_authorized_source',
        'source_path':str(source),'anchor':{'root':str(anchor)},'git':{'repository':str(repo)},'inputs':{}}))
    audit=tmp_path/'audit'; audit.mkdir(); scratch=tmp_path/'scratch'; scratch.mkdir(); runtime_root=tmp_path/'runtime'; runtime_root.mkdir()
    snapshot.write_bytes(raw({'schema':'wd.q4-real-card-snapshot-request.v1','trusted_model_root':str(runtime_root)}))
    finish.write_bytes(raw({'composer_options':{'bundle_store_root':str(bundle),'active_bundle_root':str(bundle)}}))
    request=tmp_path/'request.json'; request.write_bytes(raw({'schema':'wd.q4-card-real-candidate-request.v1',
        'decision':{'path':str(decision),'sha256':sha(decision.read_bytes())},
        'snapshot_template':{'path':str(snapshot),'sha256':sha(snapshot.read_bytes())},
        'finish':{'path':str(finish),'sha256':sha(finish.read_bytes())},'output_root':str(audit),'generation_root':str(generation)}))
    manifest=bundle/'deployment-manifest.json'
    files={ENTRY.name:sha((bundle/ENTRY.name).read_bytes()),JOB.name:sha((bundle/JOB.name).read_bytes()),
           'tools-bootstrap/tools/'+bootstrap.name:sha(bootstrap.read_bytes())}
    manifest.write_bytes(raw({'schema_version':1,'source_commit':'a'*40,'files':files}))
    runtime=tmp_path/'runtime.json'; runtime.write_bytes(raw({'schema':'wd.q4-card-orchestrator-runtime.v1',
        'bundle_root':str(bundle),'manifest_sha256':sha(manifest.read_bytes()),'interpreter_path':sys.executable,
        'interpreter_sha256':sha(Path(sys.executable).read_bytes()),'runtime_root':str(runtime_root)}))
    return locals()


def launch(c, *, timeout=30, bad_pin=False, scratch=None):
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    return subprocess.run([str(host),'-NoProfile','-NonInteractive','-File',str(c['bundle']/ENTRY.name),'-Run',
        '-BundleRoot',str(c['bundle']),'-ExpectedCommit','a'*40,'-ExpectedManifestSha256',sha(c['manifest'].read_bytes()),
        '-RequestPath',str(c['request']),'-ExpectedRequestSha256','0'*64 if bad_pin else sha(c['request'].read_bytes()),
        '-RuntimePath',str(c['runtime']),'-ExpectedRuntimeSha256',sha(c['runtime'].read_bytes()),
        '-ScratchRoot',str(scratch or c['scratch']),'-ExpectedHostSha256',sha(host.read_bytes()),'-TimeoutSeconds',str(timeout)],
        capture_output=True,text=True,timeout=60)


def test_real_job_propagates_only_verified_candidate(case):
    result=launch(case)
    assert result.returncode==0, (result.stdout,result.stderr)
    assert json.loads(result.stdout)=={'status':'PREPARED','pointer_path':str(case['pointer']),
                                      'pointer_sha256':sha(case['pointer'].read_bytes())}


def test_bad_pin_and_overlapping_scratch_refuse(case):
    for kw in ({'bad_pin':True},{'scratch':case['bundle']},{'scratch':case['generation']},{'scratch':case['bridge']}):
        result=launch(case,**kw)
        assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}


def replace_bootstrap(case, source):
    case['bootstrap'].write_text(source,encoding='ascii')
    manifest=json.loads(case['manifest'].read_bytes())
    manifest['files']['tools-bootstrap/tools/'+case['bootstrap'].name]=sha(case['bootstrap'].read_bytes())
    case['manifest'].write_bytes(raw(manifest))
    runtime=json.loads(case['runtime'].read_bytes()); runtime['manifest_sha256']=sha(case['manifest'].read_bytes())
    case['runtime'].write_bytes(raw(runtime))


def test_internal_worker_cannot_bypass_launcher_from_unrelated_job(case):
    marker=case['tmp_path']/'worker-bypass-marker'
    replace_bootstrap(case,'open('+repr(str(marker))+',"w").write("entered")\n')
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    args=['-NoProfile','-NonInteractive','-File',str(case['bundle']/ENTRY.name),'-Run','-InternalWorker',
        '-BundleRoot',str(case['bundle']),'-ExpectedCommit','a'*40,'-ExpectedManifestSha256',sha(case['manifest'].read_bytes()),
        '-RequestPath',str(case['request']),'-ExpectedRequestSha256',sha(case['request'].read_bytes()),
        '-RuntimePath',str(case['runtime']),'-ExpectedRuntimeSha256',sha(case['runtime'].read_bytes()),
        '-ScratchRoot',str(case['bundle']),'-ExpectedHostSha256',sha(host.read_bytes()),'-TimeoutSeconds','20']
    quote=lambda s:"'"+str(s).replace("'","''")+"'"
    ps=". "+quote(case['bundle']/JOB.name)+"\n"
    ps+=("$r=Invoke-WdQ4ProcessJob -Executable "+quote(host)+" -Arguments @("+','.join(map(quote,args))+
        ") -WorkingDirectory "+quote(case['bundle'])+" -ScratchRoot "+quote(case['scratch'])+
        " -InputBytes ([byte[]]@()) -TimeoutSeconds 20 -MaxOutputBytes 4096 -DetailedOutcome\n")
    ps+="if ($null -eq $r) { exit 9 }; exit 0\n"
    encoded=base64.b64encode(ps.encode('utf-16le')).decode('ascii')
    result=subprocess.run([str(host),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0, result.stderr
    assert not marker.exists(), 'internal worker bypassed launch admission from another Job'


def test_stdout_overflow_terminates_child_promptly(case):
    marker=case['tmp_path']/'overflow-child.json'
    replace_bootstrap(case,'import json,os,time\nopen('+repr(str(marker))+',"w").write(json.dumps({"pid":os.getpid()}))\n'
        'print("X"*10000,flush=True)\ntime.sleep(60)\n')
    start=time.monotonic(); result=launch(case,timeout=30); elapsed=time.monotonic()-start
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert marker.exists() and elapsed < 20
    pid=json.loads(marker.read_bytes())['pid']
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]; kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]; kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if handle:
        try: assert kernel.WaitForSingleObject(handle,5000)==0
        finally: kernel.CloseHandle(handle)


def test_pinned_json_parses_the_hashed_bytes_after_synthetic_change(tmp_path):
    target=tmp_path/'synthetic.json'; target.write_bytes(b'{"value":"verified"}')
    replacement=tmp_path/'synthetic-replacement.json'; replacement.write_bytes(b'{"value":"changed"}')
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    quote=lambda s:"'"+str(s).replace("'","''")+"'"
    injection='[IO.File]::WriteAllBytes($Path,[IO.File]::ReadAllBytes('+quote(replacement)+')); '
    script=("$t=$null;$e=$null;$ast=[Management.Automation.Language.Parser]::ParseFile("+quote(ENTRY)+",[ref]$t,[ref]$e)\n"
      "$n=$ast.Find({param($x) $x -is [Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -ceq 'PinnedJson'},$true)\n"
      "$source=$n.Extent.Text\n"
      "$needle='$text=[Text.UTF8Encoding]::new($false,$true).GetString($bytes)'\n"
      "$inject="+quote(injection)+"+$needle\n"
      "if(-not $source.Contains($needle)){exit 9}; . ([scriptblock]::Create($source.Replace($needle,$inject)))\n"
      "$obj=PinnedJson "+quote(target)+" "+quote(sha(target.read_bytes()))+" 1024\n"
      "[Console]::Out.WriteLine([string]$obj.value)\n")
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result=subprocess.run([str(host),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                          capture_output=True,text=True,timeout=20)
    assert result.returncode==0 and result.stdout.strip()=='verified', result.stderr
    assert json.loads(target.read_bytes())['value']=='changed'


def test_powershell_metacharacters_in_bundle_path_are_literals(case):
    moved=case['tmp_path']/"bundle'$(throw 'synthetic-injection')"
    case['bundle'].rename(moved); case['bundle']=moved
    case['manifest']=moved/'deployment-manifest.json'
    runtime=json.loads(case['runtime'].read_bytes()); runtime['bundle_root']=str(moved)
    case['runtime'].write_bytes(raw(runtime))
    finish=json.loads(case['finish'].read_bytes())
    finish['composer_options'].update(bundle_store_root=str(moved),active_bundle_root=str(moved))
    case['finish'].write_bytes(raw(finish))
    request=json.loads(case['request'].read_bytes()); request['finish']['sha256']=sha(case['finish'].read_bytes())
    case['request'].write_bytes(raw(request))
    result=launch(case)
    assert result.returncode==0, (result.stdout,result.stderr)
    assert json.loads(result.stdout)['status']=='PREPARED'


@pytest.mark.parametrize('source', [
    'print("synthetic-private-canary")\n',
    'import sys; print("synthetic-private-canary"); sys.exit(2)\n',
    'print(\'{"status":"PREPARED","pointer_path":"fake","pointer_sha256":"\'+"0"*64+\'"}\')\n',
])
def test_invalid_or_failed_worker_is_unknown_without_leak(case,source):
    replace_bootstrap(case,source)
    result=launch(case)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert 'synthetic-private-canary' not in result.stdout + result.stderr


def test_timeout_stops_synthetic_owned_child_and_returns_unknown(case):
    marker=case['tmp_path']/'owned-child.json'
    child_code='import time; time.sleep(60)'
    replace_bootstrap(case,'import json,subprocess,sys,time\n'
        'p=subprocess.Popen([sys.executable,"-c",'+repr(child_code)+'])\n'
        'open('+repr(str(marker))+',"w").write(json.dumps({"pid":p.pid}))\n'
        'time.sleep(60)\n')
    result=launch(case,timeout=5)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert marker.exists(), 'synthetic child was not observed before the timeout'
    pid=json.loads(marker.read_bytes())['pid']
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if handle:
        try:
            assert kernel.WaitForSingleObject(handle,5000)==0, 'observed PID still running'
        finally:
            kernel.CloseHandle(handle)
