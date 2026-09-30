"""Bridge v2 contract kernel (dormant) fixtures: isolation, parity and F12/F23.

Authored under the 2026-09-29 operator no-runs directive: NOT executed by the author.
The parity oracles are the core modules at the same base. They are imported ONLY here, in
tests (skipped when absent), and never by the kernel modules themselves.
"""
from __future__ import annotations

import ast
import importlib
import itertools
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
# Behaviour parity with the core modules: the read path is exactly core at ALL times
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
    _event(type="rco_review", status="rco_pass", message="no head at all"),
    _event(type="decision", status="rco_pass_pending_ci", payload={"head": HEAD.upper()}),
    _event(type="done", status="approved_ci_green", message="ci green"),
    _event(agent="operator", role="operator", agent_uuid=""),
    {k: v for k, v in _event().items() if k != "pid"},
]


@pytest.mark.parametrize("stamp", [None, AFTER_EPOCH, "2027-01-01T00:00:00.0000000Z"])
@pytest.mark.parametrize("index", range(len(EVENT_CORPUS)))
def test_event_validation_matches_core_at_all_times(index, stamp):
    event = EVENT_CORPUS[index]
    if stamp is not None and event.get("ts_utc") == BEFORE_EPOCH:
        event = dict(event, ts_utc=stamp)          # no time-based read behaviour: later stamps change nothing
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
# F12 (WRITE only): a new commit approval carries the exact full head
# ---------------------------------------------------------------------------

RCO, PEER = "claude-rco-1", "codex-lead-1"   # a canonical RCO; a peer whose finding is classified by status
EXACT_APPROVALS = ("rco_pass", "rco_pass_pending_ci", "build_consensus_pass", "approved", "approved_ci_green",
                   "acknowledged", "build_consensus", "concur", "concurred", "agree", "agreed")
APPROVALS = [(kind, status, PEER if kind == "finding" else RCO)
             for kind in ("decision", "rco_review", "finding") for status in EXACT_APPROVALS]
APPROVALS += [
    ("finding", "rco_pass", PEER),                       # a NON-canonical agent's finding is classified by status
    ("done", "approved_ci_green", RCO),
    ("decision", "no_changes_requested", RCO), ("rco_review", "changes_requested_retracted", RCO),   # veto clears
    ("test", "changes_requested_resolved", PEER), ("done", "approved_waiver_block_cleared", PEER),
    ("finding", "no_changes_requested_approved", PEER),
    ("decision", "RCO_PASS", RCO), ("Decision", "rco_pass", RCO), ("RCO_REVIEW", "Rco_Pass", RCO),  # mixed case
    ("decision", "rco-pass", RCO), ("rco_review", "rco pass final", RCO),                           # generic tokens
    ("decision", "not_approved", PEER), ("decision", "Acknowledged receipt", PEER),                 # gate tokens
    ("decision", "__approved__", PEER), ("DONE", "APPROVED_CI_GREEN", PEER),
    ("rco-review", "No Changes Requested", PEER), ("rco review", "Changes-Requested: resolved", PEER),  # separators
]
STAMPS = ("2020-01-01T00:00:00.0000000Z", BEFORE_EPOCH, AFTER_EPOCH)   # no stamp relaxes a new write
BAD_HEADS = [
    ({}, "no head", "lowercase 40-hex"),                                    # missing
    ({"head": None}, "null head", "lowercase 40-hex"),
    ({"head": ""}, "empty head", "lowercase 40-hex"),
    ({"head": HEAD[:12]}, "at " + HEAD[:12], "lowercase 40-hex"),            # short
    ({"head": HEAD + "0"}, "at " + HEAD + "0", "lowercase 40-hex"),          # 41 hex
    ({"head": HEAD.upper()}, "at " + HEAD.upper(), "lowercase 40-hex"),      # uppercase
    ({"head": " " + HEAD}, "at " + HEAD, "lowercase 40-hex"),                # padded
    ({"head": int(HEAD[:15], 16)}, "numeric head", "lowercase 40-hex"),      # not a string
    ({"Head": HEAD}, "at " + HEAD, "lowercase 40-hex"),                      # no case folding of the key
    ({"head": HEAD}, "a message without the head", "exact head"),            # message does not name it
    ({"head": HEAD}, "pass at " + HEAD.upper(), "exact head"),               # ordinal, case-sensitive
]


