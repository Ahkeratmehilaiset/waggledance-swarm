"""Synthetic wrapper contract; no model, real memory or installation.
Both Windows PowerShell and pwsh run the same function fixtures. The transport
spy replaces a function after dot-sourcing; it is not a production apply mode.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops/windows/reboot"
WRAPPER = OPS / "Invoke-WdQ4Query.ps1"
HOSTS = [
    pytest.param(shutil.which("powershell.exe"), id="windows-powershell"),
    pytest.param(shutil.which("pwsh"), id="pwsh"),
]
COMMIT = "a" * 40
CODE = ["tools/__init__.py", "tools/wd_q4_agent_query.py",
        "sandbox/mind/step2/runtime_bootstrap.py",
        "sandbox/mind/step2/runtime_launcher.py",
        "sandbox/mind/step2/runtime_manifest.py"]


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run_ps(host, script):
    if os.name != "nt" or not host:
        pytest.skip("this Windows shell is not installed")
    assert WRAPPER.is_file(), "tests-first RED: Q4 wrapper is missing"
    prefix = "$ErrorActionPreference='Stop'\n"
    prefix += "Import-Module (Join-Path $PSHOME 'Modules\\Microsoft.PowerShell.Utility')\n"
    prefix += ". " + quote(WRAPPER) + "\n"
    encoded = base64.b64encode((prefix + script).encode("utf-16-le")).decode()
    run = subprocess.run([host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert "query-sensitive" not in run.stdout + run.stderr
    return json.loads(run.stdout.strip())


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / COMMIT
    root.mkdir()
    files = {}
    def put(name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        files[name] = digest(data)
        return path
    python = tmp_path / "synthetic-python.exe"
    python.write_bytes(b"synthetic interpreter; never executed")
    for name in CODE:
        put("tools-bootstrap/" + name, b"# synthetic code; never imported\n")
    wheel = b"synthetic wheel; never installed"
    put("tools-bootstrap/wheels/fake-1.0-py3-none-any.whl", wheel)
    put("tools-bootstrap/site/fake/__init__.py", b"# synthetic\n")
    definition = {
        "schema": "wd.bridge-code-package.v1", "package_relative_root": "tools-bootstrap",
        "invocation_wrapper_relative": "Invoke-WdBridgePython.ps1",
        "python_files": CODE, "python_entrypoints": {"query": CODE[1]},
        "python_requirements": [{"name": "fake", "version": "1.0",
            "wheel": "fake-1.0-py3-none-any.whl", "sha256": digest(wheel),
            "import_path": "fake/__init__.py"}],
        "wheel_store_relative": "wheels", "python_site_relative": "site",
        "import_smoke": {"third_party_module": "fake", "package_modules": ["tools.wd_q4_agent_query"]},
        "isolation_environment": {"PYTHONDONTWRITEBYTECODE": "1",
                                 "PYTHONNOUSERSITE": "1", "PYTHONSAFEPATH": "1"},
    }
    put("bridge-code-files.json", json.dumps(definition).encode())
    put("BridgeCodeContext.ps1", (OPS / "BridgeCodeContext.ps1").read_bytes())
    put("Get-WdQ4ReadConfig.ps1", (OPS / "Get-WdQ4ReadConfig.ps1").read_bytes())
    put("Invoke-WdBridgePython.ps1", b"# synthetic; never run\n")
    # A missing Source wrapper is the intended pre-implementation RED.
    assert WRAPPER.is_file(), "tests-first RED: Q4 wrapper is missing"
    put("Invoke-WdQ4Query.ps1", WRAPPER.read_bytes())
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    runtime_manifest = tmp_path / "runtime-manifest.json"
    runtime_manifest.write_bytes(b"synthetic manifest; not executed")
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_bytes(b"synthetic descriptor; not executed")
    memory = tmp_path / "memory"
    memory.mkdir()
    cfg = {
        "schema": "wd.q4-read-config.v1", "enabled": True, "code_commit": COMMIT,
        "memory_root": str(memory), "runtime_root": str(runtime),
        "runtime_generation": "b" * 40, "runtime_revision": "c" * 64,
        "descriptor_path": str(descriptor), "descriptor_sha256": digest(descriptor.read_bytes()),
        "python_path": str(python), "python_sha256": digest(python.read_bytes()),
        "runtime_manifest_path": str(runtime_manifest),
        "runtime_manifest_sha256": digest(runtime_manifest.read_bytes()),
        "code_manifest_path": str(root / "bridge-code-files.json"),
        "code_manifest_sha256": files["bridge-code-files.json"],
        "lock_file": str(tmp_path / "common-reader.lock"), "lock_timeout_seconds": 0.25,
    }
    put("wd-fleet.json", json.dumps({"q4_read": cfg}).encode())
    state = {"root": root, "files": files, "python": python, "config": cfg}
    def seal():
        manifest = {"schema_version": 1, "source_commit": COMMIT, "files": files,
                    "bridge_code_package": {"python_executable": str(python),
                                           "python_executable_sha256": digest(python.read_bytes())}}
        data = json.dumps(manifest).encode()
        (root / "deployment-manifest.json").write_bytes(data)
        state["pin"] = digest(data)
    state["seal"] = seal
    state["put"] = put
    seal()
    return state


def plan_call(bundle):
    return ("Get-WdQ4QueryPlan -BundleRoot " + quote(bundle["root"]) +
            " -ExpectedBundleCommit " + quote(COMMIT) +
            " -ExpectedManifestSha256 " + quote(bundle["pin"]))


@pytest.mark.parametrize("host", HOSTS)
def test_missing_anchor_refuses_before_reading_or_launching(host):
    script = r"""
