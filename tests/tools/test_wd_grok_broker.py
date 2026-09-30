"""F20 dormant Grok broker through INJECTED fake ports (AUTHORED, NOT RUN).

The fakes stand in for the injected clock, the read-only snapshot, F0 activation, the
unchanged helper (7da35242) and the durable admission ledger. Each fake port supplies its
own fact time. The ledger fakes return MOCKED arbitration outcomes in one process; they
prove the broker's handling of each outcome, not any real concurrency. Nothing here
calls Grok, F0, a clock or the network. The real helper runs only in the contract
fixtures (RCO1 e855 S3): its status(), its prompt-cap refusal and its DEFERRING consult,
each on a pytest tmp state root with a runner that fails the test if it is ever started.
"""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone, tzinfo
import hashlib
import inspect
import json
from pathlib import Path

import pytest

from tools import bridge_v2_grok_route as route
from tools import wd_grok_broker as broker
from tools import wd_grok_helper as real_helper
from tools.bridge_v2_activation import Decision, canonical_sha256
from tools.bridge_v2_grok_route import prepare_grok_consult

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
HEAD, TREE = "a" * 40, "b" * 40
TASK = "codex-lead-1/bridge-v2-grok-route-20260930"
PROMPT = "Review the admission gate. COMPLETE, no tools."
POLICY = {"schema": "fixture-signed-policy", "expires_utc": "2026-10-30T00:00:00Z", "parameters": {"F20": {
    "model": "grok-4", "efforts": ["high"], "allowed_tools": [], "max_prompt_bytes": 48000,
    "max_intent_ttl_seconds": 900}}}
ON = {"schema": broker.CONFIG_SCHEMA, "enabled": True}
CONFIG = Path(__file__).resolve().parents[2] / "configs" / "bridge_v2_grok_admission.json"
HELPER_PATH = Path(__file__).resolve().parents[2] / "tools" / "wd_grok_helper.py"


def intent(prompt=PROMPT, policy=POLICY):
    return prepare_grok_consult(task_id=TASK, request_id="1" * 32, request_revision=1, prompt=prompt,
                                snapshot={"head": HEAD, "tree": TREE}, model="grok-4", effort="high",
                                budget_class="shared_hourly", authorization_ref=canonical_sha256(policy),
                                nonce="2" * 32, ttl_seconds=600, now=NOW - timedelta(seconds=30))


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def budget_state(**over):
    # The helper's own status shape plus the port's observed_utc (the now it passed to status).
    state = {"schema": "wd.grok-hourly.v1", "status": "answered",
             "last_attempt_utc": (NOW - timedelta(hours=2)).isoformat(), "eligible": True,
             "observed_utc": stamp(NOW)}
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
    """Read order in consult(): start, snapshot stamp, F0 stamp, budget stamp, ledger stamp, now (1-6); then
    the recheck's sample (7), its F0 stamp (8) and its final now (9)."""

    def __init__(self, *moments):
        super().__init__()
        self.moments = list(moments)

    def now(self):
        self.reads += 1
        return self.moments[min(self.reads, len(self.moments)) - 1]


class BrokenClock(Clock):
    def now(self):
        raise OSError("clock unreadable")


class _Stateful(tzinfo):
    """+05:30 on the first utcoffset() read, None afterwards: a second read would look like LOCAL time."""

    def __init__(self):
        self.reads = 0

    def utcoffset(self, dt):
        self.reads += 1
        return timedelta(hours=5, minutes=30) if self.reads == 1 else None


class _Unimplemented(tzinfo):
    pass  # the base tzinfo.utcoffset raises NotImplementedError


class _Delta(timedelta):
    pass


class _SubclassOffset(tzinfo):
    def utcoffset(self, dt):
        return _Delta(0)


class _Sub(datetime):
    pass


class FreshZoneClock(Clock):
    """Every read is a NEW +05:30 wall time for ``moment`` whose zone answers once, then None."""

    def __init__(self, moment=NOW):
        super().__init__(moment)
        self.zones = []

    def now(self):
        self.reads += 1
        self.zones.append(_Stateful())
        return (self.moment + timedelta(hours=5, minutes=30)).replace(tzinfo=self.zones[-1])