def _gates():
    # A renamed or missing gate FAILS these drift tests loudly (no importorskip fallback; RCO1 e855cb79).
    return (importlib.import_module("tools.check_rco_pass_present"),
            importlib.import_module("tools.check_bridge_changes_requested"),
            importlib.import_module("tools.idle_consensus_auto_merge"))


def test_the_floor_vocabulary_covers_every_gate_set():
    schema = _kernel("bridge_v2_event_schema")
    assert not hasattr(schema, "COMMIT_HEAD_STRICT_EPOCH_UTC")                     # no time-based read epoch
    rco, changes, idle = _gates()        # test-time imports only; the kernel never imports gate code
    # Coverage (gate subset of floor): everything a gate counts is in the floor vocabulary.
    assert rco.RCO_PASS_STATUSES <= schema.FLOOR_RCO_PASS_STATUSES
    assert rco.DECISION_TYPES_FOR_PASS <= schema.FLOOR_APPROVAL_TYPES
    assert idle.RCO_PASS_STATUSES <= schema.FLOOR_RCO_PASS_STATUSES
    assert idle.BUILD_CONSENSUS_STATUSES <= schema.FLOOR_BUILD_CONSENSUS_STATUSES
    assert idle.DECISION_EVENT_TYPES <= schema.FLOOR_APPROVAL_TYPES
    assert idle.CONSENSUS_CLEAR_EVENT_TYPES <= schema.FLOOR_CLEAR_TYPES
    assert changes.APPROVAL_STATUSES <= schema.FLOOR_APPROVAL_STATUSES
    assert changes.DONE_APPROVAL_STATUSES <= schema.FLOOR_DONE_APPROVAL_STATUSES
    assert changes.CLEAR_EVENT_TYPES <= schema.FLOOR_CLEAR_TYPES
    assert changes.NO_CHANGES_REQUESTED_CLEAR_STATUSES | changes.NO_BLOCK_CLEAR_STATUSES <= schema.FLOOR_CLEAR_STATUSES
    assert changes.CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES <= schema.FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES
    assert set(changes.CHANGES_REQUESTED_EXACT_BLOCK_PREFIXES) == set(schema.FLOOR_CHANGES_REQUESTED_PREFIXES)
    # Exemptions (floor subset of gate): the floor never exempts more than the gate itself does.
    assert schema.FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES <= changes.CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES
    assert schema.FLOOR_RCO_AGENTS <= changes._RECOGNIZED_RCOS
    # The ported block classifier is source-equivalent: every constant EQUALS the gate's own.
    assert schema.GATE_BLOCKING_STATUSES == changes.BLOCKING_STATUSES
    assert schema.GATE_BLOCKING_EVENT_TYPES == changes.BLOCKING_EVENT_TYPES
    assert schema.GATE_BLOCKING_CLEAR_TOKENS == changes.BLOCKING_CLEAR_TOKENS
    assert schema.GATE_BLOCKING_RESOLUTION_TOKENS == changes.BLOCKING_RESOLUTION_TOKENS
    assert schema.GATE_BLOCKING_RESOLUTION_NEGATION_TOKENS == changes.BLOCKING_RESOLUTION_NEGATION_TOKENS
    assert schema.GATE_BLOCKING_CLEAR_COORDINATION_TOKENS == changes.BLOCKING_CLEAR_COORDINATION_TOKENS
    assert schema.GATE_BLOCKING_WORD_TOKENS == changes.BLOCKING_WORD_TOKENS
    assert schema.GATE_NON_BLOCKING_BLOCK_PHRASES == changes.NON_BLOCKING_BLOCK_PHRASES
    assert tuple(schema.GATE_NON_BLOCKING_CONTEXT_STATUS_PREFIXES) == tuple(changes.NON_BLOCKING_CONTEXT_STATUS_PREFIXES)
    assert tuple(schema.GATE_NON_BLOCKING_CONTEXT_STATUS_SEGMENTS) == tuple(changes.NON_BLOCKING_CONTEXT_STATUS_SEGMENTS)
    assert schema.FLOOR_CHANGES_REQUESTED_CLEAR_SUFFIXES == changes.CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES


BLOCKING_EXTRAS = ("not_blocked", "not_a_blocker", "preflight_clear_blocked", "classifier_artifact_veto_no_block",
                   "blocked_pending_clear", "block_requested_withdrawn", "changes requested", "RCO-Blocked",
                   "unresolved_block", "block_cleared_required", "ack_block", "rco_blocked_withdrawn",
                   "rco_retraction_acknowledged_head_blocked", "repeat_block_acknowledged_no_reopen",
                   "rco_pass_blocked", "approved_but_blocked", "resolved_block_still_open", "cleared_block")


