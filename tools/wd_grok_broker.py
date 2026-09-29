#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F20: the DORMANT Grok broker (Tools fb59267c slice B-Grok-F20).

Nothing calls this module, and it has no default ports. It reads no file, has no
retry path, and changes no provider, payment, auth, account or credit setting. The
unchanged hourly-budgeted helper (``tools/wd_grok_helper.py``, RCO1 7da35242) is
reached only through an INJECTED helper port, at one authorized execution boundary:
``GrokBroker.consult(intent, prompt)``.

The caller supplies only the intent and the prompt; the broker works on a private
plain-JSON copy of the intent. Every fact admission needs comes from an injected port,
and each port supplies its OWN fact time (the time it acquired or evaluated the fact).
The broker never restamps a fact: it records ``read_utc`` separately (its clock right
after the read), and a fact time that is missing, unparseable or later than that read
is unknown. The pure route then refuses stale facts. The facts are:
  - the read-only snapshot (``observed_utc``);
  - the F0 Decision, its signed policy and ``evaluated_utc``, evaluated with expected
    head/tree = that observed snapshot (F0 is never evaluated without an exact head/tree);
  - the helper's hourly budget (``observed_utc``);
  - the durable admission ledger (``observed_utc``).
A missing port or an unreadable fact is blocked_unknown, never a simulated readiness.

Order: the admission config must be on (default OFF), every port must exist, and the
prompt must be exactly a str, valid UTF-8, bounded, and match the intent's digest and
byte count. Then the pure ``admit`` runs and the durable ledger reserves the admission.
The reservation may be slow, so IMMEDIATELY before the one helper call the broker
re-evaluates F0 at the same snapshot, reads the clock again and re-runs ``admit`` on the
refreshed F0 and the original facts: an expired intent, a revoked or stale F0, or a fact
now too old stops the call, and the reserved entry is finished as refused (the ledger
never refunds the hour). The helper is called exactly ONCE. Its answered attempt must
bind to the intent (``bind_answer``); any exception after the reservation still finishes
the entry. An exception leaves the helper's own durable reservation in place, so
admission then refuses as reserved or interrupted_or_unknown until the helper reconciles
it: there is no refund and no retry.
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from tools.bridge_v2_grok_route import (ADMIT, BLOCKED, FEATURE, HEX40, REFUSE, admit, aware_utc, bind_answer,
                                        parse_utc, prompt_sha256, utc_stamp)

CONFIG_SCHEMA = "wd.grok-admission-config.v1"
RESULT_SCHEMA = "wd.grok-broker-result.v1"
DISABLED = "disabled"
PORT_REASONS = (("clock", "clock_port_missing"), ("snapshot", "snapshot_port_missing"),
                ("activation", "activation_port_missing"), ("helper", "helper_port_missing"),
                ("ledger", "admission_ledger_port_missing"))
MAX_PROMPT_BYTES = 256 * 1024  # a hard ceiling before any port; the signed caps are stricter
MAX_INTENT_BYTES = 16 * 1024


class ClockPort(Protocol):
    def now(self) -> Any: ...  # an aware datetime


class SnapshotPort(Protocol):
    """{"readonly": True, "head", "tree", "observed_utc"} of the pinned read-only source.

    observed_utc is when the port itself acquired the fact; a cached value keeps its own time."""

    def observe(self) -> dict: ...


class ActivationPort(Protocol):
    """F0 for one feature, with the operator-trusted pins (policy digest, revocation floor) bound inside.

    Returns (Decision, policy, evaluated_utc). The Decision carries no head/tree, so the port MUST
    evaluate the signature at expected_head/expected_tree (it is the only binding), and evaluated_utc
    is when it evaluated, never merely when it returned a cached Decision."""

    def evaluate(self, feature: str, *, expected_head: str, expected_tree: str) -> tuple: ...


class HelperPort(Protocol):
    """The unchanged helper contract, with its state root and command bound inside the port."""

    def status(self) -> dict: ...  # wd_grok_helper.status(root, now) plus observed_utc = that now

    def consult(self, task_id: str, prompt: str) -> dict: ...  # wd_grok_helper.consult(root, task_id, prompt, command)

    def read_answer(self, report: dict) -> dict: ...  # {"text", "tool_calls", "report_sha256" of the bytes read}


