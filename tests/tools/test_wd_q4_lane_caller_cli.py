"""Tests-first CLI contract candidate; NOT a live Mind or Windows peer witness.

Proposed interface: no arguments means OFF without environment/config/stdin reads;
--enabled accepts one bounded JSON query from stdin. WD_Q4_CALLER_DESCRIPTOR and
WD_Q4_CALLER_FLEET each have _PATH/_SHA256 environment pairs. Identity comes from
AGENT_BRIDGE_AGENT, AGENT_BRIDGE_AGENT_UUID, AGENT_BRIDGE_RUN_ID and an in-process
hash of AGENT_BRIDGE_OWNER_TOKEN. These are cooperative declarations, not auth.

Only the adapter is replaced by a recorder in these unit tests. Its synthetic
return cannot establish a working native peer, cleanup or an installed route.
The implementation file must remain absent until Lead preserves the RED run.
"""
import builtins
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "tools/wd_q4_lane_caller_cli.py"
LANE = "codex-tools-1"
UUID = "00000009-0000-4000-8000-000000000000"
QUERY_CANARY = "synthetic-query-canary-never-an-answer"
TOKEN_CANARY = "synthetic-owner-token-never-output"
REFUSAL = '{"error":"lane_caller_refused","authority":"none"}\n'
LIMIT = 65536


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_cli():
    # Deliberately fails if the implementation is absent; never importorskip.
    spec = importlib.util.spec_from_file_location("synthetic_q4_cli_under_test", CLI)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NoRead:
    @property
    def buffer(self):
        return self

    def read(self, *args, **kwargs):
        raise AssertionError("OFF read stdin")


def forbidden(*args, **kwargs):
    raise AssertionError("unexpected OFF IO")


class NoEnvironment(dict):
    get = __getitem__ = __iter__ = forbidden


def test_off_reads_no_environment_config_stdin_or_chain(monkeypatch, capsys):
    cli = load_cli()
    # Lexical guards restore before pytest formats any failed assertion.
    with monkeypatch.context() as guard:
        guard.setattr(os, "environ", NoEnvironment())
        guard.setattr(os, "getcwd", forbidden)
        guard.setattr(builtins, "open", forbidden)
        guard.setattr(Path, "open", forbidden)
        guard.setattr(sys, "stdin", NoRead())
        code = cli.main([])
    observed = capsys.readouterr()
    assert code == 0
    assert observed.out == '{"status":"OFF"}\n'
    assert observed.err == ""


