"""Synthetic descriptor admission/staging; never run the live installer or assets."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "ops/windows/reboot/Deploy-WdRebootBundle.ps1"
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
COMMIT = "d" * 40
WORKER = "a" * 64
RUNTIME = "wd-mind-runtime-manifest.json"
IDENTITY = "wd-mind-interpreter-identity.json"
ENCODER = "wd-mind-e5-encoder-binding.json"
LOCATION = "wd-mind-runtime-location.json"
MESSAGE_POLICY = "wd-mind-message-policy.json"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer contract")


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _documents():
    weights = hashlib.sha256(b"synthetic weights only").hexdigest()
    policy = {"prefix": "passage: ", "truncate_chars": 2000, "normalize": True,
              "dtype": "float32", "dim": 384}
    return {
        RUNTIME: {"schema": "wd.q4-runtime-manifest.v1", "generation": "c" * 40,
                  "revision": "b" * 40, "files": [
                      {"path": "model/model.safetensors", "size": 1, "sha256": weights},
                      {"path": "site/synthetic.py", "size": 1, "sha256": "e" * 64}]},
        IDENTITY: {"schema": "wd.q4-interpreter-identity.v1", "files": [
            {"role": role, "path": name, "size": 1, "sha256": "f" * 64}
            for role, name in [("interpreter", "python.exe"), ("stdlib", "python.zip"), ("dll", "python.dll")]]},
        ENCODER: {"schema": "wd.mind-memory-encoder-binding.v1",
                  "encoder": {"model": "intfloat/multilingual-e5-small", "revision": "b" * 40,
                              "weights_sha256": weights}, "code_sha256": WORKER,
                  "passage_policy": policy, "query_policy": dict(policy, prefix="query: ")},
    }


def _fixture(tmp_path, documents=None, raw=None, outer_change=None, version=1):
    documents = documents or _documents()
    pins = {}
    for name, document in documents.items():
        data = (raw or {}).get(name, json.dumps(document, ensure_ascii=False).encode("utf-8"))
        (tmp_path / name).write_bytes(data)
        pins[name] = {"sha256": hashlib.sha256(data).hexdigest(), "schema": document["schema"]}
    outer = {"schema": f"wd.mind-runtime-descriptor-set.v{version}", "source_commit": COMMIT, "files": pins}
    if outer_change:
        outer_change(outer)
    path = tmp_path / "descriptor-set.json"
    path.write_bytes(json.dumps(outer).encode("utf-8"))
    return path


def _probe(tmp_path, path, *, mutate="", omit=False, redirect=False, main=False, redirect_root=False, enable_guardian=False, enable_fast=False):
    # Load only the actual helper definitions and the actual StageOnly output
    # block. Git, wheel staging, install, processes and runtime assets stay out.
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
        f". {_quote(ROOT / '.agent-bridge/bin/BridgeLogReader.ps1')}\n"
        "$tokens=$null;$errors=$null\n"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({_quote(DEPLOY)},[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'PARSE_FAILED'}\n"
        "$defs=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true))\n"
        "$required=@('Resolve-FullPath','Assert-WdPathWithoutReparse','Read-WdMindRuntimeDescriptorSet','Add-WdMindRuntimeDescriptorFiles')\n"
        "foreach($name in $required){if(@($defs|Where-Object {$_.Name -ceq $name}).Count -ne 1){throw ('HELPER_MISSING:'+ $name)}}\n"
        "foreach($def in $defs){if($def.Name -in $required -or $def.Name -match '^(Read|Assert|ConvertFrom)-WdMindRuntime'){\n"
        " $body=$def.Extent.Text\n" +
        (" if($def.Name -ceq 'Read-WdMindRuntimeFileSnapshot'){"
         "$needle='[IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)';"
         "if(-not $body.Contains($needle)){throw 'INJECTION_NOT_EXACT'};"
         "$body=$body.Replace($needle," + _quote("[IO.File]::Open(" + _quote(tmp_path / "other.json") +
         ", [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)") + ")}\n" if redirect else "") +
        (" if($def.Name -ceq 'Read-WdMindRuntimeDirectorySnapshot'){"
         "$needle='[WaggleDance.WdMindLocationV1]::Snapshot($full)';"
         "if(-not $body.Contains($needle)){throw 'DIRECTORY_INJECTION_NOT_EXACT'};"
         "$body=$body.Replace($needle," + _quote("[WaggleDance.WdMindLocationV1]::Snapshot(" +
         _quote(tmp_path / "synthetic-interpreter") + ")") + ")}\n" if redirect_root else "") +
        " . ([scriptblock]::Create($body))\n}}\n"
        "$hashes=[ordered]@{};$paths=[ordered]@{}\n"
        f"$stage={_quote(tmp_path / ('mind-runtime-descriptors' if main else 'staged-descriptors'))}\n"
        "try {\n"
        + (f" $head='{COMMIT}';$MindRuntimeDescriptorSetPath={_quote('' if omit else path)}\n"
           f" $toolsBootstrapSource={_quote(ROOT / '.agent-bridge/bin')};$materializationRoot={_quote(tmp_path)}\n"
           f" $hashes['tools-bootstrap/tools/wd_mind_e5_worker.py']='{WORKER.upper()}'\n"
           " $sourceHashes=$hashes;$sourcePaths=$paths;$mindRuntimeDescriptors=$null\n"
           " $entry=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith(\"if (`$MindRuntimeDescriptorSetPath -cne '')\")},$true))\n"
           " if($entry.Count -ne 1){throw 'MAIN_READ_MISSING'};. ([scriptblock]::Create($entry[0].Extent.Text))\n"
           " $set=$mindRuntimeDescriptors\n" if main else
           f" $set=Read-WdMindRuntimeDescriptorSet -Path {_quote('' if omit else path)} -SourceCommit '{COMMIT}' -WorkerSha256 '{WORKER}'\n") +
        mutate + "\n"
        + (" $entry=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Add-WdMindRuntimeDescriptorFiles'},$true))\n"
           " if($entry.Count -ne 1){throw 'MAIN_ADD_MISSING'};. ([scriptblock]::Create($entry[0].Extent.Text))\n" if main else
           " Add-WdMindRuntimeDescriptorFiles -Set $set -SourceHashes $hashes -SourcePaths $paths -StagingRoot $stage\n") +
        " $head='" + COMMIT + "';$targetRoot=$stage\n"
        " $branch='synthetic';$upstream='synthetic';$sourceHashes=$hashes\n"
        " $bridgeCodeDefinition=@{schema='synthetic'};$bridgeCodePackage=@{Hash=('A'*64)}\n"
        " $bridgePython=@{Path='inert';Sha256=('B'*64)};$bridgePythonStage=@{Wheels=@{};Site=@{}}\n"
        " $entry=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$manifestObject'},$true))\n"
        " if($entry.Count -ne 1){throw 'MANIFEST_ASSIGNMENT_MISSING'};. ([scriptblock]::Create($entry[0].Extent.Text));$manifest=$manifestObject\n" +
        " $manifestBytes=[Text.Encoding]::UTF8.GetBytes(($manifest|ConvertTo-Json -Depth 8))\n"
        " $sha=[Security.Cryptography.SHA256]::Create();try{$installedManifestHash=[BitConverter]::ToString($sha.ComputeHash($manifestBytes)).Replace('-','')}finally{$sha.Dispose()}\n"
        " $out=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.IfStatementAst] -and $n.Extent.Text.StartsWith('if ($StageOnly)') -and $n.Extent.Text.Contains('STAGE ONLY:')},$true))\n"
        " if($out.Count -ne 1){throw 'STAGE_ONLY_BLOCK_MISSING'}\n"
        " $StageOnly=$true;& ([scriptblock]::Create($out[0].Extent.Text))\n"
        " [ordered]@{accepted=$true;hashes=$hashes;paths=$paths;manifest_sha256=$installedManifestHash;manifest=$manifest}|ConvertTo-Json -Compress -Depth 8\n"
        "} catch { [ordered]@{accepted=$false;reason=$_.Exception.Message;stage_exists=(Test-Path -LiteralPath $stage)}|ConvertTo-Json -Compress;exit 23 }\n",
        encoding="utf-8")
    run = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                         capture_output=True, text=True, timeout=45)
    assert "HELPER_MISSING" not in run.stderr, run.stderr
    assert run.stdout.strip(), run.stderr
    return run, json.loads(run.stdout.strip().splitlines()[-1])


def test_runtime_descriptor_stage_only_exact_bytes_and_final_pair(tmp_path):
    path = _fixture(tmp_path)
    run, result = _probe(tmp_path, path)
    assert run.returncode == 0, result
    assert result["accepted"] and set(result["hashes"]) == {RUNTIME, IDENTITY, ENCODER}
    for name, digest in result["hashes"].items():
        assert Path(result["paths"][name]).read_bytes() == (tmp_path / name).read_bytes()
        assert digest == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest().upper()
    assert f"-ExpectedFinalCommit {COMMIT} -ExpectedFinalManifestHash {result['manifest_sha256']}" in run.stdout
    assert "operator input, not a signature" in run.stdout


def test_runtime_descriptor_default_off_creates_nothing(tmp_path):
    run, result = _probe(tmp_path, "", omit=True)
    assert run.returncode == 0 and result["hashes"] == {}
    assert not (tmp_path / "staged-descriptors").exists()


@pytest.mark.parametrize('fault', ['none', 'off', 'missing_ref', 'missing_path', 'bad_ref', 'bom', 'invalid_json', 'changed', 'collision'])
def test_operator_policy_descriptor_binds_hash_without_staging_private_bytes(tmp_path, fault):
    policy = tmp_path / 'synthetic-policy.json'
    private_bytes = b'{"schema":"synthetic-private-policy","identifiers":["synthetic-secret-marker"]}'
    if fault == 'bom': private_bytes = b'\xef\xbb\xbf' + private_bytes
    if fault == 'invalid_json': private_bytes = b'{invalid'
    policy.write_bytes(private_bytes)
    stage = tmp_path / 'staged'
    stage.mkdir()
    script = tmp_path / 'policy-probe.ps1'
    script.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
        f". {_quote(ROOT / '.agent-bridge/bin/BridgeLogReader.ps1')}\n"
        "$tokens=$null;$errors=$null\n"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({_quote(DEPLOY)},[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'PARSE_FAILED'}\n"
        "$names=@('Resolve-FullPath','Assert-WdPathWithoutReparse','Read-WdMindRuntimeFileSnapshot',"
        "'New-WdMindPolicyApprovalDescriptor','Assert-WdMindPolicyApprovalInput','Add-WdMindPolicyApprovalDescriptor')\n"
        "foreach($name in $names){$f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true));"
        "if($f.Count -ne 1){throw 'HELPER_MISSING'};. ([scriptblock]::Create($f[0].Extent.Text))}\n"
        "$hashes=[ordered]@{};$paths=[ordered]@{}\n"
        + ("$hashes['wd-mind-identifier-policy-approval.json']='occupied'\n" if fault == 'collision' else '')
        + "try {\n"
        + f" $approval=New-WdMindPolicyApprovalDescriptor -PolicyPath {_quote('' if fault in ('off','missing_path') else policy)} "
        + f"-ApprovalReferenceSha256 {_quote('' if fault in ('off','missing_ref') else ('z'*64 if fault == 'bad_ref' else 'a'*64))}\n"
        + (f" [IO.File]::WriteAllText({_quote(policy)}, '{{}}')\n" if fault == 'changed' else '')
        + f" Add-WdMindPolicyApprovalDescriptor -Approval $approval -SourceHashes $hashes -SourcePaths $paths -StagingRoot {_quote(stage)}\n"
        + f" $head='{COMMIT}';$targetRoot={_quote(stage)};$branch='synthetic';$upstream='synthetic';$sourceHashes=$hashes\n"
        + " $bridgeCodeDefinition=@{schema='synthetic'};$bridgeCodePackage=@{Hash=('A'*64)}\n"
        + " $bridgePython=@{Path='inert';Sha256=('B'*64)};$bridgePythonStage=@{Wheels=@{};Site=@{}}\n"
        + " $entry=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$manifestObject'},$true))\n"
        + " if($entry.Count -ne 1){throw 'MANIFEST_ASSIGNMENT_MISSING'};. ([scriptblock]::Create($entry[0].Extent.Text))\n"
        + " [ordered]@{accepted=$true;hashes=$hashes;paths=$paths;manifest=$manifestObject}|ConvertTo-Json -Compress -Depth 8\n"
        + "} catch { [ordered]@{accepted=$false}|ConvertTo-Json -Compress;exit 23 }\n", encoding='utf-8')
    run = subprocess.run([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                         capture_output=True, text=True, timeout=45)
    assert 'synthetic-secret-marker' not in run.stdout + run.stderr
    result = json.loads(run.stdout.strip().splitlines()[-1])
    if fault == 'none':
        assert run.returncode == 0 and result['accepted']
        files = list(stage.iterdir())
        assert len(files) == 1 and private_bytes not in files[0].read_bytes()
        descriptor = json.loads(files[0].read_bytes())
        assert descriptor == {'schema':'wd.mind-identifier-policy-approval.v1',
            'identifier_policy_sha256':hashlib.sha256(private_bytes).hexdigest(), 'approval_reference_sha256':'a'*64}
        assert result['hashes'][files[0].name].lower() == hashlib.sha256(files[0].read_bytes()).hexdigest()
        assert result['manifest']['files'][files[0].name] == result['hashes'][files[0].name]
        assert 'synthetic-secret-marker' not in json.dumps(result['manifest'])
    elif fault == 'off':
        assert run.returncode == 0 and result['accepted'] and not list(stage.iterdir())
    else:
        assert run.returncode != 0 and not result['accepted'] and not list(stage.iterdir())


def _startup_config_probe(tmp_path, *, enabled=True, policy_bytes=b'{"synthetic_only":true}',
                          policy_path_kind='normal', missing_approval=False, mutate=False,
                          collide=False, stage_name='startup-staged', approval_reference='a' * 64,
                          expected_policy_hash=None):
    policy = tmp_path / 'synthetic-startup-policy.json'
    policy.write_bytes(policy_bytes)
    if expected_policy_hash is None:
        expected_policy_hash = hashlib.sha256(policy_bytes).hexdigest() if enabled else ''
    (tmp_path / 'alias').mkdir(exist_ok=True)
    policy_argument = {'normal': str(policy), 'alias': str(tmp_path / 'alias' / '..' / policy.name),
                       'relative': policy.name, 'stream': str(policy) + ':synthetic'}[policy_path_kind]
    stage = tmp_path / stage_name
    stage.mkdir()
    script = tmp_path / (stage_name + '-probe.ps1')
    script.write_text(
        "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
        f". {_quote(ROOT / '.agent-bridge/bin/BridgeLogReader.ps1')}\n"
        "$tokens=$null;$errors=$null\n"
        f"$ast=[Management.Automation.Language.Parser]::ParseFile({_quote(DEPLOY)},[ref]$tokens,[ref]$errors)\n"
        "if($errors.Count){throw 'PARSE_FAILED'}\n"
        "$names=@('Resolve-FullPath','Assert-WdPathWithoutReparse','Read-WdMindRuntimeFileSnapshot',"
        "'New-WdMindPolicyApprovalDescriptor','Assert-WdMindPolicyApprovalInput','Add-WdMindStartupConfigDescriptor')\n"
        "foreach($name in $names){$f=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name},$true));"
        "if($f.Count -ne 1){throw 'HELPER_MISSING'};. ([scriptblock]::Create($f[0].Extent.Text))}\n"
        "$hashes=[ordered]@{};$paths=[ordered]@{}\n"
        + ("$hashes['wd-mind-startup-config.json']='occupied'\n" if collide else '')
        + "try {\n"
        + (" $approval=$null\n" if missing_approval else
           f" $approval=New-WdMindPolicyApprovalDescriptor -PolicyPath {_quote(policy_argument)} -ApprovalReferenceSha256 {_quote(approval_reference)}\n")
        + (f" [IO.File]::WriteAllText({_quote(policy)}, '{{}}')\n" if mutate else '')
        + f" Add-WdMindStartupConfigDescriptor -Enabled:${str(enabled).lower()} -Approval $approval -ExpectedPolicyHash {_quote(expected_policy_hash)} -SourceHashes $hashes -SourcePaths $paths -StagingRoot {_quote(stage)}\n"
        + f" $head='{COMMIT}';$targetRoot={_quote(stage)};$branch='synthetic';$upstream='synthetic';$sourceHashes=$hashes\n"
        + " $bridgeCodeDefinition=@{schema='synthetic'};$bridgeCodePackage=@{Hash=('A'*64)}\n"
        + " $bridgePython=@{Path='inert';Sha256=('B'*64)};$bridgePythonStage=@{Wheels=@{};Site=@{}}\n"
        + " $entry=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -ceq '$manifestObject'},$true))\n"
        + " if($entry.Count -ne 1){throw 'MANIFEST_ASSIGNMENT_MISSING'};. ([scriptblock]::Create($entry[0].Extent.Text))\n"
        + " [ordered]@{accepted=$true;hashes=$hashes;manifest=$manifestObject}|ConvertTo-Json -Compress -Depth 8\n"
        + "} catch { [ordered]@{accepted=$false}|ConvertTo-Json -Compress;exit 23 }\n", encoding='utf-8')
    # The installer helpers run in an isolated child with no inherited bridge or
    # provider credentials. The synthetic policy bytes never appear on stdout.
    allowed = ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PATH', 'PATHEXT', 'TEMP', 'TMP',
               'USERPROFILE', 'LOCALAPPDATA', 'APPDATA')
    env = {name: os.environ[name] for name in allowed if name in os.environ}
    process = subprocess.Popen([str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy',
                                'Bypass', '-File', str(script)], stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    stdout, stderr = process.communicate(timeout=45)
    assert b'synthetic_only' not in stdout + stderr
    result = json.loads(stdout.decode('utf-8-sig').strip().splitlines()[-1])
    return process.returncode, result, stage, policy


def test_startup_configuration_is_default_off(tmp_path):
    code, result, stage, _ = _startup_config_probe(tmp_path, enabled=False)
    assert code == 0 and result['accepted'] and result['hashes'] == {}
    assert not list(stage.iterdir())
    assert 'wd-mind-startup-config.json' not in result['manifest']['files']


def test_startup_configuration_binds_only_path_and_approved_hashes(tmp_path):
    secret = b'{"synthetic_only":true,"marker":"synthetic-private-marker"}'
    code, result, stage, policy = _startup_config_probe(tmp_path, policy_bytes=secret)
    assert code == 0 and result['accepted']
    config_path = stage / 'wd-mind-startup-config.json'
    encoded = config_path.read_bytes()
    assert secret not in encoded and b'synthetic-private-marker' not in encoded
    assert json.loads(encoded) == {'schema': 'wd.mind-startup-config.v1', 'enabled': True, 'authority': 'none',
        'identifier_policy_path': str(policy), 'identifier_policy_sha256': hashlib.sha256(secret).hexdigest(),
        'approval_reference_sha256': 'a' * 64}
    assert result['hashes'][config_path.name] == hashlib.sha256(encoded).hexdigest().upper()
    assert result['manifest']['files'][config_path.name] == result['hashes'][config_path.name]
    assert 'synthetic-private-marker' not in json.dumps(result['manifest'])
    assert sorted(path.name for path in stage.iterdir()) == [config_path.name]


@pytest.mark.parametrize('fault', ['alias', 'relative', 'stream', 'missing_approval', 'mutate', 'collision',
                                   'missing_pin', 'wrong_pin', 'pin_without_enable'])
def test_startup_configuration_refuses_before_staging(tmp_path, fault):
    code, result, stage, _ = _startup_config_probe(tmp_path,
        enabled=fault != 'pin_without_enable',
        policy_path_kind=fault if fault in ('alias', 'relative', 'stream') else 'normal',
        missing_approval=fault == 'missing_approval', mutate=fault == 'mutate', collide=fault == 'collision',
        expected_policy_hash={'missing_pin': '', 'wrong_pin': '0' * 64,
                              'pin_without_enable': 'a' * 64}.get(fault))
    assert code != 0 and not result['accepted']
    assert not list(stage.iterdir())


def test_startup_configuration_hash_is_deterministic_and_tracks_policy(tmp_path):
    first = _startup_config_probe(tmp_path, stage_name='first')
    second = _startup_config_probe(tmp_path, stage_name='second')
    changed = _startup_config_probe(tmp_path, stage_name='changed', policy_bytes=b'{"synthetic_only":false}')
    ref_changed = _startup_config_probe(tmp_path, stage_name='ref-changed', approval_reference='b' * 64)
    assert first[0] == second[0] == changed[0] == ref_changed[0] == 0
    name = 'wd-mind-startup-config.json'
    first_bytes = (first[2] / name).read_bytes()
    assert first_bytes == (second[2] / name).read_bytes()
    assert first[1]['hashes'][name] == second[1]['hashes'][name]
    assert first[1]['hashes'][name] != changed[1]['hashes'][name]
    assert first[1]['hashes'][name] != ref_changed[1]['hashes'][name]
    # The manifest's timestamp is intentionally variable; the file binding is not.
    assert first[1]['manifest']['files'][name] == second[1]['manifest']['files'][name]


@pytest.mark.parametrize("case", ["commit", "hash", "extra", "missing", "worker", "revision", "weights", "bool_size", "bad_path", "case_alias", "file_directory", "roles", "policy_type", "duplicate", "nan", "utf16", "oversize"])
def test_runtime_descriptor_refuses_before_materialization(tmp_path, case):
    docs = _documents()
    raw = {}
    changes = None
    if case == "commit": changes = lambda x: x.update(source_commit="0" * 40)
    if case == "hash": changes = lambda x: x["files"][RUNTIME].update(sha256="0" * 64)
    if case == "extra": changes = lambda x: x.update(secret="synthetic rejected key")
    if case == "missing": changes = lambda x: x["files"].pop(IDENTITY)
    if case == "worker": docs[ENCODER]["code_sha256"] = "0" * 64
    if case == "revision": docs[ENCODER]["encoder"]["revision"] = "0" * 40
    if case == "weights": docs[ENCODER]["encoder"]["weights_sha256"] = "0" * 64
    if case == "bool_size": docs[RUNTIME]["files"][0]["size"] = True
    if case == "bad_path": docs[RUNTIME]["files"][0]["path"] = "model/../secret"
    if case == "case_alias": docs[RUNTIME]["files"].append(dict(docs[RUNTIME]["files"][1], path="SITE/other.py"))
    if case == "file_directory": docs[RUNTIME]["files"].append(dict(docs[RUNTIME]["files"][1], path="site/synthetic.py/child"))
    if case == "roles": docs[IDENTITY]["files"][2]["role"] = "stdlib"
    if case == "policy_type": docs[ENCODER]["query_policy"]["normalize"] = 1
    if case == "duplicate": raw[RUNTIME] = json.dumps(docs[RUNTIME]).replace('"generation":', '"schema":"ignored","generation":').encode()
    if case == "nan": raw[RUNTIME] = json.dumps(docs[RUNTIME]).replace('"size": 1', '"size": NaN').encode()
    if case == "utf16": raw[RUNTIME] = json.dumps(docs[RUNTIME]).encode("utf-16-le")
    if case == "oversize": raw[ENCODER] = b" " * 65537
    path = _fixture(tmp_path, docs, raw, changes)
    run, result = _probe(tmp_path, path)
    assert run.returncode == 23 and not result["accepted"], result
    assert not result["stage_exists"]


@pytest.mark.parametrize("name", ["descriptor-set.json", RUNTIME, IDENTITY, ENCODER])
def test_runtime_descriptor_input_mutation_refuses_before_write(tmp_path, name):
    path = _fixture(tmp_path)
    mutate = " [IO.File]::AppendAllText(" + _quote(tmp_path / name) + ", ' ')"
    run, result = _probe(tmp_path, path, mutate=mutate)
    assert run.returncode == 23 and not result["stage_exists"], result


def test_runtime_descriptor_redirected_open_is_refused(tmp_path):
    path = _fixture(tmp_path)
    (tmp_path / "other.json").write_bytes(path.read_bytes())
    run, result = _probe(tmp_path, path, redirect=True)
    assert run.returncode == 23 and not result["stage_exists"], result


@pytest.mark.parametrize("omit", [False, True])
def test_runtime_descriptor_actual_main_admission_and_manifest(tmp_path, omit):
    path = _fixture(tmp_path)
    run, result = _probe(tmp_path, path, main=True, omit=omit)
    assert run.returncode == 0, result
    assert set(result["hashes"]) == ({"tools-bootstrap/tools/wd_mind_e5_worker.py"} |
                                      (set() if omit else {RUNTIME, IDENTITY, ENCODER}))
    assert (tmp_path / "mind-runtime-descriptors").exists() is not omit


def test_runtime_descriptor_refuses_existing_source_member(tmp_path):
    path = _fixture(tmp_path)
    run, result = _probe(tmp_path, path, mutate=f" $hashes['{RUNTIME}']='existing-source-pin'")
    assert run.returncode == 23 and not result["stage_exists"], result


def test_runtime_descriptor_refuses_reparse_ancestor(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    _fixture(real)
    alias = tmp_path / "alias"
    create = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command",
                             f"$ErrorActionPreference='Stop';New-Item -ItemType Junction -Path {_quote(alias)} -Target {_quote(real)}|Out-Null"],
                            capture_output=True, text=True, timeout=15)
    assert create.returncode == 0, create.stderr
    run, result = _probe(tmp_path, alias / "descriptor-set.json")
    assert run.returncode == 23 and not result["stage_exists"], result


@pytest.mark.parametrize("field", ["schema", "source_commit", "payload_schema", "pin_schema", "code_sha256", "model", "revision", "weights_sha256", "prefix", "dtype", "digest_newline"])
def test_runtime_descriptor_native_string_contract(tmp_path, field):
    docs = _documents()
    change = None
    if field in {"schema", "source_commit"}:
        change = lambda x: x.update({field: [x[field]]})
    elif field == "payload_schema": docs[ENCODER]["schema"] = [docs[ENCODER]["schema"]]
    elif field == "pin_schema": change = lambda x: x["files"][ENCODER].update(schema=[docs[ENCODER]["schema"]])
    elif field == "code_sha256": docs[ENCODER][field] = [docs[ENCODER][field]]
    elif field in {"model", "revision", "weights_sha256"}: docs[ENCODER]["encoder"][field] = [docs[ENCODER]["encoder"][field]]
    elif field == "digest_newline": docs[IDENTITY]["files"][0]["sha256"] += "\n"
    else: docs[ENCODER]["query_policy"][field] = [docs[ENCODER]["query_policy"][field]]
    path = _fixture(tmp_path, docs, outer_change=change)
    run, result = _probe(tmp_path, path)
    assert run.returncode == 23 and not result["stage_exists"], result


@pytest.mark.parametrize("last_length,accepted", [(249, True), (250, False)])
def test_runtime_descriptor_interpreter_path_budget_matches_runtime_parser(tmp_path, last_length, accepted):
    from tools import wd_mind_interpreter_identity as identity

    docs = _documents()
    docs[IDENTITY]["files"][0]["path"] = "/".join(["a" * 255] * 15 + ["b" * last_length])
    data = json.dumps(docs[IDENTITY]).encode()
    if accepted:
        identity.parse_pinned_interpreter_descriptor(data, hashlib.sha256(data).hexdigest())
    else:
        with pytest.raises(identity.IdentityRefused):
            identity.parse_pinned_interpreter_descriptor(data, hashlib.sha256(data).hexdigest())
    path = _fixture(tmp_path, docs)
    run, result = _probe(tmp_path, path)
    assert result["accepted"] is accepted, result
    assert run.returncode == (0 if accepted else 23)


def _location_documents(tmp_path):
    docs = _documents()
    docs[MESSAGE_POLICY] = {
        "schema": "wd.mind-content-policy.v3", "lanes": ["codex-lead-1", "codex-tools-1", "fable-5"],
        "types": ["message", "done", "decision"], "task_scope": "all_tasks",
        "removed_source_refs": ["1" * 64], "max_message_chars": 4096, "max_chunk_chars": 300,
    }
    runtime_root = tmp_path / "synthetic-runtime"
    interpreter_root = tmp_path / "synthetic-interpreter"
    runtime_root.mkdir()
    interpreter_root.mkdir()
    docs[LOCATION] = {
        "schema": "wd.mind-runtime-location.v1", "source_commit": COMMIT,
        "runtime_root": str(runtime_root), "interpreter_root": str(interpreter_root),
        "runtime_manifest_sha256": hashlib.sha256(json.dumps(docs[RUNTIME]).encode()).hexdigest(),
        "interpreter_identity_sha256": hashlib.sha256(json.dumps(docs[IDENTITY]).encode()).hexdigest(),
        "encoder_binding_sha256": hashlib.sha256(json.dumps(docs[ENCODER]).encode()).hexdigest(),
    }
    return docs


def test_runtime_location_v2_exact_bytes_no_asset_copy(tmp_path):
    docs = _location_documents(tmp_path)
    path = _fixture(tmp_path, docs, version=2)
    run, result = _probe(tmp_path, path, main=True)
    assert run.returncode == 0, result
    assert set(result["hashes"]) == {RUNTIME, IDENTITY, ENCODER, LOCATION, MESSAGE_POLICY, "tools-bootstrap/tools/wd_mind_e5_worker.py"}
    assert Path(result["paths"][LOCATION]).read_bytes() == (tmp_path / LOCATION).read_bytes()
    assert Path(result["paths"][MESSAGE_POLICY]).read_bytes() == (tmp_path / MESSAGE_POLICY).read_bytes()
    assert json.loads(Path(result["paths"][MESSAGE_POLICY]).read_bytes())["removed_source_refs"] == ["1" * 64]
    assert list((tmp_path / "synthetic-runtime").iterdir()) == []
    assert list((tmp_path / "synthetic-interpreter").iterdir()) == []


@pytest.mark.parametrize("case", ["v1_five", "v2_missing_location", "commit", "runtime_pin", "identity_pin", "encoder_pin", "extra", "relative", "unc", "drive_relative", "ads", "dotdot", "trailing_dot", "missing_root", "same_roots", "nested_roots", "array_root", "array_digest", "newline_digest"])
def test_runtime_location_v2_refuses_bad_binding_before_write(tmp_path, case):
    docs = _location_documents(tmp_path)
    loc = docs[LOCATION]
    version = 1 if case == "v1_five" else 2
    if case == "v2_missing_location": docs.pop(LOCATION)
    if case == "commit": loc["source_commit"] = "0" * 40
    if case == "runtime_pin": loc["runtime_manifest_sha256"] = "0" * 64
    if case == "identity_pin": loc["interpreter_identity_sha256"] = "0" * 64
    if case == "encoder_pin": loc["encoder_binding_sha256"] = "0" * 64
    if case == "extra": loc["hmac_key"] = "synthetic-rejected"
    if case == "relative": loc["runtime_root"] = "synthetic-runtime"
    if case == "unc": loc["runtime_root"] = r"\\localhost\C$\synthetic-runtime"
    if case == "drive_relative": loc["runtime_root"] = "C:synthetic-runtime"
    if case == "ads": loc["runtime_root"] += ":stream"
    if case == "dotdot": loc["runtime_root"] += "\\..\\synthetic-runtime"
    if case == "trailing_dot": loc["runtime_root"] += "."
    if case == "missing_root": loc["runtime_root"] += "-absent"
    if case == "same_roots": loc["interpreter_root"] = loc["runtime_root"]
    if case == "nested_roots":
        nested = Path(loc["runtime_root"]) / "interpreter"
        nested.mkdir()
        loc["interpreter_root"] = str(nested)
    if case == "array_root": loc["runtime_root"] = [loc["runtime_root"]]
    if case == "array_digest": loc["runtime_manifest_sha256"] = [loc["runtime_manifest_sha256"]]
    if case == "newline_digest": loc["runtime_manifest_sha256"] += "\n"
    path = _fixture(tmp_path, docs, version=version)
    run, result = _probe(tmp_path, path)
    assert run.returncode == 23 and not result["stage_exists"], result


@pytest.mark.parametrize("case", ["missing", "v1_schema", "missing_removals", "bad_ref", "duplicate_ref", "rco_lane", "unknown_type", "scope", "bool_budget", "small_chunk", "large_message", "extra", "object_refs"])
def test_runtime_location_v2_policy_refuses_before_write(tmp_path, case):
    docs = _location_documents(tmp_path)
    policy = docs[MESSAGE_POLICY]
    if case == "missing": docs.pop(MESSAGE_POLICY)
    if case == "v1_schema": policy["schema"] = "wd.mind-content-policy.v1"
    if case == "missing_removals": policy.pop("removed_source_refs")
    if case == "bad_ref": policy["removed_source_refs"] = ["not-a-ref"]
    if case == "duplicate_ref": policy["removed_source_refs"] *= 2
    if case == "rco_lane": policy["lanes"][0] = "claude-rco-1"
    if case == "unknown_type": policy["types"] = ["imaginary"]
    if case == "scope": policy["task_scope"] = "anything"
    if case == "bool_budget": policy["max_message_chars"] = True
    if case == "small_chunk": policy["max_chunk_chars"] = 15
    if case == "large_message": policy["max_message_chars"] = 4097
    if case == "extra": policy["extra"] = "not-allowed"
    if case == "object_refs": policy["removed_source_refs"] = {}
    path = _fixture(tmp_path, docs, version=2)
    run, result = _probe(tmp_path, path)
    assert run.returncode == 23 and not result["stage_exists"], result


@pytest.mark.parametrize("after_capture", [False, True])
def test_runtime_location_v2_reparse_root_is_refused(tmp_path, after_capture):
    docs = _location_documents(tmp_path)
    alias = tmp_path / "root-alias"
    script = f"New-Item -ItemType Junction -Path {_quote(alias)} -Target {_quote(tmp_path / 'synthetic-runtime')}|Out-Null"
    if after_capture:
        # The pathname switches after capture; no live root or source is touched.
        alias.mkdir()
        mutation = f"[IO.Directory]::Delete({_quote(alias)});" + script
    else:
        create = subprocess.run([str(POWERSHELL), "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, timeout=15)
        assert create.returncode == 0, create.stderr
        mutation = ""
    docs[LOCATION]["runtime_root"] = str(alias)
    path = _fixture(tmp_path, docs, version=2)
    run, result = _probe(tmp_path, path, mutate=mutation)
    assert run.returncode == 23 and not result["stage_exists"], result


def test_runtime_location_v2_opened_directory_must_match_path(tmp_path):
    path = _fixture(tmp_path, _location_documents(tmp_path), version=2)
    run, result = _probe(tmp_path, path, redirect_root=True)
    assert run.returncode == 23 and not result["stage_exists"], result
    assert "directory handle differs" in result["reason"]


def test_runtime_location_v2_replaced_directory_refuses_before_write(tmp_path):
    docs = _location_documents(tmp_path)
    path = _fixture(tmp_path, docs, version=2)
    root = tmp_path / "synthetic-runtime"
    moved = tmp_path / "retained-original-runtime"
    mutate = (f"[IO.Directory]::Move({_quote(root)}, {_quote(moved)});"
              f"[IO.Directory]::CreateDirectory({_quote(root)})|Out-Null")
    run, result = _probe(tmp_path, path, mutate=mutate)
    assert run.returncode == 23 and not result["stage_exists"], result
    assert "directory changed" in result["reason"]


@pytest.mark.parametrize("name", [LOCATION, MESSAGE_POLICY])
def test_runtime_location_v2_input_mutation_refuses_before_write(tmp_path, name):
    path = _fixture(tmp_path, _location_documents(tmp_path), version=2)
    mutate = f"[IO.File]::AppendAllText({_quote(tmp_path / name)}, ' ')"
    run, result = _probe(tmp_path, path, mutate=mutate)
    assert run.returncode == 23 and not result["stage_exists"], result


def test_runtime_location_v2_preserves_explicit_policy_allowlist(tmp_path):
    docs = _location_documents(tmp_path)
    docs[MESSAGE_POLICY]["approved_source_refs"] = ["2" * 64]
    path = _fixture(tmp_path, docs, version=2)
    run, result = _probe(tmp_path, path)
    assert run.returncode == 0, result
    policy = json.loads(Path(result["paths"][MESSAGE_POLICY]).read_bytes())
    assert policy["removed_source_refs"] == ["1" * 64]
    assert policy["approved_source_refs"] == ["2" * 64]