class Snapshot:
    def __init__(self, value=None, observed=NOW):
        self.value = {"readonly": True, "head": HEAD, "tree": TREE, "observed_utc": stamp(observed)} \
            if value is None else value

    def observe(self):
        return dict(self.value) if isinstance(self.value, dict) else self.value


class Activation:
    """evaluated_utc is the port's own evaluation time: fixed (a CACHED port), live from a clock (an uncached
    port), or then_evaluated on the recheck's evaluation."""

    def __init__(self, decision=None, policy=None, raises=None, evaluated=NOW, live=None, then=None,
                 then_evaluated=None):
        self.decision = Decision("F20", True, "enabled", canonical_sha256(POLICY), 3) if decision is None else decision
        self.policy = POLICY if policy is None else policy
        self.raises, self.calls = raises, []
        self.evaluated, self.live, self.then, self.then_evaluated = evaluated, live, then, then_evaluated

    def evaluate(self, feature, *, expected_head, expected_tree):
        self.calls.append((feature, expected_head, expected_tree))
        if self.raises is not None:
            raise self.raises
        decision = self.then if self.then is not None and len(self.calls) > 1 else self.decision
        moment = self.live.moment if self.live is not None else self.evaluated
        if self.then_evaluated is not None and len(self.calls) > 1:
            moment = self.then_evaluated
        return decision, self.policy, stamp(moment) if isinstance(moment, datetime) else moment


class Helper:
    def __init__(self, state=None, report=None, reply=None, raises=None, status_raises=None, read_raises=None):
        self.state = budget_state() if state is None else state
        self.report = answered_report() if report is None else report
        self.reply = {"text": "No blocker.", "tool_calls": [], "report_sha256": "5" * 64} if reply is None else reply
        self.raises, self.status_raises, self.read_raises = raises, status_raises, read_raises
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
        if self.read_raises is not None:
            raise self.read_raises
        return dict(self.reply)


def ledger_state(**over):
    state = {"open": [], "last_admitted_utc": None, "observed_utc": stamp(NOW)}
    state.update(over)
    return state


class Ledger:
    """A MOCKED ledger: it returns the arbitration outcome it is given, in one process."""

    def __init__(self, observed=None, win=True, finish_raises=None):
        self.observed = ledger_state() if observed is None else observed
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


class SlowLedger(Ledger):
    """A reservation that takes ``by`` on the shared injected clock (no sleep)."""

    def __init__(self, clock, by, **kwargs):
        super().__init__(**kwargs)
        self.clock, self.by = clock, by

    def reserve(self, admission):
        self.clock.moment = self.clock.moment + self.by
        return super().reserve(admission)


class AliasingLedger(Ledger):
    """Mutates the caller's intent object while the reservation is in progress."""

    def __init__(self, alias, **kwargs):
        super().__init__(**kwargs)
        self.alias = alias

    def reserve(self, admission):
        self.alias["task_id"] = "codex-lead-1/not-the-admitted-task"
        return super().reserve(admission)


_VALID = object()  # a sentinel, so it=None really passes None (RCO1 B2)


def run(config=ON, prompt=PROMPT, it=_VALID, **over):
    ports = {"clock": Clock(), "snapshot": Snapshot(), "activation": Activation(), "helper": Helper(),
             "ledger": Ledger()}
    ports.update(over)
    result = broker.GrokBroker(config, **ports).consult(intent() if it is _VALID else it, prompt)
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
    # F0 evaluated against the observed snapshot at admission, and again right before the one call
    assert ports["activation"].calls == [("F20", HEAD, TREE)] * 2
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


