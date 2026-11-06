"""Synthetic only: the default-off descriptor CLI that composes fixed plan + descriptor + materializer.

Nothing here installs, activates or repins anything. Every input lives under pytest's tmp_path. The end-to-end
proof runs the CLI, then the EXISTING launcher functions extracted from the exact ``start-wd-agent.ps1`` bytes
(``Get-WdLaneSharedQ4Reference`` with its reparse, manifest and validator checks) compute the pair the launcher
exports, the EXISTING fleet launcher function ``Get-WdQ4SharedForwardingArguments`` forwards it, and the EXISTING
caller reader accepts the descriptor bytes under that pin. A matching hash is not authentication, freshness or a
runtime proof.
"""
import base64
import builtins
import hashlib
import inspect
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "ops/windows/reboot/start-wd-agent.ps1"
FLEET_LAUNCHER = ROOT / "ops/windows/reboot/start-wd-all.ps1"
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
needs_shell = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
needs_windows = pytest.mark.skipif(os.name != "nt", reason="drive-letter paths are Windows only")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]
REQUEST_SCHEMA = "wd.q4-descriptor-cli-request.v1"
SENTINEL = "sentinelvalue7f3a"
GENERATION = "d" * 40
REASONS = {"argv_refused", "request_refused", "source_refused", "fixed_plan_refused", "descriptor_refused",
           "materialize_refused", "unexpected_refused"}
OUTER_KEYS = ("outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
              "verifier-sha256", "public-outer-sha256", "public-child-sha256", "public-query-sha256",
              "worker-sha256")
BINDING_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
                "public_policy_sha256", "descriptor_sha256", "projection_sha256")
FIXED_PINS = ("interpreter_sha256", "peer_code_sha256", "verifier_sha256", "runtime_manifest_sha256", "bundle_sha256",
              "expected_pointer_sha256", "policy_sha256", "roster_sha256", "expected_public_policy_sha256",
              "expected_source_sha256", "expected_encoder_sha256")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def module():
    from tools import wd_q4_descriptor_cli
    return wd_q4_descriptor_cli


def canonical(document):
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def fleet_bytes():
    return json.dumps({"lanes": [{"agent": x} for x in LANES if x != "codex-tools-1"],
                       "tools_supervisor": {"agent": "codex-tools-1"}}).encode()


def synthetic(tmp_path):
    """Write the synthetic sources and return (request document, source bytes, target dir)."""
    sources = tmp_path / "sources"
    sources.mkdir(exist_ok=True)
    target = tmp_path / "generations"
    target.mkdir(exist_ok=True)
    fleet = fleet_bytes()
    binding = canonical({"schema": "wd.q4-lane-binding.v1", "lane": "fable-5"})
    snapshot = canonical({"schema": "wd.q4-public-snapshot.v1", "records": 0, "mode": "public-v1"})
    (sources / "wd-fleet.json").write_bytes(fleet)
    (sources / "binding.json").write_bytes(binding)
    (sources / "snapshot.json").write_bytes(snapshot)
    runtime = tmp_path / "runtime"
    request = {
        "schema": REQUEST_SCHEMA,
        "target_dir": str(target), "generation": GENERATION,
        "fleet_path": str(sources / "wd-fleet.json"), "fleet_sha256": sha(fleet),
        "binding_path": str(sources / "binding.json"), "binding_sha256": sha(binding),
        "snapshot_path": str(sources / "snapshot.json"), "snapshot_sha256": sha(snapshot),
        "lanes": list(LANES),
        "fixed_plan": {**{name: format(i + 1, "x") * 64 for i, name in enumerate(FIXED_PINS)},
                       "runtime_generation": GENERATION, "runtime_revision": "b" * 64,
                       "runtime_root": str(runtime), "runtime_manifest_path": str(tmp_path / "runtime_manifest.json"),
                       "bundle_bytes": 102400, "approved_tasks": ["task-a", "task-b"]},
        "descriptor": {"bundle_root": str(tmp_path / "bundle"), "public_plan": str(tmp_path / "public.json"),
                       "interpreter": str(tmp_path / "python.exe"), "interpreter_sha256": "a" * 64,
                       "interpreter_max_bytes": 10000, "cwd": str(tmp_path / "code"), "timeout_seconds": 30,
                       "max_output_bytes": 32000, "runtime_manifest_path": str(tmp_path / "runtime_manifest.json"),
                       "runtime_manifest_sha256": "9" * 64,
                       "outer": {"outer-path": str(runtime / "assets" / "code" / "tools" / "wd_q4_session_outer_synthetic.py"),
                                 "outer-sha256": "c" * 64, "runtime-root": str(runtime),
                                 "runtime-generation": GENERATION, "runtime-revision": "e" * 64,
                                 "verifier-sha256": "1" * 64, "public-outer-sha256": "2" * 64,
                                 "public-child-sha256": "3" * 64, "public-query-sha256": "4" * 64,
                                 "worker-sha256": "5" * 64},
                       "expected_binding": {key: format(i + 1, "064x") for i, key in enumerate(BINDING_KEYS)}},
    }
    return request, {"fleet": fleet, "binding": binding, "snapshot": snapshot}, target


