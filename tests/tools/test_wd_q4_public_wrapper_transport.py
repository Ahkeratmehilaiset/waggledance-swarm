"""Synthetic public wrapper validation; no live store or model."""
import json
import hashlib
import importlib.util
import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "ops/windows/reboot/Invoke-WdQ4Query.ps1"


def validate(value, *, pointer="a" * 64):
    encoded = json.dumps(value, separators=(",", ":"))
    literal = encoded.replace("'", "''")
    wrapper = str(WRAPPER).replace("'", "''")
    script = (
        f". '{wrapper}'\n"
        f"$v=ConvertFrom-WdQ4StrictJson -Text '{literal}'\n"
        f"$p=@{{pointer_sha256='{pointer}';source_anchor_sha256=('b'*64);"
        "encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);"
        "public_policy_sha256=('f'*64);descriptor_sha256=('1'*64)}\n"
        "try {ConvertFrom-WdQ4PublicResult -Result $v -Expected $p | ConvertTo-Json -Depth 8 -Compress} "
        "catch {'{\"status\":\"REFUSED\"}'}"
    )
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def envelope():
    binding = dict(pointer_sha256="a" * 64, source_anchor_sha256="b" * 64,
                   encoder_sha256="c" * 64, policy_sha256="d" * 64,
                   roster_sha256="e" * 64, public_policy_sha256="f" * 64,
                   descriptor_sha256="1" * 64, memory_sha256="2" * 64,
                   projection_sha256="3" * 64, receipt_sha256="4" * 64)
    return dict(schema="wd.q4-public-query.v2", status="ANSWER", binding=binding,
                publisher_authentication="UNKNOWN", source_filter_attestation="UNKNOWN",
                freshness="UNKNOWN", same_user_aba="UNKNOWN",
                results=[dict(stored_text="PRIVATE_EXAMPLE", ref_sha256="5" * 64,
                              doc_id_sha256="6" * 64, ref_kind="sha256_of_projected_metadata",
                              canonical_event=False)])


def test_public_envelope_is_bound_and_preserves_public_projection():
    result = validate(envelope())
    assert result["status"] == "ANSWER"
    assert result["results"][0]["stored_text"] == "PRIVATE_EXAMPLE"
    assert result["source_filter_attestation"] == "UNKNOWN"
    assert result["results"][0]["ref_sha256"] == "5" * 64


@pytest.mark.parametrize("mutation", ["pointer", "unknown_claim", "extra_field", "event"])
def test_public_envelope_refuses_unbound_or_misleading_output(mutation):
    value = envelope()
    if mutation == "pointer":
        value["binding"]["pointer_sha256"] = "0" * 64
    elif mutation == "unknown_claim":
        value["freshness"] = "verified"
    elif mutation == "extra_field":
        value["secret"] = "PRIVATE_EXAMPLE"
    else:
        value["results"][0]["canonical_event"] = True
    assert validate(value)["status"] == "REFUSED"


