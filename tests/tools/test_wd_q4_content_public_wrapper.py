"""Content snapshot plan v5 through the Windows query wrapper (lead-q4-useful-work-content-fable-20261107-v1, 7b2;
v5 for RCO2 MP1 R1: the plan also names the decision's sources, which the wrapper reads and frames to the child).

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

PLAN_SCHEMA = "wd.q4-public-wrapper-plan.v5"
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
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    plan["message_source_path"], _ = write_file(tmp_path / "events.jsonl", b"{}\n")
    plan["message_generation_path"], _ = write_file(tmp_path / "generation.json", b"{}")
    plan["message_policy_path"], _ = write_file(tmp_path / "message-policy.json", b"message-policy")
    plan["doc_repo_root"] = str(repo)
    plan["doc_policy_path"], _ = write_file(tmp_path / "doc-policy.json", b"doc-policy")
    plan["doc_git_executable"], plan["doc_git_sha256"] = write_file(tmp_path / "git.exe", b"synthetic git")
    plan.update(changes)
    plan = {key: value for key, value in plan.items() if value is not ...}
    path, pin = write_file(tmp_path / "plan.json", json.dumps(plan, separators=(",", ":")).encode())
    return path, pin, plan


def trusted(plan, **changes):
    """The base plan's TrustedGit as Get-WdQ4QueryPlan derives it from the manifest-pinned fleet record."""
    value = {"git_executable": plan.get("doc_git_executable"), "git_sha256": plan.get("doc_git_sha256"), **changes}
    if value["git_executable"] is None or value["git_sha256"] is None:
        return "$base.TrustedGit=$null\n"
    return ("$base.TrustedGit=@{git_executable='" + quote(value["git_executable"]) + "';git_sha256='" +
            value["git_sha256"] + "'}\n")


def load(path, pin, plan=None, **changes):
    return (f". '{quote(WRAPPER)}'\n" + BASE + ("" if plan is None else trusted(plan, **changes)) +
            f"$plan=try {{Get-WdQ4PublicPlan -BasePlan $base -Path '{quote(path)}' -ExpectedSha256 '{pin}'}}"
            "catch {$null}\n")


@windows
def test_a_content_plan_loads_its_three_pinned_inputs(tmp_path):
    path, pin, plan = content_plan(tmp_path)
    script = load(path, pin, plan) + (
        "@{content=$plan.Content;staged=$plan.Staged;names=(@($plan.Inputs.Keys)|Sort-Object) -join ',';"
        "decision=[Text.Encoding]::ASCII.GetString($plan.Inputs.content_decision)} | ConvertTo-Json -Compress")
    assert ps(script) == {"content": True, "staged": False,
                          "names": "content_decision,doc_policy,message_policy,policy,roster", "decision": "decision"}


@windows
@pytest.mark.parametrize("group, names", [
    ("message", "content_decision,message_policy,policy,roster"),
    ("doc", "content_decision,doc_policy,policy,roster"),
])
def test_a_content_plan_with_one_source_group_reads_only_that_policy(tmp_path, group, names):
    nulls = ({"doc_repo_root": None, "doc_policy_path": None, "doc_git_executable": None, "doc_git_sha256": None}
             if group == "message" else
             {"message_source_path": None, "message_generation_path": None, "message_policy_path": None})
    path, pin, plan = content_plan(tmp_path, **nulls)
    script = load(path, pin, plan) + "@{names=(@($plan.Inputs.Keys)|Sort-Object) -join ','} | ConvertTo-Json -Compress"
    assert ps(script) == {"names": names}


@windows
@pytest.mark.parametrize("fleet, expected", [
    ('{"git_executable":"C:\\\\Program Files\\\\Git\\\\cmd\\\\git.exe","git_sha256":"' + "a" * 64 + '"}',
     {"git_executable": "C:\\Program Files\\Git\\cmd\\git.exe", "git_sha256": "a" * 64}),
    ('{"git_executable":"C:\\\\git.exe"}', None),
    ('{"git_sha256":"' + "a" * 64 + '"}', None),
    ('{"git_executable":"C:\\\\git.exe","git_sha256":"' + "A" * 64 + '"}', None),
    ('{"git_executable":"C:\\\\git.exe","git_sha256":"' + "a" * 63 + '"}', None),
    ('{"git_executable":"","git_sha256":"' + "a" * 64 + '"}', None),
    ('{"git_executable":1,"git_sha256":"' + "a" * 64 + '"}', None),
    ('{"git_executable":"C:\\\\git.exe","git_sha256":null}', None),
    ("@(1,2)", None),
    ("$null", None),
])
def test_the_trusted_git_pin_is_derived_only_from_a_complete_fleet_record(fleet, expected):
    """RCO1 Q1: a fleet record yields a pin only with git_executable and a lowercase hex git_sha256; else null."""
    value = fleet if fleet.startswith(("@", "$")) else f"ConvertFrom-WdQ4StrictJson -Text '{fleet}'"
    script = (f". '{quote(WRAPPER)}'\n$fleet={value}\n"
              "$t=Get-WdQ4TrustedGit -Fleet $fleet\n"
              "@{t=$(if($null -eq $t){$null}else{@{git_executable=$t.git_executable;git_sha256=$t.git_sha256}})}"
              " | ConvertTo-Json -Compress -Depth 4")
    assert ps(script) == {"t": expected}