function Read-WdQ4QueryBytes { throw 'read_called' }
function Invoke-WdQ4QueryTransport { throw 'launch_called' }
$env:WD_Q4_READ_ENABLED='1'
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='f'*64
$v=Invoke-WdQ4Query
$v|ConvertTo-Json -Depth 8 -Compress
"""
    result = run_ps(host, script)
    assert result["status"] == "REFUSED"
    assert result["reason"] == "external_anchor_unavailable"


@pytest.mark.parametrize("host", HOSTS)
def test_anchor_and_tuple_are_derived_without_starting_a_process(bundle, host):
    result = run_ps(host, "$p=" + plan_call(bundle) +
                    "\n$p|ConvertTo-Json -Depth 10 -Compress")
    assert result["Enabled"] is True
    cfg = result["Config"]
    assert cfg["runtime_generation"] == "b" * 40
    assert cfg["runtime_revision"] == "c" * 64
    assert cfg["interpreter_sha256"] == digest(bundle["python"].read_bytes())
    assert cfg["lock_file"] == bundle["config"]["lock_file"]


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("name", [
    "deployment-manifest.json", "BridgeCodeContext.ps1", "Get-WdQ4ReadConfig.ps1",
    "Invoke-WdQ4Query.ps1", "wd-fleet.json", "bridge-code-files.json",
    "tools-bootstrap/tools/wd_q4_agent_query.py",
    "tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py",
])
def test_every_mutated_trust_input_refuses_before_query(bundle, host, name):
    with (bundle["root"] / name).open("ab") as out:
        out.write(b"\nmutation")
    script = "function Read-WdQ4QueryBytes { throw 'read_called' }\n"
    script += "function Invoke-WdQ4QueryTransport { throw 'launch_called' }\n"
    script += "$v=Invoke-WdQ4Query -BundleRoot " + quote(bundle["root"])
    script += " -ExpectedBundleCommit " + quote(COMMIT)
    script += " -ExpectedManifestSha256 " + quote(bundle["pin"])
    script += "\n$v|ConvertTo-Json -Depth 8 -Compress"
    result = run_ps(host, script)
    assert result["status"] == "REFUSED"
    assert result["reason"] == "binding_refused"


@pytest.mark.parametrize("host", HOSTS)
def test_disabled_anchored_fleet_never_reads_query_or_launches(bundle, host):
    bundle["put"]("wd-fleet.json", b'{"q4_read":{"enabled":false}}')
    bundle["seal"]()
    script = "function Read-WdQ4QueryBytes { throw 'read_called' }\n"
    script += "function Invoke-WdQ4QueryTransport { throw 'launch_called' }\n"
    script += "$v=Invoke-WdQ4Query -BundleRoot " + quote(bundle["root"])
    script += " -ExpectedBundleCommit " + quote(COMMIT)
    script += " -ExpectedManifestSha256 " + quote(bundle["pin"])
    script += "\n$v|ConvertTo-Json -Depth 8 -Compress"
    assert run_ps(host, script)["reason"] == "disabled"


@pytest.mark.parametrize("host", HOSTS)
def test_unlisted_code_file_is_not_accepted(bundle, host):
    (bundle["root"] / "tools-bootstrap/tools/unlisted.py").write_bytes(b"# foreign\n")
    result = run_ps(host, "$v=try{$p=" + plan_call(bundle) +
                    ";@{ok=$true}}catch{@{ok=$false}}; $v|ConvertTo-Json -Compress")
    assert result["ok"] is False


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("payload", ['{"situation":"query-sensitive","time":"t"}', "oversize"])
def test_query_bytes_are_bounded_and_transport_is_a_stdin_seam(host, payload):
    data = base64.b64encode(payload.encode()).decode()
    script = r"""
