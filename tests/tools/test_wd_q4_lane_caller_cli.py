"""Tests-first CLI contract candidate; NOT a live Mind or Windows peer witness.

Proposed interface: no arguments means OFF without environment/config/stdin reads;
--enabled accepts one bounded JSON query from stdin. The existing
WD_Q4_SHARED_DESCRIPTOR_PATH/SHA256 pair selects the descriptor. Its bundle_root
contains deployment-manifest.json, externally pinned by
WD_REBOOT_EXPECTED_MANIFEST_HASH, and wd-fleet.json. Fleet bytes must match BOTH
the descriptor and the anchored manifest. No CALLER aliases substitute for this.
Identity comes from
AGENT_BRIDGE_AGENT, AGENT_BRIDGE_AGENT_UUID, AGENT_BRIDGE_RUN_ID and an in-process
hash of AGENT_BRIDGE_OWNER_TOKEN. These are cooperative declarations, not auth.

Only the adapter is replaced by a recorder in these unit tests. Its synthetic
return cannot establish a working native peer, cleanup or an installed route.
Source A43589FD stays unchanged until Lead preserves this revision's RED run.
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
LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
UUIDS = {lane: f"0000000{index + 1}-0000-4000-8000-000000000000" for index, lane in enumerate(LANES)}
UUIDS[LANE] = UUID
QUERY_CANARY = "synthetic-query-canary-never-an-answer"
TOKEN_CANARY = "synthetic-owner-token-never-output"
REFUSAL = '{"error":"lane_caller_refused","authority":"none"}\n'
LIMIT = 65536
MANIFEST_LIMIT = 1024 * 1024


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_cli():
    # Deliberately loads the actual candidate; never importorskip.
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

    worktrees = {lane: tmp_path / "lanes" / lane for lane in LANES}
    for directory in worktrees.values():
        directory.mkdir(parents=True)
    worktree = worktrees[LANE]
    monkeypatch.chdir(worktree)
    members = {lane: {"agent": lane, "agent_uuid": UUIDS[lane], "worktree": str(worktrees[lane])}
               for lane in LANES}
    runtime_root = tmp_path / "synthetic-bridge-runtime"
    for lane in LANES:
        (runtime_root / "shared" / ".codex-audit" / lane / "q4-caller-state").mkdir(parents=True)
    fleet = canon({"runtime_root": str(runtime_root),
                   "lanes": [members[lane] for lane in LANES if lane != LANE],
                   "tools_supervisor": members[LANE]})
    put("wd-fleet.json", fleet)
    manifest = {"schema_version": 1, "source_commit": "a" * 40,
                "files": {"wd-fleet.json": sha(fleet)}}
    put("deployment-manifest.json", canon(manifest))
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
        "lanes": list(LANES), "bundle_root": str(tmp_path), "public_plan": public_plan,
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
        "WD_Q4_SHARED_DESCRIPTOR_PATH": str(files["descriptor.json"]),
        "WD_Q4_SHARED_DESCRIPTOR_SHA256": sha(files["descriptor.json"].read_bytes()),
        "WD_REBOOT_EXPECTED_MANIFEST_HASH": sha(files["deployment-manifest.json"].read_bytes()),
    }
    for key in ("WD_Q4_CALLER_DESCRIPTOR_PATH", "WD_Q4_CALLER_DESCRIPTOR_SHA256",
                "WD_Q4_CALLER_FLEET_PATH", "WD_Q4_CALLER_FLEET_SHA256"):
        monkeypatch.delenv(key, raising=False)
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    # Use the real adapter exception types even though the call is synthetic.
    spec = importlib.util.spec_from_file_location(
        "synthetic_real_caller_exceptions", ROOT / "tools/wd_q4_lane_caller_adapter.py")
    real_adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(real_adapter)
    adapter = types.ModuleType("tools.wd_q4_lane_caller_adapter")
    adapter.CallerCleanupUnknown = real_adapter.CallerCleanupUnknown
    adapter.CallerRefused = real_adapter.CallerRefused
    calls = []
    outcome = {}

    def record(**kwargs):
        calls.append(kwargs)
        # The recorder tests CLI formatting only. Synthetic owner transitions
        # exercise guard sequencing; they do not prove real native cleanup.
        item, context = kwargs["_owner_guard"], kwargs["_owner_context"]
        assert item._accept_owner_context(context) is context
        if "exception" in outcome:
            raise outcome["exception"]
        selected_lane = json.loads(kwargs["identity_bytes"])["agent"]
        result = {"schema": "wd.q4-lane-caller-result.v1", "status": "REFUSED",
                  "lane": selected_lane, "lane_binding": "DECLARED_CONSISTENT",
                  "lane_authentication": "UNKNOWN", "cleanup": "chain_confirmed",
                  "inner_status": "session_refused", "result_count": 0,
                  "answer": None, "answer_sha256": None,
                  "identity_sha256": sha(kwargs["identity_bytes"]),
                  "descriptor_sha256": kwargs["expected_descriptor_sha256"],
                  "call_id": "1" * 32}
        if outcome.get("validated"):
            for name in ("adapter", "attestation", "transport"):
                ticket = context._ticket(name)
                assert context._accept_ticket(name, ticket) is ticket
                ticket._begin()
                ticket._complete()
            items = [] if "stored_text" not in outcome else [{
                "ref_sha256": sha(b"synthetic-public-ref"),
                "doc_id_sha256": sha(b"synthetic-public-doc"), "stored_text": outcome["stored_text"]}]
            inner_status = "ANSWER" if items else "ABSTAIN"
            answer = {"schema": "wd.q4-lane-answer.v1", "lane": selected_lane,
                      "status": inner_status, "reason": "", "result_count": len(items), "results": items,
                      "binding": dict(expected_binding), "trust": {
                          "lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                          "publisher_authentication": "UNKNOWN", "projection_public_attestation": "UNKNOWN",
                          "transport": "caller_injected", "projection": "caller_pinned"}}
            result.update(status="VALIDATED", inner_status=inner_status, answer=answer, result_count=len(items),
                          answer_sha256=sha(canon(answer)))
        result.update(outcome.get("result", {}))
        return result

    adapter.call_lane_session = record
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)
    import tools
    monkeypatch.setattr(tools, "wd_q4_lane_caller_adapter", adapter, raising=False)
    query = canon({"situation": QUERY_CANARY, "time": "2026-01-01T00:00:00Z"})
    values = {"files": files, "descriptor": descriptor, "manifest": manifest, "env": environment,
            "calls": calls, "outcome": outcome, "query": query,
            "worktree": worktree, "worktrees": worktrees, "cli": load_cli(), "adapter": adapter,
            "real_adapter": real_adapter}
    values["latest"] = prepare_synthetic_latest(values, monkeypatch)
    return values


def invoke(case, monkeypatch, raw=None, argv=None):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(
        case["query"] if raw is None else raw), encoding="utf-8"))
    return case["cli"].main(["--enabled"] if argv is None else argv)


def prepare_synthetic_latest(case, monkeypatch):
    root = case["files"]["descriptor.json"].parent / "synthetic-index"
    root.mkdir()
    pointer = root / "q4-latest.json"
    pointer.write_bytes(b"synthetic-index-generation-one")
    binding = case["descriptor"]["expected_binding"]
    binding["pointer_sha256"] = sha(pointer.read_bytes())
    plan = {"schema": "wd.q4-public-wrapper-plan.v1", "config": {"descriptor_sha256": binding["descriptor_sha256"]},
            "root": str(root), "policy_path": str(root / "policy"), "roster_path": str(root / "roster"),
            "public_policy_path": str(root / "public-policy"),
            **{key: binding[key] for key in ("pointer_sha256", "source_anchor_sha256", "encoder_sha256",
                                              "policy_sha256", "roster_sha256", "public_policy_sha256")}}
    plan_raw = canon(plan)
    case["files"]["public-plan.json"].write_bytes(plan_raw)
    binding_raw = canon({"schema": "wd.q4-lane-binding.v1", "public_plan_sha256": sha(plan_raw), "binding": binding})
    case["files"]["binding.json"].write_bytes(binding_raw)
    case["descriptor"]["binding_sha256"] = sha(binding_raw)
    descriptor_raw = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(descriptor_raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(descriptor_raw))
    return pointer


def test_stale_snapshot_refuses_before_query_and_adapter(case, monkeypatch, capsys):
    pointer = case["latest"]
    case["outcome"]["validated"] = True
    pointer.write_bytes(b"synthetic-index-generation-two")
    assert invoke(case, monkeypatch) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == REFUSAL
    assert case["calls"] == []


def test_snapshot_changed_during_query_is_never_published(case, monkeypatch, capsys):
    pointer = case["latest"]
    case["outcome"]["validated"] = True
    original = case["adapter"].call_lane_session
    def update_pointer(**kwargs):
        result = original(**kwargs)
        pointer.write_bytes(b"synthetic-index-generation-two")
        return result
    monkeypatch.setattr(case["adapter"], "call_lane_session", update_pointer)
    assert invoke(case, monkeypatch) == 2
    out = capsys.readouterr()
    assert out.out == "" and out.err == REFUSAL
    assert len(case["calls"]) == 1
    state = case["files"]["descriptor.json"].parent / "synthetic-bridge-runtime" / "shared" / ".codex-audit" / LANE / "q4-caller-state"
    assert not (state / (LANE + ".pending.json")).exists()


def test_missing_current_pointer_refuses_before_query(case, monkeypatch, capsys):
    case["latest"].unlink()
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_unpinned_public_plan_refuses_before_query(case, monkeypatch, capsys):
    case["files"]["public-plan.json"].write_bytes(b'{}')
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def assert_refused(code, capsys, expected_code=2):
    observed = capsys.readouterr()
    assert code == expected_code
    assert observed.out == ""
    assert observed.err == REFUSAL
    assert QUERY_CANARY not in observed.out + observed.err
    assert TOKEN_CANARY not in observed.out + observed.err


@pytest.mark.parametrize("key", [
    "AGENT_BRIDGE_AGENT", "AGENT_BRIDGE_AGENT_UUID", "AGENT_BRIDGE_RUN_ID",
    "AGENT_BRIDGE_OWNER_TOKEN", "WD_Q4_SHARED_DESCRIPTOR_PATH",
    "WD_Q4_SHARED_DESCRIPTOR_SHA256", "WD_REBOOT_EXPECTED_MANIFEST_HASH",
])
def test_missing_identity_or_pin_refuses_before_adapter(case, monkeypatch, capsys, key):
    monkeypatch.delenv(key)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("key,value", [
    ("AGENT_BRIDGE_AGENT", "fable-5"),
    ("AGENT_BRIDGE_AGENT_UUID", "00000008-0000-4000-8000-000000000000"),
    ("WD_Q4_SHARED_DESCRIPTOR_SHA256", "0" * 64),
    ("WD_REBOOT_EXPECTED_MANIFEST_HASH", "0" * 64),
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
    {"unexpected": QUERY_CANARY},
    {"answer": {"stored_text": "z" * (LIMIT + 1)}},
], ids=["strong-auth", "strong-binding", "wrong-lane", "extra-field", "oversized"])
def test_untrusted_oversized_or_stronger_auth_output_is_not_published(case, monkeypatch, capsys, result):
    case["outcome"]["result"] = result
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) <= 1


@pytest.mark.parametrize("exc,expected_code", [
    (ValueError(QUERY_CANARY), 2), (RuntimeError(TOKEN_CANARY), 2), (TimeoutError(QUERY_CANARY), 4),
    (KeyboardInterrupt(), 130), (SystemExit(7), 130), (SystemExit(0), 130),
    (SystemExit(None), 130), (SystemExit(TOKEN_CANARY), 130),
], ids=["value-error", "runtime-error", "timeout", "keyboard-interrupt",
        "exit-nonzero", "exit-zero", "exit-none", "exit-detail"])
def test_unavailable_chain_uses_fixed_refusal_without_exception_detail(case, monkeypatch, capsys, exc, expected_code):
    legacy_baseline(case, monkeypatch)
    case["outcome"]["exception"] = exc
    assert_refused(invoke(case, monkeypatch), capsys, expected_code)
    assert len(case["calls"]) == 1


def legacy_baseline(case, monkeypatch):
    """Reach PR1985's outcome checks for RED; new code must use SHARED instead.

    These are test-only old settings, never an implementation fallback. They
    isolate E1/E2 from the separate launcher-name mismatch on the old Source.
    """
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_PATH", str(case["files"]["descriptor.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_SHA256", sha(case["files"]["descriptor.json"].read_bytes()))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_PATH", str(case["files"]["wd-fleet.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_SHA256", sha(case["files"]["wd-fleet.json"].read_bytes()))


def replace_manifest(case, monkeypatch, raw):
    case["files"]["deployment-manifest.json"].write_bytes(raw)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", sha(raw))


@pytest.mark.parametrize("lane", LANES, ids=LANES)
def test_all_five_shared_descriptor_lanes_are_declared_consistent(case, monkeypatch, capsys, lane):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
    monkeypatch.chdir(case["worktrees"][lane])
    case["outcome"]["validated"] = True
    assert invoke(case, monkeypatch) == 0
    observed = capsys.readouterr()
    assert observed.err == ""
    result = json.loads(observed.out)
    assert result["lane"] == lane and result["lane_binding"] == "DECLARED_CONSISTENT"
    assert result["lane_authentication"] == "UNKNOWN"
    assert QUERY_CANARY not in observed.out and TOKEN_CANARY not in observed.out
    assert len(case["calls"]) == 1
    identity = json.loads(case["calls"][0]["identity_bytes"])
    assert identity["worktree"] == str(case["worktrees"][lane])
    assert identity["cwd"] == str(Path.cwd())


def test_legacy_caller_settings_cannot_replace_missing_shared_descriptor(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    monkeypatch.delenv("WD_Q4_SHARED_DESCRIPTOR_PATH")
    monkeypatch.delenv("WD_Q4_SHARED_DESCRIPTOR_SHA256")
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_stale_caller_aliases_cannot_override_anchored_shared_configuration(case, monkeypatch, capsys):
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_PATH", str(case["files"]["descriptor.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_DESCRIPTOR_SHA256", "0" * 64)
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_PATH", str(case["files"]["wd-fleet.json"]))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_SHA256", "0" * 64)
    case["outcome"]["validated"] = True
    assert invoke(case, monkeypatch) == 0
    observed = capsys.readouterr()
    assert observed.err == "" and json.loads(observed.out)["lane_authentication"] == "UNKNOWN"
    assert len(case["calls"]) == 1


def test_manifest_bytes_must_match_external_anchor(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    case["files"]["deployment-manifest.json"].write_bytes(b'{"files":{}}')
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("state", [
    "missing", "empty", "bad-json", "duplicate-files", "bad-schema", "boolean-schema",
    "missing-fleet-pin", "wrong-fleet-pin", "non-hex-fleet-pin", "oversized",
], ids=["missing", "empty", "bad-json", "duplicate-files", "bad-schema", "boolean-schema",
        "missing-fleet-pin", "wrong-fleet-pin", "non-hex-fleet-pin", "oversized"])
def test_anchored_manifest_still_requires_closed_fleet_pin_contract(case, monkeypatch, capsys, state):
    legacy_baseline(case, monkeypatch)
    manifest = json.loads(canon(case["manifest"]))
    if state == "missing":
        case["files"]["deployment-manifest.json"].unlink()
    else:
        if state == "empty":
            raw = b""
        elif state == "bad-json":
            raw = b"not-json"
        elif state == "duplicate-files":
            raw = b'{"schema_version":1,"files":{},"files":{}}'
        else:
            if state == "bad-schema":
                manifest["schema_version"] = 2
            elif state == "boolean-schema":
                manifest["schema_version"] = True
            elif state == "missing-fleet-pin":
                manifest["files"].pop("wd-fleet.json")
            elif state == "wrong-fleet-pin":
                manifest["files"]["wd-fleet.json"] = "0" * 64
            elif state == "non-hex-fleet-pin":
                manifest["files"]["wd-fleet.json"] = "not-a-pin"
            elif state == "oversized":
                manifest["padding"] = "x" * MANIFEST_LIMIT
            raw = canon(manifest)
        replace_manifest(case, monkeypatch, raw)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_descriptor_fleet_pin_must_equal_manifest_pin(case, monkeypatch, capsys):
    case["descriptor"]["fleet_sha256"] = "0" * 64
    raw = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(raw)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(raw))
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


def test_rehashing_tampered_fleet_and_descriptor_cannot_replace_manifest_anchor(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    fleet = case["files"]["wd-fleet.json"].read_bytes() + b" "
    case["files"]["wd-fleet.json"].write_bytes(fleet)
    case["descriptor"]["fleet_sha256"] = sha(fleet)
    descriptor = canon(case["descriptor"])
    case["files"]["descriptor.json"].write_bytes(descriptor)
    monkeypatch.setenv("WD_Q4_SHARED_DESCRIPTOR_SHA256", sha(descriptor))
    monkeypatch.setenv("WD_Q4_CALLER_FLEET_SHA256", sha(fleet))
    assert_refused(invoke(case, monkeypatch), capsys)
    assert case["calls"] == []


@pytest.mark.parametrize("status,cleanup,inner,expected_code", [
    ("UNKNOWN", "unknown", "transport_cleanup_unknown", 3),
    ("UNKNOWN", "unknown", "attestation_cleanup_unknown", 3),
    ("REFUSED", "unknown", "session_refused", 3),
    ("VALIDATED", "unknown", "ANSWER", 3),
    ("TIMEOUT", "unknown", "timeout", 3),
    ("TIMEOUT", "chain_confirmed", "timeout", 4),
], ids=["transport-unknown", "attestation-unknown", "refused-but-unknown",
        "validated-but-unknown", "unknown-overrides-timeout", "timeout-confirmed"])
def test_cleanup_uncertainty_and_timeout_are_distinct_terminal_outcomes(
        case, monkeypatch, capsys, status, cleanup, inner, expected_code):
    legacy_baseline(case, monkeypatch)
    case["outcome"]["result"] = {"status": status, "cleanup": cleanup, "inner_status": inner}
    assert_refused(invoke(case, monkeypatch), capsys, expected_code)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("situation", ["status", "done", "codex-lead-1", "a"],
                         ids=["status-word", "done-word", "lane-name", "single-letter"])
def test_public_stored_text_and_metadata_may_coincide_with_short_queries(case, monkeypatch, capsys, situation):
    legacy_baseline(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text="status done codex-lead-1 synthetic public record")
    raw = canon({"situation": situation, "time": "2026-01-01T00:00:00Z"})
    assert invoke(case, monkeypatch, raw) == 0
    observed = capsys.readouterr()
    result = json.loads(observed.out)
    assert observed.err == "" and result["status"] == "VALIDATED"
    assert result["answer"]["results"][0]["stored_text"] == case["outcome"]["stored_text"]
    assert result["lane_authentication"] == "UNKNOWN"
    assert TOKEN_CANARY not in observed.out and len(case["calls"]) == 1


def test_token_secrecy_remains_required_even_for_validated_public_output(case, monkeypatch, capsys):
    legacy_baseline(case, monkeypatch)
    case["outcome"].update(validated=True, stored_text=TOKEN_CANARY)
    assert_refused(invoke(case, monkeypatch), capsys)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("lane", LANES, ids=LANES)
@pytest.mark.parametrize("exception_name,expected", [
    ("CallerCleanupUnknown", 3), ("CallerRefused", 2),
], ids=["cleanup-unknown", "ordinary-refusal"])
def test_adapter_precheck_exception_maps_to_cleanup_or_refusal_without_leaks(
        case, monkeypatch, capsys, lane, exception_name, expected):
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
    monkeypatch.chdir(case["worktrees"][lane])
    # This synthetic detail must never be rendered, including on stderr.
    case["outcome"]["exception"] = getattr(case["adapter"], exception_name)(
        "synthetic-exception-detail " + QUERY_CANARY + " " + TOKEN_CANARY)
    assert_refused(invoke(case, monkeypatch), capsys, expected)
    assert len(case["calls"]) == 1


@pytest.mark.parametrize("lane", LANES, ids=LANES)
def test_enabled_cli_and_real_adapter_parent_path_needs_no_model_import(case, monkeypatch, capsys, lane):
    """The session is synthetic; this is not a live peer or installed-bundle test."""
    import importlib
    import tools

    real = case["real_adapter"]
    monkeypatch.setitem(sys.modules, "tools.wd_q4_lane_caller_adapter", real)
    monkeypatch.setattr(tools, "wd_q4_lane_caller_adapter", real, raising=False)
    monkeypatch.setattr(real, "_QUARANTINED", False)
    monkeypatch.setattr(real, "_HOLD", [])
    monkeypatch.setenv("AGENT_BRIDGE_AGENT", lane)
    monkeypatch.setenv("AGENT_BRIDGE_AGENT_UUID", UUIDS[lane])
    monkeypatch.chdir(case["worktrees"][lane])
    calls = []
    def fake_session(**kwargs):
        calls.append(kwargs["lane"])
        # Synthetic stand-ins preserve this test's parent-import scope only;
        # they are not native transport or attestation cleanup evidence.
        item, context = kwargs["_owner_guard"], kwargs["_owner_context"]
        assert item._accept_owner_context(context) is context
        for name in ("transport", "attestation"):
            ticket = context._ticket(name)
            assert context._accept_ticket(name, ticket) is ticket
            ticket._begin()
            ticket._complete()
        return {"schema": "wd.q4-lane-answer.v1", "lane": lane, "status": "ABSTAIN",
                "reason": "", "result_count": 0, "results": [],
                "binding": dict(case["descriptor"]["expected_binding"]), "trust": {
                    "lane_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                    "publisher_authentication": "UNKNOWN", "projection_public_attestation": "UNKNOWN",
                    "transport": "caller_injected", "projection": "caller_pinned"}}
    monkeypatch.setattr(real.session, "query_session", fake_session)
    blocked = {"numpy", "faiss", "torch", "sentence_transformers", "transformers"}
    attempted = []
    old_import, old_module = builtins.__import__, importlib.import_module
    def reject(name):
        if name.split(".")[0] in blocked:
            attempted.append(name.split(".")[0])
            raise ImportError("synthetic parent dependency blocked")
    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        reject(name)
        return old_import(name, globals, locals, fromlist, level)
    def guarded_module(name, package=None):
        reject(name)
        return old_module(name, package)
    monkeypatch.setattr(builtins, "__import__", guarded)
    monkeypatch.setattr(importlib, "import_module", guarded_module)
    code = invoke(case, monkeypatch)
    observed = capsys.readouterr()
    assert code == 0 and observed.err == ""
    result = json.loads(observed.out)
    assert result["lane"] == lane and result["inner_status"] == "ABSTAIN"
    assert result["lane_authentication"] == "UNKNOWN" and calls == [lane]
    assert not attempted and QUERY_CANARY not in observed.out and TOKEN_CANARY not in observed.out