def test_the_ported_blocking_classifier_equals_the_gates_own():
    """Behavioural drift fixture: the kernel port answers exactly as the gate over a wide corpus."""
    schema, gates = _kernel("bridge_v2_event_schema"), _gates()
    changes = gates[1]
    types = sorted({kind.lower() for kind in CORPUS_TYPES} | {"", "Blocked", "rco-review"})
    compared = 0
    for status in _corpus_statuses(gates) + list(BLOCKING_EXTRAS):
        for kind in types:
            assert schema._gate_blocking(status, kind) == changes._is_blocking_status(status, event_type=kind), \
                (status, kind)
            compared += 1
    assert compared > 1000, compared


def _gate_counts(gates, kind, status, agent):
    """True when at least one gate would COUNT this line as an approval or a veto clear. Each gate's
    own order is replayed with the gate's OWN classifiers (test-time import only)."""
    rco, changes, idle = gates
    t_low, s_low = kind.lower(), status.lower()
    canonical = agent in changes._RECOGNIZED_RCOS
    if t_low == "finding" and canonical:
        # The veto channel first (RCO1 e855cb79 B1): the changes gate latches a canonical RCO's finding as a
        # VETO by type, whatever its status. idle's own counting of it (a pass or a clear) is that gate's
        # ordering issue, out of this scope and pinned by the residual test below.
        return False
    if t_low in rco.DECISION_TYPES_FOR_PASS and s_low in rco.RCO_PASS_STATUSES:
        return True                                                               # check_rco_pass_present
    if idle._is_consensus_clear(s_low, event_type=t_low):
        return True                                                               # idle: a consensus clear
    if t_low in idle.DECISION_EVENT_TYPES and (s_low in idle.RCO_PASS_STATUSES if canonical
                                               else s_low in idle.BUILD_CONSENSUS_STATUSES):
        return True                                                               # idle: approvals
    if t_low in changes._TAXONOMY_BLOCK_BY_TYPE and (t_low not in changes._TAXONOMY_RCO_GATED_TYPES or canonical):
        return False                                                              # changes: a veto by type
    if changes._is_clear_status(s_low):
        return t_low in (changes.RCO_RETRACTION_EVENT_TYPES if canonical else changes.CLEAR_EVENT_TYPES)
    if changes._is_blocking_status(s_low, event_type=t_low):
        return False
    if canonical and t_low not in changes.RCO_RETRACTION_EVENT_TYPES:
        return False
    if t_low == "done" and s_low not in changes.DONE_APPROVAL_STATUSES:
        return False
    if t_low not in {"decision", "rco_review", "finding", "done"}:
        return False
    return changes._is_approval_status(s_low)


CORPUS_TYPES = ("decision", "Decision", "DECISION", "rco_review", "RCO_REVIEW", "rco-review", "rco review", "finding",
                "Finding", "done", "Done", "test", "TEST", "---", "message", "ack", "handoff", "blocked", "heartbeat")


def _corpus_statuses(gates):
    rco, changes, idle = gates
    base = (set(rco.RCO_PASS_STATUSES) | set(idle.RCO_PASS_STATUSES) | set(idle.BUILD_CONSENSUS_STATUSES)
            | set(changes.APPROVAL_STATUSES) | set(changes.DONE_APPROVAL_STATUSES)
            | set(changes.NO_CHANGES_REQUESTED_CLEAR_STATUSES) | set(changes.NO_BLOCK_CLEAR_STATUSES)
            | {prefix + "_" + suffix for prefix in changes.CHANGES_REQUESTED_EXACT_BLOCK_PREFIXES
               for suffix in changes.CHANGES_REQUESTED_NON_BLOCKING_SUFFIXES}
            | set(changes.BLOCKING_STATUSES) | set(changes.INFORMATIONAL_FINDING_STATUSES)
            | {"rco pass final", "rco-pass", "not_approved", "approved_by_lead", "acknowledged receipt",
               "rco_pass_blocked", "approved_but_blocked", "changes_requested_acknowledged", "answered", "done"})
    variants = set()
    for status in base:
        variants |= {status, status.upper(), status.title(), status.replace("_", "-"), status.replace("_", " "),
                     "__" + status + "__"}
    return sorted(variants)