@pytest.mark.parametrize("value", [  # each dict is fresh, so the read-only/head/tree guard itself decides (RCO1 S2)
    {"readonly": False, "head": HEAD, "tree": TREE, "observed_utc": stamp(NOW)},
    {"readonly": 1, "head": HEAD, "tree": TREE, "observed_utc": stamp(NOW)},
    {"readonly": True, "head": HEAD, "observed_utc": stamp(NOW)},
    {"readonly": True, "head": HEAD.upper(), "tree": TREE, "observed_utc": stamp(NOW)},
    "a0633ef2",
])
def test_f0_never_runs_without_an_exact_read_only_snapshot(value):
    result, ports = run(snapshot=Snapshot(value))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["snapshot_unknown"])
    assert untouched(ports)


def test_a_snapshot_other_than_the_intents_refuses():
    result, ports = run(snapshot=Snapshot({"readonly": True, "head": "c" * 40, "tree": TREE,
                                           "observed_utc": stamp(NOW)}))
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
    (Clock(_Sub(2026, 9, 30, 12, 0, tzinfo=timezone.utc)), "blocked_unknown", "time_unknown"),  # a subclass
    (Clock(datetime(2026, 9, 30, 12, 0, tzinfo=_Unimplemented())), "blocked_unknown", "time_unknown"),  # raised
    (Clock(datetime(2026, 9, 30, 12, 0, tzinfo=_SubclassOffset())), "blocked_unknown", "time_unknown"),
    (BrokenClock(), "blocked_unknown", "port_observation_unknown"),
    # F0 evaluated (by the port) at NOW, admission judged at NOW + 61 s: the F0 decision is stale
    (SequenceClock(NOW, NOW, NOW, NOW + timedelta(seconds=61)), "refuse", "f0_stale"),
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


def test_mocked_ledger_arbitration_outcomes_never_reach_the_helper():
    """MOCKED outcomes from a fake ledger; this is not a concurrency proof (the real CAS is the ledger's)."""
    result, ports = run(ledger=Ledger(observed=ledger_state(open=[{"intent_sha256": "9" * 64}])))
    assert result["reasons"] == ["admission_in_flight"]
    assert ports["helper"].calls == [] and ports["ledger"].reserved == []
    result, ports = run(ledger=Ledger(observed=ledger_state(last_admitted_utc="2026-09-30T11:40:00Z")))
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
    assert imported == {"__future__", "json", "typing", "tools.bridge_v2_grok_route"}
    assert "exception_path" not in source and "exception_sha256" not in source
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {"getattr", "setattr", "hasattr", "exec", "eval", "compile", "globals", "vars", "__import__"}


# --- fact freshness: the port's own fact time is kept, never restamped (Tools f907 #1) -----------

OLD, FUTURE = NOW - timedelta(seconds=121), NOW + timedelta(seconds=1)


@pytest.mark.parametrize("over, verdict, reason", [
    ({"snapshot": Snapshot(observed=OLD)}, "blocked_unknown", "snapshot_unknown"),  # a cached, stale fact
    ({"snapshot": Snapshot(observed=FUTURE)}, "blocked_unknown", "snapshot_unknown"),  # later than the read
    ({"snapshot": Snapshot({"readonly": True, "head": HEAD, "tree": TREE})}, "blocked_unknown", "snapshot_unknown"),
    ({"helper": Helper(state=budget_state(observed_utc=stamp(OLD)))}, "blocked_unknown", "budget_unknown"),
    ({"helper": Helper(state=budget_state(observed_utc=stamp(FUTURE)))}, "blocked_unknown", "budget_unknown"),
    ({"helper": Helper(state=budget_state(observed_utc=None))}, "blocked_unknown", "budget_unknown"),
    ({"ledger": Ledger(observed=ledger_state(observed_utc=stamp(OLD)))}, "blocked_unknown",
     "admission_ledger_unknown"),
    ({"ledger": Ledger(observed={"open": [], "last_admitted_utc": None})}, "blocked_unknown",
     "admission_ledger_unknown"),
    ({"activation": Activation(evaluated=NOW - timedelta(seconds=61))}, "refuse", "f0_stale"),  # a cached F0
    ({"activation": Activation(evaluated=FUTURE)}, "blocked_unknown", "f0_unknown"),
    ({"activation": Activation(evaluated=None)}, "blocked_unknown", "f0_unknown"),
])
def test_a_port_fact_time_is_kept_and_stale_future_or_missing_refuses(over, verdict, reason):
    result, ports = run(**over)
    assert (result["verdict"], result["reasons"]) == (verdict, [reason])
    assert ports["helper"].calls == [] and ports["ledger"].reserved == []