function Get-WdQ4QueryPlan { [pscustomobject]@{Enabled=$true;Config=@{descriptor_sha256=('7'*64)}} }
function Invoke-WdQ4QueryTransport {
 param($Plan,[byte[]]$Query)
 if($Query.Length -gt 65536){throw 'over_bound'}
 [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='ABSTAIN';scope='wd.q4-shared-projected-events.v1';
 descriptor_sha256=('7'*64);memory_sha256=('8'*64);results=@()}
}
"""
    script += ("$raw=New-Object byte[] 65537\n" if payload == "oversize" else
               "$raw=[Convert]::FromBase64String(" + quote(data) + ")\n")
    script += "$stream=[IO.MemoryStream]::new([byte[]]$raw)\n"
    script += "$v=Invoke-WdQ4Query -InputStream $stream\n$v|ConvertTo-Json -Depth 8 -Compress"
    result = run_ps(host, script)
    assert result["status"] == ("REFUSED" if payload == "oversize" else "ABSTAIN")


@pytest.mark.parametrize("host", HOSTS)
def test_query_is_not_in_plan_arguments_or_environment(host):
    script = r"""
function Get-WdQ4QueryPlan { [pscustomobject]@{Enabled=$true;Config=@{descriptor_sha256=('7'*64)}} }
function Invoke-WdQ4QueryTransport {
 param($Plan,[byte[]]$Query)
 if([Text.Encoding]::UTF8.GetString($Query) -notlike '*query-sensitive*'){throw 'stdin_missing'}
 if(($Plan|ConvertTo-Json -Depth 9) -like '*query-sensitive*'){throw 'plan_leaked'}
 foreach($v in [Environment]::GetEnvironmentVariables().Values){
  if([string]$v -like '*query-sensitive*'){throw 'env_leaked'}
 }
 [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='ABSTAIN';scope='wd.q4-shared-projected-events.v1';
 descriptor_sha256=('7'*64);memory_sha256=('8'*64);results=@()}
}
$raw=[Text.Encoding]::UTF8.GetBytes('{"situation":"query-sensitive","time":"t"}')
$stream=[IO.MemoryStream]::new([byte[]]$raw)
Invoke-WdQ4Query -InputStream $stream|ConvertTo-Json -Depth 8 -Compress
"""
    assert run_ps(host, script)["status"] == "ABSTAIN"


def test_transport_source_has_isolation_bounds_and_no_query_parameter():
    assert WRAPPER.is_file(), "tests-first RED: Q4 wrapper is missing"
    text = WRAPPER.read_text(encoding="utf-8")
    assert "'-I', '-S', '-B'" in text
    assert "65536" in text and "262144" in text
    assert "query_with_verified_config" in text
    assert "Assert-WdBridgeCodePackageIntegrity" in text
    assert "$env:WD_Q4" not in text
    assert "-Query " not in text


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("field,value", [
    ("status", ["ANSWER"]), ("memory_sha256", ["8" * 64]),
    ("memory_sha256", 888), ("descriptor_sha256", "0" * 64),
    ("scope", "wd.q4-private-memory.v1"), ("results", "not-an-array"),
])
def test_unknown_result_types_and_bindings_fail_closed(host, field, value):
    mutation = base64.b64encode(json.dumps({field: value}).encode()).decode()
    script = r"""