@pytest.fixture
def case(tmp_path, monkeypatch):
    files = {}

    def put(name, raw):
        path = tmp_path / name
        path.write_bytes(raw)
        files[name] = path
        return str(path)

    worktree = tmp_path / "lane"
    worktree.mkdir()
    monkeypatch.chdir(worktree)
    fleet = canon({"lanes": [], "tools_supervisor": {
        "agent": LANE, "agent_uuid": UUID, "worktree": str(worktree)}})
    put("fleet.json", fleet)
    binding = b'{"schema":"wd.q4-lane-binding.v1","synthetic":true}'
    fixed_plan = b'{"backend":"public-snapshot-v1","synthetic":true}'
    snapshot = b"synthetic-fixed-bundle"
    runtime = b"synthetic-runtime-manifest"
    interpreter = b"synthetic-interpreter-never-executed"
    binding_path = put("binding.json", binding)
    fixed_path = put("fixed.json", fixed_plan)
    snapshot_path = put("snapshot.bin", snapshot)
    runtime_path = put("runtime.json", runtime)
    interpreter_path = put("interpreter.bin", interpreter)
    public_plan = put("public-plan.json", b'{"synthetic":true}')
    expected_binding = {key: "2" * 64 for key in (
        "pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
        "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256")}
    descriptor = {
        "schema": "wd.q4-shared-session-config.v2", "fleet_sha256": sha(fleet),
        "lanes": [LANE], "bundle_root": str(tmp_path), "public_plan": public_plan,
        "interpreter": interpreter_path, "cwd": str(worktree),
        "binding_path": binding_path, "fixed_plan_path": fixed_path,
        "snapshot_path": snapshot_path, "runtime_manifest_path": runtime_path,
        "interpreter_sha256": sha(interpreter), "binding_sha256": sha(binding),
        "fixed_plan_sha256": sha(fixed_plan), "snapshot_sha256": sha(snapshot),
        "runtime_manifest_sha256": sha(runtime), "interpreter_max_bytes": LIMIT,
        "timeout_seconds": 60, "max_output_bytes": LIMIT,
        "outer": {}, "expected_binding": expected_binding,
    }
    put("descriptor.json", canon(descriptor))
    environment = {
        "AGENT_BRIDGE_AGENT": LANE, "AGENT_BRIDGE_AGENT_UUID": UUID,
        "AGENT_BRIDGE_RUN_ID": "synthetic-cli-run", "AGENT_BRIDGE_OWNER_TOKEN": TOKEN_CANARY,
        "WD_Q4_CALLER_DESCRIPTOR_PATH": str(files["descriptor.json"]),
        "WD_Q4_CALLER_DESCRIPTOR_SHA256": sha(files["descriptor.json"].read_bytes()),
        "WD_Q4_CALLER_FLEET_PATH": str(files["fleet.json"]),
        "WD_Q4_CALLER_FLEET_SHA256": sha(fleet),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    adapter = types.ModuleType("tools.wd_q4_lane_caller_adapter")
    calls = []
    outcome = {}

    def record(**kwargs):
        calls.append(kwargs)
        if "exception" in outcome:
            raise outcome["exception"]
        result = {"schema": "wd.q4-lane-caller-result.v1", "status": "REFUSED",
                  "lane": LANE, "lane_binding": "DECLARED_CONSISTENT",
                  "lane_authentication": "UNKNOWN", "cleanup": "chain_confirmed",
                  "inner_status": "session_refused", "result_count": 0,
                  "answer": None, "answer_sha256": None,
                  "identity_sha256": sha(kwargs["identity_bytes"]),
                  "descriptor_sha256": kwargs["expected_descriptor_sha256"],
                  "call_id": "1" * 32}
        if outcome.get("validated"):
            answer = {"schema": "wd.q4-lane-answer.v1", "lane": LANE,
                      "status": "ABSTAIN", "reason": "", "result_count": 0, "results": [],
                      "binding": dict(expected_binding), "trust": {
                          "lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                          "publisher_authentication": "UNKNOWN", "projection_public_attestation": "UNKNOWN",
                          "transport": "caller_injected", "projection": "caller_pinned"}}
            result.update(status="VALIDATED", inner_status="ABSTAIN", answer=answer,
                          answer_sha256=sha(canon(answer)))
        result.update(outcome.get("result", {}))
        return result

    adapter.call_lane_session = record
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)
    import tools
    monkeypatch.setattr(tools, "wd_q4_lane_caller_adapter", adapter, raising=False)
    query = canon({"situation": QUERY_CANARY, "time": "2026-01-01T00:00:00Z"})
    return {"files": files, "descriptor": descriptor, "env": environment,
            "calls": calls, "outcome": outcome, "query": query,
            "worktree": worktree, "cli": load_cli()}


def invoke(case, monkeypatch, raw=None, argv=None):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(
        case["query"] if raw is None else raw), encoding="utf-8"))
    return case["cli"].main(["--enabled"] if argv is None else argv)


def assert_refused(code, capsys):
    observed = capsys.readouterr()
    assert code == 2
    assert observed.out == ""
    assert observed.err == REFUSAL
    assert QUERY_CANARY not in observed.out + observed.err
    assert TOKEN_CANARY not in observed.out + observed.err


@pytest.mark.parametrize("key", [
    "AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID", "AGENT_BRIDGE_RUN_ID",
    "AGENT_BRIDGE_OWNER_TOKEN", "WD_Q4_CALLER_DESCRIPTOR_SHA256", "WD_Q4_CALLER_FLEET_SHA256",
])
def test_missing_identity_or_pin_refuses_before_adapter(case, monkeypatch, capsys, key):
    monkeypatch.delenv(key)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("key,value", [
    ("AGENT_BRIDGE_AGENT", "fable-5"),
    ("AGENT_BRIDGE_AGENT_UUID", "00000008-0000-4000-8000-000000000000"),
    ("WD_Q4_CALLER_DESCRIPTOR_SHA256", "0" * 64),
    ("WD_Q4_CALLER_FLEET_SHA256", "0" * 64),
])
def test_wrong_declared_identity_or_pin_refuses_before_adapter(case, monkeypatch, capsys, key, value):
    monkeypatch.setenv(key, value)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_actual_cwd_must_match_pinned_fleet_worktree(case, monkeypatch, capsys, tmp_path):
    other = tmp_path / "other-lane"
    other.mkdir()
    monkeypatch.chdir(other)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("name", ["binding.json", "fixed.json", "snapshot.bin", "runtime.json", "interpreter.bin"])