def test_facts_just_inside_their_bounds_are_the_success_twin():
    edge = NOW - timedelta(seconds=120)
    result, ports = run(snapshot=Snapshot(observed=edge), helper=Helper(state=budget_state(observed_utc=stamp(edge))),
                        ledger=Ledger(observed=ledger_state(observed_utc=stamp(edge))),
                        activation=Activation(evaluated=NOW - timedelta(seconds=60), then_evaluated=NOW))
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert len(ports["helper"].calls) == 1


# --- a slow reservation is rechecked immediately before the one call (Tools f907 #2) ------------

def test_a_slow_reservation_past_the_f0_bound_stops_before_the_call_and_finishes_the_entry():
    clock = Clock()
    result, ports = run(clock=clock, ledger=SlowLedger(clock, timedelta(seconds=61)))  # F0 fixed (cached) at NOW
    assert (result["verdict"], result["reasons"]) == ("refuse", ["recheck_failed", "f0_cached"])
    assert ports["helper"].calls == [] and len(ports["ledger"].reserved) == 1
    assert [outcome["reasons"] for _, outcome in ports["ledger"].finished] == [["recheck_failed", "f0_cached"]]


def test_a_cached_f0_inside_its_60_s_window_is_still_refused_at_the_recheck():
    """RCO1 e855 S1: a port caching per head/tree could serve a pre-revocation Decision for up to 60 s."""
    clock = Clock()
    result, ports = run(clock=clock, ledger=SlowLedger(clock, timedelta(seconds=5)))  # evaluated NOW, sampled NOW+5
    assert (result["verdict"], result["reasons"]) == ("refuse", ["recheck_failed", "f0_cached"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1
    clock = Clock()
    fresh = Activation(live=clock)  # the twin: evaluated at the recheck itself
    result, ports = run(clock=clock, activation=fresh, ledger=SlowLedger(clock, timedelta(seconds=5)))
    assert (result["verdict"], result["reasons"]) == ("answered_bound", []) and len(ports["helper"].calls) == 1


@pytest.mark.parametrize("then_evaluated,reasons", [
    ("2026-09-30T12:00:00.050000Z", ["recheck_failed", "f0_cached"]),  # cached 650 ms before the sample
    ("2026-09-30T12:00:00Z", ["recheck_failed", "f0_time_precision_unknown"]),  # a whole second proves nothing
    ("2026-09-30T12:00:00.700000Z", []),  # the genuine fresh twin: evaluated at the sample itself
], ids=["cached_050", "whole_second", "fresh_700"])
def test_a_same_second_cached_f0_is_refused_at_full_precision(then_evaluated, reasons):
    """RCO1 e932 SF1: the old whole-second floor let a Decision evaluated at .050 pass a recheck sampled at .700."""
    clock = Clock(NOW + timedelta(milliseconds=700))  # every read, the recheck's sample included, is 12:00:00.700
    result, ports = run(clock=clock, activation=Activation(then_evaluated=then_evaluated))
    assert result["reasons"] == reasons and len(ports["activation"].calls) == 2
    if reasons:
        assert result["verdict"] == "refuse" and ports["helper"].calls == [] and len(ports["ledger"].finished) == 1
    else:
        assert result["verdict"] == "answered_bound" and len(ports["helper"].calls) == 1


@pytest.mark.parametrize("version, verdict, reason", [
    (2, "refuse", "revocation_regressed"),
    (None, "blocked_unknown", "revocation_unknown"),
])
def test_a_regressed_or_unknown_revocation_version_is_never_paid_for(version, verdict, reason):
    then = Decision("F20", True, "enabled", canonical_sha256(POLICY), version)
    result, ports = run(activation=Activation(then=then))
    assert (result["verdict"], result["reasons"]) == (verdict, ["recheck_failed", reason])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1
    newer, ports = run(activation=Activation(then=Decision("F20", True, "enabled", canonical_sha256(POLICY), 4)))
    assert (newer["verdict"], newer["reasons"]) == ("answered_bound", [])  # the twin: a newer version is fine


def _policy_run(policy, by):
    clock = Clock()
    decision = Decision("F20", True, "enabled", canonical_sha256(policy), 3)
    return run(it=intent(policy=policy), clock=clock, ledger=SlowLedger(clock, by),
               activation=Activation(decision=decision, policy=policy, live=clock))


def test_a_policy_expiring_before_the_call_is_never_paid_for():
    expiring = dict(POLICY, expires_utc=stamp(NOW + timedelta(seconds=3)))
    result, ports = _policy_run(expiring, timedelta(seconds=5))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["recheck_failed", "policy_expired"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1
    unknown = {key: value for key, value in POLICY.items() if key != "expires_utc"}
    result, ports = _policy_run(unknown, timedelta(seconds=5))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["recheck_failed", "policy_expiry_unknown"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1
    later = dict(POLICY, expires_utc=stamp(NOW + timedelta(seconds=6)))
    result, ports = _policy_run(later, timedelta(seconds=5))  # the twin: still ahead at the final read
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])


@pytest.mark.parametrize("moments", [
    [NOW] * 6 + [NOW - timedelta(seconds=1)],  # the recheck sample is before the admission's now
    [NOW] * 8 + [NOW - timedelta(seconds=1)],  # the final read is before the sample
])
def test_a_clock_running_backwards_at_the_recheck_is_never_paid_for(moments):
    result, ports = run(clock=SequenceClock(*moments))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["recheck_failed", "clock_regressed"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1


def test_a_fact_that_aged_past_its_bound_during_the_reservation_stops_the_call():
    clock = Clock()
    result, ports = run(clock=clock, activation=Activation(live=clock), ledger=SlowLedger(clock, timedelta(seconds=121)))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["recheck_failed", "snapshot_unknown"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1


def test_an_intent_expiring_during_the_reservation_is_never_paid_for():
    clock = Clock()
    live = Activation(live=clock)  # an uncached F0 port: fresh on every evaluation
    result, ports = run(clock=clock, activation=live, ledger=SlowLedger(clock, timedelta(seconds=570)))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["recheck_failed", "intent_expired_or_future"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1


def test_an_f0_revoked_during_the_reservation_is_never_paid_for():
    clock = Clock()
    revoked = Activation(live=clock, then=Decision("F20", False, "revoked", canonical_sha256(POLICY), 4))
    result, ports = run(clock=clock, activation=revoked, ledger=SlowLedger(clock, timedelta(seconds=5)))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["recheck_failed", "f0_disabled"])
    assert ports["helper"].calls == [] and len(revoked.calls) == 2 and len(ports["ledger"].finished) == 1


def test_a_failing_recheck_port_is_unknown_and_never_paid_for():
    class FlakyActivation(Activation):
        def evaluate(self, feature, *, expected_head, expected_tree):
            if self.calls:
                raise OSError("F0 store unreadable")
            return super().evaluate(feature, expected_head=expected_head, expected_tree=expected_tree)

    result, ports = run(activation=FlakyActivation())
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["recheck_failed", "recheck_unknown:OSError"])
    assert ports["helper"].calls == [] and len(ports["ledger"].finished) == 1


def test_a_slow_but_still_valid_reservation_is_the_success_twin():
    clock = Clock()
    live = Activation(live=clock)
    result, ports = run(clock=clock, activation=live, ledger=SlowLedger(clock, timedelta(seconds=30)))
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert len(ports["helper"].calls) == 1 and len(live.calls) == 2


# --- every clock read takes the offset once (RCO2 23:49:44Z S1) -----------------------------------

def test_every_clock_read_takes_the_offset_once_including_the_final_recheck():
    """Read order: start, snapshot, F0, budget, ledger, now; then the recheck's sample, F0 and final now (9)."""
    clock = FreshZoneClock()
    result, ports = run(clock=clock)
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert clock.reads == 9 and [zone.reads for zone in clock.zones] == [1] * 9  # never a second (local) read
    assert result["admission"]["admitted_utc"] == "2026-09-30T12:00:00Z"


@pytest.mark.parametrize("position, reason", [
    (7, "time_unknown"),  # the recheck's sample, before F0 is evaluated
    (8, "f0_unknown"),  # the recheck's F0 stamp
    (9, "time_unknown"),  # the final now
])
@pytest.mark.parametrize("late", [
    _Sub(2026, 9, 30, 12, 0, 5, tzinfo=timezone.utc),  # a datetime subclass
    datetime(2026, 9, 30, 12, 0, 5, tzinfo=_Unimplemented()),  # NotImplementedError, never raised out
    datetime(2026, 9, 30, 12, 0, 5, tzinfo=_SubclassOffset()),  # a timedelta subclass offset
    datetime.max.replace(tzinfo=timezone(-timedelta(hours=23, minutes=59))),  # past datetime.max in UTC
    datetime(2026, 9, 30, 12, 0, 5),  # naive
])
def test_an_unknown_time_at_the_final_recheck_is_never_paid_for(late, position, reason):
    moments = [NOW] * 9
    moments[position - 1] = late
    result, ports = run(clock=SequenceClock(*moments))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["recheck_failed", reason])
    assert ports["helper"].calls == [] and len(ports["ledger"].reserved) == 1  # spent: reserved, never refunded
    assert [outcome["reasons"] for _, outcome in ports["ledger"].finished] == [["recheck_failed", reason]]


@pytest.mark.parametrize("zone_now", [
    lambda: datetime(2026, 9, 30, 17, 30, 5, tzinfo=_Stateful()),  # a stateful zone, read once
    lambda: datetime(2026, 9, 30, 4, 30, 5, 999999, tzinfo=timezone(-timedelta(hours=7, minutes=30))),
])
def test_a_zoned_clock_at_the_final_recheck_is_the_success_twin(zone_now):
    late = zone_now()
    clock = SequenceClock(*([NOW] * 8), late)
    result, ports = run(clock=clock)
    assert (result["verdict"], result["reasons"]) == ("answered_bound", []) and len(ports["helper"].calls) == 1
    if isinstance(late.tzinfo, _Stateful):
        assert late.tzinfo.reads == 1


# --- the intent and the prompt (RCO2 SF1/SF2/N1/N2; Tools f907 #3) -------------------------------

def test_an_alias_mutated_after_admission_cannot_redirect_the_call():
    it = intent()
    result, ports = run(it=it, ledger=AliasingLedger(it))
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert ports["helper"].calls == [(TASK, PROMPT)]  # the admitted task, not the mutated alias


def test_an_exception_while_binding_still_finishes_the_reservation():
    result, ports = run(helper=Helper(read_raises=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad report")))
    assert (result["verdict"], result["reasons"]) == ("blocked_unknown", ["answer_binding_unknown:UnicodeDecodeError"])
    assert len(ports["helper"].calls) == 1 and len(ports["ledger"].finished) == 1


def test_a_lone_surrogate_answer_is_refused_not_raised():
    result, ports = run(helper=Helper(reply={"text": "\ud800", "tool_calls": [], "report_sha256": "5" * 64}))
    assert (result["verdict"], result["reasons"]) == ("refuse", ["answer_unknown"])
    assert len(ports["ledger"].finished) == 1


class _Str(str):
    pass


@pytest.mark.parametrize("prompt", ["\ud800", _Str(PROMPT), PROMPT.encode("utf-8"), None,
                                    "x" * (broker.MAX_PROMPT_BYTES + 1)])
def test_malformed_or_oversized_prompts_refuse_before_any_port(prompt):
    result, ports = run(prompt=prompt)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["prompt_or_inputs_mismatch"])
    assert untouched(ports) and ports["clock"].reads == 0


@pytest.mark.parametrize("task_id,prompt", [("-x", PROMPT), (TASK, " "), (TASK, "\n\t ")],
                         ids=["dash_task_id", "blank_prompt", "whitespace_prompt"])
def test_the_helpers_own_input_rules_refuse_before_any_port_or_reservation(task_id, prompt):
    """RCO1 e932 SF2: the helper refuses these only after the ledger has reserved (and never refunded) the hour.
    The digest and byte count match, so only the helper's own rules can refuse here."""
    it = dict(intent(), task_id=task_id, prompt_sha256=route.prompt_sha256(prompt),
              prompt_bytes=len(prompt.encode("utf-8")))
    result, ports = run(it=it, prompt=prompt)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["helper_inputs_invalid"])
    assert untouched(ports) and ports["clock"].reads == 0 and ports["ledger"].finished == []
    twin, ports = run()  # the same path with the helper's rules met
    assert (twin["verdict"], twin["reasons"]) == ("answered_bound", []) and len(ports["helper"].calls) == 1


@pytest.mark.parametrize("it", [None, [], dict(intent(), extra={1, 2}), dict(intent(), pad="x" * (16 * 1024))])
def test_non_json_or_oversized_intents_refuse_before_any_port(it):
    result, ports = run(it=it)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["prompt_or_inputs_mismatch"])
    assert untouched(ports) and ports["clock"].reads == 0


def test_a_multibyte_prompt_is_counted_in_utf8_bytes():
    text = "Katselmoi pääsyportti: äöå ✓ — COMPLETE, no tools."
    assert len(text.encode("utf-8")) > len(text)
    result, ports = run(it=intent(prompt=text), prompt=text)
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert ports["helper"].calls == [(TASK, text)]
    result, ports = run(it=dict(intent(prompt=text), prompt_bytes=len(text)), prompt=text)  # counted in chars
    assert (result["verdict"], result["reasons"]) == ("refuse", ["prompt_or_inputs_mismatch"])
    assert untouched(ports)


def _never_run(*args, **kwargs):
    raise AssertionError("the real helper's runner must never start in these fixtures")


def test_a_prompt_over_the_helpers_own_cap_is_refused_before_any_port(tmp_path):
    """RCO1 e855 N1: the helper refuses more than 48000 bytes before its own reservation, but only after the
    broker's ledger would already have reserved (and never refunded) the hour. Its own intent, so not vacuous."""
    assert broker.MAX_PROMPT_BYTES == route.HELPER_MAX_PROMPT_BYTES == 48000
    with pytest.raises(ValueError, match="48000"):
        real_helper.consult(tmp_path, TASK, "x" * 48001, ["never-run"], runner=_never_run, now=NOW)
    assert list(tmp_path.iterdir()) == []  # refused before the helper's lock or state
    over = "x" * 48001
    result, ports = run(it=intent(prompt=over), prompt=over)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["prompt_or_inputs_mismatch"])
    assert untouched(ports) and ports["clock"].reads == 0
    edge = "x" * 48000
    result, ports = run(it=intent(prompt=edge), prompt=edge)
    assert (result["verdict"], result["reasons"]) == ("answered_bound", []) and ports["helper"].calls == [(TASK, edge)]