function Get-WdQ4QueryPlan { [pscustomobject]@{Enabled=$true;Config=@{descriptor_sha256=('7'*64)}} }
function Invoke-WdQ4QueryTransport {
 param($Plan,[byte[]]$Query)
 $v=[pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='ABSTAIN';scope='wd.q4-shared-projected-events.v1';
 descriptor_sha256=('7'*64);memory_sha256=('8'*64);results=@()}
 foreach($p in $script:mutation.PSObject.Properties){$v.($p.Name)=$p.Value}
 $v
}
"""
    script += "$script:mutation=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(" + quote(mutation) + "))|ConvertFrom-Json\n"
    script += "$s=[IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes('{}'))\n"
    script += "Invoke-WdQ4Query -InputStream $s|ConvertTo-Json -Depth 8 -Compress"
    assert run_ps(host, script)["status"] == "REFUSED"


@pytest.mark.parametrize("host", HOSTS)
def test_binary_stdin_keeps_utf8_bytes_exactly_and_rejects_oversize(host):
    script = r"""
$raw=[byte[]](0x7b,0x22,0x78,0x22,0x3a,0x22,0xc3,0xa4,0x22,0x7d)
$s=[IO.MemoryStream]::new($raw)
$actual=Read-WdQ4QueryBytes -InputStream $s
$v=@{same=([Convert]::ToBase64String($actual) -ceq [Convert]::ToBase64String($raw));count=$actual.Length}
$v|ConvertTo-Json -Compress
"""
    result = run_ps(host, script)
    assert result == {"same": True, "count": 10}


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("value", ["", "space path", 'a"b', "end\\", "with space\\"])
def test_windows_crt_argument_quoting_matches_reference(host, value):
    data = base64.b64encode(value.encode()).decode()
    script = "$s=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(" + quote(data) + "))\n"
    script += "@{value=(ConvertTo-WdQ4Argument $s)}|ConvertTo-Json -Compress"
    # Python's CRT formatter leaves bare quote-only arguments unquoted, while
    # the wrapper deliberately quotes them. Check the established wrapper form.
    if value and not any(c.isspace() or c == '"' for c in value):
        expected = value
    else:
        import re
        expected = '"' + re.sub(r'(\\+)$', r'\1\1',
                                 re.sub(r'(\\*)"', r'\1\1\\"', value)) + '"'
    assert run_ps(host, script)["value"] == expected


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("mode", ["answer", "invalid-json", "oversize", "timeout", "array-json", "duplicate-json"])
def test_real_transport_uses_only_a_synthetic_stdlib_child(tmp_path, host, mode):
    # Unit seam only: no trust claim is made by directly supplying a Plan.
    import sys
    root = tmp_path / "synthetic-code"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "__init__.py").write_text("", encoding="utf-8")
    implementation = """import sys,time
class VerifiedQ4Config:
 def __init__(self,**values): self.values=values
def query_with_verified_config(config,stream):
 raw=stream.read(65537)
 assert b'query-sensitive' in raw
 assert all('query-sensitive' not in a for a in sys.argv)
 return {'status':'ABSTAIN','scope':'wd.q4-shared-projected-events.v1',
         'descriptor_sha256':'7'*64,'memory_sha256':'8'*64,'results':[]}
