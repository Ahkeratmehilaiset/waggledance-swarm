"""Synthetic pins only: the v2 shared-session descriptor producer against the existing PS validator.

No launcher, query, live data or runtime. Pure tests use synthetic Windows paths;
Windows validator tests use pytest's actual tmp_path and paths need not exist.
"""
import base64
import builtins
import copy
import hashlib
import inspect
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
needs_shell = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]
SCHEMA = "wd.q4-shared-session-config.v2"
SENTINEL = "sentinelvalue7f3a"
OUTER_KEYS = ("outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
              "verifier-sha256", "public-outer-sha256", "public-child-sha256", "public-query-sha256",
              "worker-sha256")
BINDING_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
                "public_policy_sha256", "descriptor_sha256", "projection_sha256")
REASONS = {"fleet_refused", "lanes_refused", "path_refused", "pin_refused", "limit_refused", "schema_refused"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def module():
    from tools import wd_q4_shared_descriptor_producer
    return wd_q4_shared_descriptor_producer


def fleet_bytes():
    return json.dumps({"lanes": [{"agent": x} for x in LANES if x != "codex-tools-1"],
                       "tools_supervisor": {"agent": "codex-tools-1"}, "other": {"kept": True}}).encode()


def kwargs(tmp_path, *, real_paths=False):
    fleet = fleet_bytes()
    tmp_path = tmp_path if real_paths else PureWindowsPath(r"C:\synthetic\q4-descriptor")
    root = tmp_path / "runtime"
    out = {"fleet_bytes": fleet, "expected_fleet_sha256": sha(fleet), "lanes": list(LANES),
           "bundle_root": str(tmp_path / "bundle"), "public_plan": str(tmp_path / "public.json"),
           "interpreter": str(tmp_path / "python.exe"), "interpreter_sha256": "a" * 64,
           "interpreter_max_bytes": 10000, "cwd": str(tmp_path / "code"), "timeout_seconds": 30,
           "max_output_bytes": 32000,
           "outer": {"outer-path": str(root / "assets" / "code" / "tools" / "wd_q4_session_outer_synthetic.py"),
                     "outer-sha256": "c" * 64, "runtime-root": str(root), "runtime-generation": "d" * 40,
                     "runtime-revision": "e" * 64, "verifier-sha256": "1" * 64, "public-outer-sha256": "2" * 64,
                     "public-child-sha256": "3" * 64, "public-query-sha256": "4" * 64, "worker-sha256": "5" * 64},
           "expected_binding": {key: format(i + 1, "064x") for i, key in enumerate(BINDING_KEYS)}}
    for i, name in enumerate(("binding", "fixed_plan", "snapshot", "runtime_manifest")):
        out[name + "_path"] = str(tmp_path / (name + ".json"))
        out[name + "_sha256"] = format(i + 6, "x") * 64
    return out


def document(kw):
    """The descriptor the validator expects for these inputs (fleet bytes become fleet_sha256)."""
    doc = {k: copy.deepcopy(v) for k, v in kw.items() if k not in ("fleet_bytes", "expected_fleet_sha256")}
    doc.update(schema=SCHEMA, fleet_sha256=kw["expected_fleet_sha256"])
    return doc


def build(kw, **extra):
    return module().build_shared_descriptor(enabled=True, **{**kw, **extra})


def validate(tmp_path, raw, kw, lane="codex-lead-1", descriptor_pin=None):
    fleet = tmp_path / "fleet.json"
    fleet.write_bytes(kw["fleet_bytes"])
    desc = tmp_path / "descriptor.json"
    desc.write_bytes(raw)
    script = ("$ErrorActionPreference='Stop'\ntry {\n"
              f". {quote(HELPER)}\n"
              f"$v=Get-WdQ4SharedSessionConfig -FleetPath {quote(fleet)} -FleetSha256 {quote(sha(kw['fleet_bytes']))} "
              f"-DescriptorPath {quote(desc)} -DescriptorSha256 {quote(descriptor_pin or sha(raw))} "
              f"-Lane {quote(lane)} -Enabled\n"
              "[ordered]@{ok=$true;value=$v}|ConvertTo-Json -Depth 8 -Compress\n"
              "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n")
    code = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, (process.stdout, process.stderr)
    return json.loads(process.stdout)


def refused(reason, call):
    m = module()
    with pytest.raises(m.DescriptorRefused) as caught:
        call()
    assert caught.value.args == (reason,) and reason in REASONS
    return caught.value


# 1 off by default and exact surface ----------------------------------------------------------------------------

def test_disabled_returns_off_without_validating_anything():
    assert module().build_shared_descriptor(fleet_bytes=b"not json", lanes="bad") == {"status": "OFF"}


@pytest.mark.parametrize("enabled", [1, "true", "1", [True]])
def test_only_the_exact_true_enables(tmp_path, enabled):
    assert module().build_shared_descriptor(**{**kwargs(tmp_path), "enabled": enabled}) == {"status": "OFF"}


def test_lanes_must_be_a_list(tmp_path):
    kw = kwargs(tmp_path)
    kw["lanes"] = tuple(LANES)
    refused("lanes_refused", lambda: build(kw))


def test_the_surface_is_explicit_keyword_only_pins_and_has_no_query():
    parameters = inspect.signature(module().build_shared_descriptor).parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert set(parameters) == {"enabled", "fleet_bytes", "expected_fleet_sha256", "lanes", "bundle_root",
                               "public_plan", "interpreter", "interpreter_sha256", "interpreter_max_bytes", "cwd",
                               "timeout_seconds", "max_output_bytes", "binding_path", "binding_sha256",
                               "fixed_plan_path", "fixed_plan_sha256", "snapshot_path", "snapshot_sha256",
                               "runtime_manifest_path", "runtime_manifest_sha256", "outer", "expected_binding"}
    assert module().SCHEMA == SCHEMA


# 2 validator compatibility ---------------------------------------------------------------------------------------

@needs_shell
@pytest.mark.parametrize("lane", LANES)
def test_the_existing_validator_accepts_the_produced_descriptor_for_every_lane(tmp_path, lane):
    kw = kwargs(tmp_path, real_paths=True)
    result = build(kw)
    assert set(result) == {"descriptor_bytes", "descriptor_sha256"}
    checked = validate(tmp_path, result["descriptor_bytes"], kw, lane=lane,
                       descriptor_pin=result["descriptor_sha256"])
    assert checked["ok"], checked
    value = checked["value"]
    assert value["lane"] == lane and value["fleet_sha256"] == sha(kw["fleet_bytes"])
    assert value["outer"] == kw["outer"] and value["expected_binding"] == kw["expected_binding"]
    assert value["worker"]["snapshot"] == kw["snapshot_path"]
    assert value["worker"]["snapshot-sha256"] == kw["snapshot_sha256"]
    assert value["worker"]["timeout-seconds"] == "30" and "query" not in value


@needs_shell
def test_a_one_lane_pilot_descriptor_admits_only_that_lane(tmp_path):
    kw = kwargs(tmp_path, real_paths=True)
    kw["lanes"] = ["fable-5"]
    raw = build(kw)["descriptor_bytes"]
    assert validate(tmp_path, raw, kw, lane="fable-5")["ok"]
    assert not validate(tmp_path, raw, kw, lane="codex-lead-1")["ok"]


@needs_shell
def test_a_changed_byte_no_longer_matches_the_returned_pin(tmp_path):
    kw = kwargs(tmp_path, real_paths=True)
    result = build(kw)
    raw = result["descriptor_bytes"].replace(b'"timeout_seconds":30', b'"timeout_seconds":31')
    assert raw != result["descriptor_bytes"]
    assert validate(tmp_path, raw, kw, descriptor_pin=result["descriptor_sha256"]) == {
        "ok": False, "reason": "q4_shared_session_config_refused"}


# 3 deterministic bytes -------------------------------------------------------------------------------------------

def test_output_is_deterministic_canonical_ascii_json_with_its_own_sha256(tmp_path):
    kw = kwargs(tmp_path)
    first, second = build(kw), build(copy.deepcopy(kw))
    assert first == second
    raw = first["descriptor_bytes"]
    assert type(raw) is bytes and first["descriptor_sha256"] == sha(raw)
    assert raw.isascii() and not raw.startswith(b"\xef\xbb\xbf") and raw.startswith(b"{") and raw.endswith(b"}")
    assert json.loads(raw) == document(kw)
    assert raw == json.dumps(document(kw), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def test_mapping_insertion_order_does_not_change_the_bytes(tmp_path):
    kw = kwargs(tmp_path)
    reordered = copy.deepcopy(kw)
    reordered["outer"] = dict(reversed(list(kw["outer"].items())))
    reordered["expected_binding"] = dict(reversed(list(kw["expected_binding"].items())))
    assert build(kw) == build(reordered)


def test_the_caller_inputs_are_not_mutated(tmp_path):
    kw = kwargs(tmp_path)
    before = copy.deepcopy(kw)
    build(kw)
    assert kw == before


def test_no_file_is_opened_or_stat_called(tmp_path, monkeypatch):
    kw = kwargs(tmp_path)
    module()

    def blocked(*args, **kwargs):
        raise AssertionError("filesystem access")

    with monkeypatch.context() as scoped:
        for owner, name in ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "scandir")):
            scoped.setattr(owner, name, blocked)
        result = build(kw)
    assert result["descriptor_sha256"] == sha(result["descriptor_bytes"])