class LedgerPort(Protocol):
    """The durable admission ledger (a dormant, separately reviewed LedgerPort; nothing here wires one).

    reserve must be a compare-and-set under the ledger's own lock: it re-reads the latest state,
    and wins only if nothing is open, the shared hour is free and its apply time is within
    [admitted_utc, admitted_utc + 60 s] of the admission. finish never refunds the hour."""

    def observe(self) -> dict: ...  # {"open": [...], "last_admitted_utc", "observed_utc"}

    def reserve(self, admission: dict) -> bool: ...  # True only for the one admission that wins

    def finish(self, admission: dict, outcome: dict) -> None: ...


def _result(verdict: str, reasons: list, **extra) -> dict:
    return {"schema": RESULT_SCHEMA, "verdict": verdict, "reasons": reasons, **extra,
            "execution_allowed": False, "authority": "none"}


def _hex40(value: Any) -> bool:
    return isinstance(value, str) and HEX40.fullmatch(value) is not None


def config_enabled(config: Any) -> bool:
    return isinstance(config, dict) and config.get("schema") == CONFIG_SCHEMA and config.get("enabled") is True


def _private_intent(intent: Any) -> dict | None:
    """A bounded plain-JSON copy, so an alias mutated later cannot change what was admitted."""
    if type(intent) is not dict:
        return None
    try:
        text = json.dumps(intent, sort_keys=True, ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None
    return json.loads(text) if len(text) <= MAX_INTENT_BYTES else None


def _prompt_ok(prompt: Any, intent: dict | None) -> bool:
    """Exactly a str, valid UTF-8 (a lone surrogate refuses, never raises), bounded, and the intent's own."""
    if intent is None or type(prompt) is not str or len(prompt) > MAX_PROMPT_BYTES:
        return False
    try:
        size = len(prompt.encode("utf-8"))
    except UnicodeEncodeError:
        return False
    return size <= MAX_PROMPT_BYTES and size == intent.get("prompt_bytes") \
        and prompt_sha256(prompt) == intent.get("prompt_sha256")


class GrokBroker:
    """Dormant. At most one helper call per consult(), and only after admission, a won ledger reservation
    and a passed recheck immediately before the call."""

    def __init__(self, config: Any, *, clock: Any = None, snapshot: Any = None, activation: Any = None,
                 helper: Any = None, ledger: Any = None) -> None:
        self.config = config
        self.clock, self.snapshot, self.activation = clock, snapshot, activation
        self.helper, self.ledger = helper, ledger

    def _fact(self, value: Any) -> dict | None:
        """A port read with the port's own fact time KEPT, plus read_utc; a missing or future fact time is None."""
        read = aware_utc(self.clock.now())
        observed = parse_utc(value.get("observed_utc")) if isinstance(value, dict) else None
        if read is None or observed is None or observed > read:
            return None
        return dict(value, read_utc=utc_stamp(read))

    def _evaluate(self, head: str, tree: str) -> tuple:
        """(f0 block or None, policy). evaluated_utc is the port's own evaluation time, never the return time."""
        decision, policy, evaluated_utc = self.activation.evaluate(FEATURE, expected_head=head, expected_tree=tree)
        read = aware_utc(self.clock.now())
        evaluated = parse_utc(evaluated_utc)
        if read is None or evaluated is None or evaluated > read:
            return None, policy
        return {"decision": decision, "evaluated_utc": evaluated_utc, "read_utc": utc_stamp(read),
                "head": head, "tree": tree}, policy

    def _recheck(self, intent: dict, evidence: dict, admission: dict) -> dict | None:
        """None if the admission still holds at a fresh clock read with a fresh F0; else the refusal."""
        f0, policy = self._evaluate(evidence["f0"]["head"], evidence["f0"]["tree"])
        if f0 is None:
            return _result(BLOCKED, ["recheck_failed", "f0_unknown"], admission=admission)
        now = aware_utc(self.clock.now())
        if now is None:
            return _result(BLOCKED, ["recheck_failed", "time_unknown"], admission=admission)
        again = admit(intent, dict(evidence, f0=f0, policy=policy, now_utc=now.isoformat()))
        if again["verdict"] != ADMIT or again["intent_sha256"] != admission["intent_sha256"] \
                or again["policy_sha256"] != admission["policy_sha256"]:
            return _result(again["verdict"] if again["verdict"] != ADMIT else BLOCKED,
                           ["recheck_failed"] + (again["reasons"] if again["verdict"] != ADMIT else
                                                 ["policy_changed"]), admission=admission)
        return None

    def _finish(self, admission: dict, outcome: dict) -> dict:
        try:
            self.ledger.finish(admission, outcome)
        except Exception:  # noqa: BLE001 - the open ledger entry keeps later admissions refused (fail-closed)
            outcome = dict(outcome, reasons=outcome["reasons"] + ["ledger_finish_unknown"])
        return outcome

    def consult(self, intent: Any, prompt: Any) -> dict:
        if not config_enabled(self.config):
            return _result(DISABLED, ["admission_config_off"])
        ports = {"clock": self.clock, "snapshot": self.snapshot, "activation": self.activation,
                 "helper": self.helper, "ledger": self.ledger}
        missing = [reason for name, reason in PORT_REASONS if ports[name] is None]
        if missing:
            return _result(BLOCKED, missing)
        intent = _private_intent(intent)
        if not _prompt_ok(prompt, intent):
            return _result(REFUSE, ["prompt_or_inputs_mismatch"])
        try:
            if aware_utc(self.clock.now()) is None:
                return _result(BLOCKED, ["time_unknown"])
            snapshot = self._fact(self.snapshot.observe())
            head, tree = (snapshot.get("head"), snapshot.get("tree")) if snapshot else (None, None)
            if not (snapshot and snapshot.get("readonly") is True and _hex40(head) and _hex40(tree)):
                return _result(BLOCKED, ["snapshot_unknown"])  # F0 would skip an absent head/tree check
            f0, policy = self._evaluate(head, tree)
            if f0 is None:
                return _result(BLOCKED, ["f0_unknown"])
            evidence = {"f0": f0, "policy": policy, "snapshot": snapshot,
                        "budget": self._fact(self.helper.status()),
                        "admission_ledger": self._fact(self.ledger.observe())}
            now = aware_utc(self.clock.now())
            evidence["now_utc"] = now.isoformat() if now is not None else None
        except Exception:  # noqa: BLE001 - an unreadable port is unknown, never ready
            return _result(BLOCKED, ["port_observation_unknown"])
        admission = admit(intent, evidence)
        if admission["verdict"] != ADMIT:
            return _result(admission["verdict"], admission["reasons"], admission=admission)
        try:
            won = self.ledger.reserve(admission)
        except Exception:  # noqa: BLE001
            return _result(BLOCKED, ["admission_ledger_unknown"], admission=admission)
        if won is not True:
            return _result(REFUSE, ["admission_lost_race"], admission=admission)
        try:
            stopped = self._recheck(intent, evidence, admission)
        except Exception as exc:  # noqa: BLE001 - an unreadable recheck never pays for a call
            stopped = _result(BLOCKED, ["recheck_failed", "recheck_unknown:" + type(exc).__name__],
                              admission=admission)
        if stopped is not None:
            return self._finish(admission, stopped)  # reserved, not called: the hour stays spent (no refund)
        try:
            report = self.helper.consult(intent["task_id"], prompt)  # exactly one attempt; never retried
        except Exception as exc:  # noqa: BLE001 - the helper keeps its durable reservation; no refund
            return self._finish(admission, _result(BLOCKED, ["helper_outcome_unknown:" + type(exc).__name__],
                                                   admission=admission))
        try:
            answer = self.helper.read_answer(report) if isinstance(report, dict) \
                and report.get("status") == "answered" else None
            bound = bind_answer(intent, admission, report, answer)
        except Exception as exc:  # noqa: BLE001 - the attempt happened; the entry is still finished
            return self._finish(admission, _result(BLOCKED, ["answer_binding_unknown:" + type(exc).__name__],
                                                   admission=admission))
        return self._finish(admission, _result(bound["verdict"], bound["reasons"], admission=admission,
                                               answer=bound))
