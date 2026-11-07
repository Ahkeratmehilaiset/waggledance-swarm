"""Content snapshot plan v4 through the Windows query wrapper (lead-q4-useful-work-content-fable-20261107-v1, 7b2).

Route B, Lead 20:06Z: the plan pins the policy, roster and content route decision files and the encoder, memory and
projection digests. ``Get-WdQ4PublicPlan`` reads the three files by their pins, ``Invoke-WdQ4PublicTransport`` frames
them to the public child's ``run_content`` inside the real Windows Job (the child here is synthetic), and
``ConvertFrom-WdQ4ContentResult`` accepts only a closed reply carrying every pin. Other plan schemas are unchanged.
"""
import hashlib
import json
import sys

import pytest

from test_lane_card_decision_forward import BASE, WRAPPER, ROOT, ps, quote, windows

PLAN_SCHEMA = "wd.q4-public-wrapper-plan.v4"
PINS = {"encoder_sha256": "c" * 64, "memory_sha256": "2" * 64, "projection_sha256": "3" * 64}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_file(path, data):
    path.write_bytes(data)
    return str(path), digest(data)


def content_plan(tmp_path, **changes):
    plan = dict(PINS, schema=PLAN_SCHEMA, config={"descriptor_sha256": "1" * 64})
    for name, data in (("policy", b"policy-bytes"), ("roster", b"roster-bytes"), ("content_decision", b"decision")):
        plan[name + "_path"], plan[name + "_sha256"] = write_file(tmp_path / (name + ".json"), data)
    plan.update(changes)
    plan = {key: value for key, value in plan.items() if value is not ...}
    path, pin = write_file(tmp_path / "plan.json", json.dumps(plan, separators=(",", ":")).encode())
    return path, pin, plan


def load(path, pin):
    return (f". '{quote(WRAPPER)}'\n" + BASE +
            f"$plan=try {{Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}'}}"
            "catch {$null}\n")


@windows
def test_a_content_plan_loads_its_three_pinned_inputs(tmp_path):
    path, pin, plan = content_plan(tmp_path)
    script = load(path, pin) + (
        "@{content=$plan.Content;staged=$plan.Staged;names=(@($plan.Inputs.Keys)|Sort-Object) -join ',';"
        "decision=[Text.Encoding]::ASCII.GetString($plan.Inputs.content_decision)} | ConvertTo-Json -Compress")
    assert ps(script) == {"content": True, "staged": False, "names": "content_decision,policy,roster",
                          "decision": "decision"}


@windows
@pytest.mark.parametrize("change", [
    {"root": "C:\\public"},
    {"memory_sha256": ...},
    {"projection_sha256": "x" * 64},
    {"encoder_sha256": "C" * 64},
    {"content_decision_sha256": "0" * 64},
    {"content_decision_path": ""},
    {"card_decision_sha256": "9" * 64},
    {"schema": "wd.q4-public-wrapper-plan.v5"},
])
def test_a_partial_tampered_or_extended_content_plan_is_refused(tmp_path, change):
    path, pin, _ = content_plan(tmp_path, **change)
    assert ps(load(path, pin) + "@{loaded=($null -ne $plan)} | ConvertTo-Json -Compress") == {"loaded": False}


@windows
def test_a_changed_decision_file_is_refused(tmp_path):
    path, pin, plan = content_plan(tmp_path)
    script = load(path, pin) + (
        f"[IO.File]::WriteAllText('{quote(plan['content_decision_path'])}','changed')\n"
        f"$again=try {{$null=Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}';'accepted'}}"
        "catch{'refused'}\n@{first=($null -ne $plan);again=$again} | ConvertTo-Json -Compress")
    assert ps(script) == {"first": True, "again": "refused"}


def binding(**changes):
    value = dict(source_mode="content-snapshot-v1", encoder_sha256="c" * 64, policy_sha256="d" * 64,
                 roster_sha256="e" * 64, content_decision_sha256="9" * 64, descriptor_sha256="1" * 64,
                 memory_sha256="2" * 64, projection_sha256="3" * 64)
    value.update(changes)
    return value


def reply(**changes):
    value = dict(schema="wd.q4-content-snapshot-query.v1", status="ANSWER", binding=binding(),
                 publisher_authentication="UNKNOWN", source_filter_attestation="UNKNOWN", freshness="UNKNOWN",
                 same_user_aba="UNKNOWN", completeness="UNKNOWN",
                 results=[dict(stored_text="Hex-muisti tallentaa p_00e4_00e4t_00f6kset", ref_sha256="5" * 64,
                               doc_id_sha256="6" * 64, ref_kind="sha256_of_projected_metadata",
                               canonical_event=False)])
    value.update(changes)
    return value