def test_public_transport_sends_query_and_policies_only_in_framed_stdin():
    result = json.dumps(envelope(), separators=(",", ":"))
    helper = b"synthetic helper"
    digest = hashlib.sha256(helper).hexdigest()
    wrapper = str(WRAPPER).replace("'", "''")
    result_literal = result.replace("'", "''")
    script = f"""
. '{wrapper}'
$script:WdQ4PinnedJobHash='{digest.upper()}'
$script:WdQ4PinnedJobBlock={{
  param($Executable,$Arguments,$WorkingDirectory,$ScratchRoot,$InputBytes,$TimeoutSeconds,$MaxOutputBytes)
  if (($Arguments -join '|') -match 'SECRET_QUERY|SECRET_POLICY|SECRET_ROSTER|SECRET_PUBLIC') {{throw 'input_in_argv'}}
  $argumentText=$Arguments -join '|'
  if ($Arguments.Count -ne 5 -or $argumentText.Contains('C:\\code') -or $argumentText.Contains('C:\\public') -or
      $argumentText.Contains('C:\\python.exe') -or $argumentText.Contains('aaaaaaaaaaaaaaaa')) {{throw 'metadata_in_argv'}}
  $position=0
  $h=[byte[]]$InputBytes[$position..($position+3)];[Array]::Reverse($h);$n=[BitConverter]::ToUInt32($h,0);$position+=4
  $meta=ConvertFrom-Json ([Text.Encoding]::UTF8.GetString($InputBytes,$position,$n));$position+=$n
  if($meta.code_root -cne 'C:\\code' -or $meta.root -cne 'C:\\public' -or $meta.pointer_sha256 -cne ('a'*64)) {{throw 'bad_metadata'}}
  foreach($s in @('SECRET_POLICY','SECRET_ROSTER','SECRET_PUBLIC','SECRET_QUERY')) {{
    $h=[byte[]]$InputBytes[$position..($position+3)];[Array]::Reverse($h);$n=[BitConverter]::ToUInt32($h,0);$position+=4
    if ([Text.Encoding]::UTF8.GetString($InputBytes,$position,$n) -cne $s) {{throw 'bad_frame'}}
    $position+=$n
  }}
  if($position -ne $InputBytes.Length) {{throw 'bad_frame'}}
  return @{{ExitCode=0;Stdout=[Text.Encoding]::UTF8.GetBytes('{result_literal}')}}
}}
$base=@{{CodeRoot='C:\\code';Config=@{{timeout=30;interpreter='C:\\python.exe';scratch_root='C:\\scratch';verifier_sha256=('7'*64);descriptor_sha256=('1'*64)}};
  JobHelperBytes=[Text.Encoding]::UTF8.GetBytes('synthetic helper');JobHelperSha256='{digest}';
  PublicPins=@{{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}}}
$doc=@{{pointer_sha256=('a'*64);source_anchor_sha256=('b'*64);encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);public_policy_sha256=('f'*64)}}
$plan=@{{Base=$base;Document=$doc;Root='C:\\public';Inputs=@{{policy=[Text.Encoding]::UTF8.GetBytes('SECRET_POLICY');roster=[Text.Encoding]::UTF8.GetBytes('SECRET_ROSTER');public_policy=[Text.Encoding]::UTF8.GetBytes('SECRET_PUBLIC')}}}}
Invoke-WdQ4PublicTransport -Plan $plan -Query ([Text.Encoding]::UTF8.GetBytes('SECRET_QUERY')) | ConvertTo-Json -Depth 8 -Compress
"""
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "ANSWER"


def test_public_plan_requires_independent_plan_and_policy_pins(tmp_path):
    root = tmp_path / "public"
    root.mkdir()
    policies = {}
    for name in ("policy", "roster", "public_policy"):
        path = tmp_path / (name + ".json")
        data = b'{}'
        path.write_bytes(data)
        policies[name + "_path"] = str(path)
        policies[name + "_sha256"] = hashlib.sha256(data).hexdigest()
    document = dict(schema="wd.q4-public-wrapper-plan.v1", root=str(root), config={},
                    pointer_sha256="a" * 64, source_anchor_sha256="b" * 64,
                    encoder_sha256="c" * 64, **policies)
    raw = json.dumps(document, separators=(",", ":")).encode()
    plan_path = tmp_path / "plan.json"
    plan_path.write_bytes(raw)
    quote = lambda p: str(p).replace("'", "''")
    script = f"""
. '{quote(WRAPPER)}'
$base=@{{Enabled=$true;PublicPins=@{{'tools/wd_q4_public_outer.py'=('1'*64);'tools/wd_q4_public_child.py'=('2'*64);'tools/wd_q4_public_query.py'=('3'*64)}}}}
$good=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(plan_path)}' -ExpectedSha256 '{hashlib.sha256(raw).hexdigest()}'
$wrong=try {{$null=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(plan_path)}' -ExpectedSha256 ('0'*64);'accepted'}}catch{{'refused'}}
[IO.File]::WriteAllText('{quote(policies['policy_path'])}','{{"changed":true}}')
$tampered=try {{$null=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(plan_path)}' -ExpectedSha256 '{hashlib.sha256(raw).hexdigest()}';'accepted'}}catch{{'refused'}}
@{{good=($good.Inputs.policy.Length -eq 2);wrong=$wrong;tampered=$tampered}} | ConvertTo-Json -Compress
"""
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"good": True, "wrong": "refused", "tampered": "refused"}


