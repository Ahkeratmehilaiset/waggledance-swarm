"""Bridge v2 contract kernel (dormant) fixtures: isolation, parity and F12/F23.

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.
The parity oracles are the core modules at the same base. They are imported ONLY here, in
tests (skipped when absent), and never by the kernel modules themselves.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = {
    "bridge_v2_event_schema": "bridge_event_schema",
    "bridge_v2_request_contract": "bridge_request_contract",
    "bridge_v2_identity_registry": "bridge_identity_registry",
    "bridge_v2_log_reader": "bridge_log_reader",
    "bridge_v2_workflow": "bridge_workflow",
}
HEAD = "0123456789abcdef0123456789abcdef01234567"
UUID_A = "2b2f6ff9-06c2-4ec8-b526-f10071ce7103"
UUID_B = "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
BEFORE_EPOCH = "2026-09-29T12:00:00.0000000Z"
AFTER_EPOCH = "2026-09-30T08:00:00.0000000Z"


def _kernel(name: str):
    return importlib.import_module("tools." + name)


def _core(name: str):
    return pytest.importorskip("waggledance.core." + KERNEL[name])


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _outcome(fn, *args, **kwargs):
    try:
        result = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - parity compares the failure type and text too
        return ("error", type(exc).__name__, str(exc))
    if hasattr(result, "model_dump"):
        return ("ok", result.model_dump())
    return ("ok", repr(result))


def _event(**changes) -> dict:
    event = {"ts_utc": BEFORE_EPOCH, "agent": "claude-rco-1", "type": "message", "task_id": "task/1",
             "status": "answered", "severity": "", "to": "codex-lead-1", "message": "m", "paths": [],
             "write_scope": [], "run_id": "run-1", "role": "rco-security", "agent_uuid": UUID_A,
             "session_id": "session-1", "capabilities": [], "pid": 1, "cwd": "C:\\work", "payload": {}}
    event.update(changes)
    return event


# ---------------------------------------------------------------------------
# Isolation and source parity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(KERNEL))
def test_kernel_modules_never_import_the_product_package(name):
    source = _source(ROOT / "tools" / f"{name}.py")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "waggledance" for alias in node.names), name
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] != "waggledance", name
    for dynamic in ("__import__", "import_module", "importlib", "sys.modules", "waggledance"):
        assert dynamic not in source, (name, dynamic)


@pytest.mark.parametrize("name", ["bridge_v2_request_contract", "bridge_v2_identity_registry", "bridge_v2_log_reader"])
def test_pure_ports_equal_the_core_source(name):
    core = ROOT / "waggledance" / "core" / f"{KERNEL[name]}.py"
    if not core.is_file():
        pytest.skip("core module absent")
    assert _source(ROOT / "tools" / f"{name}.py") == _source(core)


def test_the_workflow_port_differs_from_core_only_by_its_import():
    core = ROOT / "waggledance" / "core" / "bridge_workflow.py"
    if not core.is_file():
        pytest.skip("core module absent")
    expected = _source(core).replace("from waggledance.core.bridge_request_contract import",
                                     "from tools.bridge_v2_request_contract import")
    assert _source(ROOT / "tools" / "bridge_v2_workflow.py") == expected
    assert _kernel("bridge_v2_workflow").reply_matches_request is _kernel("bridge_v2_request_contract").reply_matches_request


# ---------------------------------------------------------------------------
# Behaviour parity with the core modules (before the F12 epoch)
# ---------------------------------------------------------------------------

EVENT_CORPUS = [
    _event(),
    _event(agent="Bad Agent"),
    _event(ts_utc="2026-09-29T12:00:00"),
    _event(ts_utc="2026-09-29T12:00:00+02:00"),
    _event(type=""),
    _event(to="not an agent!"),
    _event(role="RCO"),
    _event(pid="1"),
    _event(payload={"task_id": "other/task"}),
    _event(request_id="bad id with spaces"),
    _event(type="wake_request", to="", status="request"),
    _event(type="message", status="acknowledged", task_id=""),
    _event(type="decision", status="rco_pass", message="short", payload={"head": "abc"}),
    _event(agent="operator", role="operator", agent_uuid=""),
    {k: v for k, v in _event().items() if k != "pid"},
]


@pytest.mark.parametrize("index", range(len(EVENT_CORPUS)))
def test_event_validation_matches_core_before_the_f12_epoch(index):
    event = EVENT_CORPUS[index]
    core, port = _core("bridge_v2_event_schema"), _kernel("bridge_v2_event_schema")
    assert _outcome(port.validate_event, event) == _outcome(core.validate_event, event)
    line = json.dumps(event)
    assert _outcome(port.validate_event_line, line, line_no=7) == _outcome(core.validate_event_line, line, line_no=7)


@pytest.mark.parametrize("line", ['{"a": 1, "a": 2}', "not json", "[]", '{"ts_utc": NaN}', "{" * 80 + "}" * 80])
def test_malformed_lines_are_refused_identically(line):
    core, port = _core("bridge_v2_event_schema"), _kernel("bridge_v2_event_schema")
    assert _outcome(port.validate_event_line, line) == _outcome(core.validate_event_line, line)


REQUEST = _event(agent="codex-lead-1", role="lead-impl", agent_uuid=UUID_B, session_id="lead-s", run_id="lead-r",
                 type="wake_request", status="request", to="claude-rco-1", request_id="req-1",
                 expected_responders={"claude-rco-1": {"agent_uuid": UUID_A, "session_id": "session-1",
                                                       "run_id": "run-1"}})
REPLY = _event(ts_utc="2026-09-29T12:05:00.0000000Z", in_reply_to_request_id="req-1")
REPLY_VARIANTS = [REPLY, dict(REPLY, session_id="other"), dict(REPLY, in_reply_to_request_id="req-2"),
                  dict(REPLY, ts_utc="2026-09-29T11:00:00.0000000Z"), dict(REPLY, agent="claude-rco-2")]


@pytest.mark.parametrize("index", range(len(REPLY_VARIANTS)))
def test_request_contract_matches_core(index):
    core, port = _core("bridge_v2_request_contract"), _kernel("bridge_v2_request_contract")
    reply = REPLY_VARIANTS[index]
    for name, args, kwargs in [
        ("reply_matches_request", (REQUEST, reply, "claude-rco-1"), {}),
        ("reply_matches_request", (REQUEST, reply, "claude-rco-1"), {"require_explicit_correlation": True}),
        ("reply_follows_request", (REQUEST, reply), {"request_position": 1, "reply_position": 2}),
        ("request_is_bound", (REQUEST,), {}),
        ("request_key", (REQUEST, "claude-rco-1"), {}),
        ("request_content", (REQUEST,), {}),
        ("field", (reply, "in_reply_to_request_id"), {}),
        ("timestamp", (reply.get("ts_utc"),), {}),
        ("terminal_status_negated", ("not_done",), {}),
    ]:
        assert _outcome(getattr(port, name), *args, **kwargs) == _outcome(getattr(core, name), *args, **kwargs), name


@pytest.mark.parametrize("content", ['{"agents": {"claude-rco-1": "' + UUID_A + '"}}', "{}", "[]", "not json",
                                     '{"claude-rco-1": "' + UUID_A + '"}'])
def test_identity_registry_matches_core(tmp_path, content):
    core, port = _core("bridge_v2_identity_registry"), _kernel("bridge_v2_identity_registry")
    path = tmp_path / "registry.json"
    path.write_text(content, encoding="utf-8")
    assert _outcome(port.load_bridge_identity_registry, path) == _outcome(core.load_bridge_identity_registry, path)
    assert _outcome(port.load_bridge_identity_registry, tmp_path / "missing.json", allow_missing=True) == \
        _outcome(core.load_bridge_identity_registry, tmp_path / "missing.json", allow_missing=True)
    registry = {"claude-rco-1": UUID_A}
    for event in (_event(), _event(agent_uuid=UUID_B), _event(agent_uuid=""), _event(agent="claude-rco-2")):
        assert port.bridge_identity_binding_status(event, registry=registry) == \
            core.bridge_identity_binding_status(event, registry=registry)
        assert port.event_matches_registered_identity(event, registry=registry) == \
            core.event_matches_registered_identity(event, registry=registry)


def test_log_reader_matches_core(tmp_path):
    core, port = _core("bridge_v2_log_reader"), _kernel("bridge_v2_log_reader")
    log = tmp_path / "events.jsonl"
    log.write_bytes(b"".join(json.dumps(_event(task_id=f"t/{i}")).encode() + b"\n" for i in range(3)) + b'{"partial"')
    assert repr(port.read_bridge_log(log)) == repr(core.read_bridge_log(log))
    assert repr(port.read_bridge_log(log, max_rows=1)) == repr(core.read_bridge_log(log, max_rows=1))
    for rows in (1, 2, 0, True):
        assert repr(port.read_bridge_log_tail_lines(log, tail_rows=rows)) == \
            repr(core.read_bridge_log_tail_lines(log, tail_rows=rows))
    for text in ('{"a": 1}', '{"a": 1, "a": 2}', '{"a": NaN}', "[1]", '{"A": 1, "a": 2}'):
        assert _outcome(port.parse_bridge_json_object, text) == _outcome(core.parse_bridge_json_object, text)


def test_workflow_matches_core():
    core, port = _core("bridge_v2_workflow"), _kernel("bridge_v2_workflow")
    for agent in ("codex-lead-1", "claude-rco-1", "grok-scout-1", "operator", "codex"):
        assert port.worker_class(agent) == core.worker_class(agent)
    requester = {"agent": "codex-lead-1", "agent_uuid": UUID_B, "session_id": "lead-s", "run_id": "lead-r"}
    responder = {"agent": "claude-rco-1", "agent_uuid": UUID_A, "session_id": "session-1", "run_id": "run-1"}
    plan = {"role": "reviewer", "target": "claude-rco-1", "authorization_ref": "auth-1", "task_id": "task/1",
            "revision": "r1", "instruction": "review", "requester": requester, "responder": responder,
            "data": {"evidence": "e", "cases": ["c"]}, "consumes_fields": ["evidence", "cases"],
            "result_fields": ["verdict", "limits"]}

    def stable(result):
        return {k: v for k, v in result.items() if k not in ("ts_utc", "request_id")}

    assert stable(port.prepare_request(plan)) == stable(core.prepare_request(plan))
    for broken in (dict(plan, role="advisor", target="grok-scout-1"), dict(plan, target="claude-rco-2"),
                   dict(plan, consumes_fields=["inputs"]), dict(plan, result_fields=[])):
        assert _outcome(port.prepare_request, broken) == _outcome(core.prepare_request, broken)
    observations = [{"request_id": "req-1", "target": "claude-rco-1", "requester": "codex-lead-1",
                     "requester_session_id": "lead-s", "stage": "request_durable",
                     "observed_at_utc": "2026-09-29T12:00:00Z"},
                    {"request_id": "req-1", "target": "claude-rco-1", "requester": "codex-lead-1",
                     "requester_session_id": "lead-s", "stage": "watcher_seen",
                     "observed_at_utc": "2026-09-29T11:59:00Z"}]
    assert port.latency_report(REQUEST, observations, target="claude-rco-1") == \
        core.latency_report(REQUEST, observations, target="claude-rco-1")


# ---------------------------------------------------------------------------
# F12: commit statuses carry the exact full head
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["rco_pass", "build_consensus_pass"])
def test_f12_commit_status_decisions_carry_the_exact_full_head(status):
    schema = _kernel("bridge_v2_event_schema")
    good = _event(ts_utc=AFTER_EPOCH, type="decision", status=status, message="pass at " + HEAD,
                  payload={"head": HEAD})
    assert schema.validate_event(good).payload["head"] == HEAD
    for payload, message in (({"head": HEAD[:12]}, "at " + HEAD[:12]), ({"head": HEAD.upper()}, "at " + HEAD.upper()),
                             ({}, "no head"), ({"head": HEAD}, "a message without the head"),
                             ({"head": None}, "null head")):
        with pytest.raises(ValueError, match="head"):
            schema.validate_event(_event(ts_utc=AFTER_EPOCH, type="decision", status=status, message=message,
                                         payload=payload))


def test_f12_keeps_legacy_lines_and_other_types_unchanged():
    schema = _kernel("bridge_v2_event_schema")
    legacy = _event(ts_utc=BEFORE_EPOCH, type="decision", status="rco_pass", message="short", payload={"head": "abc"})
    assert schema.validate_event(legacy).status == "rco_pass"          # the historical log still validates
    assert schema.validate_event(_event(ts_utc=AFTER_EPOCH, type="message", status="rco_pass")).type == "message"
    assert schema.validate_event(_event(ts_utc=AFTER_EPOCH, type="decision", status="approved")).status == "approved"


def test_f12_is_the_only_intended_difference_from_core():
    core, port = _core("bridge_v2_event_schema"), _kernel("bridge_v2_event_schema")
    short = _event(ts_utc=AFTER_EPOCH, type="decision", status="build_consensus_pass", message="x",
                   payload={"head": "abc1234"})
    assert _outcome(core.validate_event, short)[0] == "ok"
    assert _outcome(port.validate_event, short)[0] == "error"


# ---------------------------------------------------------------------------
# F23: reserved labels need externally observed session provenance
# ---------------------------------------------------------------------------

def test_f23_reserved_labels_need_externally_observed_session_provenance():
    schema = _kernel("bridge_v2_event_schema")
    operator = _event(agent="operator", role="operator", agent_uuid="", session_id="operator-terminal-1")
    with pytest.raises(ValueError, match="verified session provenance"):
        schema.validate_event_for_write(operator)
    for bad in ("operator",
                {"agent": "operator", "session_id": "operator-terminal-1", "observed_by": "launcher"},
                schema.SessionProvenance("operator", "another-session", "launcher"),
                schema.SessionProvenance("system", "operator-terminal-1", "launcher"),
                schema.SessionProvenance("operator", "operator-terminal-1", "   ")):
        with pytest.raises(ValueError):
            schema.validate_event_for_write(operator, provenance=bad)
    accepted = schema.validate_event_for_write(
        operator, provenance=schema.SessionProvenance("operator", "operator-terminal-1", "operator-terminal-launcher"))
    assert accepted.agent == "operator"                                  # success twin
    system = _event(agent="system", role="", agent_uuid="", session_id="")
    with pytest.raises(ValueError):
        schema.validate_event_for_write(system, provenance=schema.SessionProvenance("system", "", "sweep"))


def test_f23_role_and_environment_claims_never_suffice_and_readers_see_unverified():
    schema = _kernel("bridge_v2_event_schema")
    with pytest.raises(ValueError, match="reserved role"):
        schema.validate_event_for_write(_event(role="operator"))       # a lane cannot borrow the role
    assert schema.validate_event_for_write(_event()).agent == "claude-rco-1"   # ordinary lanes unaffected
    operator = schema.validate_event(_event(agent="operator", role="operator", agent_uuid=""))
    assert schema.reserved_label_status(operator) == "reserved_label_unverified"
    assert schema.reserved_label_status(schema.validate_event(_event())) == "not_reserved"