EXPECTED = ("$p=@{encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);"
            "content_decision_sha256=('9'*64);descriptor_sha256=('1'*64);memory_sha256=('2'*64);"
            "projection_sha256=('3'*64)}\n")


@windows
@pytest.mark.parametrize("fault", ["none", "decision", "memory", "projection", "descriptor", "mode", "schema",
                                   "freshness", "extra_result", "extra_binding", "card_reply", "long_text"])
def test_the_content_reply_must_carry_every_pin_and_the_exact_shape(fault):
    value = reply()
    if fault == "decision": value["binding"]["content_decision_sha256"] = "8" * 64
    if fault == "memory": value["binding"]["memory_sha256"] = "8" * 64
    if fault == "projection": value["binding"]["projection_sha256"] = "8" * 64
    if fault == "descriptor": value["binding"]["descriptor_sha256"] = "8" * 64
    if fault == "mode": value["binding"]["source_mode"] = "card-snapshot-v1"
    if fault == "schema": value["schema"] = "wd.q4-card-snapshot-query.v1"
    if fault == "freshness": value["freshness"] = "VERIFIED"
    if fault == "extra_result": value["results"][0]["untrusted"] = True
    if fault == "extra_binding": value["binding"]["card_policy_sha256"] = "f" * 64
    if fault == "card_reply": value["binding"] = dict(binding(), pointer_sha256="a" * 64)
    if fault == "long_text": value["results"][0]["stored_text"] = "x" * 321
    literal = json.dumps(value, separators=(",", ":")).replace("'", "''")
    script = (f". '{quote(WRAPPER)}'\n$v=ConvertFrom-WdQ4StrictJson -Text '{literal}'\n" + EXPECTED +
              "try {ConvertFrom-WdQ4ContentResult -Result $v -Expected $p | ConvertTo-Json -Depth 8 -Compress} "
              "catch {'{\"status\":\"REFUSED\"}'}")
    assert ps(script)["status"] == ("ANSWER" if fault == "none" else "REFUSED")