@windows
def test_the_repository_fleet_record_yields_no_trusted_git_pin_yet():
    """Truth of the missing interface: ops/windows/reboot/wd-fleet.json names git_executable but no git_sha256, so a
    deployed wrapper passes no trusted pin and every document-bearing content plan fails closed."""
    fleet_path = ROOT / "ops/windows/reboot/wd-fleet.json"
    fleet = json.loads(fleet_path.read_text(encoding="utf-8"))
    assert "git_executable" in fleet and "git_sha256" not in fleet
    script = (f". '{quote(WRAPPER)}'\n"
              f"$fleet=ConvertFrom-WdQ4StrictJson -Text ([IO.File]::ReadAllText('{quote(fleet_path)}'))\n"
              "@{t=$(if($null -eq (Get-WdQ4TrustedGit -Fleet $fleet)){'null'}else{'pin'})} | ConvertTo-Json -Compress")
    assert ps(script) == {"t": "null"}


@windows
@pytest.mark.parametrize("change", [
    {"git_sha256": None},
    {"git_sha256": "0" * 64},
    {"git_executable": "C:\\other\\git.exe"},
])
def test_a_content_plan_whose_git_is_not_the_trusted_fleet_git_is_refused(tmp_path, change):
    """RCO1 Q1: the plan may not choose the program; without a fleet Git digest no document plan loads at all."""
    path, pin, plan = content_plan(tmp_path)
    assert ps(load(path, pin, plan, **change) + "@{loaded=($null -ne $plan)} | ConvertTo-Json -Compress") == {
        "loaded": False}
    nulls = {"doc_repo_root": None, "doc_policy_path": None, "doc_git_executable": None, "doc_git_sha256": None}
    path, pin, plan = content_plan(tmp_path, **nulls)
    assert ps(load(path, pin, plan) + "@{loaded=($null -ne $plan)} | ConvertTo-Json -Compress") == {"loaded": True}


@windows
@pytest.mark.parametrize("change", [
    {"root": "C:\\public"},
    {"memory_sha256": ...},
    {"projection_sha256": "x" * 64},
    {"encoder_sha256": "C" * 64},
    {"content_decision_sha256": "0" * 64},
    {"content_decision_path": ""},
    {"card_decision_sha256": "9" * 64},
    {"schema": "wd.q4-public-wrapper-plan.v4"},
    {"schema": "wd.q4-public-wrapper-plan.v6"},
    {"message_source_path": ...},
    {"doc_git_sha256": ...},
    {"message_source_path": None},
    {"doc_git_sha256": None},
    {"message_source_path": None, "message_generation_path": None, "message_policy_path": None,
     "doc_repo_root": None, "doc_policy_path": None, "doc_git_executable": None, "doc_git_sha256": None},
    {"doc_git_sha256": "A" * 64},
    {"doc_repo_root": "relative\\repo"},
    {"message_source_path": "C:/forward/events.jsonl"},
    {"doc_policy_path": "C:\\missing\\doc-policy.json"},
])
def test_a_partial_tampered_or_extended_content_plan_is_refused(tmp_path, change):
    path, pin, plan = content_plan(tmp_path, **change)
    assert ps(load(path, pin, plan) + "@{loaded=($null -ne $plan)} | ConvertTo-Json -Compress") == {"loaded": False}


@windows
def test_a_changed_decision_file_is_refused(tmp_path):
    path, pin, plan = content_plan(tmp_path)
    script = load(path, pin, plan) + (
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
        "  assert pins['content_sources']=={'message_source_path':'C:\\\\s\\\\events.jsonl',"
        "'message_generation_path':'C:\\\\s\\\\generation.json','message_policy_bytes':b'message-secret',"
        "'doc_repo_root':'C:\\\\repo','doc_policy_bytes':b'doc-secret','doc_git_executable':'C:\\\\git.exe',"
        "'doc_git_sha256':'a'*64}\n"
        "  assert pins['trusted_git']=={'git_executable':'C:\\\\git.exe','git_sha256':'a'*64}\n"
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
 TrustedGit=@{{git_executable='C:\\git.exe';git_sha256=('a'*64)}};
 PublicPins=@{{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}}}