NO_IO_APIS = ((builtins, "open"), (os, "open"), (os, "stat"), (os, "lstat"), (os, "scandir"))


def _refused_input(kw, producer, scoped):
    kw["lanes"] = []  # the producer refuses with a closed reason before any document exists


def _blocked_api_reached_inside_build(kw, producer, scoped):
    scoped.setattr(producer, "_fold", lambda text: os.stat(text))  # a producer helper hits the guard itself


def _helper_raises(exc):
    def arm(kw, producer, scoped):
        def helper(text):
            raise exc("synthetic failure")
        scoped.setattr(producer, "_fold", helper)
    return arm


@pytest.mark.parametrize("arm, exc, match", [
    (_refused_input, lambda producer: producer.DescriptorRefused, "^lanes_refused$"),
    (_blocked_api_reached_inside_build, lambda producer: AssertionError, "^filesystem access$"),
    (_helper_raises(KeyboardInterrupt), lambda producer: KeyboardInterrupt, "^synthetic failure$"),
    (_helper_raises(SystemExit), lambda producer: SystemExit, "^synthetic failure$"),
], ids=["producer-refuses", "blocked-api-reached-inside-build", "KeyboardInterrupt", "SystemExit"])
def test_the_no_io_guard_is_restored_when_build_raises_inside_it(tmp_path, monkeypatch, arm, exc, match):
    """RCO1 G1: build runs under all five blocked patches and raises through them (a closed refusal, the
    guard's own AssertionError reached from a producer helper, and two BaseException paths). Every original
    must be back before pytest formats anything, so one failure cannot abort the rest of the session."""
    kw = kwargs(tmp_path)
    producer = module()
    originals = [getattr(owner, name) for owner, name in NO_IO_APIS]

    def blocked(*args, **kwargs):
        raise AssertionError("filesystem access")

    with pytest.raises(exc(producer), match=match):
        with monkeypatch.context() as scoped:
            for owner, name in NO_IO_APIS:
                scoped.setattr(owner, name, blocked)
            arm(kw, producer, scoped)
            assert all(getattr(owner, name) is blocked for owner, name in NO_IO_APIS)
            build(kw)
    assert all(getattr(owner, name) is original for (owner, name), original in zip(NO_IO_APIS, originals))


