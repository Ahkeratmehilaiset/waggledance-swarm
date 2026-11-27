"""Synthetic, audit-only reproduction of optional Mind package verification."""
import hashlib
import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT=Path.cwd()
REBOOT=ROOT/"ops/windows/reboot"
PS=Path(os.environ.get("SystemRoot","C:/Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
pytestmark=pytest.mark.skipif(sys.platform!="win32" or not PS.is_file(),reason="Windows PowerShell bundle bootstrap")
MIND="tools/wd_bridge_mind_session.py"
LEGACY="tools/bridge_next_action.py"
DEPENDENCY="tools/wd_mind_fixture.py"

def digest(data):
    return hashlib.sha256(data).hexdigest().upper()

@pytest.fixture
def bundle(tmp_path):
    root=tmp_path/"b"
    code=root/"tools-bootstrap"
    contents={
        "tools/__init__.py": b"",
        MIND:b"print('SYNTHETIC_CALLER_RAN')\n",
        LEGACY:b"print('SYNTHETIC_LEGACY_RAN')\n",
        DEPENDENCY:b"VALUE = 'pinned fixture'\n",
        "python-wheels/wdfake-0.1.0-py3-none-any.whl":b"synthetic-not-installed-wheel",
        "python-site/wdfake/__init__.py":b"",
    }
    for relative,data in contents.items():
        path=code/relative
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(data)
    definition={
        "schema":"wd.bridge-code-package.v1","package_relative_root":"tools-bootstrap",
        "invocation_wrapper_relative":"Invoke-WdBridgePython.ps1",
        "python_files":["tools/__init__.py",MIND,LEGACY,DEPENDENCY],
        "python_entrypoints":{"mind_session":MIND,"next_action":LEGACY},
        "python_requirements":[{"name":"wdfake","version":"0.1.0","wheel":"wdfake-0.1.0-py3-none-any.whl","sha256":digest(contents["python-wheels/wdfake-0.1.0-py3-none-any.whl"]),"import_path":"wdfake/__init__.py"}],
        "wheel_store_relative":"python-wheels","python_site_relative":"python-site",
        "import_smoke":{"third_party_module":"wdfake","package_modules":["tools.wd_bridge_mind_session"]},
        "isolation_environment":{"PYTHONIOENCODING":"utf-8","PYTHONDONTWRITEBYTECODE":"1","PYTHONNOUSERSITE":"1","PYTHONSAFEPATH":"1"},
    }
    (root/"bridge-code-files.json").write_text(json.dumps(definition),encoding="utf-8")
    for name in ("BridgeCodeContext.ps1","Invoke-WdBridgePython.ps1"):
        (root/name).write_bytes((REBOOT/name).read_bytes())
    files={"tools-bootstrap/"+relative:digest(data) for relative,data in contents.items()}
    for name in ("bridge-code-files.json","BridgeCodeContext.ps1","Invoke-WdBridgePython.ps1"):
        files[name]=digest((root/name).read_bytes())
    (root/"deployment-manifest.json").write_text(json.dumps({"schema_version":1,"source_commit":"0"*40,"files":files}),encoding="utf-8")
    return root

def invoke(bundle,tool=MIND,*,verify=False,anchor=True):
    env=dict(os.environ)
    env["PSModulePath"]=str(PS.parent/"Modules")
    # Fixtures use their own externally anchored manifest, never the installed bundle.
    for name in list(env):
        if name.startswith("WD_BRIDGE_") or name=="WD_REBOOT_EXPECTED_MANIFEST_HASH":
            env.pop(name)
    env["WD_BRIDGE_PYTHON"]=sys.executable
    env["WD_BRIDGE_PYTHON_SHA256"]=digest(Path(sys.executable).read_bytes())
    if anchor:
        env["WD_REBOOT_EXPECTED_MANIFEST_HASH"]=digest((bundle/"deployment-manifest.json").read_bytes())
    wrapper=str(bundle/"Invoke-WdBridgePython.ps1").replace("'","''")
    command=f"$ErrorActionPreference='Stop'; & '{wrapper}' '{tool}'"+(" -VerifyPackage" if verify else "")+"; exit $LASTEXITCODE"
    result=subprocess.run([str(PS),"-NoProfile","-NonInteractive","-Command",command],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    target=bundle.parent/("observed-"+tool.rsplit("/",1)[-1]+("-verify" if verify else "")+".json")
    target.write_text(json.dumps({"synthetic":True,"tool":tool,"verify_switch":verify,"external_anchor_supplied":anchor,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr,"command":[str(PS),"-NoProfile","-NonInteractive","-Command",command],"cwd":str(ROOT)},indent=2),encoding="utf-8")
    return result

@pytest.mark.parametrize("change",["tampered","missing","extra"])
def test_mind_rejects_changed_package(bundle,change):
    dependency=bundle/"tools-bootstrap"/DEPENDENCY
    if change=="tampered":
        dependency.write_text("VALUE = 'changed fixture'\n",encoding="utf-8")
    elif change=="missing":
        dependency.unlink()
    else:
        (dependency.parent/"wd_mind_unlisted.py").write_text("UNLISTED = True\n",encoding="utf-8")
    result=invoke(bundle)
    assert result.returncode!=0 and "SYNTHETIC_CALLER_RAN" not in result.stdout, "Mind executed without mandatory whole-package verification: "+result.stdout+result.stderr

def test_mind_requires_external_anchor(bundle):
    result=invoke(bundle,anchor=False)
    assert result.returncode!=0 and "SYNTHETIC_CALLER_RAN" not in result.stdout, "Mind executed without external manifest anchor: "+result.stdout+result.stderr

def test_explicit_verify_refuses_tamper(bundle):
    (bundle/"tools-bootstrap"/DEPENDENCY).write_text("CHANGED = True\n",encoding="utf-8")
    result=invoke(bundle,verify=True)
    assert result.returncode!=0 and "hash mismatch" in result.stderr and "SYNTHETIC_CALLER_RAN" not in result.stdout, result.stdout+result.stderr

def test_dynamic_member_is_not_entrypoint(bundle):
    result=invoke(bundle,tool=DEPENDENCY)
    assert result.returncode!=0 and "outside the packaged entrypoints" in result.stderr, result.stdout+result.stderr

def test_valid_mind_still_runs(bundle):
    result=invoke(bundle)
    assert result.returncode==0 and result.stdout.strip()=="SYNTHETIC_CALLER_RAN", result.stdout+result.stderr

def test_legacy_entrypoint_still_runs(bundle):
    result=invoke(bundle,tool=LEGACY)
    assert result.returncode==0 and result.stdout.strip()=="SYNTHETIC_LEGACY_RAN", result.stdout+result.stderr

# Frozen Git-tree/archive member contracts; all repositories below are synthetic.
import zipfile

DEPLOY=REBOOT/"Deploy-WdRebootBundle.ps1"
NATIVE_GIT=Path("C:/Program Files/Git/mingw64/bin/git.exe")


def _native_probe(body, *, env=None):
    deploy=str(DEPLOY).replace("'", "''")
    command=f"""
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('{deploy}',[ref]$null,[ref]$null)
foreach($f in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}},$false)){{
 . ([scriptblock]::Create($f.Extent.Text))
}}
{body}
"""
    environment={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    environment["PSModulePath"]=str(PS.parent/"Modules")
    if env is not None:
        environment.update(env)
    return subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-Command", command],
                          cwd=ROOT, env=environment, capture_output=True, text=True, timeout=30)


@pytest.mark.parametrize("pin", ["", "0" * 64])
def test_native_git_missing_or_wrong_pin_refuses_before_repository_lookup(pin):
    path=str(NATIVE_GIT).replace("'", "''")
    result=_native_probe(f"""
try {{
 $binding=Open-WdPinnedGit -GitExecutable '{path}' -ExpectedGitSha256 '{pin}'
 Close-WdPinnedGit -GitContext $binding
 throw 'UNEXPECTED_ACCEPT'
}} catch {{
 if ($_.Exception.Message -notlike 'Mind Git: pin*') {{ throw }}
 'PIN_REFUSED_BEFORE_SPAWN'
}}
""")
    assert result.returncode == 0 and result.stdout.strip() == "PIN_REFUSED_BEFORE_SPAWN", result.stdout+result.stderr


def test_native_git_missing_descriptor_refuses_even_valid_synthetic_repository(tree):
    # The current unpinned entrypoint accepts this. The new route must refuse
    # before Git even when the source itself is a harmless, valid repository.
    result=_package(tree, pinned=False)
    _refused(result)
    assert "Mind Git:" in result.stdout


@pytest.mark.parametrize("pinned", [False, True])
def test_native_git_path_shadow_cannot_fabricate_repository(tmp_path, monkeypatch, pinned):
    fake=tmp_path/"fake-git"
    fake.mkdir()
    executable=fake/"git.exe"
    source=r'''using System;
class FakeGit {
 static int Main(string[] args) {
  string command=String.Join(" ",args);
  string root=Environment.GetEnvironmentVariable("WD_TEST_GIT_ROOT");
  string oid=Environment.GetEnvironmentVariable("WD_TEST_GIT_OID");
  if(command.Contains("rev-parse HEAD")) Console.WriteLine(new string('1',40));
  else if(command.Contains("rev-parse --show-toplevel")) Console.WriteLine(root);
  else if(command.Contains("ls-tree -r")) Console.WriteLine("100644 blob "+oid+"\ttools/wd_mind_a.py");
  else if(command.Contains("ls-tree --full-tree")) Console.WriteLine("040000 tree "+new string('2',40)+"\ttools");
  else if(command.Contains("status --porcelain")) {}
  else if(command.Contains("cat-file -s")) Console.WriteLine(9);
  else if(command.Contains("cat-file blob")) Console.Write("FAKE = 1\n");
  else return 2;
  return 0;
 }
}'''
    destination=str(executable).replace("'", "''")
    compile_result=_native_probe("Add-Type -TypeDefinition @'\n"+source+"\n'@ -OutputType ConsoleApplication -OutputAssembly '"+destination+"'")
    assert compile_result.returncode == 0, compile_result.stderr
    repo=tmp_path/"not-a-repository"
    (repo/"tools").mkdir(parents=True)
    data=b"FAKE = 1\n"
    (repo/"tools/wd_mind_a.py").write_bytes(data)
    monkeypatch.setenv("WD_TEST_GIT_ROOT", str(repo))
    monkeypatch.setenv("WD_TEST_GIT_OID", hashlib.sha1(b"blob 9\0"+data).hexdigest())
    monkeypatch.setenv("PATH", str(fake)+os.pathsep+os.environ.get("PATH", ""))
    assert not (repo/".git").exists()
    result=_package(repo, commit="1"*40, pinned=pinned)
    _refused(result)
    assert ("HEAD differs" if pinned else "Mind Git:") in result.stdout


def _binding_script():
    # This observed test-fixture pin is explicitly not a production anchor.
    return f"$binding=Open-WdPinnedGit -GitExecutable '{NATIVE_GIT}' -ExpectedGitSha256 '{digest(NATIVE_GIT.read_bytes())}'\n"


@pytest.mark.parametrize("rights,propagation,volume,accepted", [
    (2, "None", False, False), (4, "None", False, False),
    (64, "None", False, False), (65536, "None", False, False),
    (262144, "None", False, False), (524288, "None", False, False),
    (0x40000000, "None", False, False), (0x10000000, "None", False, False),
    (0x40000000, "InheritOnly", False, True),
    (1179817, "None", False, True), (4, "None", True, True),
    (2, "None", True, False), (64, "None", True, False),
])
def test_native_git_acl_mutation_owner_and_inheritance_rules(rights, propagation, volume, accepted):
    # Real .NET ACL rule objects; no host DACL is changed. A deny ACE does not
    # turn an unsafe allow into a proof of safety under this conservative rule.
    flags="CIIO" if propagation == "InheritOnly" else "CI"
    body=f"""
$acl=New-Object Security.AccessControl.FileSecurity
$acl.SetSecurityDescriptorSddlForm('O:BAG:BAD:(D;;FA;;;BU)(A;{flags};0x{rights:x};;;BU)')
$rules=@($acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier]))
try {{
 Assert-WdGitAclRules -OwnerSid 'S-1-5-32-544' -Rules $rules -VolumeRoot:${str(volume).lower()}
 'ACCEPT'
}} catch {{ 'REFUSE' }}
"""
    result=_native_probe(body)
    assert result.returncode == 0 and result.stdout.strip() == ("ACCEPT" if accepted else "REFUSE"), result.stdout+result.stderr


def test_native_git_acl_owner_and_null_dacl_fail_closed():
    result=_native_probe(r"""
try { Assert-WdGitAclRules -OwnerSid 'S-1-5-21-1-2-3-1000' -Rules @(); throw 'owner accepted' }
catch { if ($_.Exception.Message -cne 'Mind Git: untrusted path owner') { throw } }
# An empty DACL grants no access; a NULL DACL is a different, unsafe condition.
Assert-WdGitAclRules -OwnerSid 'S-1-5-32-544' -Rules @()
function Get-Acl {
 param($LiteralPath)
 $acl=New-Object Security.AccessControl.FileSecurity
 $acl.SetSecurityDescriptorSddlForm('O:BAG:BAD:NO_ACCESS_CONTROL')
 return $acl
}
try { Assert-WdNativeGitPath -Path 'C:\Program Files\Git\mingw64\bin\git.exe'; throw 'null DACL accepted' }
catch { if ($_.Exception.Message -cne 'Mind Git: null path DACL') { throw } }
'ACL_REFUSED'
""")
    assert result.returncode == 0 and result.stdout.strip() == "ACL_REFUSED", result.stdout+result.stderr


@pytest.mark.parametrize("component", ["C:\\", "C:\\Program Files", "C:\\Program Files\\Git\\mingw64\\bin\\git.exe"])
def test_native_git_observed_reparse_component_is_refused(component):
    # Model a path component changing to a reparse point at the inspection seam;
    # never alter the installed Git path or the host's ACLs.
    literal=component.replace("'", "''")
    result=_native_probe(f"""
function Get-Item {{
 param($LiteralPath, [switch]$Force)
 if ($LiteralPath -ceq '{literal}') {{ return [pscustomobject]@{{Attributes=[IO.FileAttributes]::ReparsePoint}} }}
 Microsoft.PowerShell.Management\\Get-Item -LiteralPath $LiteralPath -Force
}}
try {{ Assert-WdNativeGitPath -Path '{NATIVE_GIT}'; throw 'reparse accepted' }}
catch {{ if ($_.Exception.Message -notlike '*reparse point*') {{ throw }} }}
'REPARSE_REFUSED'
""")
    assert result.returncode == 0 and result.stdout.strip() == "REPARSE_REFUSED", result.stdout+result.stderr


@pytest.mark.parametrize("key", ["GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_EXEC_PATH", "GIT_DIR"])
def test_native_git_environment_override_refused_before_spawn(tree, key):
    result=_native_probe(_binding_script()+f"""
try {{
 [Environment]::SetEnvironmentVariable('{key}', 'invented-override', 'Process')
 try {{ Invoke-GitCapture -GitContext $binding -Worktree '{tree}' -ArgumentList @('rev-parse','HEAD'); throw 'override accepted' }}
 catch {{ if ($_.Exception.Message -cne 'Mind Git: inherited Git environment override') {{ throw }} }}
}} finally {{ Close-WdPinnedGit -GitContext $binding }}
'OVERRIDE_REFUSED'
""")
    assert result.returncode == 0 and result.stdout.strip() == "OVERRIDE_REFUSED", result.stdout+result.stderr


@pytest.mark.parametrize("route", ["text", "blob", "archive"])
@pytest.mark.parametrize("change", ["pin", "path", "closed_handle", "postcheck"])
def test_native_git_all_routes_refuse_descriptor_change(tree, route, change):
    oid=_git(tree,"rev-parse","HEAD:tools/wd_mind_a.py")
    archive=tree.parent/"refused.zip"
    action={
        "text":f"Invoke-GitCapture -GitContext $binding -Worktree '{tree}' -ArgumentList @('rev-parse','HEAD')",
        "blob":f"Get-WdMindGitBlobSha256 -GitContext $binding -Worktree '{tree}' -BlobOid '{oid}' -ExpectedSize 6",
        "archive":f"Invoke-GitCapture -GitContext $binding -Worktree '{tree}' -ArgumentList @('archive','--format=zip','--output={archive}','HEAD')",
    }[route]
    alteration={
        "pin":"$binding.ExpectedSha256='0'*64",
        "path":"$binding.Path='C:\\Program Files\\Git\\cmd\\git.exe'",
        "closed_handle":"$binding.Handle.Dispose()",
        "postcheck":"""$script:originalAssert=${function:Assert-WdPinnedGit}
$script:checkCount=0
function Assert-WdPinnedGit {
 param($GitContext)
 $script:checkCount++
 if ($script:checkCount -gt 1) { throw 'Mind Git: synthetic post-use path change' }
 & $script:originalAssert -GitContext $GitContext
}""",
    }[change]
    result=_native_probe(_binding_script()+f"""
try {{
 {alteration}
 try {{ {action} | Out-Null; throw 'change accepted' }}
 catch {{ if ($_.Exception.Message -notlike 'Mind Git:*') {{ throw }} }}
}} finally {{ Close-WdPinnedGit -GitContext $binding }}
if ($null -ne $binding.Handle) {{ throw 'handle retained after close' }}
'CHANGE_REFUSED'
""")
    assert result.returncode == 0 and result.stdout.strip() == "CHANGE_REFUSED", result.stdout+result.stderr
    if change != "postcheck":
        assert not archive.exists()


def test_native_git_open_error_closes_held_handle():
    result=_native_probe("""
function Assert-WdPinnedGit {
 param($GitContext)
 $script:observedHandle=$GitContext.Handle
 throw 'synthetic opening failure'
}
try {
"""+_binding_script()+"""
 throw 'failure accepted'
} catch { if ($_.Exception.Message -cne 'synthetic opening failure') { throw } }
if ($script:observedHandle.CanRead) { throw 'opening failure leaked handle' }
'HANDLE_CLOSED'
""")
    assert result.returncode == 0 and result.stdout.strip() == "HANDLE_CLOSED", result.stdout+result.stderr


def test_native_git_argument_quoting_preserves_windows_argv():
    import ctypes
    from ctypes import wintypes
    values=['', 'C:\\invented repo\\', 'C:\\invented 雪\\', 'a"b', '\\"', 'semi; $(literal)']
    encoded=base64.b64encode(json.dumps(values).encode()).decode()
    result=_native_probe(f"""
$values=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}'))|ConvertFrom-Json
$arguments=(@($values|ForEach-Object {{ConvertTo-WdGitArgument -Value $_}}) -join ' ')
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($arguments))
""")
    assert result.returncode == 0, result.stderr
    arguments=base64.b64decode(result.stdout.strip()).decode()
    shell32=ctypes.WinDLL('shell32',use_last_error=True)
    kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
    shell32.CommandLineToArgvW.argtypes=(wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int))
    shell32.CommandLineToArgvW.restype=ctypes.POINTER(wintypes.LPWSTR)
    kernel32.LocalFree.argtypes=(ctypes.c_void_p,)
    count=ctypes.c_int()
    parsed=shell32.CommandLineToArgvW('fixture.exe '+arguments,ctypes.byref(count))
    assert parsed
    try:
        assert [parsed[i] for i in range(count.value)] == ['fixture.exe',*values]
    finally:
        kernel32.LocalFree(parsed)


def test_native_git_text_blob_archive_share_pin_despite_aliases_and_spaced_unicode_path(tree):
    repo=tree.parent/"repo space 雪"
    tree.rename(repo)
    oid=_git(repo,"rev-parse","HEAD:tools/wd_mind_a.py")
    archive=repo.parent/"archive space 雪.zip"
    # Supply a trailing slash deliberately: the historical manually quoted -C
    # argument swallowed the following argument at this boundary.
    root=str(repo)+"\\"
    result=_native_probe(_binding_script()+f"""
try {{
 function git {{ throw 'git function must not execute' }}
 Set-Alias -Name git -Value Write-Warning
 $head=Invoke-GitCapture -GitContext $binding -Worktree '{root}' -ArgumentList @('rev-parse','HEAD')
 if ($head.ExitCode -ne 0 -or $head.Text -cnotmatch '^[0-9a-f]{{40}}$') {{ throw 'text failed' }}
 $hash=Get-WdMindGitBlobSha256 -GitContext $binding -Worktree '{root}' -BlobOid '{oid}' -ExpectedSize 6
 if ($hash -cne '{digest(b'A = 1'+bytes([10]))}') {{ throw 'blob failed' }}
 $result=Invoke-GitCapture -GitContext $binding -Worktree '{root}' -ArgumentList @('archive','--format=zip','--output={archive}','HEAD')
 if ($result.ExitCode -ne 0) {{ throw 'archive failed' }}
}} finally {{ Close-WdPinnedGit -GitContext $binding }}
'ALL_ROUTES_MATCH'
""")
    assert result.returncode == 0 and result.stdout.strip() == "ALL_ROUTES_MATCH", result.stdout+result.stderr
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("tools/wd_mind_a.py") == b"A = 1\n"


def _replace_ref_case(tree):
    original=_git(tree,"rev-parse","HEAD")
    (tree/"tools/unrelated.py").write_bytes(b"REPLACED_OUTSIDE_MIND = True\n")
    replacement=_commit(tree)
    _git(tree,"replace",original,replacement)
    _git(tree,"update-ref","HEAD",original)
    archive=tree.parent/"replace-ref.zip"
    body=_binding_script()+f"""
try {{
 $package=Get-WdMindPackageMembers -GitContext $binding -Worktree '{tree}' -Commit '{original}'
 if ($package.source_commit -cne '{original}') {{ throw 'package changed' }}
 $result=Invoke-GitCapture -GitContext $binding -Worktree '{tree}' -ArgumentList @('-c','core.autocrlf=false','archive','--format=zip','--output={archive}','{original}')
 if ($result.ExitCode -ne 0) {{ throw 'archive failed' }}
}} finally {{ Close-WdPinnedGit -GitContext $binding }}
'ARCHIVE_CREATED'
"""
    return archive,body


def test_native_git_replace_ref_cannot_change_archive_outside_mind_members(tree):
    archive,body=_replace_ref_case(tree)
    result=_native_probe(body)
    assert result.returncode == 0 and result.stdout.strip() == "ARCHIVE_CREATED", result.stdout+result.stderr
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.read("tools/unrelated.py") == b"UNRELATED = True\n"


@pytest.mark.parametrize("mode", ["return", "throw 'synthetic exit'"])
def test_native_git_main_finally_closes_handle_on_early_exit(mode):
    deploy=str(DEPLOY).replace("'", "''")
    mode=mode.replace("'", "''")
    result=_native_probe(f"""
$ast=[Management.Automation.Language.Parser]::ParseFile('{deploy}',[ref]$null,[ref]$null)
$outer=@($ast.EndBlock.Statements|Where-Object {{ $_ -is [Management.Automation.Language.TryStatementAst] }})[-1]
if ($outer.Body.Extent.Text.IndexOf('Open-WdPinnedGit') -gt $outer.Body.Extent.Text.IndexOf('$gitRootProbe =')) {{ throw 'pin opened after root probe' }}
if (-not $outer.Body.Extent.Text.Contains('if ($StageOnly)') -or -not $outer.Body.Extent.Text.Contains('if ($DryRun)')) {{ throw 'early exit outside pin lifetime' }}
$cleanup=$outer.Finally.Extent.Text
"""+_binding_script()+f"""
$script:held=$binding.Handle
$GitContext=$binding
try {{ & ([scriptblock]::Create('try {{ {mode} }} finally '+$cleanup)) }} catch {{ if ($_.Exception.Message -cne 'synthetic exit') {{ throw }} }}
if ($script:held.CanRead) {{ throw 'main exit leaked handle' }}
'MAIN_HANDLE_CLOSED'
""")
    assert result.returncode == 0 and result.stdout.strip() == "MAIN_HANDLE_CLOSED", result.stdout+result.stderr

def _git(repo,*args,input=None):
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    command=[str(NATIVE_GIT),"-c","core.autocrlf=false","-c","core.longpaths=true","-c","core.excludesFile=","-c","user.name=Fixture","-c","user.email=fixture@example.invalid","-c","commit.gpgsign=false","-c",f"core.hooksPath={repo/'empty-hooks'}",*args]
    result=subprocess.run(command,cwd=repo,env=env,input=input,capture_output=True,timeout=30)
    assert result.returncode==0, result.stderr.decode(errors="replace")
    return result.stdout.decode().strip()

@pytest.fixture
def tree(tmp_path):
    repo=tmp_path/"g"
    repo.mkdir()
    _git(repo,"init","-q")
    (repo/"empty-hooks").mkdir()
    (repo/"tools").mkdir()
    (repo/"tools/wd_mind_z.py").write_bytes(b"Z = 1\n")
    (repo/"tools/wd_mind_a.py").write_bytes(b"A = 1\n")
    (repo/"tools/unrelated.py").write_bytes(b"UNRELATED = True\n")
    _git(repo,"add","tools")
    _git(repo,"commit","-qm","synthetic fixture")
    return repo

def _commit(repo):
    _git(repo,"add","-A")
    _git(repo,"commit","-qm","synthetic fixture update")
    return _git(repo,"rev-parse","HEAD")

def _package(tree,*,archive=None,commit=None,pinned=True):
    head=commit or _git(tree,"rev-parse","HEAD")
    deploy=str(DEPLOY).replace("'","''")
    repo=str(tree).replace("'","''")
    command=f"""
$ErrorActionPreference='Stop'
$ast=[Management.Automation.Language.Parser]::ParseFile('{deploy}',[ref]$null,[ref]$null)
foreach($f in $ast.FindAll({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]}},$false)){{
 . ([scriptblock]::Create($f.Extent.Text))
}}
Get-Command Get-WdMindPackageMembers -CommandType Function -ErrorAction Stop|Out-Null
Get-Command Assert-WdMindPackageArchive -CommandType Function -ErrorAction Stop|Out-Null
$binding=$null
try{{
"""
    if pinned:
        # Synthetic test input only, observed from a fixed literal path; this
        # is not production authorization and never derives a pin from PATH.
        native=str(NATIVE_GIT).replace("'", "''")
        command+=f" $binding=Open-WdPinnedGit -GitExecutable '{native}' -ExpectedGitSha256 '{digest(NATIVE_GIT.read_bytes())}'\n"
    command+=f" $package=Get-WdMindPackageMembers -Worktree '{repo}' -Commit '{head}' -GitContext $binding\n"
    if archive is not None:
        root=str(archive).replace("'","''")
        command+=f"\n Assert-WdMindPackageArchive -ArchiveRoot '{root}' -Package $package|Out-Null\n"
    command+="$package|ConvertTo-Json -Depth 12 -Compress\n}catch{ $_.Exception.Message;exit 2 }finally{ Close-WdPinnedGit -GitContext $binding }\n"
    env={k:v for k,v in os.environ.items() if not k.startswith("GIT_")}
    env["PSModulePath"]=str(PS.parent/"Modules")
    result=subprocess.run([str(PS),"-NoProfile","-NonInteractive","-Command",command],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    (tree.parent/"selection-observed.json").write_text(json.dumps({"synthetic":True,"head":head,"returncode":result.returncode,"stdout":result.stdout,"stderr":result.stderr,"command":[str(PS),"-NoProfile","-NonInteractive","-Command",command]},indent=2),encoding="utf-8")
    return result

def _refused(result):
    assert result.returncode==2 and "Mind package:" in result.stdout, result.stdout+result.stderr

def _archive(tree):
    archive=tree.parent/"tree.zip"
    _git(tree,"archive","--format=zip",f"--output={archive}","HEAD")
    root=tree.parent/"archived"
    root.mkdir()
    with zipfile.ZipFile(archive) as z:
        z.extractall(root)
    return root

def test_frozen_tree_selection_is_exact_ordered_and_not_an_entrypoint(tree):
    result=_package(tree)
    assert result.returncode==0, result.stdout+result.stderr
    package=json.loads(result.stdout)
    assert package["schema"]=="wd.mind-code-package.v1"
    assert package["source_commit"]==_git(tree,"rev-parse","HEAD")
    assert [row["relative_path"] for row in package["files"]]==["tools/wd_mind_a.py","tools/wd_mind_z.py"]
    for row in package["files"]:
        data=(tree/row["relative_path"]).read_bytes()
        assert row["sha256"]==digest(data)
        assert row["bytes"]==len(data)
        assert row["blob_oid"]==_git(tree,"rev-parse","HEAD:"+row["relative_path"])

@pytest.mark.parametrize("change",["dirty","hidden_dirty","missing","untracked"])
def test_frozen_tree_refuses_worktree_disagreement(tree,change):
    target=tree/"tools/wd_mind_a.py"
    if change=="hidden_dirty":
        _git(tree,"update-index","--assume-unchanged","tools/wd_mind_a.py")
    if change in ("dirty","hidden_dirty"):
        target.write_text("CHANGED = True\n",encoding="utf-8")
    elif change=="missing":
        target.unlink()
    else:
        (tree/"tools/wd_mind_new.py").write_text("NEW = True\n",encoding="utf-8")
    _refused(_package(tree))

@pytest.mark.parametrize("name,mode",[
    ("tools/wd_mind_Upper.py","100644"),
    ("tools/wd_mind_alias.py.","100644"),
    ("Tools/wd_mind_root.py","100644"),
    ("tools/wd_mind_link.py","120000"),
    ("tools/wd_mind_submodule.py","160000"),
])
def test_frozen_tree_refuses_aliases_and_non_blob_members(tree,name,mode):
    oid=_git(tree,"rev-parse","HEAD") if mode=="160000" else _git(tree,"hash-object","-w","--stdin",input=b"fixture\n")
    if name.endswith("."):
        # Windows update-index rejects this spelling itself. Construct the
        # immutable foreign tree directly; never materialize an aliased file.
        subtree=_git(tree,"mktree",input=f"{mode} blob {oid}\t{name.split('/')[-1]}\n".encode())
        root=_git(tree,"mktree",input=f"040000 tree {subtree}\ttools\n".encode())
        commit=_git(tree,"commit-tree",root,"-p","HEAD","-m","synthetic foreign alias")
        _git(tree,"update-ref","HEAD",commit)
    else:
        _git(tree,"update-index","--add","--cacheinfo",f"{mode},{oid},{name}")
        _git(tree,"commit","-qm","synthetic unsafe tree")
    result=_package(tree)
    _refused(result)
    assert ("case alias of tools root" if name.startswith("Tools/") else "unsafe, aliased or non-blob Mind member") in result.stdout

def test_frozen_tree_refuses_member_count_over_budget(tree):
    for n in range(255):
        (tree/f"tools/wd_mind_new_{n}.py").write_text("X=1\n",encoding="utf-8")
    _commit(tree)
    _refused(_package(tree))

def test_frozen_tree_refuses_large_blob_before_reading(tree):
    (tree/"tools/wd_mind_big.py").write_bytes(b"x"*(8*1024*1024+1))
    _commit(tree)
    _refused(_package(tree))

def test_frozen_tree_refuses_head_change(tree):
    old=_git(tree,"rev-parse","HEAD")
    (tree/"tools/wd_mind_new.py").write_text("X=1\n",encoding="utf-8")
    _commit(tree)
    _refused(_package(tree,commit=old))

def test_frozen_archive_accepts_exact_blob_bytes(tree):
    result=_package(tree,archive=_archive(tree))
    assert result.returncode==0, result.stdout+result.stderr

@pytest.mark.parametrize("change",["tampered","missing","extra","export_subst","export_ignore"])
def test_frozen_archive_refuses_transformation_or_member_disagreement(tree,change):
    if change=="export_subst":
        (tree/".gitattributes").write_text("tools/wd_mind_a.py export-subst\n",encoding="utf-8")
        (tree/"tools/wd_mind_a.py").write_text("VERSION = '$Format:%H$'\n",encoding="utf-8")
        _commit(tree)
    elif change=="export_ignore":
        (tree/".gitattributes").write_text("tools/wd_mind_a.py export-ignore\n",encoding="utf-8")
        _commit(tree)
    root=_archive(tree)
    target=root/"tools/wd_mind_a.py"
    if change=="tampered":
        target.write_text("CHANGED = True\n",encoding="utf-8")
    elif change=="missing":
        target.unlink()
    elif change=="extra":
        (target.parent/"wd_mind_new.py").write_text("NEW = True\n",encoding="utf-8")
    _refused(_package(tree,archive=root))

@pytest.mark.parametrize("key",["tools","root"])
def test_frozen_archive_refuses_junctions_without_following(tree,key):
    root=_archive(tree)
    target=root/"tools" if key=="tools" else root
    hold=tree.parent/"held"
    target.rename(hold)
    result=subprocess.run(["cmd.exe","/d","/c","mklink","/J",str(target),str(hold)],capture_output=True,text=True,timeout=10)
    assert result.returncode==0, result.stdout+result.stderr
    try:
        _refused(_package(tree,archive=root))
    finally:
        target.rmdir()  # Remove only this owned junction, never recurse into its target.
        assert (hold/"wd_mind_a.py").exists() if key=="tools" else (hold/"tools/wd_mind_a.py").exists()

def test_package_integration_uses_frozen_members_without_changing_entrypoints():
    text=DEPLOY.read_text()
    assert "$mindPackage = Get-WdMindPackageMembers" in text
    assert text.index("$mindPackage = Get-WdMindPackageMembers")<text.index("$statusProbe =")
    assert text.index("Assert-WdMindPackageArchive -ArchiveRoot")<text.index("$sourceHashes = Get-RelativeFileHashMap")
    assert "mind_modules = $mindPackage" in text
    assert ".mind_modules.files" not in (REBOOT/"BridgeCodeContext.ps1").read_text().split("$entrypoints =")[1].split("$manual =")[0]

def _metadata(bundle):
    manifest=json.loads((bundle/"deployment-manifest.json").read_bytes())
    data=(bundle/"tools-bootstrap"/DEPENDENCY).read_bytes()
    row={"relative_path":DEPENDENCY,"blob_oid":hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest(),"sha256":digest(data),"bytes":len(data)}
    package={"schema":"wd.mind-code-package.v1","source_commit":manifest["source_commit"],"files":[row]}
    manifest["bridge_code_package"]={"mind_modules":package}
    return manifest,package

def _save_manifest(bundle,manifest):
    (bundle/"deployment-manifest.json").write_text(json.dumps(manifest),encoding="utf-8")

def test_complete_mind_metadata_accepts_exact_members(bundle):
    manifest,package=_metadata(bundle)
    _save_manifest(bundle,manifest)
    result=invoke(bundle)
    assert result.returncode==0 and "SYNTHETIC_CALLER_RAN" in result.stdout, result.stdout+result.stderr

@pytest.mark.parametrize("change",["schema","commit","hash","size","blob","duplicate","missing_member","extra_manifest"])
def test_complete_mind_metadata_refuses_inconsistent_members(bundle,change):
    manifest,package=_metadata(bundle)
    row=package["files"][0]
    if change=="schema":
        package["schema"]="unsupported"
    elif change=="commit":
        package["source_commit"]="a"*40
    elif change=="hash":
        row["sha256"]="B"*64
    elif change=="size":
        row["bytes"]+=1
    elif change=="blob":
        row["blob_oid"]="b"*40
    elif change=="duplicate":
        package["files"].append(dict(row))
    elif change=="missing_member":
        package["files"]=[]
    else:
        extra="tools/wd_mind_extra.py"
        data=b"EXTRA=True\n"
        (bundle/"tools-bootstrap"/extra).write_bytes(data)
        manifest["files"]["tools-bootstrap/"+extra]=digest(data)
    _save_manifest(bundle,manifest)
    result=invoke(bundle)
    assert result.returncode!=0 and "SYNTHETIC_CALLER_RAN" not in result.stdout, result.stdout+result.stderr

@pytest.mark.parametrize("tool",["tools/bridge_mind_workspace.py","tools/wd_bridge_mind_delta_push.py","sandbox/mind/join_turn.py","tools/explicit_read.py","sandbox/mind/hyphen-reader.py"])
def test_existing_mind_paths_all_verify_complete_package(bundle,tool):
    definition=json.loads((bundle/"bridge-code-files.json").read_bytes())
    path=bundle/"tools-bootstrap"/tool
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b"print('SYNTHETIC_CALLER_RAN')\n")
    definition["python_files"].append(tool)
    definition["python_entrypoints"]["mind_fixture"]=tool
    (bundle/"bridge-code-files.json").write_text(json.dumps(definition),encoding="utf-8")
    manifest=json.loads((bundle/"deployment-manifest.json").read_bytes())
    manifest["files"]["tools-bootstrap/"+tool]=digest(path.read_bytes())
    manifest["files"]["bridge-code-files.json"]=digest((bundle/"bridge-code-files.json").read_bytes())
    _save_manifest(bundle,manifest)
    (bundle/"tools-bootstrap"/DEPENDENCY).write_text("CHANGED=True\n",encoding="utf-8")
    result=invoke(bundle,tool=tool)
    assert result.returncode!=0 and "SYNTHETIC_CALLER_RAN" not in result.stdout, result.stdout+result.stderr
