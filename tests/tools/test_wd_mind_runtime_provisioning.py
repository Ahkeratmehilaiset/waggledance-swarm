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
        "$required=@('Resolve-FullPath','Assert-WdPathWithoutReparse','Read-WdMindRuntimeDescriptorSet','Add-WdMindRuntimeDescriptorFiles','Add-WdMindGuardianManifest')\n"
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
        (" $manifest.files['tools-bootstrap/tools/wd_mind_runtime_process.py']='B'*64;$manifest.files['Invoke-WdBridgePython.ps1']='C'*64\n"
         " $EnableMindGuardianStartup=[switch]$true;$mindRuntimeDescriptors=$set\n" +
         f" $EnableMindGuardianFastBuild=[switch]${str(enable_fast).lower()}\n" +
         " $entry=@($ast.FindAll({param($n)$n -is [Management.Automation.Language.CommandAst] -and $n.GetCommandName() -ceq 'Add-WdMindGuardianManifest'},$true))\n"
         " if($entry.Count -ne 1){throw 'GUARDIAN_MAIN_MISSING'};. ([scriptblock]::Create($entry[0].Extent.Text))\n" if enable_guardian else "") +
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


def test_guardian_exact_packaged_wrapper_transports_stdin_stop(tmp_path):
    """Real PS/VerifyPackage/Python transport; only an inert audit child runs."""
    import base64
    bundle = tmp_path / "transport bundle Å"
    bootstrap = bundle / "tools-bootstrap"
    bootstrap.mkdir(parents=True)
    code = ("import json,sys,threading\n"
            "received=[]; done=threading.Event()\n"
            "def read():\n"
            " received.append(sys.stdin.readline(16)); done.set()\n"
            "threading.Thread(target=read,daemon=True).start()\n"
            "print(json.dumps({'ready':True}),flush=True)\n"
            "ok=done.wait(4) and received == ['STOP\\n']\n"
            "print(json.dumps({'stop_received':ok,'isolated':bool(sys.flags.no_site)}),flush=True)\n"
            "sys.exit(0 if ok else 2)\n").encode()
    wheel = b"inert synthetic wheel, never imported or installed"
    files = {"tools/guardian_transport.py": code, "tools/__init__.py": b"# inert\n",
             "bridge-wheels/synthetic-1-py3-none-any.whl": wheel,
             "bridge-site/synthetic.py": b"# inert\n"}
    for name, raw in files.items():
        path = bootstrap / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    definition = {"schema":"wd.bridge-code-package.v1", "package_relative_root":"tools-bootstrap",
        "invocation_wrapper_relative":"Invoke-WdBridgePython.ps1", "wheel_store_relative":"bridge-wheels",
        "python_site_relative":"bridge-site", "python_files":["tools/guardian_transport.py","tools/__init__.py"],
        "python_entrypoints":{"synthetic":"tools/guardian_transport.py"},
        "python_requirements":[{"name":"synthetic","version":"1","wheel":"synthetic-1-py3-none-any.whl",
            "sha256":hashlib.sha256(wheel).hexdigest(),"import_path":"synthetic.py"}],
        "import_smoke":{"third_party_module":"synthetic","package_modules":["tools.guardian_transport"]},
        "isolation_environment":{"PYTHONSAFEPATH":"1","PYTHONNOUSERSITE":"1","PYTHONDONTWRITEBYTECODE":"1"}}
    (bundle / "bridge-code-files.json").write_text(json.dumps(definition), encoding="utf-8")
    for name in ("Invoke-WdBridgePython.ps1", "BridgeCodeContext.ps1"):
        (bundle / name).write_bytes((ROOT / "ops/windows/reboot" / name).read_bytes())
    executable = str(Path(sys.executable).resolve())
    (bundle / "wd-fleet.json").write_text(json.dumps({"bridge_python":{"executable":executable}}), encoding="utf-8")
    pins = {path.relative_to(bundle).as_posix():hashlib.sha256(path.read_bytes()).hexdigest().upper()
            for path in bundle.rglob("*") if path.is_file()}
    manifest = json.dumps({"schema_version":1,"files":pins}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    environment = dict(os.environ, WD_REBOOT_EXPECTED_MANIFEST_HASH=hashlib.sha256(manifest).hexdigest().upper(),
        WD_BRIDGE_PYTHON=executable, WD_BRIDGE_PYTHON_SHA256=hashlib.sha256(Path(executable).read_bytes()).hexdigest().upper())
    # Match the generated wrapper's process-local Desktop module path repair.
    environment["PSModulePath"] = str(POWERSHELL.parent / "Modules")
    command = f"& {_quote(bundle / 'Invoke-WdBridgePython.ps1')} -Tool 'tools/guardian_transport.py' -VerifyPackage; exit $LASTEXITCODE"
    encoded = base64.b64encode(command.encode("utf-16-le")).decode()
    process = subprocess.Popen([str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-EncodedCommand", encoded], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
    # Popen.communicate never kills on timeout. The inert child has its own
    # four-second exit; a timeout is a test failure with its process retained.
    ready = process.stdout.readline(1024)
    out, err = process.communicate(b"STOP\n", timeout=15)
    assert json.loads(ready) == {"ready":True}, (ready, err)
    assert process.returncode == 0 and out, (out, err.decode("utf-8", "replace"))
    assert json.loads(out) == {"stop_received":True,"isolated":True}


@pytest.mark.parametrize("enabled,v2,fast,accepted", [(False,False,False,True),(True,False,False,False),
    (True,True,False,True),(False,True,True,False),(True,False,True,False),(True,True,True,True)])
def test_guardian_manifest_opt_in_requires_v2_descriptor_set(tmp_path, enabled, v2, fast, accepted):
    script = tmp_path / "guardian-manifest.ps1"
    script.write_text("$ErrorActionPreference='Stop';$t=$null;$e=$null\n"
        f"$a=[Management.Automation.Language.Parser]::ParseFile({_quote(DEPLOY)},[ref]$t,[ref]$e)\n"
        "$d=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Add-WdMindGuardianManifest'},$true))\n"
        "if($d.Count -ne 1){throw 'GUARDIAN_MANIFEST_HELPER_MISSING'};. ([scriptblock]::Create($d[0].Extent.Text))\n"
        "$m=[ordered]@{files=[ordered]@{}};$set=$null\n" +
        ("$set=[pscustomobject]@{LocationRoots=@('runtime','interpreter');Files=@{}}\n"
         "$names=@('wd-mind-runtime-manifest.json','wd-mind-interpreter-identity.json','wd-mind-e5-encoder-binding.json','wd-mind-runtime-location.json','wd-mind-message-policy.json')\n"
         "foreach($name in $names){$set.Files[$name]=@{};$m.files[$name]='A'*64}\n"
         "$m.files['tools-bootstrap/tools/wd_mind_runtime_process.py']='B'*64\n"
         "$m.files['Invoke-WdBridgePython.ps1']='C'*64\n" if v2 else "") +
        f"try{{Add-WdMindGuardianManifest -Manifest $m -Descriptors $set -Enabled:${str(enabled).lower()} -FastBuild:${str(fast).lower()};"
        "[ordered]@{accepted=$true;manifest=$m}|ConvertTo-Json -Compress -Depth 8}catch{[ordered]@{accepted=$false}|ConvertTo-Json -Compress}\n",
        encoding="utf-8")
    result = subprocess.run([str(POWERSHELL),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)],
        capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)
    assert row["accepted"] is accepted
    if accepted:
        assert ("mind_startup_guardian" in row["manifest"]) is enabled
        if enabled:
            expected = {"schema":"wd.mind-startup-guardian.v1",
                "enabled":True,"entrypoint":"tools/wd_mind_runtime_process.py","ready_timeout_seconds":15}
            if fast: expected.update(schema='wd.mind-startup-guardian.v2',fast_build=True,ready_timeout_seconds=600)
            assert row["manifest"]["mind_startup_guardian"] == expected


def test_guardian_actual_stage_manifest_binds_opt_in_to_final_pair(tmp_path):
    path = _fixture(tmp_path, _location_documents(tmp_path), version=2)
    run, result = _probe(tmp_path, path, main=True, enable_guardian=True)
    assert run.returncode == 0, result
    assert result["manifest"]["mind_startup_guardian"]["enabled"] is True
    assert result["manifest_sha256"] in run.stdout


def test_guardian_fast_build_stage_manifest_is_explicit_and_hash_bound(tmp_path):
    path = _fixture(tmp_path, _location_documents(tmp_path), version=2)
    run, result = _probe(tmp_path, path, main=True, enable_guardian=True, enable_fast=True)
    assert run.returncode == 0, result
    assert result['manifest']['mind_startup_guardian'] == {
        'schema':'wd.mind-startup-guardian.v2', 'enabled':True,
        'entrypoint':'tools/wd_mind_runtime_process.py', 'ready_timeout_seconds':600, 'fast_build':True}
    assert result['manifest_sha256'] in run.stdout


def _generated_fleet_wrapper(tmp_path):
    output = tmp_path / "generated-fleet.ps1"
    script = tmp_path / "generate.ps1"
    script.write_text("$ErrorActionPreference='Stop';$t=$null;$e=$null\n"
        f"$a=[Management.Automation.Language.Parser]::ParseFile({_quote(DEPLOY)},[ref]$t,[ref]$e)\n"
        "$d=$a.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'New-ForwardingWrapper'},$true)\n"
        ". ([scriptblock]::Create($d.Extent.Text))\n"
        "$w=New-ForwardingWrapper -Target 'C:\\synthetic\\start-wd-all.ps1' -ExpectedHash ('A'*64) -ExpectedManifestHash ('B'*64) -WrapperKind fleet -ExpectedFinalCommit ('c'*40) -ExpectedFinalManifestHash ('B'*64)\n"
        f"[IO.File]::WriteAllText({_quote(output)},$w,(New-Object Text.UTF8Encoding($false)))\n",
        encoding="utf-8")
    run = subprocess.run([str(POWERSHELL),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)],
        capture_output=True,text=True,timeout=15)
    assert run.returncode == 0, run.stderr
    return output


def test_generated_wrapper_guardian_precedes_uac_and_stops_only_own_child(tmp_path):
    wrapper = _generated_fleet_wrapper(tmp_path).read_text(encoding="utf-8")
    start = "$wdMindGuardian = Start-WdWrapperMindGuardian"
    assert start in wrapper
    assert wrapper.index(start) < wrapper.index("$elevated = Start-Process")
    assert "Stop-WdWrapperMindGuardian -Owner $wdMindGuardian" in wrapper
    assert "tools/wd_mind_runtime_process.py" in wrapper and "-VerifyPackage" in wrapper
    assert "mind_guardian_status_only" in wrapper
    assert ".Kill(" not in wrapper and "Stop-Process" not in wrapper


def _wrapper_guardian_probe(tmp_path, case):
    wrapper = _generated_fleet_wrapper(tmp_path)
    bundle = tmp_path / "fake pinned bundle"
    bundle.mkdir()
    helper = bundle / "Invoke-WdBridgePython.ps1"
    helper.write_bytes(b"# pinned synthetic placeholder; never execute\n")
    spec = {"schema":"wd.mind-startup-guardian.v1","enabled":True,
            "entrypoint":"tools/wd_mind_runtime_process.py","ready_timeout_seconds":15}
    if case == "extra_config": spec["fallback"] = True
    if case == "array_schema": spec["schema"] = [spec["schema"]]
    if case.startswith('fast_'):
        spec.update(schema='wd.mind-startup-guardian.v2', fast_build=True)
    if case == 'fast_ready_long': spec['ready_timeout_seconds'] = 600
    if case == 'fast_timeout_over': spec['ready_timeout_seconds'] = 601
    if case == 'fast_missing': del spec['fast_build']
    if case == 'fast_false': spec['fast_build'] = False
    if case == 'fast_integer': spec['fast_build'] = 1
    if case == 'fast_extra': spec['fallback'] = True
    if case == 'legacy_fast': spec['fast_build'] = True
    document = {"schema_version":1,"mind_startup_guardian":spec,"files":{
        name:"A"*64 for name in [RUNTIME,IDENTITY,ENCODER,LOCATION,MESSAGE_POLICY,"tools-bootstrap/tools/wd_mind_runtime_process.py"]},
        "bridge_code_package":{"python_executable":sys.executable,"python_executable_sha256":"C"*64}}
    document["files"]["Invoke-WdBridgePython.ps1"] = hashlib.sha256(helper.read_bytes()).hexdigest().upper()
    if case == "off": document.pop("mind_startup_guardian")
    if case == "missing_descriptor": document["files"].pop(LOCATION)
    raw = json.dumps(document).encode()
    (bundle / "deployment-manifest.json").write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    if case == "wrong_pin": pin = "0"*64
    if case == "changed_helper": helper.write_bytes(b"# changed helper\n")
    status = {"schema":"wd.mind-guardian-status.v1","state":"ACTIVE","owner_report":"READY","owned":True,
              "marker_disposition_committed":False,"authority":"none","same_user_race":"UNKNOWN"}
    if case == "hold": status["state"] = "HOLD"
    if case == "owner_hold": status["owner_report"] = "HOLD"
    if case == "starting": status["owner_report"] = "STARTING"
    if case == "disposed": status["marker_disposition_committed"] = True
    if case == "bad_authority": status["authority"] = "trusted"
    if case == "extra_status": status["extra"] = True
    data = json.dumps(status,sort_keys=True,separators=(",",":")).encode() + b"\n"
    if case == "duplicate_status": data=data.replace(b'"owned":true',b'"owned":true,"owned":true')
    if case == "utf16": data=data.decode().encode("utf-16-le")
    if case == "oversize": data=b"x"*9000
    (tmp_path / "stdout.bin").write_bytes(data)
    (tmp_path / "stderr.bin").write_bytes(b"synthetic error" if case == "stderr" else b"")
    script = tmp_path / "fake-guardian.ps1"
    script.write_text("$ErrorActionPreference='Stop';Set-StrictMode -Version Latest;$t=$null;$e=$null\n"
        f"$a=[Management.Automation.Language.Parser]::ParseFile({_quote(wrapper)},[ref]$t,[ref]$e)\n"
        "if($e.Count){throw 'WRAPPER_PARSE'}\n"
        "$d=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true))\n"
        "foreach($f in $d){if($f.Name -match 'WdWrapperMind|^Test-WdWrapperAdministrator$|^ConvertTo-WdSingleQuotedLiteral$'){. ([scriptblock]::Create($f.Extent.Text))}}\n"
        "$script:receiveDeadline=0;$script:actualReceive=${function:Receive-WdWrapperMindGuardian}\n"
        "function Receive-WdWrapperMindGuardian {param($Owner,[long]$TimeoutMilliseconds);$script:receiveDeadline=$TimeoutMilliseconds; & $script:actualReceive -Owner $Owner -TimeoutMilliseconds $TimeoutMilliseconds}\n"
        f"function Test-WdWrapperAdministrator {{ return ${str(case == 'elevated').lower()} }}\n"
        "$script:spawns=0;$script:argv=''\n"
        "function Invoke-WdWrapperMindGuardianSpawn {param($Owner,$StartInfo)\n"
        "$script:spawns++;$script:argv=$StartInfo.Arguments\n"
        f"$out=[IO.MemoryStream]::new([IO.File]::ReadAllBytes({_quote(tmp_path / 'stdout.bin')}))\n"
        f"$err=[IO.MemoryStream]::new([IO.File]::ReadAllBytes({_quote(tmp_path / 'stderr.bin')}))\n"
        "$Owner.Process=[pscustomobject]@{HasExited=$false;StandardOutput=[pscustomobject]@{BaseStream=$out};StandardError=[pscustomobject]@{BaseStream=$err};StandardInput=[pscustomobject]@{BaseStream=[IO.MemoryStream]::new()}}\n" +
        ("throw 'synthetic post-start error'\n" if case == "spawn_error" else "") + "}\n" +
        ("$script:reader=${function:Read-WdWrapperMindPinned};$script:reads=0\n"
         "function Read-WdWrapperMindPinned {param($Path,$Pin,$Limit=4194304);$x=& $script:reader $Path $Pin $Limit;"
         "if($Path.EndsWith('deployment-manifest.json')){$script:reads++;if($script:reads -eq 1){[IO.File]::AppendAllText($Path,' ')}};return $x}\n" if case == "mutation" else "") +
        f"$document=Get-Content -LiteralPath {_quote(bundle / 'deployment-manifest.json')} -Raw|ConvertFrom-Json\n"
        f"$owner=Start-WdWrapperMindGuardian -BundleRoot {_quote(bundle)} -ExpectedManifestHash '{pin}' -Deployment $document -HostPath {_quote(POWERSHELL)}\n"
        f"$again=Start-WdWrapperMindGuardian -BundleRoot {_quote(bundle)} -ExpectedManifestHash '{pin}' -Deployment $document -HostPath {_quote(POWERSHELL)}\n"
        "$stop='';if($null -ne $owner.Process){Stop-WdWrapperMindGuardian -Owner $owner;Stop-WdWrapperMindGuardian -Owner $owner;$stop=[Text.Encoding]::ASCII.GetString($owner.Process.StandardInput.BaseStream.ToArray())}\n"
        "[ordered]@{outcome=$owner.Outcome;reason=$owner.Reason;spawns=$script:spawns;retained=($null -ne $owner.Process);same=[object]::ReferenceEquals($owner,$again);stop=$stop;argv=$script:argv;receive_deadline=$script:receiveDeadline}|ConvertTo-Json -Compress\n",
        encoding="utf-8")
    env = dict(os.environ, PSModulePath=str(POWERSHELL.parent / "Modules"))
    result = subprocess.run([str(POWERSHELL),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)],
        capture_output=True,text=True,timeout=25,env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("case", ["off","wrong_pin","changed_helper","missing_descriptor","extra_config","array_schema","elevated","mutation"])
def test_generated_guardian_wrapper_refuses_before_spawn(tmp_path, case):
    row = _wrapper_guardian_probe(tmp_path, case)
    assert row["outcome"] == ("off" if case == "off" else "status_only")
    assert row["spawns"] == 0 and row["retained"] is False and row["same"] is True


@pytest.mark.parametrize("case", ["ready","hold","owner_hold","bad_authority","extra_status","duplicate_status","utf16","oversize","stderr","spawn_error"])
def test_generated_guardian_wrapper_closed_status_and_retained_own_stop(tmp_path, case):
    import base64
    row = _wrapper_guardian_probe(tmp_path, case)
    assert row["outcome"] == ("ready" if case == "ready" else "status_only"), row
    assert row["spawns"] == 1 and row["retained"] is True and row["same"] is True
    assert row["stop"] == "STOP\n"
    command = base64.b64decode(row["argv"].split()[-1]).decode("utf-16-le")
    assert "-VerifyPackage" in command and "--guardian --bundle-root" in command


@pytest.mark.parametrize('case', ['fast_ready','fast_ready_long','fast_timeout_over','fast_missing','fast_false','fast_integer','fast_extra','legacy_fast'])
def test_generated_guardian_wrapper_fast_build_schema_and_stop(tmp_path, case):
    import base64
    row = _wrapper_guardian_probe(tmp_path, case)
    accepted = case in ('fast_ready', 'fast_ready_long')
    assert row['outcome'] == ('ready' if accepted else 'status_only'), row
    assert row['spawns'] == int(accepted) and row['retained'] is accepted
    if accepted:
        assert row['stop'] == 'STOP\n'
        assert row['receive_deadline'] == (600000 if case == 'fast_ready_long' else 15000)
        command = base64.b64decode(row['argv'].split()[-1]).decode('utf-16-le')
        assert '--fast-build' not in command  # Owner flag comes only from the manifest inside the guardian.


def test_generated_guardian_wrapper_pending_io_keeps_tasks_and_has_deadline(tmp_path):
    wrapper = _generated_fleet_wrapper(tmp_path)
    script = tmp_path / "pending.ps1"
    script.write_text("$ErrorActionPreference='Stop';$t=$null;$e=$null\n"
        f"$a=[Management.Automation.Language.Parser]::ParseFile({_quote(wrapper)},[ref]$t,[ref]$e)\n"
        "$d=@($a.FindAll({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst]},$true))\n"
        "foreach($f in $d){if($f.Name -in @('Receive-WdWrapperMindGuardian','Stop-WdWrapperMindGuardian')){. ([scriptblock]::Create($f.Extent.Text))}}\n"
        "$out=[Threading.Tasks.TaskCompletionSource[int]]::new();$err=[Threading.Tasks.TaskCompletionSource[int]]::new()\n"
        "$owner=[pscustomobject]@{Outcome='status_only';Reason='unknown';Process=[pscustomobject]@{HasExited=$false};Ready=$false;OutRead=$out.Task;ErrRead=$err.Task;OutBuffer=(New-Object byte[] 512);ErrBuffer=(New-Object byte[] 512);OutTotal=0;ErrTotal=0;Pending=''}\n"
        "$clock=[Diagnostics.Stopwatch]::StartNew();Receive-WdWrapperMindGuardian $owner 20;$elapsed=$clock.ElapsedMilliseconds\n"
        "[ordered]@{reason=$owner.Reason;outcome=$owner.Outcome;elapsed=$elapsed;out_same=[object]::ReferenceEquals($out.Task,$owner.OutRead);err_same=[object]::ReferenceEquals($err.Task,$owner.ErrRead);pending=(-not $owner.OutRead.IsCompleted -and -not $owner.ErrRead.IsCompleted)}|ConvertTo-Json -Compress\n",
        encoding="utf-8")
    run = subprocess.run([str(POWERSHELL),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)],
        capture_output=True,text=True,timeout=15)
    assert run.returncode == 0, run.stderr
    row = json.loads(run.stdout)
    assert row["reason"] == "guardian_timeout" and row["outcome"] == "status_only"
    assert row["pending"] and row["out_same"] and row["err_same"] and row["elapsed"] < 1000