def test_f12_every_line_a_gate_counts_is_floored_with_a_full_head_twin():
    """Fail-safe direction (gate subset of floor), over case, separator and generic-token variants."""
    schema, gates = _kernel("bridge_v2_event_schema"), _gates()
    counted = 0
    for kind, status, agent in itertools.product(CORPUS_TYPES, _corpus_statuses(gates), (RCO, PEER)):
        if not _gate_counts(gates, kind, status, agent):
            continue
        headless = _event(type=kind, status=status, agent=agent, message="no head", payload={})
        try:
            read = schema.validate_event(headless)
        except ValueError:
            continue   # the base schema refuses the shape, so no writer can emit it at all
        counted += 1
        assert schema.approval_shape(read) is not None, (kind, status, agent)
        assert schema.commit_head_status(read) == "approval_shaped_head_format_invalid"
        with pytest.raises(ValueError, match="lowercase 40-hex"):
            schema.validate_event_for_write(headless)
        good = dict(headless, message="at " + HEAD, payload={"head": HEAD})     # canonical success twin
        assert schema.validate_event_for_write(good).payload["head"] == HEAD
    assert counted > 100, counted                                                # the corpus is never vacuous


@pytest.mark.parametrize("kind,status,agent", APPROVALS)
def test_f12_every_new_approval_shaped_line_carries_the_exact_full_head(kind, status, agent):
    schema = _kernel("bridge_v2_event_schema")
    for ts in STAMPS:                                                                # full-head success twins
        good = _event(ts_utc=ts, type=kind, status=status, agent=agent, message="pass at " + HEAD,
                      payload={"head": HEAD})
        assert schema.validate_event_for_write(good).payload["head"] == HEAD
        assert schema.commit_head_status(schema.validate_event(good)) == "approval_shaped_head_format_valid"
    for payload, message, reason in BAD_HEADS:
        for ts in STAMPS:
            event = _event(ts_utc=ts, type=kind, status=status, agent=agent, message=message, payload=payload)
            with pytest.raises(ValueError, match=reason):
                schema.validate_event_for_write(event)                              # a new write: refused at any stamp
            read = schema.validate_event(event)                                     # read: accepted exactly like core
            assert read.status == status
            assert schema.commit_head_status(read) == "approval_shaped_head_format_invalid"   # a label, never authority


def test_f12_read_path_equals_core_at_every_stamp_and_only_new_writes_differ():
    core, port = _core("bridge_v2_event_schema"), _kernel("bridge_v2_event_schema")
    for kind, status, agent in APPROVALS:
        for ts in STAMPS + ("2027-01-01T00:00:00.0000000Z",):
            short = _event(ts_utc=ts, type=kind, status=status, agent=agent, message="x", payload={"head": "abc1234"})
            assert _outcome(port.validate_event, short) == _outcome(core.validate_event, short)
            assert _outcome(port.validate_event, short)[0] == "ok"
            assert _outcome(port.validate_event_for_write, short)[0] == "error"


def test_f12_general_lines_and_canonical_vetoes_are_never_floored():
    schema = _kernel("bridge_v2_event_schema")
    for event in (_event(type="done", status="done", message="finished, no head"),      # a generic done
                  _event(type="done", status="rco_pass", message="a done is never a pass"),
                  _event(type="done", status="build_consensus_pass", message="nor a consensus vote"),
                  _event(type="done", status="approved-ci-green", message="no gate counts this spelling"),
                  _event(type="decision", status="changes_requested", payload={"head": "abc"}),
                  _event(type="decision", status="Changes-Requested", message="a veto needs no head"),
                  _event(type="decision", status="changes_requested_acknowledged", message="prefix veto"),
                  _event(type="decision", status="blocked", message="exact block"),
                  _event(type="finding", status="changes_requested", message="a veto needs no head"),
                  _event(type="finding", status="approved", message="a canonical RCO finding is a veto"),
                  _event(type="finding", status="acknowledged", message="still a veto by type"),
                  _event(type="decision", status="build-consensus", message="no gate counts this spelling"),
                  _event(type="message", status="rco_pass", message="informational, not a decision"),
                  _event(type="message", status="acknowledged", message="an ACK is never authority"),
                  _event(type="ack", status="approved", message="an ACK is never authority"),
                  _event(type="handoff", status="build_consensus_pass", message="not a counted type"),
                  _event(type="blocked", status="no_changes_requested", message="a veto type never clears"),
                  _event()):
        assert schema.validate_event_for_write(event).status == event["status"]
        assert schema.approval_shape(schema.validate_event(event)) is None
        assert schema.commit_head_status(schema.validate_event(event)) == "not_approval_shaped"