# 4 refusals: producer refuses, and the validator refuses the same raw document ---------------------------------

def _set(section, key, value):
    def change(kw):
        kw[section][key] = value
    return change


def _drop(section, key):
    return lambda kw: kw[section].pop(key)


DOCUMENT_CASES = [
    ("pin_refused", lambda kw: kw.update(interpreter_sha256="A" * 64)),
    ("pin_refused", lambda kw: kw.update(binding_sha256="b" * 63)),
    ("pin_refused", lambda kw: kw.update(snapshot_sha256=None)),
    ("pin_refused", lambda kw: kw.update(runtime_manifest_sha256="g" * 64)),
    ("pin_refused", _set("outer", "worker-sha256", "F" * 64)),
    ("pin_refused", _set("outer", "runtime-generation", "d" * 39)),
    ("pin_refused", _set("outer", "runtime-revision", "latest")),
    ("pin_refused", _set("expected_binding", "pointer_sha256", "0" * 65)),
    ("pin_refused", lambda kw: kw.update(snapshot_path=kw["binding_path"])),
    ("pin_refused", lambda kw: kw.update(interpreter=kw["outer"]["outer-path"].upper())),
    ("lanes_refused", lambda kw: kw.update(lanes=[])),
    ("lanes_refused", lambda kw: kw.update(lanes=["fable-5", "fable-5"])),
    ("lanes_refused", lambda kw: kw.update(lanes=["fable-5", "codex-lead-2"])),
    ("lanes_refused", lambda kw: kw.update(lanes=["FABLE-5"])),
    ("lanes_refused", lambda kw: kw.update(lanes="fable-5")),
    ("path_refused", lambda kw: kw.update(snapshot_path="relative\\snapshot.json")),
    ("path_refused", lambda kw: kw.update(cwd=kw["cwd"].replace("\\", "/"))),
    ("path_refused", lambda kw: kw.update(bundle_root="\\\\server\\share\\bundle")),
    ("path_refused", lambda kw: kw.update(public_plan=kw["public_plan"] + "\\..\\x.json")),
    ("path_refused", lambda kw: kw.update(binding_path=kw["binding_path"] + "\\NUL.txt")),
    ("path_refused", lambda kw: kw.update(fixed_plan_path=kw["fixed_plan_path"] + ".")),
    ("path_refused", lambda kw: kw.update(interpreter=kw["interpreter"] + "\\a:b")),
    ("path_refused", lambda kw: kw.update(cwd=kw["cwd"] + "\\x\x01y")),
    ("path_refused", lambda kw: kw.update(cwd=kw["cwd"] + "\\")),
    ("path_refused", lambda kw: kw.update(cwd=kw["cwd"] + "/x")),
    ("path_refused", _set("outer", "outer-path", "C:\\elsewhere\\wd_q4_session_outer_synthetic.py")),
    ("path_refused", lambda kw: kw.update(runtime_manifest_path=kw["outer"]["runtime-root"] + "\\m.json")),
    ("path_refused", lambda kw: kw.update(runtime_manifest_path=kw["outer"]["runtime-root"])),
    ("path_refused", lambda kw: kw.update(runtime_manifest_path="Q" + kw["runtime_manifest_path"][1:])),
    ("limit_refused", lambda kw: kw.update(timeout_seconds=1801)),
    ("limit_refused", lambda kw: kw.update(timeout_seconds=0)),
    ("limit_refused", lambda kw: kw.update(max_output_bytes=65537)),
    ("limit_refused", lambda kw: kw.update(interpreter_max_bytes=134217729)),
    ("limit_refused", lambda kw: kw.update(timeout_seconds=True)),
    ("limit_refused", lambda kw: kw.update(timeout_seconds=30.0)),
    ("limit_refused", lambda kw: kw.update(timeout_seconds="30")),
    ("schema_refused", _drop("outer", "verifier-sha256")),
    ("schema_refused", _set("outer", "query", "what is current")),
    ("schema_refused", _drop("expected_binding", "projection_sha256")),
    ("schema_refused", _set("expected_binding", "query", "1" * 64)),
    ("schema_refused", lambda kw: kw.update(outer=list(kw["outer"].items()))),
    ("schema_refused", _set("outer", "runtime-root", "C:\\runtime\\x\x01y")),
]
CASE_IDS = [f"{i:02d}-{reason}" for i, (reason, _) in enumerate(DOCUMENT_CASES)]