@windows
def test_the_content_plan_reaches_the_real_windows_job_as_framed_inputs(tmp_path):
    """The real outer Windows Job carries the content tuple to run_content; this child is synthetic."""
    root = tmp_path / "code"
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "__init__.py").write_text("")
    (tools / "wd_q4_agent_query.py").write_text("class VerifiedQ4Config:\n def __init__(self, **kw): pass\n")
    output = reply()
    (tools / "wd_q4_public_outer.py").write_text(
        "import json,sys,types\n"
        "def bootstrap_public_imports(**kwargs):\n"
        " def run_content(config,stream,stdout,**pins):\n"
        "  assert stream.read()==b'query-secret'\n"
        "  assert pins['policy_bytes']==b'policy-secret' and pins['roster_bytes']==b'roster-secret'\n"
        "  assert pins['content_decision_bytes']==b'decision-secret' and pins['enabled'] is True\n"
        "  assert pins['content_decision_sha256']=='9'*64 and pins['expected_encoder_sha256']=='c'*64\n"
        "  assert pins['expected_memory_sha256']=='2'*64 and pins['expected_projection_sha256']=='3'*64\n"
        "  assert pins['policy_sha256']=='d'*64 and pins['roster_sha256']=='e'*64\n"
        "  assert all('secret' not in item for item in sys.argv)\n"
        "  stdout.write(json.dumps(" + repr(output) + "));stdout.flush();return 0\n"
        " def run_public(*a, **k): raise AssertionError('not the content route')\n"
        " return {'status':'MODULES_IMPORTED','child':types.SimpleNamespace(run_content=run_content,"
        "run_public=run_public)}\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    helper = ROOT / "ops/windows/reboot/WdQ4ProcessJob.ps1"
    script = f"""
$ErrorActionPreference='Stop'
. '{quote(WRAPPER)}'
$h=[IO.File]::ReadAllBytes('{quote(helper)}');$sha=[Security.Cryptography.SHA256]::Create()
$hash=[BitConverter]::ToString($sha.ComputeHash($h)).Replace('-','');$sha.Dispose()
$base=@{{CodeRoot='{quote(root)}';JobHelperBytes=$h;JobHelperSha256=$hash;
 Config=@{{interpreter='{quote(sys.executable)}';timeout=10;scratch_root='{quote(scratch)}';verifier_sha256=('7'*64);descriptor_sha256=('1'*64);runtime_root='C:\\r';runtime_manifest='C:\\m';runtime_manifest_sha256=('8'*64);runtime_generation=('9'*40);runtime_revision=('8'*40)}};
 PublicPins=@{{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}}}
$doc=@{{encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);content_decision_sha256=('9'*64);memory_sha256=('2'*64);projection_sha256=('3'*64)}}
$plan=@{{Base=$base;Document=$doc;Root=$null;Staged=$false;Content=$true;Inputs=@{{policy=[Text.Encoding]::UTF8.GetBytes('policy-secret');roster=[Text.Encoding]::UTF8.GetBytes('roster-secret');content_decision=[Text.Encoding]::UTF8.GetBytes('decision-secret')}}}}
Invoke-WdQ4PublicTransport -Plan $plan -Query ([Text.Encoding]::UTF8.GetBytes('query-secret')) | ConvertTo-Json -Depth 8 -Compress
"""
    out = ps(script)
    assert out["status"] == "ANSWER" and out["binding"]["source_mode"] == "content-snapshot-v1"


@windows
def test_the_v1_v2_and_v3_plan_readers_are_unchanged_by_the_content_branch(tmp_path):
    text = WRAPPER.read_text(encoding="utf-8")
    assert "'wd.q4-public-wrapper-plan.v1'" in text and "'wd.q4-public-wrapper-plan.v3'" in text
    assert text.count("function Get-WdQ4ContentPlan") == 1 and text.count("function ConvertFrom-WdQ4ContentResult") == 1


@windows
def test_a_content_plan_whose_config_is_not_an_object_is_refused(tmp_path):
    path, pin, _ = content_plan(tmp_path, config="descriptor")
    assert ps(load(path, pin) + "@{loaded=($null -ne $plan)} | ConvertTo-Json -Compress") == {"loaded": False}


@windows
def test_a_content_plan_needs_the_three_public_code_pins(tmp_path):
    path, pin, _ = content_plan(tmp_path)
    script = (f". '{quote(WRAPPER)}'\n"
              "$base=@{Enabled=$true;PublicPins=@{'tools/wd_q4_public_outer.py'=('1'*64);"
              "'tools/wd_q4_public_child.py'=('2'*64)}}\n"
              f"$plan=try {{Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}'}}"
              "catch {$null}\n@{loaded=($null -ne $plan)} | ConvertTo-Json -Compress")
    assert ps(script) == {"loaded": False}


HELPER = ("$hb=[Text.Encoding]::UTF8.GetBytes('function Invoke-WdQ4ProcessJob {param($Executable,$Arguments,"
          "$WorkingDirectory,$ScratchRoot,$InputBytes,$TimeoutSeconds,$MaxOutputBytes) $null}');"
          "$sx=[Security.Cryptography.SHA256]::Create();$hh=[BitConverter]::ToString($sx.ComputeHash($hb)).Replace('-','');"
          "$sx.Dispose()\n")
TRANSPORT = (HELPER + "$base=@{CodeRoot='C:\\code';JobHelperBytes=$hb;JobHelperSha256=$hh;"
             "Config=@{descriptor_sha256=('1'*64);verifier_sha256=('7'*64);timeout=10;interpreter='C:\\py.exe';"
             "scratch_root='C:\\s'};"
             "PublicPins=@{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}\n")


@windows
@pytest.mark.parametrize("fault", ["none", "card_input", "staged", "bad_pin"])
def test_the_content_transport_refuses_a_mixed_or_unpinned_plan_before_any_job(fault):
    doc = ("$doc=@{encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);"
           "content_decision_sha256=('9'*64);memory_sha256=('2'*64);projection_sha256=" +
           ("'x'" if fault == "bad_pin" else "('3'*64)") + "}\n")
    inputs = "policy=[byte[]]@(1);roster=[byte[]]@(2);content_decision=[byte[]]@(3)"
    if fault == "card_input":
        inputs += ";card_decision=[byte[]]@(4)"
    staged = "$true" if fault == "staged" else "$false"
    script = (f". '{quote(WRAPPER)}'\n" + TRANSPORT + doc +
              f"$plan=@{{Base=$base;Document=$doc;Root=$null;Staged={staged};Content=$true;Inputs=@{{{inputs}}}}}\n"
              "$r=try {$null=Invoke-WdQ4PublicTransport -Plan $plan -Query ([byte[]]@(5));'accepted'} catch {[string]$_}\n"
              "@{r=$r} | ConvertTo-Json -Compress")
    assert ps(script) == {"r": "runtime_refused" if fault == "none" else "binding_refused"}
