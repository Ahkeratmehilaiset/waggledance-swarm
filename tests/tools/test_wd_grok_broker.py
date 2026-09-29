"""F20 dormant Grok broker through INJECTED fake ports (AUTHORED, NOT RUN).

The fakes stand in for the unchanged helper (7da35242) and the durable admission
ledger (not built). Nothing here calls Grok, the real helper, a clock or the network.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

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


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def intent(prompt=PROMPT):
    return prepare_grok_consult(task_id=TASK, request_id="1" * 32, request_revision=1, prompt=prompt,
                                snapshot={"head": HEAD, "tree": TREE}, model="grok-4", effort="high",
                                budget_class="shared_hourly", authorization_ref=canonical_sha256(POLICY),
                                nonce="2" * 32, ttl_seconds=600, now=NOW - timedelta(seconds=30))


def evidence():
    # No budget and no ledger: the broker observes those through its ports.
    return {"now_utc": stamp(NOW),
            "f0": {"decision": Decision("F20", True, "enabled", canonical_sha256(POLICY), 3),
                   "evaluated_utc": stamp(NOW - timedelta(seconds=10)), "head": HEAD, "tree": TREE},
            "policy": POLICY,
            "snapshot": {"observed_utc": stamp(NOW - timedelta(seconds=5)), "readonly": True, "head": HEAD,
                         "tree": TREE}}


def budget_state(**over):
    state = {"observed_utc": stamp(NOW - timedelta(seconds=5)), "schema": "wd.grok-hourly.v1", "status": "answered",
             "last_attempt_utc": (NOW - timedelta(hours=2)).isoformat(), "eligible": True}
    state.update(over)
    return state


def answered_report(**over):
    report = {"schema": "wd.grok-hourly.v1", "status": "answered", "task_id": TASK, "request_id": "4" * 32,
              "last_attempt_utc": (NOW + timedelta(seconds=1)).isoformat(), "report_sha256": "5" * 64}
    report.update(over)
    return report


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
        self.observed = {"observed_utc": stamp(NOW - timedelta(seconds=5)), "open": [],
                         "last_admitted_utc": None} if observed is None else observed
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


def run(helper=None, ledger=None, config=ON, ev=None, prompt=PROMPT):
    helper = Helper() if helper is None else helper
    ledger = Ledger() if ledger is None else ledger
    result = broker.GrokBroker(config, helper, ledger).consult(intent(), evidence() if ev is None else ev, prompt)
    return result, helper, ledger


def test_shipped_config_is_off_and_grants_nothing():
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    assert config["schema"] == broker.CONFIG_SCHEMA and config["enabled"] is False
    assert broker.config_enabled(config) is False
    result, helper, ledger = run(config=config)
    assert (result["verdict"], result["reasons"]) == ("disabled", ["admission_config_off"])
    assert helper.calls == [] and ledger.reserved == []
    for almost in ({"schema": broker.CONFIG_SCHEMA, "enabled": "true"}, {"enabled": True}, None):
        assert broker.config_enabled(almost) is False


def test_missing_ports_are_blocked_unknown_not_simulated_readiness():
    no_helper = broker.GrokBroker(ON, None, Ledger()).consult(intent(), evidence(), PROMPT)
    no_ledger = broker.GrokBroker(ON, Helper(), None).consult(intent(), evidence(), PROMPT)
    assert (no_helper["verdict"], no_helper["reasons"]) == ("blocked_unknown", ["helper_port_missing"])
    assert (no_ledger["verdict"], no_ledger["reasons"]) == ("blocked_unknown", ["admission_ledger_port_missing"])


def test_valid_bounded_success_twin_calls_the_helper_exactly_once():
    result, helper, ledger = run()
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert helper.calls == [(TASK, PROMPT)] and len(helper.reads) == 1
    assert len(ledger.reserved) == 1 and len(ledger.finished) == 1
    assert ledger.reserved[0]["intent_sha256"] == canonical_sha256(intent())
    assert result["answer"]["nonce"] == "2" * 32 and result["answer"]["report_sha256"] == "5" * 64
    assert (result["execution_allowed"], result["authority"]) == (False, "none")


def test_wrong_prompt_is_refused_before_any_port_is_used():
    result, helper, ledger = run(prompt="A different prompt.")
    assert (result["verdict"], result["reasons"]) == ("refuse", ["prompt_or_inputs_mismatch"])
    assert helper.calls == [] and ledger.reserved == []


def test_budget_comes_from_the_port_not_the_caller():
    ev = dict(evidence(), budget=budget_state(), admission_ledger={"observed_utc": stamp(NOW), "open": []})
    result, helper, _ = run(helper=Helper(state=budget_state(status="reserved", eligible=False)), ev=ev)
    assert result["reasons"] == ["unreconciled_attempt:reserved"] and helper.calls == []


def test_exhausted_cooldown_failed_and_interrupted_attempts_refuse_without_a_call():
    for state, reason in (
            (budget_state(eligible=False), "hourly_budget_used"),
            (budget_state(last_attempt_utc=(NOW - timedelta(minutes=30)).isoformat()), "hourly_budget_used"),
            (budget_state(status="failed", eligible=False,
                          last_attempt_utc=(NOW - timedelta(minutes=10)).isoformat()), "hourly_budget_used"),
            (budget_state(status="interrupted_or_unknown", eligible=False),
             "unreconciled_attempt:interrupted_or_unknown")):
        result, helper, ledger = run(helper=Helper(state=state))
        assert (result["verdict"], result["reasons"]) == ("refuse", [reason])
        assert helper.calls == [] and ledger.reserved == []


def test_unknown_budget_or_unreadable_port_blocks():
    result, helper, _ = run(helper=Helper(state=budget_state(schema="wd.grok-hourly.v0")))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["budget_unknown"]) and helper.calls == []
    unread = Helper(status_raises=ValueError("Grok state missing; initialize through the controlled installer"))
    result, helper, _ = run(helper=unread)
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["port_observation_unknown"])
    assert helper.calls == []


def test_stale_f0_refuses_without_a_call():
    ev = evidence()
    ev["f0"]["evaluated_utc"] = stamp(NOW - timedelta(minutes=5))
    result, helper, _ = run(ev=ev)
    assert result["reasons"] == ["f0_stale"] and helper.calls == []


def test_concurrent_admission_is_serialized_by_the_ledger():
    busy = Ledger(observed={"observed_utc": stamp(NOW), "open": [{"intent_sha256": "9" * 64}]})
    result, helper, ledger = run(ledger=busy)
    assert result["reasons"] == ["admission_in_flight"] and helper.calls == [] and ledger.reserved == []
    lost = Ledger(win=False)
    result, helper, ledger = run(ledger=lost)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["admission_lost_race"])
    assert helper.calls == [] and len(ledger.reserved) == 1 and ledger.finished == []
    result, helper, _ = run(ledger=UnlockableLedger())
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["admission_ledger_unknown"])
    assert helper.calls == []


def test_a_failed_attempt_is_bound_as_refused_and_never_retried():
    result, helper, ledger = run(helper=Helper(report=answered_report(status="failed")))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["not_the_answered_attempt"])
    assert len(helper.calls) == 1 and helper.reads == [] and len(ledger.finished) == 1


def test_a_helper_exception_is_unknown_and_never_retried():
    result, helper, ledger = run(helper=Helper(raises=TimeoutError()))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["helper_outcome_unknown:TimeoutError"])
    assert len(helper.calls) == 1 and len(ledger.finished) == 1


def test_wrong_reply_and_forbidden_transcript_are_refused():
    for helper, reason in (
            (Helper(report=answered_report(task_id="codex-lead-1/other-task")), "not_the_answered_attempt"),
            (Helper(reply={"text": "x", "tool_calls": [], "report_sha256": "6" * 64}), "answer_not_from_report"),
            (Helper(reply={"text": "x", "tool_calls": ["bash"], "report_sha256": "5" * 64}),
             "forbidden_tool_in_transcript")):
        result, helper, _ = run(helper=helper)
        assert (result["verdict"], result["reasons"]) == ("refuse", [reason])
        assert len(helper.calls) == 1  # the attempt happened and counts against the hour


def test_a_ledger_finish_failure_stays_visible():
    result, _, _ = run(ledger=Ledger(finish_raises=OSError("disk")))
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