CANONICAL_FINDING_STATUSES = ("changes_requested_concurrence", "changes_requested_retracted",
                              "changes_requested_withdrawn", "changes_requested_resolved",
                              "no_changes_requested_approved", "rco_pass", "RCO_PASS", "approved", "acknowledged")


@pytest.mark.parametrize("agent", sorted({"claude-rco-1", "claude-rco-2"}))
@pytest.mark.parametrize("kind", ["finding", "Finding"])
@pytest.mark.parametrize("status", CANONICAL_FINDING_STATUSES)
def test_f12_a_canonical_rco_finding_is_never_floored_whatever_its_status(agent, kind, status):
    """RCO1 e855cb79 B1: a veto that cannot be written fails open. The live shape is claude-rco-1
    finding/changes_requested_concurrence with payload {} and no head."""
    schema = _kernel("bridge_v2_event_schema")
    veto = _event(type=kind, status=status, agent=agent, message="This is an RCO block", payload={})
    assert schema.validate_event_for_write(veto).status == status                 # written, no head needed
    assert schema.approval_shape(schema.validate_event(veto)) is None
    peer = dict(veto, agent=PEER)                                                  # twin: a peer's finding IS floored
    with pytest.raises(ValueError, match="lowercase 40-hex"):
        schema.validate_event_for_write(peer)
    good = dict(peer, message="at " + HEAD, payload={"head": HEAD})
    assert schema.validate_event_for_write(good).payload["head"] == HEAD


@pytest.mark.parametrize("status", ["rco_pass_blocked", "approved_but_blocked", "rco_retraction_acknowledged_head_blocked",
                                    "repeat_block_acknowledged_no_reopen", "approved_but_rco_blocked"])
def test_f12_a_status_the_changes_gate_calls_a_block_is_never_floored(status):
    # RCO1 e855cb79 S1: check_bridge_changes_requested classifies these as BLOCKS before any approval token,
    # and no other gate counts them, so a headless write must stay possible (the veto channel).
    schema, changes = _kernel("bridge_v2_event_schema"), _gates()[1]
    assert changes._is_blocking_status(status, event_type="decision")               # the gate's own verdict
    for agent in (PEER, RCO):
        block = _event(type="decision", status=status, agent=agent, message="still blocked", payload={})
        assert schema.validate_event_for_write(block).status == status
        assert schema.approval_shape(schema.validate_event(block)) is None
    twin = _event(type="decision", status="acknowledged_and_approved", agent=PEER, message="no head", payload={})
    with pytest.raises(ValueError, match="lowercase 40-hex"):                          # twin: no block word, floored
        schema.validate_event_for_write(twin)


def test_idle_counting_of_a_canonical_rco_finding_is_a_disclosed_gate_residual():
    """Pinned, not hidden: idle_consensus_auto_merge counts a clear (and an rco_pass) on a canonical RCO's
    finding before any type latch, while the changes gate vetoes it by type. The floor follows the veto
    channel and never refuses it; the idle ordering needs its own operator-explicit gate change. When
    that lands, the first assertion flips and this test must be updated deliberately."""
    schema, idle = _kernel("bridge_v2_event_schema"), _gates()[2]
    assert idle._is_consensus_clear("changes_requested_concurrence", event_type="finding")
    veto = _event(type="finding", status="changes_requested_concurrence", agent=RCO, payload={})
    assert schema.approval_shape(schema.validate_event(veto)) is None


def test_f12_write_guard_composes_with_the_reserved_provenance_gate():
    schema = _kernel("bridge_v2_event_schema")
    provenance = schema.SessionProvenance("operator", "operator-terminal-1", "operator-terminal-launcher")
    operator = _event(agent="operator", role="operator", agent_uuid="", session_id="operator-terminal-1",
                      type="decision", status="build_consensus_pass", message="consensus at " + HEAD,
                      payload={"head": HEAD})
    with pytest.raises(ValueError, match="verified session provenance"):
        schema.validate_event_for_write(operator)                                   # F23 still applies
    assert schema.validate_event_for_write(operator, provenance=provenance).payload["head"] == HEAD
    headless = dict(operator, message="consensus", payload={})
    assert schema.validate_event(headless).status == "build_consensus_pass"         # read: accepted like core
    with pytest.raises(ValueError, match="lowercase 40-hex"):
        schema.validate_event_for_write(headless, provenance=provenance)           # provenance never relaxes F12
    with pytest.raises(ValueError, match="verified session provenance"):
        schema.validate_event_for_write(headless)                                   # identity is refused first


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