@pytest.mark.parametrize("reason, change", DOCUMENT_CASES, ids=CASE_IDS)
def test_an_invalid_pin_path_lane_limit_or_shape_refuses_with_a_closed_reason(tmp_path, reason, change):
    kw = kwargs(tmp_path)
    change(kw)
    refused(reason, lambda: build(kw))


@needs_shell
@pytest.mark.parametrize("reason, change", DOCUMENT_CASES, ids=CASE_IDS)
def test_the_validator_also_refuses_each_refused_document(tmp_path, reason, change):
    kw = kwargs(tmp_path, real_paths=True)
    change(kw)
    raw = json.dumps(document(kw), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert validate(tmp_path, raw, kw) == {"ok": False, "reason": "q4_shared_session_config_refused"}


@pytest.mark.parametrize("change", [
    lambda kw: kw.update(expected_fleet_sha256="0" * 64),
    lambda kw: kw.update(expected_fleet_sha256=sha(kw["fleet_bytes"]).upper()),
    lambda kw: kw.update(fleet_bytes=kw["fleet_bytes"].decode()),
    lambda kw: kw.update(fleet_bytes=b"[]", expected_fleet_sha256=sha(b"[]")),
    lambda kw: kw.update(fleet_bytes=b'{"lanes":[],"lanes":[]}', expected_fleet_sha256=sha(b'{"lanes":[],"lanes":[]}')),
], ids=["wrong-pin", "upper-pin", "text-not-bytes", "not-object", "duplicate-key"])
def test_fleet_bytes_must_match_their_pin_and_parse_strictly(tmp_path, change):
    kw = kwargs(tmp_path)
    change(kw)
    refused("fleet_refused", lambda: build(kw))


def _fleet(lanes, tools="codex-tools-1", extra=""):
    body = json.dumps({"lanes": [{"agent": x} for x in lanes], "tools_supervisor": {"agent": tools}})
    return (body[:-1] + extra + "}").encode()


@needs_shell
@pytest.mark.parametrize("raw", [
    _fleet(["codex-lead-1", "claude-rco-1", "claude-rco-2"]),
    _fleet(["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5"], tools="fable-5"),
    _fleet(["codex-lead-1", "claude-rco-1", "claude-rco-2", "claude-rco-2"]),
    _fleet(["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5"], extra=',"LANES":[]'),
    _fleet(["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5", "codex-tools-1"]),
    _fleet(["codex-lead-1", "claude-rco-1", "claude-rco-2", "codex-tools-1"], tools="fable-5"),
], ids=["three-lanes", "wrong-tools", "duplicate-agent", "case-colliding-key", "five-lanes", "swapped-tools"])
def test_a_fleet_the_validator_refuses_is_refused_before_any_descriptor(tmp_path, raw):
    kw = kwargs(tmp_path, real_paths=True)
    kw.update(fleet_bytes=raw, expected_fleet_sha256=sha(raw))
    refused("fleet_refused", lambda: build(kw))
    doc = json.dumps(document(kw), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    assert not validate(tmp_path, doc, kw)["ok"]


def test_a_refusal_carries_no_caller_value(tmp_path):
    kw = kwargs(tmp_path)
    kw["snapshot_path"] = "relative\\" + SENTINEL
    error = refused("path_refused", lambda: build(kw))
    assert SENTINEL not in repr(error) and SENTINEL not in str(error)
    assert error.__cause__ is None and error.__context__ is None