@pytest.mark.parametrize("cancel,restore_exit", [(False,0),(True,0),(False,2)])
def test_generated_auto_wrapper_unavailable_continues_and_uac_cancel_stops_own_child(tmp_path, cancel, restore_exit):
    """Execute the generated -Auto flow with inert process/UAC seams only."""
    wrapper = _generated_fleet_wrapper(tmp_path)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    trace = tmp_path / "synthetic-actions.txt"
    target = bundle / "start-wd-all.ps1"
    target.write_bytes(b"throw 'fake target must only appear in inert encoded UAC command'\n")
    limited = bundle / "WdBridgeLimitedBootstrap.ps1"
    limited.write_text("function Invoke-WdBridgeWrapperBootstrap {\n"
        f"[IO.File]::AppendAllText({_quote(trace)},'BOOTSTRAP' + [Environment]::NewLine)\n"
        "return [pscustomobject]@{ok=$true;stage='synthetic';reasons=@()}\n}\n",encoding="utf-8")
    manifest = json.dumps({"schema_version":1,"files":{
        "WdBridgeLimitedBootstrap.ps1":hashlib.sha256(limited.read_bytes()).hexdigest().upper()}}).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    digest = hashlib.sha256(manifest).hexdigest().upper()
    (tmp_path / "WD_REBOOT_STATE_CURRENT.json").write_text(json.dumps({
        "source_commit":"c"*40,"final_commit":"c"*40,"manifest_sha256":digest,
        "final_manifest_sha256":digest,"active_bundle":str(bundle)}),encoding="utf-8")
    text = wrapper.read_text(encoding="utf-8").replace(r"C:\synthetic\start-wd-all.ps1",str(target))
    text = text.replace("A"*64,hashlib.sha256(target.read_bytes()).hexdigest().upper()).replace("B"*64,digest)
    inject = ("function Test-WdWrapperAdministrator { return $false }\n"
        "function Start-WdWrapperMindGuardian {\n"
        f"[IO.File]::AppendAllText({_quote(trace)},'MIND_STATUS_ONLY' + [Environment]::NewLine)\n"
        "$script:syntheticGuardian=[pscustomobject]@{Outcome='status_only';Reason='guardian_timeout';Process=[pscustomobject]@{synthetic=$true}};return $script:syntheticGuardian\n}\n"
        "function Stop-WdWrapperMindGuardian {param($Owner)\n"
        "if(-not [object]::ReferenceEquals($Owner,$script:syntheticGuardian)){throw 'wrong child'}\n"
        f"[IO.File]::AppendAllText({_quote(trace)},'STOP_OWNED' + [Environment]::NewLine)\n}}\n"
        "function Start-Process {\n"
        f"[IO.File]::AppendAllText({_quote(trace)},'UAC' + [Environment]::NewLine)\n" +
        ("throw 'synthetic UAC refusal'\n" if cancel else
         f"$p=[pscustomobject]@{{Handle=1;ExitCode={restore_exit}}};$p|Add-Member ScriptMethod WaitForExit {{param($ms)return $true}};$p|Add-Member ScriptMethod Refresh {{}};return $p\n") + "}\n")
    boundary="if ($Auto -and -not (Test-WdWrapperAdministrator)) {"
    assert text.count(boundary) == 1
    wrapper.write_text(text.replace(boundary,inject+boundary),encoding="utf-8")
    harness = tmp_path / "auto-harness.ps1"
    harness.write_text(f"try {{ & {_quote(wrapper)} -Auto }} catch {{ Write-Output 'EXPECTED_SYNTHETIC_REFUSAL';exit 23 }}\n",encoding="utf-8")
    env=dict(os.environ,PSModulePath=str(POWERSHELL.parent / "Modules"))
    run=subprocess.run([str(POWERSHELL),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(harness)],
        capture_output=True,text=True,timeout=15,env=env)
    assert run.returncode == (23 if cancel or restore_exit else 0), (run.stdout,run.stderr)
    expected=["MIND_STATUS_ONLY","UAC"]
    if cancel: expected.append("STOP_OWNED")
    elif not restore_exit: expected.append("BOOTSTRAP")
    assert trace.read_text().splitlines() == expected
    assert "Mind unavailable; lane read may refuse" in run.stdout
    assert "continues with status only" not in run.stdout
    if restore_exit:
        assert "Mind guardian retained; no cleanup proven" in run.stdout