class MutatingLedger(Ledger):
    """Mutates every admission and outcome it is handed, as a buggy or hostile port could."""

    def reserve(self, admission):
        admission["allowed_tools"].append("bash")
        admission["intent_sha256"] = "0" * 64
        return super().reserve(admission)

    def finish(self, admission, outcome):
        admission["allowed_tools"].append("bash")
        outcome["reasons"].append("forged")
        super().finish(admission, outcome)


def test_a_port_mutating_its_copy_changes_nothing_the_broker_trusts():
    """RCO1 e855 N4: reserve and finish get copies, so bind_answer's allowlist and the result stay the broker's."""
    ledger = MutatingLedger()
    helper = Helper(reply={"text": "x", "tool_calls": ["bash"], "report_sha256": "5" * 64})
    result, _ = run(ledger=ledger, helper=helper)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["forbidden_tool_in_transcript"])
    assert result["admission"]["allowed_tools"] == [] and result["admission"]["intent_sha256"] != "0" * 64
    assert ledger.reserved[0]["allowed_tools"] == ["bash"] and len(ledger.finished) == 1  # only the copies changed


# --- the real helper's contract (RCO1 e855 S3): a reviewed blob, the fields admit reads, deferred ---------

class RealStatusHelper(Helper):
    """The REAL helper's status() on a tmp state root; the fake consult and read_answer otherwise."""

    def __init__(self, root, **state):
        super().__init__()
        self.root = root
        self.write(**state)

    def write(self, **state):
        text = json.dumps(dict({"schema": real_helper.SCHEMA}, **state))
        (self.root / "hourly-state.json").write_text(text, encoding="utf-8")

    def status(self):
        return dict(real_helper.status(self.root, NOW), observed_utc=stamp(NOW))


