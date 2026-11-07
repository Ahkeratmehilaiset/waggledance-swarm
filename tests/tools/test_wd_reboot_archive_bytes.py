"""Exercise only the deployer's archive seam against isolated Git fixtures.

Never invoke deployment, staging, providers or the installed bundle. Global Git
configuration is a test-owned file; task and machine Git configuration stay intact.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
SHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(os.name != "nt" or not SHELL, reason="Windows PowerShell required")
TEXT = "ops/windows/reboot/text with spaces.ps1"
BINARY = "ops/windows/reboot/payload.bin"
FILES = {TEXT: b"first\nsecond\n", BINARY: b"\x00\xff\r\n\x80\n",
         ".agent-bridge/bin/helper.ps1": b"helper\n",
         "tools/CheckedPowerShellResult.ps1": b"checked\n",
         "configs/bridge_identity_registry.json": b"{}\n"}


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def fixture(tmp_path, mode=None, attributes=None):
    repo = tmp_path / "git"
    repo.mkdir()
    env = os.environ.copy()
    config = tmp_path / "global.gitconfig"
    config.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1")
    def git(*args, data=None):
        return subprocess.check_output(["git", "-C", str(repo), *args], input=data, env=env).decode().strip()
    git("init", "-q")
    for name, data in FILES.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        oid = git("hash-object", "-w", "--stdin", data=data)
        git("update-index", "--add", "--cacheinfo", "100644", oid, name)
    if attributes:
        oid = git("hash-object", "-w", "--stdin", data=attributes.encode())
        git("update-index", "--add", "--cacheinfo", "100644", oid, ".gitattributes")
    tree = git("write-tree")
    head = git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
               "commit-tree", tree, "-m", "synthetic archive fixture")
    if mode:
        oid = head if mode == "160000" else git("hash-object", "-w", "--stdin", data=b"outside")
        git("update-index", "--add", "--cacheinfo", mode, oid, "ops/windows/reboot/special")
        head = git("-c", "user.name=Synthetic", "-c", "user.email=synthetic@example.invalid",
                   "commit-tree", git("write-tree"), "-m", "special mode")
    material = tmp_path / "material"
    material.mkdir()
    return repo, head, material, env, config.read_bytes()


def invoke(tmp_path, *, mode=None, attributes=None, tamper=None):
    repo, head, material, env, config_before = fixture(tmp_path, mode, attributes)
    script = rf'''
$ErrorActionPreference='Stop'
if ($PSVersionTable.PSEdition -eq 'Desktop') {{ $env:PSModulePath=Join-Path $PSHOME 'Modules' }}
Import-Module Microsoft.PowerShell.Utility
Import-Module Microsoft.PowerShell.Management
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({quote(DEPLOY)},[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'deployer parse failed'}}
foreach($name in @('Invoke-GitCapture','Resolve-FullPath','Assert-ChildPath','Assert-WdPathWithoutReparse','Assert-WdArchiveMatchesCommit')){{
 $node=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}},$true)
 if($null -ne $node){{. ([scriptblock]::Create($node.Extent.Text))}}
}}
$source=[IO.File]::ReadAllText({quote(DEPLOY)})
$start=$source.IndexOf('$archivePath = Join-Path $materializationRoot',[StringComparison]::Ordinal)
$end=$source.IndexOf('$materializedRebootRoot = Join-Path $archiveRoot',[StringComparison]::Ordinal)
if($start -lt 0 -or $end -le $start){{throw 'archive seam not found'}}
$seam=$source.Substring($start,$end-$start)
$gitRoot={quote(repo)};$head={quote(head)};$materializationRoot={quote(material)};$bridgeCodeSourceFiles=@()
'''
    if tamper:
        injection = {"text": f"[IO.File]::WriteAllBytes((Join-Path $archiveRoot {quote(TEXT)}),[byte[]](1,2,3)); ",
                     "binary": f"[IO.File]::WriteAllBytes((Join-Path $archiveRoot {quote(BINARY)}),[byte[]](9)); ",
                     "extra": "[IO.File]::WriteAllText((Join-Path $archiveRoot 'extra.txt'),'extra'); "}[tamper]
        script += "$seam=$seam.Replace('Assert-WdArchiveMatchesCommit -Worktree'," + quote(injection + "Assert-WdArchiveMatchesCommit -Worktree") + ")\n"
    script += ". ([scriptblock]::Create($seam))\nWrite-Output 'ARCHIVE_VERIFIED'\n"
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16le")).decode()],
                            env=env, capture_output=True, text=True, timeout=60)
    assert Path(env["GIT_CONFIG_GLOBAL"]).read_bytes() == config_before
    return result, material / "tree"


def test_archive_preserves_lf_and_binary_with_global_autocrlf(tmp_path):
    result, tree = invoke(tmp_path)
    assert result.returncode == 0, result.stderr
    for name, expected in FILES.items():
        assert (tree / name).read_bytes() == expected, name


@pytest.mark.parametrize("tamper", ["text", "binary", "extra"])
def test_archive_rejects_changed_or_uncommitted_bytes(tmp_path, tamper):
    result, _ = invoke(tmp_path, tamper=tamper)
    assert result.returncode != 0 and "archive_commit_" in result.stderr


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_archive_rejects_symlink_and_submodule(tmp_path, mode):
    result, _ = invoke(tmp_path, mode=mode)
    assert result.returncode != 0 and "archive_commit_" in result.stderr


@pytest.mark.parametrize("attributes", [
    '"ops/windows/reboot/text with spaces.ps1" export-ignore\n',
    '"ops/windows/reboot/text with spaces.ps1" text eol=crlf\n',
])
def test_archive_rejects_attribute_omission_or_conversion(tmp_path, attributes):
    result, _ = invoke(tmp_path, attributes=attributes)
    assert result.returncode != 0 and "archive_commit_" in result.stderr


def test_archive_validation_precedes_source_loading_and_staging():
    text = DEPLOY.read_text(encoding="utf-8-sig")
    guard = text.index("Assert-WdArchiveMatchesCommit -Worktree")
    assert guard < text.index("$sourceHashes = Get-RelativeFileHashMap")
    assert guard < text.index(". (Join-Path $materializedRebootRoot 'BridgeCodeContext.ps1')")
