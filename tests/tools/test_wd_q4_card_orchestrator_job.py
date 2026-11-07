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
import re

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


def test_launcher_creation_pin_matches_raw_windows_filetime(tmp_path):
    from tests.tools.test_wd_q4_card_orchestrator_runtime_bootstrap import creation_filetime
    source=ENTRY.read_text(encoding='utf-8-sig')
    assert '.StartTime' not in source
    match=re.search(r"(?s)Add-Type -TypeDefinition @'\r?\n(.*?)\r?\n'@ -ErrorAction Stop",source)
    assert match and 'GetProcessTimes' in match.group(1)
    script=("$ErrorActionPreference='Stop'\nAdd-Type -TypeDefinition @'\n"+match.group(1)+"\n'@\n"
        "[Console]::Out.WriteLine(([ordered]@{pid=$PID;created=[WdQ4CardJobPathsV1]::CreationFiletime()} | ConvertTo-Json -Compress))\n"
        "[Console]::Out.Flush()\n[Threading.Thread]::Sleep(30000)\n")
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    child=subprocess.Popen([str(host),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                           cwd=tmp_path,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        line=child.stdout.readline()
        assert line, child.stderr.read()
        observed=json.loads(line)
        assert observed['pid']==child.pid and observed['created']==creation_filetime(child.pid)
    finally:
        if child.poll() is None: child.terminate()
        child.communicate(timeout=10)


@pytest.fixture
def case(tmp_path):
    bundle=tmp_path/'bundle'; code=bundle/'tools-bootstrap'/'tools'; code.mkdir(parents=True)
    shutil.copyfile(ENTRY,bundle/ENTRY.name); shutil.copyfile(JOB,bundle/JOB.name)
    bootstrap=code/'wd_q4_card_orchestrator_runtime_bootstrap.py'
    wrapper=bundle/'Invoke-WdBridgePython.ps1'; context=bundle/'BridgeCodeContext.ps1'
    fleet=bundle/'wd-fleet.json'; wrapper_marker=tmp_path/'synthetic-wrapper-entered'
    context.write_text('# synthetic context\n',encoding='ascii')
    fleet.write_bytes(raw({'bridge_python':{'executable':sys.executable}}))
    wrapper.write_text("param([string]$Tool,[switch]$VerifyPackage,[Parameter(ValueFromRemainingArguments=$true)][string[]]$ToolArguments)\n"
        "if ($Tool -cne 'tools/wd_q4_card_orchestrator_runtime_bootstrap.py' -or -not $VerifyPackage) { throw 'wrong-wrapper' }\n"
        "[IO.File]::WriteAllText('"+str(wrapper_marker).replace("'","''")+"','entered')\n"
        "& $env:WD_BRIDGE_PYTHON -S -B (Join-Path $PSScriptRoot 'tools-bootstrap\\tools\\wd_q4_card_orchestrator_runtime_bootstrap.py') @ToolArguments\n"
        "$global:LASTEXITCODE=$LASTEXITCODE\n",encoding='ascii')
    generation=tmp_path/'generation'; (generation/'stage').mkdir(parents=True)
    pointer=generation/'stage'/'candidate-pointer.json'; pointer.write_bytes(b'synthetic')
    bootstrap.write_text('import json,sys,os,subprocess\n'
        'if not sys.flags.isolated: sys.exit(subprocess.run([sys.executable,"-I","-S","-B",__file__,*sys.argv[1:]]).returncode)\n'
        'assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode\n'
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
           'tools-bootstrap/tools/'+bootstrap.name:sha(bootstrap.read_bytes()),
           wrapper.name:sha(wrapper.read_bytes()),context.name:sha(context.read_bytes()),fleet.name:sha(fleet.read_bytes())}
    manifest.write_bytes(raw({'schema_version':1,'source_commit':'a'*40,'files':files}))
    runtime=tmp_path/'runtime.json'; runtime.write_bytes(raw({'schema':'wd.q4-card-orchestrator-runtime.v1',
        'bundle_root':str(bundle),'manifest_sha256':sha(manifest.read_bytes()),'interpreter_path':sys.executable,
        'interpreter_sha256':sha(Path(sys.executable).read_bytes()),'runtime_root':str(runtime_root)}))
    return locals()


def launch(c, *, timeout=30, bad_pin=False, scratch=None, receipt=None):
    host=Path(os.environ['SYSTEMROOT'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    args=[str(host),'-NoProfile','-NonInteractive','-File',str(c['bundle']/ENTRY.name),'-Run',
        '-BundleRoot',str(c['bundle']),'-ExpectedCommit','a'*40,'-ExpectedManifestSha256',sha(c['manifest'].read_bytes()),
        '-RequestPath',str(c['request']),'-ExpectedRequestSha256','0'*64 if bad_pin else sha(c['request'].read_bytes()),
        '-RuntimePath',str(c['runtime']),'-ExpectedRuntimeSha256',sha(c['runtime'].read_bytes()),
        '-ScratchRoot',str(scratch or c['scratch']),'-ExpectedHostSha256',sha(host.read_bytes()),
        '-ExpectedBridgePythonSha256',sha(Path(sys.executable).read_bytes()),'-TimeoutSeconds',str(timeout)]
    if receipt is not None:
        args += ['-ReceiptPath', str(receipt)]
    return subprocess.run(args, capture_output=True,text=True,timeout=60)


def test_real_job_propagates_only_verified_candidate(case):
    result=launch(case)
    assert result.returncode==0, (result.stdout,result.stderr)
    assert case['wrapper_marker'].exists(), 'the pinned bridge wrapper was bypassed'
    assert json.loads(result.stdout)=={'status':'PREPARED','pointer_path':str(case['pointer']),
                                      'pointer_sha256':sha(case['pointer'].read_bytes())}


def test_owned_job_writes_opt_in_receipt_with_original_request_and_confirmed_cleanup(case):
    receipt = case['scratch'] / 'refresh-job-receipt.json'
    result = launch(case, receipt=receipt)
    assert result.returncode == 0, (result.stdout, result.stderr)
    answer = json.loads(result.stdout)
    assert json.loads(receipt.read_bytes()) == {
        'schema': 'wd.q4-card-refresh-job.v1', 'outcome': 'completed',
        'cleanup': 'confirmed', 'exit_code': 0,
        'request': {'path': str(case['request']), 'sha256': sha(case['request'].read_bytes())},
        'result': answer,
    }


@pytest.mark.parametrize('outside', [False, True])
def test_receipt_existing_or_outside_scratch_refuses_before_job(case, outside):
    receipt = (case['tmp_path'] if outside else case['scratch']) / 'existing.json'
    receipt.write_bytes(b'unchanged')
    result = launch(case, receipt=receipt)
    assert result.returncode == 2 and json.loads(result.stdout) == {'status': 'REFUSED'}
    assert receipt.read_bytes() == b'unchanged' and not case['wrapper_marker'].exists()


@pytest.mark.parametrize('matching', [True, False])
def test_real_job_accepts_only_shared_generation_pointer(case, matching):
    shared = case['tmp_path'] / 'shared-generations'
    shared.mkdir()
    generation = 'c' * 40
    pointer = shared / ('candidate-pointer-' + generation + '.json')
    pointer.write_bytes(case['pointer'].read_bytes())
    finish = json.loads(case['finish'].read_bytes())
    finish['composer_options'].update(shared_stage_root=str(shared), generation=generation if matching else 'd' * 40)
    case['finish'].write_bytes(raw(finish))
    request = json.loads(case['request'].read_bytes())
    request['finish']['sha256'] = sha(case['finish'].read_bytes())
    case['request'].write_bytes(raw(request))
    bootstrap = case['bootstrap']
    before = bootstrap.read_text(encoding='ascii')
    assert repr(str(case['pointer'])) in before
    bootstrap.write_text(before.replace(repr(str(case['pointer'])), repr(str(pointer))), encoding='ascii')
    manifest = json.loads(case['manifest'].read_bytes())
    manifest['files']['tools-bootstrap/tools/' + bootstrap.name] = sha(bootstrap.read_bytes())
    case['manifest'].write_bytes(raw(manifest))
    runtime = json.loads(case['runtime'].read_bytes())
    runtime['manifest_sha256'] = sha(case['manifest'].read_bytes())
    case['runtime'].write_bytes(raw(runtime))
    result = launch(case)
    if matching:
        assert result.returncode == 0, (result.stdout, result.stderr)
        assert json.loads(result.stdout)['pointer_path'] == str(pointer)
    else:
        assert result.returncode == 3 and json.loads(result.stdout) == {'status': 'UNKNOWN'}


@pytest.mark.parametrize('fault', ['uppercase', 'short', 'missing', 'scratch_overlap'])
def test_shared_stage_refuses_bad_generation_or_overlapping_scratch_before_job(case, fault):
    shared = case['scratch'] if fault == 'scratch_overlap' else case['tmp_path'] / 'shared-generations'
    if fault != 'scratch_overlap':
        shared.mkdir()
    finish = json.loads(case['finish'].read_bytes())
    options = finish['composer_options']
    options['shared_stage_root'] = str(shared)
    if fault != 'missing':
        options['generation'] = 'A' * 40 if fault == 'uppercase' else 'a' * (39 if fault == 'short' else 40)
    case['finish'].write_bytes(raw(finish))
    request = json.loads(case['request'].read_bytes())
    request['finish']['sha256'] = sha(case['finish'].read_bytes())
    case['request'].write_bytes(raw(request))
    result = launch(case)
    assert result.returncode == 2 and json.loads(result.stdout) == {'status': 'REFUSED'}
    assert not case['wrapper_marker'].exists()


def test_job_child_does_not_change_calling_session_wrapper_pin(case,monkeypatch):
    old=r'C:\synthetic-session-pin\Invoke-WdBridgePython.ps1'
    monkeypatch.setenv('WD_BRIDGE_PYTHON_WRAPPER',old)
    result=launch(case)
    assert result.returncode==0 and case['wrapper_marker'].exists()
    assert os.environ['WD_BRIDGE_PYTHON_WRAPPER']==old


def test_pre_wrapper_failure_is_a_confirmed_refusal(case):
    launcher=case['bundle']/ENTRY.name
    script=launcher.read_text(encoding='utf-8')
    anchor=' $script:bytes=0'
    assert script.count(anchor)==1
    launcher.write_text(script.replace(anchor,anchor+"\n throw 'synthetic-before-wrapper'"),encoding='utf-8')
    manifest=json.loads(case['manifest'].read_bytes()); manifest['files'][ENTRY.name]=sha(launcher.read_bytes())
    case['manifest'].write_bytes(raw(manifest))
    runtime=json.loads(case['runtime'].read_bytes()); runtime['manifest_sha256']=sha(case['manifest'].read_bytes())
    case['runtime'].write_bytes(raw(runtime))
    result=launch(case)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not case['wrapper_marker'].exists()


def test_failure_after_wrapper_entry_remains_unknown(case):
    case['wrapper'].write_text("throw 'synthetic-after-wrapper-entry'\n",encoding='ascii')
    manifest=json.loads(case['manifest'].read_bytes())
    manifest['files'][case['wrapper'].name]=sha(case['wrapper'].read_bytes())
    case['manifest'].write_bytes(raw(manifest))
    runtime=json.loads(case['runtime'].read_bytes()); runtime['manifest_sha256']=sha(case['manifest'].read_bytes())
    case['runtime'].write_bytes(raw(runtime))
    result=launch(case)
    assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
    assert 'synthetic-after-wrapper-entry' not in result.stdout+result.stderr


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
        '-ScratchRoot',str(case['bundle']),'-ExpectedHostSha256',sha(host.read_bytes()),
        '-ExpectedBridgePythonSha256',sha(Path(sys.executable).read_bytes()),'-TimeoutSeconds','20']
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
      "$b=$ast.Find({param($x) $x -is [Management.Automation.Language.FunctionDefinitionAst] -and $x.Name -ceq 'PinnedBytes'},$true)\n"
      ". ([scriptblock]::Create($b.Extent.Text))\n"
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


@pytest.mark.parametrize('quote_char',['\u2018','\u2019','\u201a','\u201b'])
def test_typographic_quotes_refuse_before_worker(case,quote_char):
    marker=case['tmp_path']/'typographic-worker-entered'
    replace_bootstrap(case,'open('+repr(str(marker))+',"w").write("entered")\n')
    moved=case['tmp_path']/('bundle'+quote_char+'quoted')
    case['bundle'].rename(moved); case['bundle']=moved; case['manifest']=moved/'deployment-manifest.json'
    runtime=json.loads(case['runtime'].read_bytes()); runtime['bundle_root']=str(moved)
    case['runtime'].write_bytes(raw(runtime))
    finish=json.loads(case['finish'].read_bytes())
    finish['composer_options'].update(bundle_store_root=str(moved),active_bundle_root=str(moved))
    case['finish'].write_bytes(raw(finish))
    request=json.loads(case['request'].read_bytes()); request['finish']['sha256']=sha(case['finish'].read_bytes())
    case['request'].write_bytes(raw(request))
    result=launch(case)
    assert result.returncode==2 and json.loads(result.stdout)=={'status':'REFUSED'}
    assert not marker.exists()


def test_job_helper_uses_the_verified_bytes_after_synthetic_swap(case):
    launcher=case['bundle']/ENTRY.name
    script=launcher.read_text(encoding='utf-8')
    marker=". ([scriptblock]::Create($helperText))"
    assert marker in script
    injection="[IO.File]::WriteAllText($jobHelper,'throw ''synthetic-tamper'''); "+marker
    launcher.write_text(script.replace(marker,injection),encoding='utf-8')
    manifest=json.loads(case['manifest'].read_bytes()); manifest['files'][ENTRY.name]=sha(launcher.read_bytes())
    case['manifest'].write_bytes(raw(manifest))
    runtime=json.loads(case['runtime'].read_bytes()); runtime['manifest_sha256']=sha(case['manifest'].read_bytes())
    case['runtime'].write_bytes(raw(runtime))
    result=launch(case)
    assert result.returncode==0 and json.loads(result.stdout)['status']=='PREPARED', (result.stdout,result.stderr)
    assert 'synthetic-tamper' in (case['bundle']/JOB.name).read_text()


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


@pytest.mark.parametrize('fault',[None,'entrypoint_missing','code_tampered'])
def test_job_uses_actual_packaged_bridge_wrapper_and_bootstrap(case,tmp_path,fault):
    from tests.tools.test_wd_bridge_code_context import _stage_fake_bundle, PWSH, HAS_BRIDGE_PYTHON
    from sandbox.mind.step2 import runtime_builder
    if PWSH is None or not HAS_BRIDGE_PYTHON:
        pytest.skip('pinned bridge interpreter unavailable')
    relative='tools/wd_q4_card_orchestrator_runtime_bootstrap.py'
    bundle=_stage_fake_bundle(tmp_path/'staged', real_files=(relative,'tools/wd_q4_live_update_bootstrap.py',
        'sandbox/mind/step2/runtime_manifest.py','tools/wd_q4_card_audit_orchestrator.py'),
        real_entrypoints={'q4_card_orchestrator_runtime':relative})
    shutil.copyfile(ENTRY,bundle/ENTRY.name); shutil.copyfile(JOB,bundle/JOB.name)
    marker=tmp_path/'actual-wrapper-cli-entered'
    fake_cli=bundle/'tools-bootstrap/tools/wd_q4_card_audit_orchestrator.py'
    fake_cli.write_text('import json\ndef main(args):\n open('+repr(str(marker))+',"w").write("entered")\n'
        ' print(json.dumps({"status":"PREPARED","pointer_path":'+repr(str(case['pointer']))+',"pointer_sha256":'+
        repr(sha(case['pointer'].read_bytes()))+'},sort_keys=True,separators=(",",":")))\n return 0\n',encoding='ascii')
    manifest_path=bundle/'deployment-manifest.json'; manifest=json.loads(manifest_path.read_bytes())
    manifest['source_commit']='a'*40
    for path,relative_path in ((bundle/ENTRY.name,ENTRY.name),(bundle/JOB.name,JOB.name),
                               (fake_cli,'tools-bootstrap/tools/wd_q4_card_audit_orchestrator.py')):
        manifest['files'][relative_path]=sha(path.read_bytes()).upper()
    if fault=='entrypoint_missing':
        definition_path=bundle/'bridge-code-files.json'; definition=json.loads(definition_path.read_bytes())
        definition['python_entrypoints'].pop('q4_card_orchestrator_runtime')
        definition_path.write_bytes(raw(definition))
        manifest['files']['bridge-code-files.json']=sha(definition_path.read_bytes()).upper()
    manifest_path.write_bytes(raw(manifest))
    if fault=='code_tampered':
        fake_cli.write_bytes(fake_cli.read_bytes()+b'\n# unpinned synthetic mutation\n')
    runtime_root=case['runtime_root']
    for name in ('assets/code','site','model'): (runtime_root/name).mkdir(parents=True)
    (runtime_root/'assets/code/stub').write_bytes(b'code')
    (runtime_root/'site/stub').write_bytes(b'site')
    (runtime_root/'model/stub').write_bytes(b'model')
    runtime_manifest=tmp_path/'runtime-manifest.json'
    built=runtime_builder.build_runtime_manifest(runtime_root,runtime_manifest,generation='a'*40,revision='b'*40)
    snapshot=json.loads(case['snapshot'].read_bytes()); snapshot['trusted_model_root']=str(runtime_root/'model')
    case['snapshot'].write_bytes(raw(snapshot))
    finish=json.loads(case['finish'].read_bytes())
    finish['composer_options'].update(bundle_store_root=str(bundle),active_bundle_root=str(bundle))
    case['finish'].write_bytes(raw(finish))
    request=json.loads(case['request'].read_bytes())
    request['snapshot_template']['sha256']=sha(case['snapshot'].read_bytes())
    request['finish']['sha256']=sha(case['finish'].read_bytes()); case['request'].write_bytes(raw(request))
    verifier=bundle/'tools-bootstrap/sandbox/mind/step2/runtime_manifest.py'
    runtime={'schema':'wd.q4-card-orchestrator-runtime.v1','bundle_root':str(bundle),
        'manifest_sha256':sha(manifest_path.read_bytes()),'interpreter_path':sys.executable,
        'interpreter_sha256':sha(Path(sys.executable).read_bytes()),'runtime_root':str(runtime_root),
        'runtime_manifest_path':str(runtime_manifest),'runtime_manifest_sha256':built['manifest_sha256'],
        'runtime_generation':'a'*40,'runtime_revision':'b'*40,'verifier_sha256':sha(verifier.read_bytes())}
    case['runtime'].write_bytes(raw(runtime)); case['bundle']=bundle; case['manifest']=manifest_path
    result=launch(case,timeout=45)
    if fault:
        assert result.returncode==3 and json.loads(result.stdout)=={'status':'UNKNOWN'}
        assert not marker.exists()
        return
    assert result.returncode==0, (result.stdout,result.stderr)
    assert marker.exists() and json.loads(result.stdout)['status']=='PREPARED'