def test_actual_input_bytes_must_match_descriptor_pins(case, monkeypatch, capsys, name):
    case["files"][name].write_bytes(b"tampered-synthetic-input")
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("raw", [
    b"", b"\xff", b"x" * (LIMIT + 1), b"[]", b'{"situation":"x"}',
    b'{"situation":"x","time":"2026-01-01T00:00:00Z","extra":1}',
    b'{"situation":"x","situation":"y","time":"2026-01-01T00:00:00Z"}',
], ids=["empty", "invalid-utf8", "oversized", "array", "missing-time", "extra-field", "duplicate-field"])
def test_unknown_or_unbounded_stdin_refuses_before_adapter(case, monkeypatch, capsys, raw):
    assert_refused(invoke(case, monkeypatch, raw), capsys)
    assert case["calls"] == []


def test_query_is_not_accepted_from_argv(case, monkeypatch, capsys):
    assert_refused(invoke(case, monkeypatch, argv=["--enabled", "--query", QUERY_CANARY]), capsys)
    assert case["calls"] == []


def test_delegation_once_builds_only_cooperative_in_process_identity(case, monkeypatch, capsys):
    # A synthetic refusal is enough to inspect the call without inventing native success.
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1
    call = case["calls"][0]
    identity = json.loads(call["identity_bytes"])
    assert identity["agent"] == LANE and identity["agent_uuid"] == UUID
    assert identity["caller_pid"] == os.getpid()
    assert identity["cwd"] == str(Path.cwd())
    assert identity["worktree"] == str(case["worktree"])
    assert identity["owner_token_sha256"] == sha(TOKEN_CANARY.encode("utf-8"))
    assert call["expected_identity_sha256"] == sha(call["identity_bytes"])
    assert TOKEN_CANARY not in call["identity_bytes"].decode("ascii")
    assert call["situation"] == QUERY_CANARY
    assert call["inputs"]["binding_bytes"] == case["files"]["binding.json"].read_bytes()


def test_synthetic_adapter_abstention_is_bounded_canonical_and_never_authentication(case, monkeypatch, capsys):
    # This recorder does not exercise a native peer or establish real cleanup.
    case["outcome"]["validated"] = True
    assert invoke(case, monkeypatch) == 0
    observed = capsys.readouterr()
    assert observed.err == "" and len(observed.out.encode("ascii")) <= LIMIT
    result = json.loads(observed.out)
    assert observed.out == canon(result).decode("ascii") + "\n"
    assert result["status"] == "VALIDATED" and result["inner_status"] == "ABSTAIN"
    assert result["lane"] == LANE and result["lane_binding"] == "DECLARED_CONSISTENT"
    assert result["lane_authentication"] == "UNKNOWN"
    assert result["answer_sha256"] == sha(canon(result["answer"]))
    assert QUERY_CANARY not in observed.out and TOKEN_CANARY not in observed.out
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("result", [
    {"lane_authentication": "AUTHENTICATED"},
    {"lane_binding": "AUTHENTICATED"}, {"lane": "fable-5"},
    {"cleanup": "unknown", "status": "UNKNOWN"},
    {"unexpected": QUERY_CANARY},
    {"answer": {"stored_text": "z" * (LIMIT + 1)}},
], ids=["strong-auth", "strong-binding", "wrong-lane", "unknown-cleanup", "extra-field", "oversized"])
def test_untrusted_oversized_or_stronger_auth_output_is_not_published(case, monkeypatch, capsys, result):
    case["outcome"]["result"] = result
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) <= 1


@pytest.mark.parametrize("exc", [ValueError(QUERY_CANARY), RuntimeError(TOKEN_CANARY), TimeoutError(QUERY_CANARY)],
                         ids=["value-error", "runtime-error", "timeout"])
def test_unavailable_chain_uses_fixed_refusal_without_exception_detail(case, monkeypatch, capsys, exc):
    case["outcome"]["exception"] = exc
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1