def write_request(tmp_path, request, name="request.json"):
    path = tmp_path / name
    path.write_bytes(canonical(request))
    return path


def run(argv, capsys):
    code = module().main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ok_output(out):
    lines = out.splitlines()
    assert len(lines) == 1 and out.endswith("\n")
    document = json.loads(lines[0])
    assert set(document) == {"descriptor_path", "descriptor_sha256"}
    return document


def refusal(out, err, code, reason):
    assert err == ""
    assert code == 1
    assert out == '{"error":"descriptor_cli_refused","reason":"' + reason + '"}\n', out
    assert reason in REASONS


def tree(path):
    return sorted(str(p.relative_to(path)) for p in Path(path).rglob("*"))


# 1 off by default and the exact surface --------------------------------------------------------------------------
def test_no_arguments_prints_off_and_touches_nothing(capsys, monkeypatch):
    m = module()

    def blocked(*args, **kwargs):
        raise AssertionError("I/O attempted while OFF")
    for owner, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "mkdir"), (os, "scandir")):
        monkeypatch.setattr(owner, name, blocked)
    code = m.main([])
    monkeypatch.undo()
    captured = capsys.readouterr()
    assert code == 0 and captured.out == '{"status":"OFF"}\n' and captured.err == ""


@needs_windows
def test_a_request_without_enable_is_off_and_the_request_file_is_never_opened(tmp_path, capsys, monkeypatch):
    request, sources, target = synthetic(tmp_path)
    path = write_request(tmp_path, request)
    m = module()

    def blocked(*args, **kwargs):
        raise AssertionError("I/O attempted while OFF")
    for owner, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "mkdir")):
        monkeypatch.setattr(owner, name, blocked)
    code = m.main(["--request", str(path)])
    monkeypatch.undo()
    captured = capsys.readouterr()
    assert code == 0 and captured.out == '{"status":"OFF"}\n' and captured.err == ""
    assert tree(target) == []


@pytest.mark.parametrize("argv", [
    ["--enable"], ["--request"], ["--request", "a", "--enable", "x"], ["--enable", "--request", "a", "--enable"],
    ["--request", "a", "--enabled"], ["--request", "a", "--enable", "--enable"], ["-r", "a", "--enable"],
    ["--request", "", "--enable"], ["--request", "a", "--Enable"], ["--request", "a", "--enable="],
])
def test_only_the_exact_argument_shape_is_accepted(argv, capsys):
    code, out, err = run(argv, capsys)
    refusal(out, err, code, "argv_refused")