def test_real_process_job_calls_fixed_outer_and_public_child_with_framed_input(tmp_path):
    """Synthetic outer/child; tests the actual Windows process and stdin transport only."""
    root = tmp_path / "code"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "__init__.py").write_text("", encoding="utf-8")
    (tools / "wd_q4_agent_query.py").write_text(
        "class VerifiedQ4Config:\n def __init__(self,**kwargs): self.kwargs=kwargs\n", encoding="utf-8")
    output = envelope()
    (tools / "wd_q4_public_outer.py").write_text(
        "import json,sys,types\n"
        "def bootstrap_public_imports(**kwargs):\n"
        " assert kwargs['enabled'] is True\n"
        " def run_public(config,stream,stdout,**pins):\n"
        "  assert stream.read()==b'query-secret'\n"
        "  assert pins['policy_bytes']==b'policy-secret'\n"
        "  assert pins['roster_bytes']==b'roster-secret'\n"
        "  assert pins['public_policy_bytes']==b'public-secret'\n"
        "  assert len(sys.argv)==1 and all('secret' not in item for item in sys.argv)\n"
        "  stdout.write(json.dumps(" + repr(output) + "));stdout.flush();return 0\n"
        " return {'status':'MODULES_IMPORTED','child':types.SimpleNamespace(run_public=run_public)}\n",
        encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    helper = ROOT / "ops/windows/reboot/WdQ4ProcessJob.ps1"
    quote = lambda p: str(p).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{quote(WRAPPER)}'
$h=[IO.File]::ReadAllBytes('{quote(helper)}');$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash($h)).Replace('-','');$sha.Dispose()
$base=@{{CodeRoot='{quote(root)}';JobHelperBytes=$h;JobHelperSha256=$hash;
 Config=@{{interpreter='{quote(sys.executable)}';timeout=10;scratch_root='{quote(scratch)}';verifier_sha256=('7'*64);descriptor_sha256=('1'*64);runtime_root='C:\\r';runtime_manifest='C:\\m';runtime_manifest_sha256=('8'*64);runtime_generation=('9'*40);runtime_revision=('8'*40)}};
 PublicPins=@{{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}}}
$doc=@{{pointer_sha256=('a'*64);source_anchor_sha256=('b'*64);encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);public_policy_sha256=('f'*64)}}
$plan=@{{Base=$base;Document=$doc;Root='C:\\public';Inputs=@{{policy=[Text.Encoding]::UTF8.GetBytes('policy-secret');roster=[Text.Encoding]::UTF8.GetBytes('roster-secret');public_policy=[Text.Encoding]::UTF8.GetBytes('public-secret')}}}}
$result=Invoke-WdQ4PublicTransport -Plan $plan -Query ([Text.Encoding]::UTF8.GetBytes('query-secret'))
$result | ConvertTo-Json -Depth 8 -Compress
"""
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=45)
    assert run.returncode == 0, (run.stdout, run.stderr)
    assert "secret" not in run.stdout + run.stderr
    assert json.loads(run.stdout)["status"] == "ANSWER"


@pytest.mark.parametrize("kind", ["preflight", "disabled"])
def test_public_preflight_refusal_always_uses_public_schema(kind):
    wrapper = str(WRAPPER).replace("'", "''")
    behavior = "throw 'external_anchor_unavailable'" if kind == "preflight" else "return @{Enabled=$false}"
    script = f"""
. '{wrapper}'
function Get-WdQ4QueryPlan {{{behavior}}}
$result=Invoke-WdQ4Query -Public
$result|ConvertTo-Json -Depth 8 -Compress
"""
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    value = json.loads(run.stdout)
    assert value["schema"] == "wd.q4-public-query.v2"
    assert value["status"] == "REFUSED"


def test_oversized_public_metadata_refuses_before_process_start():
    wrapper = str(WRAPPER).replace("'", "''")
    script = f"""
. '{wrapper}'
$base=@{{CodeRoot='C:\\code';Config=@{{}};PublicPins=@{{}}}}
$plan=@{{Base=$base;Document=@{{}};Root=('x'*65537);Inputs=@{{}}}}
$script:WdQ4PinnedJobBlock={{throw 'process_started'}}
$result=try {{Invoke-WdQ4PublicTransport $plan ([byte[]]@(1));'accepted'}} catch {{$_.Exception.Message}}
@{{status=$result}}|ConvertTo-Json -Compress
"""
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["status"] == "binding_refused"


def test_public_binding_does_not_read_or_enable_legacy_config():
    wrapper = str(WRAPPER).replace("'", "''")
    script = f"""