def test_generated_guardian_stop_pending_retains_own_channel_without_retry(tmp_path):
    wrapper = _generated_fleet_wrapper(tmp_path)
    script = tmp_path / "pending-stop.ps1"
    script.write_text("$ErrorActionPreference='Stop';$t=$null;$e=$null\n"
        f"$a=[Management.Automation.Language.Parser]::ParseFile({_quote(wrapper)},[ref]$t,[ref]$e)\n"
        "$f=$a.Find({param($n)$n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Stop-WdWrapperMindGuardian'},$true)\n"
        ". ([scriptblock]::Create($f.Extent.Text))\n"
        "$script:pending=[Threading.Tasks.TaskCompletionSource[int]]::new();$script:writes=0\n"
        "$stream=[pscustomobject]@{};$stream|Add-Member ScriptMethod WriteAsync {param($b,$o,$n);$script:writes++;return $script:pending.Task}\n"
        "$owner=[pscustomobject]@{Reason='';Process=[pscustomobject]@{StandardInput=[pscustomobject]@{BaseStream=$stream}};StopAttempted=$false;StopBuffer=$null;StopTask=$null}\n"
        "$script:WdWrapperMindGuardianOwner=$owner\n"
        "$foreign=[pscustomobject]@{Process=$owner.Process;StopAttempted=$false}\n"
        "Stop-WdWrapperMindGuardian $foreign\n"
        "$clock=[Diagnostics.Stopwatch]::StartNew();Stop-WdWrapperMindGuardian $owner;Stop-WdWrapperMindGuardian $owner;$elapsed=$clock.ElapsedMilliseconds\n"
        "[ordered]@{reason=$owner.Reason;writes=$script:writes;elapsed=$elapsed;pending=(-not $owner.StopTask.IsCompleted);same=[object]::ReferenceEquals($script:pending.Task,$owner.StopTask);bytes=[Text.Encoding]::ASCII.GetString($owner.StopBuffer)}|ConvertTo-Json -Compress\n",
        encoding="utf-8")
    run=subprocess.run([str(POWERSHELL),"-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass","-File",str(script)],
        capture_output=True,text=True,timeout=15)
    assert run.returncode == 0, run.stderr
    row=json.loads(run.stdout)
    assert row["reason"] == "guardian_stop_pending" and row["writes"] == 1
    assert row["pending"] and row["same"] and row["bytes"] == "STOP\n" and row["elapsed"] < 1000