"""
    if mode == "invalid-json":
        implementation = "print('not json')\n" + implementation
    if mode == "oversize":
        implementation = "print('x'*300000)\n" + implementation
    if mode == "timeout":
        implementation = "import time\ntime.sleep(5)\n" + implementation
    payload = {"schema": "wd.q4-agent-wrapper.v1", "status": "ABSTAIN",
               "scope": "wd.q4-shared-projected-events.v1",
               "descriptor_sha256": "7" * 64, "memory_sha256": "8" * 64, "results": []}
    if mode == "array-json":
        implementation = "import sys\nprint(" + repr(json.dumps([payload])) + ")\nsys.exit(0)\n"
    if mode == "duplicate-json":
        encoded = json.dumps(payload).replace('"status": "ABSTAIN"',
                                             '"status": "ABSTAIN", "status": "ABSTAIN"')
        implementation = "import sys\nprint(" + repr(encoded) + ")\nsys.exit(0)\n"
    (tools / "wd_q4_agent_query.py").write_text(implementation, encoding="utf-8")
    script = "$script:plan=[pscustomobject]@{Enabled=$true;CodeRoot=" + quote(root)
    script += ";Config=@{interpreter=" + quote(sys.executable)
    script += ";timeout=1.5;descriptor_sha256=('7'*64)}}\n"
    script += r"""
function Get-WdQ4QueryPlan { $script:plan }
$raw=[Text.Encoding]::UTF8.GetBytes('{"situation":"query-sensitive","time":"t"}')
$s=[IO.MemoryStream]::new([byte[]]$raw)
Invoke-WdQ4Query -InputStream $s|ConvertTo-Json -Depth 8 -Compress
"""
    result = run_ps(host, script)
    assert result["status"] == ("ABSTAIN" if mode == "answer" else "REFUSED")


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("canonical", [None, 0, True, False])
def test_noncanonical_result_declaration_is_strict_bool(host, canonical):
    encoded = base64.b64encode(json.dumps(canonical).encode()).decode()
    script = r"""
function Get-WdQ4QueryPlan { [pscustomobject]@{Enabled=$true;Config=@{descriptor_sha256=('7'*64)}} }
function Invoke-WdQ4QueryTransport {
 param($Plan,[byte[]]$Query)
 [pscustomobject]@{schema='wd.q4-agent-wrapper.v1';status='ANSWER';scope='wd.q4-shared-projected-events.v1';
 descriptor_sha256=('7'*64);memory_sha256=('8'*64);results=@(
  [pscustomobject]@{ref_sha256=('9'*64);doc_id_sha256=('a'*64);
   ref_kind='sha256_of_projected_metadata';canonical_event=$script:canonical})}
}
"""
    script += "$script:canonical=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(" + quote(encoded) + "))|ConvertFrom-Json\n"
    script += "$s=[IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes('{}'))\n"
    script += "Invoke-WdQ4Query -InputStream $s|ConvertTo-Json -Depth 8 -Compress"
    assert run_ps(host, script)["status"] == ("ANSWER" if canonical is False else "REFUSED")



@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize("change", [
    {"schema": None}, {"schema": "other"}, {"extra": "unreported-form"},
    {"status": ["REFUSED"]}, {"scope": ["wd.q4-shared-projected-events.v1"]},
    {"descriptor_sha256": ["7" * 64]}, {"results": None},
    {"status": "ANSWER", "results": []},
])
def test_outer_boundary_owns_complete_schema_even_with_transport_replaced(host, change):
    data = base64.b64encode(json.dumps(change).encode()).decode()
    script = r"""
function Get-WdQ4QueryPlan { [pscustomobject]@{Enabled=$true;Config=@{descriptor_sha256=('7'*64)}} }
function Invoke-WdQ4QueryTransport {
 param($Plan,[byte[]]$Query)
 $v=[ordered]@{schema='wd.q4-agent-wrapper.v1';status='ABSTAIN';
 scope='wd.q4-shared-projected-events.v1';descriptor_sha256=('7'*64);memory_sha256=('8'*64);results=@()}
 foreach($p in $script:mutation.PSObject.Properties){$v[$p.Name]=$p.Value}
 [pscustomobject]$v
}
"""
    script += "$script:mutation=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(" + quote(data) + "))|ConvertFrom-Json\n"
    script += "$s=[IO.MemoryStream]::new([Text.Encoding]::UTF8.GetBytes('{}'))\n"
    script += "Invoke-WdQ4Query -InputStream $s|ConvertTo-Json -Depth 8 -Compress"
    assert run_ps(host, script)["status"] == "REFUSED"