$ErrorActionPreference='Stop'
. '{wrapper}'
function Get-WdQ4ReadConfig {{throw 'legacy_config_was_called'}}
$c=@{{interpreter='C:\\python.exe';interpreter_sha256=('1'*64);bootstrap='C:\\code\\sandbox\\mind\\step2\\runtime_bootstrap.py';
bootstrap_sha256=('2'*64);verifier_sha256=('3'*64);runtime_root='C:\\runtime';runtime_manifest='C:\\manifest.json';runtime_manifest_sha256=('4'*64);
runtime_generation=('a'*40);runtime_revision=('b'*40);memory='C:\\index\\gen';descriptor='C:\\index\\gen\\descriptor.json';descriptor_sha256=('5'*64);
lock_file='C:\\lock';lock_timeout='1';timeout=30;scratch_root='C:\\scratch'}}
$d=[pscustomobject]@{{config=[pscustomobject]$c}}
try {{$v=Get-WdQ4PublicBinding -Document $d;@{{status='ok';memory=$v.WD_Q4_MEMORY_ROOT;legacy=($null -ne $v.PSObject.Properties['WD_Q4_READ_ENABLED'])}}|ConvertTo-Json -Compress}}
catch {{'{{"status":"REFUSED"}}'}}
"""
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"status": "ok", "memory": r"C:\index\gen", "legacy": False}


def test_public_package_plan_works_with_disabled_legacy_fleet(tmp_path):
    """Actual bundle verifier with synthetic packaged bytes; no child or model launch."""
    spec = importlib.util.spec_from_file_location("synthetic_legacy_wrapper_tests", ROOT / "tests/tools/test_wd_q4_agent_wrapper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = module.bundle.__wrapped__(tmp_path)
    package_path = bundle["root"] / "bridge-code-files.json"
    package = json.loads(package_path.read_text())
    for relative in ("tools/wd_q4_public_outer.py", "tools/wd_q4_public_child.py", "tools/wd_q4_public_query.py"):
        package["python_files"].append(relative)
        bundle["put"]("tools-bootstrap/" + relative, b"# synthetic; never run\n")
    bundle["put"]("bridge-code-files.json", json.dumps(package).encode())
    bundle["put"]("wd-fleet.json", json.dumps({"q4_read": {"enabled": False}}).encode())
    bundle["seal"]()
    cfg = bundle["config"]
    code_root = bundle["root"] / "tools-bootstrap"
    config = dict(interpreter=cfg["python_path"], interpreter_sha256=cfg["python_sha256"],
                  bootstrap=str(code_root / "sandbox/mind/step2/runtime_bootstrap.py"),
                  bootstrap_sha256=bundle["files"]["tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py"],
                  verifier_sha256=bundle["files"]["tools-bootstrap/sandbox/mind/step2/runtime_manifest.py"],
                  runtime_root=cfg["runtime_root"], runtime_manifest=cfg["runtime_manifest_path"],
                  runtime_manifest_sha256=cfg["runtime_manifest_sha256"], runtime_generation=cfg["runtime_generation"],
                  runtime_revision=cfg["runtime_revision"], memory=cfg["memory_root"],
                  descriptor=cfg["descriptor_path"], descriptor_sha256=cfg["descriptor_sha256"],
                  lock_file=cfg["lock_file"], lock_timeout="0.25", timeout=30,
                  scratch_root=cfg["scratch_root"])
    document = dict(config=config, schema="wd.q4-public-wrapper-plan.v1", root=cfg["memory_root"],
                    pointer_sha256="a" * 64, source_anchor_sha256="b" * 64, encoder_sha256="c" * 64)
    for name in ("policy", "roster", "public_policy"):
        path = tmp_path / (name + ".json")
        path.write_bytes(b"{}")
        document[name + "_path"] = str(path)
        document[name + "_sha256"] = hashlib.sha256(b"{}").hexdigest()
    raw = json.dumps(document, separators=(",", ":")).encode()
    plan_path = tmp_path / "public-plan.json"
    plan_path.write_bytes(raw)
    q = lambda x: "'" + str(x).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'\n. " + q(WRAPPER) + "\n"
              "try {$p=Get-WdQ4QueryPlan -BundleRoot " + q(bundle["root"]) +
              " -ExpectedBundleCommit " + q(module.COMMIT) + " -ExpectedManifestSha256 " + q(bundle["pin"]) +
              " -Public -PublicPlanPath " + q(plan_path) + " -ExpectedPublicPlanSha256 " + q(hashlib.sha256(raw).hexdigest()) +
              ";@{status='ok';enabled=$p.Enabled;outer=$p.PublicPins['tools/wd_q4_public_outer.py']}|ConvertTo-Json -Compress}"
              "catch {@{status='refused';reason=$_.Exception.Message}|ConvertTo-Json -Compress}")
    run = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout)
    assert result == {"status": "ok", "enabled": True,
                      "outer": bundle["files"]["tools-bootstrap/tools/wd_q4_public_outer.py"]}