$doc=@{{encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);content_decision_sha256=('9'*64);memory_sha256=('2'*64);projection_sha256=('3'*64);message_source_path='C:\\s\\events.jsonl';message_generation_path='C:\\s\\generation.json';doc_repo_root='C:\\repo';doc_git_executable='C:\\git.exe';doc_git_sha256=('a'*64)}}
$plan=@{{Base=$base;Document=$doc;Root=$null;Staged=$false;Content=$true;Inputs=@{{policy=[Text.Encoding]::UTF8.GetBytes('policy-secret');roster=[Text.Encoding]::UTF8.GetBytes('roster-secret');content_decision=[Text.Encoding]::UTF8.GetBytes('decision-secret');message_policy=[Text.Encoding]::UTF8.GetBytes('message-secret');doc_policy=[Text.Encoding]::UTF8.GetBytes('doc-secret')}}}}
Invoke-WdQ4PublicTransport -Plan $plan -Query ([Text.Encoding]::UTF8.GetBytes('query-secret')) | ConvertTo-Json -Depth 8 -Compress
"""
    out = ps(script)
    assert out["status"] == "ANSWER" and out["binding"]["source_mode"] == "content-snapshot-v1"


@windows
def test_the_base_plan_derives_its_trusted_git_from_the_manifest_pinned_fleet_snapshot():
    """Call-site truth (the full bundle walk is not run here): the only TrustedGit source is the verified fleet
    snapshot, derived by Get-WdQ4TrustedGit and returned on the base plan; no other assignment exists."""
    text = WRAPPER.read_text(encoding="utf-8")
    assert text.count("$trustedGit=") == 1
    assert "$trustedGit=Get-WdQ4TrustedGit -Fleet (ConvertFrom-WdQ4StrictJson -Text $snapshots['wd-fleet.json'].Text)" in text
    assert "TrustedGit=$trustedGit;" in text and text.count("function Get-WdQ4TrustedGit") == 1


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
             "scratch_root='C:\\s'};TrustedGit=@{git_executable='C:\\g.exe';git_sha256=('a'*64)};"
             "PublicPins=@{'tools/wd_q4_public_child.py'=('8'*64);'tools/wd_q4_public_query.py'=('9'*64)}}\n")


@windows
@pytest.mark.parametrize("fault", ["none", "card_input", "staged", "bad_pin", "no_message_policy", "no_doc_policy",
                                   "no_sources", "bad_git_pin", "message_only", "doc_only", "stray_message_policy",
                                   "stray_doc_policy", "no_trusted_git", "trusted_git_other_pin",
                                   "plan_git_other_program"])
def test_the_content_transport_refuses_a_mixed_or_unpinned_plan_before_any_job(fault):
    message = "" if fault in ("no_sources", "doc_only", "stray_message_policy") else "message_source_path='C:\\e.jsonl';"
    git = "'A'" if fault == "bad_git_pin" else "('a'*64)"
    exe = "'C:\\evil.exe'" if fault == "plan_git_other_program" else "'C:\\g.exe'"
    docs = ("" if fault in ("no_sources", "message_only", "stray_doc_policy") else
            "doc_repo_root='C:\\r';doc_git_executable=" + exe + ";doc_git_sha256=" + git + ";")
    base = TRANSPORT
    if fault == "no_trusted_git":
        base = base.replace("TrustedGit=@{git_executable='C:\\g.exe';git_sha256=('a'*64)};", "")
    if fault == "trusted_git_other_pin":
        base = base.replace("git_sha256=('a'*64)};", "git_sha256=('b'*64)};")
    assert fault not in ("no_trusted_git", "trusted_git_other_pin") or base != TRANSPORT
    doc = ("$doc=@{" + message + docs + "encoder_sha256=('c'*64);policy_sha256=('d'*64);roster_sha256=('e'*64);"
           "content_decision_sha256=('9'*64);memory_sha256=('2'*64);projection_sha256=" +
           ("'x'" if fault == "bad_pin" else "('3'*64)") + "}\n")
    inputs = "policy=[byte[]]@(1);roster=[byte[]]@(2);content_decision=[byte[]]@(3)"
    if fault not in ("no_message_policy", "no_sources", "doc_only"):
        inputs += ";message_policy=[byte[]]@(6)"
    if fault not in ("no_doc_policy", "no_sources", "message_only"):
        inputs += ";doc_policy=[byte[]]@(7)"
    if fault == "card_input":
        inputs += ";card_decision=[byte[]]@(4)"
    staged = "$true" if fault == "staged" else "$false"
    script = (f". '{quote(WRAPPER)}'\n" + base + doc +
              f"$plan=@{{Base=$base;Document=$doc;Root=$null;Staged={staged};Content=$true;Inputs=@{{{inputs}}}}}\n"
              "$r=try {$null=Invoke-WdQ4PublicTransport -Plan $plan -Query ([byte[]]@(5));'accepted'} catch {[string]$_}\n"
              "@{r=$r} | ConvertTo-Json -Compress")
    ok = fault in ("none", "message_only", "doc_only")
    assert ps(script) == {"r": "runtime_refused" if ok else "binding_refused"}