def test_the_module_surface_is_closed_and_quiet():
    m = module()
    assert set(m.REASONS) == REASONS and m.REQUEST_SCHEMA == REQUEST_SCHEMA
    assert m.REFUSAL_PREFIX == '{"error":"descriptor_cli_refused","reason":"'
    assert m.LIMITS == {"request": 65536, "fleet": 1048576, "binding": 65536, "snapshot": 16 * 1024 * 1024}
    sig = inspect.signature(m.main)
    assert list(sig.parameters) == ["argv"] and sig.parameters["argv"].default is None
    source = inspect.getsource(m)
    for token in ("import logging", "print(", "subprocess", "urllib", "socket", "os.environ", "getenv", "argparse",
                  "tempfile", "shutil", "os.remove", "os.unlink", "rmtree", "rmdir", "sys.stderr", "traceback",
                  "input("):
        assert token not in source, token


# 2 the happy path prints only the pair ----------------------------------------------------------------------------
@needs_windows
def test_enabled_materializes_and_prints_only_the_descriptor_path_and_pin(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    path = write_request(tmp_path, request)
    code, out, err = run(["--request", str(path), "--enable"], capsys)
    assert code == 0 and err == ""
    document = ok_output(out)
    descriptor = Path(document["descriptor_path"])
    assert descriptor.parent == target / GENERATION
    raw = descriptor.read_bytes()
    assert sha(raw) == document["descriptor_sha256"]
    body = json.loads(raw)
    assert body["schema"] == "wd.q4-shared-session-config.v2" and body["fleet_sha256"] == sha(sources["fleet"])
    for name in ("binding", "snapshot"):
        assert Path(body[name + "_path"]).read_bytes() == sources[name]
        assert body[name + "_sha256"] == sha(sources[name])
    fixed = Path(body["fixed_plan_path"]).read_bytes()
    assert body["fixed_plan_sha256"] == sha(fixed)
    plan = json.loads(fixed)
    assert plan["schema"] == "wd.q4-fixed-plan.v2" and plan["approved_tasks"] == ["task-a", "task-b"]
    assert len(tree(target / GENERATION)) == 4
    for forbidden in (SENTINEL, "a" * 64, str(tmp_path / "sources"), "approved_tasks"):
        assert forbidden not in out


@needs_windows
def test_the_output_is_one_compact_ascii_json_line_without_whitespace_or_extra_keys(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    assert code == 0
    line = out[:-1]
    assert line.isascii() and " " not in line.replace(str(target), "") and "\n" not in line
    assert line == json.dumps(json.loads(line), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@needs_windows
def test_the_descriptor_is_the_producer_output_byte_for_byte(tmp_path, capsys):
    from tools import wd_q4_descriptor_materializer as materializer
    from tools import wd_q4_fixed_plan_producer as fixed_plan
    from tools import wd_q4_shared_descriptor_producer as producer
    request, sources, target = synthetic(tmp_path)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    document = ok_output(out)
    plan = materializer.plan_generation_paths(request["target_dir"], GENERATION)
    fixed = fixed_plan.build_fixed_plan(enabled=True, **request["fixed_plan"])
    expected = producer.build_shared_descriptor(
        enabled=True, fleet_bytes=sources["fleet"], expected_fleet_sha256=request["fleet_sha256"],
        lanes=request["lanes"], binding_path=plan["binding_path"], binding_sha256=sha(sources["binding"]),
        fixed_plan_path=plan["fixed_plan_path"], fixed_plan_sha256=fixed["plan_sha256"],
        snapshot_path=plan["snapshot_path"], snapshot_sha256=sha(sources["snapshot"]), **request["descriptor"])
    assert Path(document["descriptor_path"]).read_bytes() == expected["descriptor_bytes"]
    assert document["descriptor_sha256"] == expected["descriptor_sha256"]
    assert document["descriptor_path"] == plan["descriptor_path"]


@needs_windows
def test_a_second_run_for_the_same_generation_refuses_and_leaves_the_first_intact(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    path = write_request(tmp_path, request)
    code, out, err = run(["--request", str(path), "--enable"], capsys)
    first = ok_output(out)
    before = Path(first["descriptor_path"]).read_bytes()
    code, out, err = run(["--request", str(path), "--enable"], capsys)
    refusal(out, err, code, "materialize_refused")
    assert Path(first["descriptor_path"]).read_bytes() == before and len(tree(target / GENERATION)) == 4


# 3 refusals: request, sources, producers --------------------------------------------------------------------------
def _edit(change):
    def apply(document):
        document = json.loads(json.dumps(document))
        change(document)
        return document
    return apply


REQUEST_CASES = [
    ("request_refused", _edit(lambda d: d.__setitem__("schema", "wd.q4-descriptor-cli-request.v0"))),
    ("request_refused", _edit(lambda d: d.pop("lanes"))),
    ("request_refused", _edit(lambda d: d.__setitem__("extra", 1))),
    ("request_refused", _edit(lambda d: d.__setitem__("fleet_sha256", "A" * 64))),
    ("request_refused", _edit(lambda d: d.__setitem__("binding_sha256", SENTINEL))),
    ("request_refused", _edit(lambda d: d.__setitem__("fleet_path", 7))),
    ("request_refused", _edit(lambda d: d.__setitem__("fixed_plan", []))),
    ("request_refused", _edit(lambda d: d.__setitem__("descriptor", "x"))),
    ("request_refused", _edit(lambda d: d["fixed_plan"].__setitem__("enabled", True))),
    ("request_refused", _edit(lambda d: d["descriptor"].__setitem__("binding_path", "C:\\\\x\\\\y.json"))),
    ("request_refused", _edit(lambda d: d["descriptor"].__setitem__("fleet_bytes", "x"))),
    ("source_refused", _edit(lambda d: d.__setitem__("fleet_sha256", "0" * 64))),
    ("source_refused", _edit(lambda d: d.__setitem__("binding_sha256", "0" * 64))),
    ("source_refused", _edit(lambda d: d.__setitem__("snapshot_sha256", "0" * 64))),
    ("source_refused", _edit(lambda d: d.__setitem__("snapshot_path", d["snapshot_path"] + ".missing"))),
    ("fixed_plan_refused", _edit(lambda d: d["fixed_plan"].__setitem__("approved_tasks", ["task-b", "task-a"]))),
    ("fixed_plan_refused", _edit(lambda d: d["fixed_plan"].__setitem__("bundle_bytes", 0))),
    ("descriptor_refused", _edit(lambda d: d.__setitem__("lanes", ["fable-5", "fable-5"]))),
    ("descriptor_refused", _edit(lambda d: d["descriptor"].__setitem__("timeout_seconds", 0))),
    ("descriptor_refused", _edit(lambda d: d["descriptor"]["outer"].__setitem__("worker-sha256", "x"))),
    ("materialize_refused", _edit(lambda d: d.__setitem__("generation", "D" * 40))),
    ("materialize_refused", _edit(lambda d: d.__setitem__("target_dir", d["target_dir"] + "\\absent"))),
]


@needs_windows
@pytest.mark.parametrize("reason, change", REQUEST_CASES)
def test_every_invalid_request_source_or_producer_input_refuses_with_a_closed_reason(tmp_path, capsys, reason, change):
    request, sources, target = synthetic(tmp_path)
    code, out, err = run(["--request", str(write_request(tmp_path, change(request))), "--enable"], capsys)
    refusal(out, err, code, reason)
    assert tree(target) == []


@needs_windows
@pytest.mark.parametrize("raw", [b"", b"[]", b"null", b"{", b'{"a":1,"a":2}', b"\xef\xbb\xbf{}", b"{}" + b" " * 70000],
                         ids=["empty", "list", "null", "truncated", "duplicate-key", "bom", "oversized"])
def test_request_bytes_that_are_not_one_strict_json_object_refuse(tmp_path, capsys, raw):
    path = tmp_path / "request.json"
    path.write_bytes(raw)
    code, out, err = run(["--request", str(path), "--enable"], capsys)
    refusal(out, err, code, "request_refused")


@needs_windows
def test_a_missing_or_relative_request_path_refuses(tmp_path, capsys):
    code, out, err = run(["--request", str(tmp_path / "absent.json"), "--enable"], capsys)
    refusal(out, err, code, "request_refused")
    code, out, err = run(["--request", "relative.json", "--enable"], capsys)
    refusal(out, err, code, "request_refused")


@needs_windows
def test_an_oversized_source_refuses_before_any_write(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    big = b"{" + b" " * 65536 + b"}"
    Path(request["binding_path"]).write_bytes(big)
    request["binding_sha256"] = sha(big)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "source_refused")
    assert tree(target) == []


@needs_windows
def test_a_reparse_point_on_a_source_path_refuses(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    real = tmp_path / "sources"
    link = tmp_path / "linked"
    try:
        os.symlink(str(real), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    request["snapshot_path"] = str(link / "snapshot.json")
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "source_refused")
    assert tree(target) == []


@needs_windows
def test_refusal_output_never_carries_paths_values_or_exception_text(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    request["snapshot_path"] = str(tmp_path / ("sources-" + SENTINEL) / "snapshot.json")
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "source_refused")
    assert SENTINEL not in out and "\\" not in out and "Error" not in out


@needs_windows
def test_an_unexpected_failure_inside_a_producer_is_a_closed_refusal(tmp_path, capsys, monkeypatch):
    from tools import wd_q4_descriptor_materializer as materializer
    request, sources, target = synthetic(tmp_path)

    def boom(**kwargs):
        raise RuntimeError("secret detail " + SENTINEL)
    monkeypatch.setattr(materializer, "materialize_descriptor", boom)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "unexpected_refused")
    assert SENTINEL not in out


# 4 the real entry point in a child interpreter ---------------------------------------------------------------------
@needs_windows
def test_the_module_entry_point_runs_in_a_child_interpreter_with_a_scrubbed_environment(tmp_path):
    request, sources, target = synthetic(tmp_path)
    path = write_request(tmp_path, request)
    env = {"SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "PATH": os.environ.get("PATH", ""),
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "ascii"}
    off = subprocess.run([sys.executable, "-m", "tools.wd_q4_descriptor_cli"], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert (off.returncode, off.stdout, off.stderr) == (0, '{"status":"OFF"}\n', "")
    on = subprocess.run([sys.executable, "-m", "tools.wd_q4_descriptor_cli", "--request", str(path), "--enable"],
                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert on.returncode == 0 and on.stderr == "", (on.stdout, on.stderr)
    document = ok_output(on.stdout)
    assert sha(Path(document["descriptor_path"]).read_bytes()) == document["descriptor_sha256"]


# 5 end to end: CLI -> launcher functions compute and forward the pair -> caller reads under the pin ---------------
def launcher_reference(tmp_path, descriptor_path, descriptor_pin, fleet_raw, lane):
    """Run the EXISTING launcher functions from the exact start-wd-agent.ps1 bytes, as the agent startup test does."""
    bundle = tmp_path / "bundle-launch"
    bundle.mkdir(exist_ok=True)
    helper = bundle / HELPER.name
    helper.write_bytes(HELPER.read_bytes())
    fleet = bundle / "wd-fleet.json"
    fleet.write_bytes(fleet_raw)
    manifest = bundle / "deployment-manifest.json"
    manifest.write_bytes(json.dumps({"files": {"wd-fleet.json": sha(fleet_raw).upper(),
                                               HELPER.name: sha(helper.read_bytes()).upper()}}).encode())
    script = f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
if($PSVersionTable.PSEdition -eq 'Desktop'){{$env:PSModulePath=Join-Path $PSHOME 'Modules'}}
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(LAUNCHER)},[ref]$tokens,[ref]$errors)
foreach ($name in @('Assert-LanePathWithoutReparse','Read-Utf8LaneSnapshot','Clear-WdInheritedQ4ReadSettings','Get-WdLaneSharedQ4Reference')) {{
  $fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq $name}},$true)
  if ($null -eq $fn) {{ throw 'missing expected function' }}
  . ([scriptblock]::Create($fn.Extent.Text))
}}
$all=[Management.Automation.Language.Parser]::ParseFile({q(FLEET_LAUNCHER)},[ref]$tokens,[ref]$errors)
$fwd=$all.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Get-WdQ4SharedForwardingArguments'}},$true)
if ($null -eq $fwd) {{ throw 'missing forwarding function' }}
. ([scriptblock]::Create($fwd.Extent.Text))
$env:WD_Q4_SHARED_DESCRIPTOR_PATH='inherited_spoof_secret'
Clear-WdInheritedQ4ReadSettings
try {{
 $v=Get-WdLaneSharedQ4Reference -DescriptorPath {q(descriptor_path)} -DescriptorSha256 {q(descriptor_pin)} `
   -FleetPath {q(fleet)} -DeploymentManifestPath {q(manifest)} -ExpectedManifestSha256 {q(sha(manifest.read_bytes()))} `
   -BundleRoot {q(bundle)} -TrustedRoot {q(Path(tmp_path.anchor))} -Lane {q(lane)}
 $f=$null
 if ($null -ne $v) {{ $f=Get-WdQ4SharedForwardingArguments -Path ([string]$v.WD_Q4_SHARED_DESCRIPTOR_PATH) -Sha256 ([string]$v.WD_Q4_SHARED_DESCRIPTOR_SHA256) }}
 @{{ok=$true;value=$v;forward=$f;spoof=(Test-Path Env:WD_Q4_SHARED_DESCRIPTOR_PATH)}}|ConvertTo-Json -Depth 8 -Compress
}} catch {{ @{{ok=$false;reason=$_.Exception.Message;spoof=(Test-Path Env:WD_Q4_SHARED_DESCRIPTOR_PATH)}}|ConvertTo-Json -Compress }}
"""
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return json.loads(result.stdout)


@needs_shell
@pytest.mark.parametrize("lane", LANES)
def test_end_to_end_cli_then_launcher_forwarding_then_caller_pin_read(tmp_path, capsys, lane):
    from tools import wd_q4_lane_caller_cli as caller
    request, sources, target = synthetic(tmp_path)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    assert code == 0
    pair = ok_output(out)
    checked = launcher_reference(tmp_path, pair["descriptor_path"], pair["descriptor_sha256"], sources["fleet"], lane)
    assert checked["ok"], checked
    assert checked["spoof"] is False
    value = checked["value"]
    assert set(value) == {"WD_Q4_SHARED_DESCRIPTOR_PATH", "WD_Q4_SHARED_DESCRIPTOR_SHA256"}
    assert value["WD_Q4_SHARED_DESCRIPTOR_PATH"] == pair["descriptor_path"]
    assert value["WD_Q4_SHARED_DESCRIPTOR_SHA256"] == pair["descriptor_sha256"]
    forward = checked["forward"]
    assert forward["native"] == ["-Q4SharedDescriptorPath", '"' + pair["descriptor_path"] + '"',
                                 "-Q4SharedDescriptorSha256", pair["descriptor_sha256"]]
    raw = caller._read(value["WD_Q4_SHARED_DESCRIPTOR_PATH"], 65536, value["WD_Q4_SHARED_DESCRIPTOR_SHA256"])
    assert sha(raw) == pair["descriptor_sha256"] and json.loads(raw)["lanes"] == LANES
    with pytest.raises(ValueError):
        caller._read(value["WD_Q4_SHARED_DESCRIPTOR_PATH"], 65536, "0" * 64)


@needs_shell
def test_end_to_end_launcher_refuses_a_tampered_descriptor_file_under_the_printed_pin(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    pair = ok_output(out)
    descriptor = Path(pair["descriptor_path"])
    descriptor.write_bytes(descriptor.read_bytes() + b" ")
    checked = launcher_reference(tmp_path, pair["descriptor_path"], pair["descriptor_sha256"], sources["fleet"], "fable-5")
    assert checked["ok"] is False and checked["spoof"] is False


@needs_shell
def test_end_to_end_launcher_stays_off_without_the_pair(tmp_path):
    checked = launcher_reference(tmp_path, "", "", fleet_bytes(), "fable-5")
    assert checked["ok"] and checked["value"] is None and checked["spoof"] is False


# 6 documentation --------------------------------------------------------------------------------------------------
def test_the_docstring_states_the_boundaries():
    doc = module().__doc__
    for phrase in ("default-off", "--enable", "explicit", "descriptor path", "sha256", "no secret", "closed reason",
                   "never", "not authentication", "not freshness", "not a runtime proof", "no install", "no activation",
                   "repin", "wd_q4_fixed_plan_producer", "wd_q4_shared_descriptor_producer",
                   "wd_q4_descriptor_materializer", "WD_Q4_SHARED_DESCRIPTOR_PATH", "operator"):
        assert phrase in doc, phrase


# 7 advisory claims (consultation 5f095c06) reproduced or rejected with RED-first negative tests --------------------
def _mixed(path):
    head, tail = str(path).rsplit("\\", 1)
    return head + "/" + tail


def _dotdot(path):
    head, tail = str(path).rsplit("\\", 1)
    return head + "\..\\" + head.rsplit("\\", 1)[1] + "\\" + tail


def _double(path):
    return str(path).replace("\\", "\\\\", 1)


def _stream(path):
    with open(str(path) + ":alt", "wb") as handle:
        handle.write(Path(path).read_bytes())
    return str(path) + ":alt"


def _nul(path):
    return str(path)[:-5] + "\u0000.json"


def _trailing(path):
    return str(path) + "\\"


@needs_windows
@pytest.mark.parametrize("shape", [_mixed, _dotdot, _double, _stream, _nul, _trailing],
                         ids=["mixed", "dotdot", "double", "stream", "nul", "trailing"])
def test_a_request_path_outside_the_single_separator_discipline_refuses_before_any_read(tmp_path, capsys, shape):
    request, sources, target = synthetic(tmp_path)
    nest = tmp_path / "req"
    nest.mkdir()
    path = shape(write_request(nest, request))
    code, out, err = run(["--request", path, "--enable"], capsys)
    refusal(out, err, code, "request_refused")
    assert tree(target) == []


@needs_windows
def test_a_request_path_whose_mixed_separator_hides_a_reparse_parent_refuses(tmp_path, capsys):
    request, sources, target = synthetic(tmp_path)
    real = tmp_path / "req"
    real.mkdir()
    link = tmp_path / "linked-req"
    try:
        os.symlink(str(real), str(link), target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted")
    write_request(real, request)
    code, out, err = run(["--request", str(link) + "/request.json", "--enable"], capsys)
    refusal(out, err, code, "request_refused")
    assert tree(target) == []


@needs_windows
@pytest.mark.parametrize("shape", [_dotdot, _stream], ids=["dotdot", "stream"])
def test_a_source_path_with_a_dot_dot_component_or_a_stream_refuses(tmp_path, capsys, shape):
    request, sources, target = synthetic(tmp_path)
    request["snapshot_path"] = shape(Path(request["snapshot_path"]))
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "source_refused")
    assert tree(target) == []


@needs_windows
@pytest.mark.parametrize("change", [
    lambda d: d.__setitem__("target_dir", d["target_dir"] + "\u0000x"),
    lambda d: d.__setitem__("generation", d["generation"][:-1] + "\u0000"),
    lambda d: d["fixed_plan"].__setitem__("runtime_root", "C:\\" + "\u0000root"),
    lambda d: d["descriptor"].__setitem__("cwd", "C:\code\t"),
    lambda d: d["lanes"].append("fable-5\u0000"),
    lambda d: d["fixed_plan"].__setitem__("approved_tasks", ["task-a", "\u007f"]),
    lambda d: d.__setitem__("snapshot_path", d["snapshot_path"][:-5] + "\u0000.json"),
], ids=["target", "generation", "fixed_plan", "descriptor", "lanes", "nested_list", "source_path"])
def test_a_control_character_anywhere_in_the_request_is_a_request_refusal(tmp_path, capsys, change):
    request, sources, target = synthetic(tmp_path)
    change(request)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "request_refused")
    assert tree(target) == []


@needs_windows
@pytest.mark.parametrize("field, literal", [
    (b'"bundle_bytes":102400', b'"bundle_bytes":102400.0'),
    (b'"bundle_bytes":102400', b'"bundle_bytes":1e400'),
    (b'"timeout_seconds":30', b'"timeout_seconds":30.0'),
    (b'"interpreter_max_bytes":10000', b'"interpreter_max_bytes":1e4'),
], ids=["float", "overflow", "descriptor_float", "exponent"])
def test_a_number_that_is_not_an_integer_literal_is_a_request_refusal(tmp_path, capsys, field, literal):
    request, sources, target = synthetic(tmp_path)
    raw = canonical(request)
    assert raw.count(field) == 1
    path = tmp_path / "request.json"
    path.write_bytes(raw.replace(field, literal))
    code, out, err = run(["--request", str(path), "--enable"], capsys)
    refusal(out, err, code, "request_refused")
    assert tree(target) == []


@needs_windows
def test_a_fixed_plan_pin_that_does_not_match_the_plan_bytes_is_refused_before_the_descriptor(tmp_path, capsys, monkeypatch):
    request, sources, target = synthetic(tmp_path)
    from tools import wd_q4_fixed_plan_producer
    real = wd_q4_fixed_plan_producer.build_fixed_plan

    def lying(**kwargs):
        out = real(**kwargs)
        return {"plan_bytes": out["plan_bytes"], "plan_sha256": "0" * 64}
    monkeypatch.setattr(wd_q4_fixed_plan_producer, "build_fixed_plan", lying)
    code, out, err = run(["--request", str(write_request(tmp_path, request)), "--enable"], capsys)
    refusal(out, err, code, "fixed_plan_refused")
    assert tree(target) == []


@needs_windows
@pytest.mark.parametrize("victim, reason", [("request.json", "request_refused"), ("snapshot.json", "source_refused")])
def test_an_opened_file_whose_identity_differs_from_the_checked_path_is_refused(tmp_path, capsys, monkeypatch, victim, reason):
    request, sources, target = synthetic(tmp_path)
    path = write_request(tmp_path, request)
    module()
    real = os.fstat
    swapped = {"count": 0}

    def fake(fd):
        info = real(fd)
        if info.st_size == (path if victim == "request.json" else tmp_path / "sources" / "snapshot.json").stat().st_size:
            swapped["count"] += 1
            return types.SimpleNamespace(st_mode=info.st_mode, st_ino=info.st_ino + 1, st_dev=info.st_dev,
                                         st_size=info.st_size, st_file_attributes=getattr(info, "st_file_attributes", 0))
        return info
    monkeypatch.setattr(os, "fstat", fake)
    code, out, err = run(["--request", str(path), "--enable"], capsys)
    refusal(out, err, code, reason)
    assert swapped["count"] >= 1
    assert tree(target) == []


class _ClosedStdout:
    def write(self, _text):
        raise BrokenPipeError(32, "closed")

    def flush(self):
        raise BrokenPipeError(32, "closed")


@needs_windows
@pytest.mark.parametrize("enabled", [False, True], ids=["off", "enabled"])
def test_a_closed_stdout_ends_with_exit_one_and_nothing_on_stderr(tmp_path, capsys, monkeypatch, enabled):
    request, sources, target = synthetic(tmp_path)
    path = write_request(tmp_path, request)
    module()
    monkeypatch.setattr(sys, "stdout", _ClosedStdout())
    argv = ["--request", str(path)] + (["--enable"] if enabled else [])
    code = module().main(argv)
    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == ""
    assert (tree(target) != []) is enabled