class RealDeferringHelper(RealStatusHelper):
    """Another caller takes the helper's hour after admission, so the REAL consult defers (its runner never runs)."""

    def consult(self, task_id, prompt):
        self.calls.append((task_id, prompt))
        self.write(status="answered", last_attempt_utc=(NOW - timedelta(minutes=10)).isoformat())
        self.report = real_helper.consult(self.root, task_id, prompt, ["never-run"], runner=_never_run, now=NOW)
        return dict(self.report)


def test_the_helper_is_a_reviewed_blob_with_the_fields_admission_reads(tmp_path):
    data = HELPER_PATH.read_bytes().replace(b"\r\n", b"\n")  # the committed LF bytes under core.autocrlf
    assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() in route.HELPER_BLOBS
    assert real_helper.SCHEMA == route.HELPER_STATE_SCHEMA
    helper = RealStatusHelper(tmp_path, status="answered", last_attempt_utc=(NOW - timedelta(hours=2)).isoformat())
    assert set(route.HELPER_STATUS_FIELDS) <= set(helper.status())
    result, _ = run(helper=helper)
    assert (result["verdict"], result["reasons"]) == ("answered_bound", []) and len(helper.calls) == 1


@pytest.mark.parametrize("state, reason", [
    ({"status": "reserved", "last_attempt_utc": (NOW - timedelta(minutes=1)).isoformat(), "timeout_seconds": 300},
     "unreconciled_attempt:reserved"),
    ({"status": "reserved", "last_attempt_utc": (NOW - timedelta(minutes=10)).isoformat(), "timeout_seconds": 300},
     "unreconciled_attempt:interrupted_or_unknown"),  # the real helper's past-deadline observation
    ({"status": "failed", "last_attempt_utc": (NOW - timedelta(minutes=10)).isoformat()}, "hourly_budget_used"),
])
def test_the_real_helper_status_refuses_through_the_broker(tmp_path, state, reason):
    helper = RealStatusHelper(tmp_path, **state)
    result, ports = run(helper=helper)
    assert (result["verdict"], result["reasons"]) == ("refuse", [reason])
    assert helper.calls == [] and ports["ledger"].reserved == []


def test_the_real_helpers_deferred_report_is_refused_and_never_read(tmp_path):
    """A deferred report has request_id None and no attempt: it never binds, and read_answer never runs."""
    helper = RealDeferringHelper(tmp_path, status="answered",
                                 last_attempt_utc=(NOW - timedelta(hours=2)).isoformat())
    result, ports = run(helper=helper)
    assert (result["verdict"], result["reasons"]) == ("refuse", ["not_the_answered_attempt"])
    assert (helper.report["status"], helper.report["request_id"], helper.report["consultation_attempted"]) \
        == ("deferred", None, False)
    assert len(helper.calls) == 1 and helper.reads == [] and len(ports["ledger"].finished) == 1
