"""F20 dormant Grok broker through INJECTED fake ports (AUTHORED, NOT RUN).

The fakes stand in for the injected clock, the read-only snapshot, F0 activation, the
unchanged helper (7da35242) and the durable admission ledger (not built). Nothing here
calls Grok, the real helper, F0, a clock or the network.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import inspect
import json
from pathlib import Path

import pytest

from tools import wd_grok_broker as broker
from tools.bridge_v2_activation import Decision, canonical_sha256
from tools.bridge_v2_grok_route import prepare_grok_consult

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
HEAD, TREE = "a" * 40, "b" * 40
TASK = "codex-lead-1/bridge-v2-grok-route-20260930"
PROMPT = "Review the admission gate. COMPLETE, no tools."
POLICY = {"schema": "fixture-signed-policy", "parameters": {"F20": {
    "model": "grok-4", "efforts": ["high"], "allowed_tools": [], "max_prompt_bytes": 48000,
    "max_intent_ttl_seconds": 900}}}
ON = {"schema": broker.CONFIG_SCHEMA, "enabled": True}
CONFIG = Path(__file__).resolve().parents[2] / "configs" / "bridge_v2_grok_admission.json"


def intent(prompt=PROMPT):
    return prepare_grok_consult(task_id=TASK, request_id="1" * 32, request_revision=1, prompt=prompt,
                                snapshot={"head": HEAD, "tree": TREE}, model="grok-4", effort="high",
                                budget_class="shared_hourly", authorization_ref=canonical_sha256(POLICY),
                                nonce="2" * 32, ttl_seconds=600, now=NOW - timedelta(seconds=30))


def budget_state(**over):
    # The helper's own status shape; the broker stamps observed_utc itself.
    state = {"schema": "wd.grok-hourly.v1", "status": "answered",
             "last_attempt_utc": (NOW - timedelta(hours=2)).isoformat(), "eligible": True}
    state.update(over)
    return state


def answered_report(**over):
    report = {"schema": "wd.grok-hourly.v1", "status": "answered", "task_id": TASK, "request_id": "4" * 32,
              "last_attempt_utc": (NOW + timedelta(seconds=1)).isoformat(), "report_sha256": "5" * 64}
    report.update(over)
    return report


class Clock:
    def __init__(self, moment=NOW):
        self.moment, self.reads = moment, 0

    def now(self):
        self.reads += 1
        return self.moment


class SequenceClock(Clock):
    """Read order in consult(): start, snapshot stamp, F0 stamp, budget stamp, ledger stamp, now."""

    def __init__(self, *moments):
        super().__init__()
        self.moments = list(moments)

    def now(self):
        self.reads += 1
        return self.moments[min(self.reads, len(self.moments)) - 1]


class BrokenClock(Clock):
    def now(self):
        raise OSError("clock unreadable")


class Snapshot:
    def __init__(self, value=None):
        self.value = {"readonly": True, "head": HEAD, "tree": TREE} if value is None else value

    def observe(self):
        return dict(self.value) if isinstance(self.value, dict) else self.value


class Activation:
    def __init__(self, decision=None, policy=None, raises=None):
        self.decision = Decision("F20", True, "enabled", canonical_sha256(POLICY), 3) if decision is None else decision
        self.policy = POLICY if policy is None else policy
        self.raises, self.calls = raises, []

    def evaluate(self, feature, *, expected_head, expected_tree):
        self.calls.append((feature, expected_head, expected_tree))
        if self.raises is not None:
            raise self.raises
        return self.decision, self.policy


class Helper:
    def __init__(self, state=None, report=None, reply=None, raises=None, status_raises=None):
        self.state = budget_state() if state is None else state
        self.report = answered_report() if report is None else report
        self.reply = {"text": "No blocker.", "tool_calls": [], "report_sha256": "5" * 64} if reply is None else reply
        self.raises, self.status_raises = raises, status_raises
        self.calls, self.reads = [], []

    def status(self):
        if self.status_raises is not None:
            raise self.status_raises
        return dict(self.state)

    def consult(self, task_id, prompt):
        self.calls.append((task_id, prompt))
        if self.raises is not None:
            raise self.raises
        return dict(self.report)

    def read_answer(self, report):
        self.reads.append(report)
        return dict(self.reply)


class Ledger:
    def __init__(self, observed=None, win=True, finish_raises=None):
        self.observed = {"open": [], "last_admitted_utc": None} if observed is None else observed
        self.win, self.finish_raises = win, finish_raises
        self.reserved, self.finished = [], []

    def observe(self):
        return dict(self.observed)

    def reserve(self, admission):
        self.reserved.append(admission)
        return self.win

    def finish(self, admission, outcome):
        self.finished.append((admission, outcome))
        if self.finish_raises is not None:
            raise self.finish_raises


class UnlockableLedger(Ledger):
    def reserve(self, admission):
        raise OSError("ledger lock unknown")


def run(config=ON, prompt=PROMPT, it=None, **over):
    ports = {"clock": Clock(), "snapshot": Snapshot(), "activation": Activation(), "helper": Helper(),
             "ledger": Ledger()}
    ports.update(over)
    result = broker.GrokBroker(config, **ports).consult(intent() if it is None else it, prompt)
    return result, ports


def untouched(ports):
    return ports["activation"].calls == [] and ports["helper"].calls == [] and ports["ledger"].reserved == []


def test_shipped_config_is_off_and_grants_nothing():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["schema"] == broker.CONFIG_SCHEMA and config["enabled"] is False
    assert broker.config_enabled(config) is False
    result, ports = run(config=config)
    assert (result["verdict"], result["reasons"]) == ("disabled", ["admission_config_off"])
    assert untouched(ports) and ports["clock"].reads == 0
    for almost in ({"schema": broker.CONFIG_SCHEMA, "enabled": "true"}, {"enabled": True}, None):
        assert broker.config_enabled(almost) is False


@pytest.mark.parametrize("name, reason", [
    ("clock", "clock_port_missing"), ("snapshot", "snapshot_port_missing"),
    ("activation", "activation_port_missing"), ("helper", "helper_port_missing"),
    ("ledger", "admission_ledger_port_missing"),
])
def test_each_missing_port_is_blocked_unknown_not_simulated_readiness(name, reason):
    result, _ = run(**{name: None})
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", [reason])


def test_a_broker_without_ports_lists_every_missing_port():
    result = broker.GrokBroker(ON).consult(intent(), PROMPT)
    assert result["reasons"] == ["clock_port_missing", "snapshot_port_missing", "activation_port_missing",
                                 "helper_port_missing", "admission_ledger_port_missing"]


def test_the_caller_supplies_only_the_intent_and_the_prompt():
    assert list(inspect.signature(broker.GrokBroker.consult).parameters) == ["self", "intent", "prompt"]


def test_valid_bounded_success_twin_calls_the_helper_exactly_once():
    result, ports = run()
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert ports["activation"].calls == [("F20", HEAD, TREE)]  # F0 evaluated against the observed snapshot
    assert ports["helper"].calls == [(TASK, PROMPT)] and len(ports["helper"].reads) == 1
    assert len(ports["ledger"].reserved) == 1 and len(ports["ledger"].finished) == 1
    assert result["admission"]["verdict"] == "admit" and result["admission"]["admitted_utc"] == "2026-09-30T12:00:00Z"
    assert result["answer"]["nonce"] == "2" * 32 and result["answer"]["snapshot"] == {"head": HEAD, "tree": TREE}
    assert (result["execution_allowed"], result["authority"]) == (False, "none")


def test_wrong_or_understated_prompt_is_refused_before_any_port_is_read():
    for it, prompt in ((None, "A different prompt."), (dict(intent(), prompt_bytes=1), PROMPT)):
        result, ports = run(it=it, prompt=prompt)
        assert (result["verdict"], result["reasons"]) == ("refuse", ["prompt_or_inputs_mismatch"])
        assert untouched(ports) and ports["clock"].reads == 0


@pytest.mark.parametrize("value", [
    {"readonly": False, "head": HEAD, "tree": TREE},
    {"readonly": True, "head": HEAD},
    {"readonly": True, "head": HEAD.upper(), "tree": TREE},
    "a0633ef2",
])
def test_f0_never_runs_without_an_exact_read_only_snapshot(value):
    result, ports = run(snapshot=Snapshot(value))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["snapshot_unknown"])
    assert untouched(ports)


def test_a_snapshot_other_than_the_intents_refuses():
    result, ports = run(snapshot=Snapshot({"readonly": True, "head": "c" * 40, "tree": TREE}))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["snapshot_mismatch"])
    assert ports["activation"].calls == [("F20", "c" * 40, TREE)] and ports["helper"].calls == []


@pytest.mark.parametrize("activation, verdict, reason", [
    (Activation(decision=Decision("F20", False, "disabled", canonical_sha256(POLICY), 3)), "refuse", "f0_disabled"),
    (Activation(decision=Decision("F24", True, "enabled", canonical_sha256(POLICY), 3)), "refuse", "f0_disabled"),
    (Activation(decision={"feature": "F20", "enabled": True, "policy_sha256": canonical_sha256(POLICY)}),
     "blocked_unknown", "f0_unknown"),
    (Activation(policy=dict(POLICY, schema="another-policy")), "refuse", "authorization_unbound"),
    (Activation(raises=ValueError("f0 policy unreadable")), "blocked_unknown", "port_observation_unknown"),
])
def test_f0_and_policy_come_from_the_activation_port(activation, verdict, reason):
    result, ports = run(activation=activation)
    assert (result["verdict"], result["reasons"]) == (verdict, [reason])
    assert ports["helper"].calls == [] and ports["ledger"].reserved == []


@pytest.mark.parametrize("clock, verdict, reason", [
    (Clock(datetime(2026, 9, 30, 12, 0)), "blocked_unknown", "time_unknown"),  # naive
    (BrokenClock(), "blocked_unknown", "port_observation_unknown"),
    # F0 evaluated at NOW, admission judged at NOW + 61 s: the F0 decision is stale
    (SequenceClock(NOW, NOW, NOW, NOW + timedelta(seconds=61)), "refuse", "f0_stale"),
    # the snapshot read at NOW is 121 s old when admission is judged
    (SequenceClock(NOW, NOW, NOW + timedelta(seconds=100), NOW + timedelta(seconds=121)), "blocked_unknown",
     "snapshot_unknown"),
])
def test_the_injected_clock_bounds_every_observation(clock, verdict, reason):
    result, ports = run(clock=clock)
    assert (result["verdict"], result["reasons"]) == (verdict, [reason])
    assert ports["helper"].calls == [] and ports["ledger"].reserved == []


def test_exhausted_cooldown_failed_and_unreconciled_attempts_refuse_without_a_call():
    for state, reason in (
            (budget_state(eligible=False), "hourly_budget_used"),
            (budget_state(last_attempt_utc=(NOW - timedelta(minutes=30)).isoformat()), "hourly_budget_used"),
            (budget_state(status="failed", eligible=False,
                          last_attempt_utc=(NOW - timedelta(minutes=10)).isoformat()), "hourly_budget_used"),
            (budget_state(status="reserved", eligible=False), "unreconciled_attempt:reserved"),
            (budget_state(status="interrupted_or_unknown", eligible=False),
             "unreconciled_attempt:interrupted_or_unknown")):
        result, ports = run(helper=Helper(state=state))
        assert (result["verdict"], result["reasons"]) == ("refuse", [reason])
        assert ports["helper"].calls == [] and ports["ledger"].reserved == []


def test_unknown_budget_or_unreadable_helper_blocks():
    result, ports = run(helper=Helper(state=budget_state(schema="wd.grok-hourly.v0")))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["budget_unknown"])
    assert ports["helper"].calls == []
    unread = Helper(status_raises=ValueError("Grok state missing; initialize through the controlled installer"))
    result, ports = run(helper=unread)
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["port_observation_unknown"])
    assert ports["helper"].calls == []


def test_concurrent_admission_is_serialized_by_the_ledger():
    result, ports = run(ledger=Ledger(observed={"open": [{"intent_sha256": "9" * 64}]}))
    assert result["reasons"] == ["admission_in_flight"]
    assert ports["helper"].calls == [] and ports["ledger"].reserved == []
    result, ports = run(ledger=Ledger(observed={"open": [], "last_admitted_utc": "2026-09-30T11:40:00Z"}))
    assert result["reasons"] == ["hourly_budget_used"] and ports["helper"].calls == []
    result, ports = run(ledger=Ledger(win=False))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["admission_lost_race"])
    assert ports["helper"].calls == [] and len(ports["ledger"].reserved) == 1 and ports["ledger"].finished == []
    result, ports = run(ledger=UnlockableLedger())
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["admission_ledger_unknown"])
    assert ports["helper"].calls == []


def test_a_failed_attempt_is_bound_as_refused_and_never_retried():
    result, ports = run(helper=Helper(report=answered_report(status="failed")))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["not_the_answered_attempt"])
    assert len(ports["helper"].calls) == 1 and ports["helper"].reads == [] and len(ports["ledger"].finished) == 1


def test_a_helper_exception_is_unknown_and_never_retried():
    result, ports = run(helper=Helper(raises=TimeoutError()))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["helper_outcome_unknown:TimeoutError"])
    assert len(ports["helper"].calls) == 1 and len(ports["ledger"].finished) == 1


def test_wrong_reply_and_forbidden_transcript_are_refused():
    for helper, reason in (
            (Helper(report=answered_report(task_id="codex-lead-1/other-task")), "not_the_answered_attempt"),
            (Helper(report=answered_report(last_attempt_utc=(NOW + timedelta(minutes=10)).isoformat())),
             "attempt_unbound"),
            (Helper(reply={"text": "x", "tool_calls": [], "report_sha256": "6" * 64}), "answer_not_from_report"),
            (Helper(reply={"text": "x", "tool_calls": ["bash"], "report_sha256": "5" * 64}),
             "forbidden_tool_in_transcript")):
        result, ports = run(helper=helper)
        assert (result["verdict"], result["reasons"]) == ("refuse", [reason])
        assert len(ports["helper"].calls) == 1  # the attempt happened and counts against the hour


def test_a_ledger_finish_failure_stays_visible():
    result, _ = run(ledger=Ledger(finish_raises=OSError("disk")))
    assert result["verdict"] == "answered_bound" and result["reasons"] == ["ledger_finish_unknown"]


def test_broker_has_no_retry_exemption_or_side_channel():
    source = Path(broker.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [node for node in ast.walk(tree) if isinstance(node, (ast.For, ast.While, ast.AsyncFor))]
    consults = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "consult" and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "helper"]
    assert len(consults) == 1 and len(consults[0].args) == 2 and consults[0].keywords == []
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"__future__", "typing", "tools.bridge_v2_grok_route"}
    assert "exception_path" not in source and "exception_sha256" not in source
