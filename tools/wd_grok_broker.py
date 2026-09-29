#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F20: the DORMANT Grok broker (Tools fb59267c slice B-Grok-F20).

Nothing calls this module, and it has no default ports. It reads no file, has no
retry path, and changes no provider, payment, auth, account or credit setting. The
unchanged hourly-budgeted helper (``tools/wd_grok_helper.py``, RCO1 7da35242) is
reached only through an INJECTED helper port, at one authorized execution boundary:
``GrokBroker.consult(intent, prompt)``.

The caller supplies only the intent and the prompt. Every fact admission needs comes
from an injected port, stamped by the injected clock right after each read:
  - the read-only snapshot;
  - the F0 Decision and its signed policy, evaluated with expected head/tree = that
    observed snapshot (F0 is never evaluated without an exact head/tree);
  - the helper's hourly budget;
  - the durable admission ledger.
A missing port or an unreadable fact is blocked_unknown, never a simulated readiness.

Order: the admission config must be on (default OFF), every port must exist, and the
prompt must match the intent's digest and byte count. Then the pure ``admit`` runs, the
durable ledger reserves the admission (serialized), and the helper is called exactly
ONCE. Its answered attempt must bind to the intent (``bind_answer``). An exception
leaves the helper's own durable reservation in place, so admission then refuses as
reserved or interrupted_or_unknown until the helper reconciles it: there is no refund
and no retry.
"""
from __future__ import annotations

from typing import Any, Protocol

from tools.bridge_v2_grok_route import (ADMIT, BLOCKED, FEATURE, HEX40, REFUSE, admit, bind_answer, prompt_sha256,
                                        utc_stamp)

CONFIG_SCHEMA = "wd.grok-admission-config.v1"
RESULT_SCHEMA = "wd.grok-broker-result.v1"
DISABLED = "disabled"
PORTS = {"clock": "clock_port_missing", "snapshot": "snapshot_port_missing",
         "activation": "activation_port_missing", "helper": "helper_port_missing",
         "ledger": "admission_ledger_port_missing"}


class ClockPort(Protocol):
    def now(self) -> Any: ...  # an aware datetime


class SnapshotPort(Protocol):
    def observe(self) -> dict: ...  # {"readonly": True, "head", "tree"} of the pinned read-only source


class ActivationPort(Protocol):
    """F0 for one feature, with the operator-trusted pins (policy digest, revocation floor) bound inside."""

    def evaluate(self, feature: str, *, expected_head: str, expected_tree: str) -> tuple: ...  # (Decision, policy)


class HelperPort(Protocol):
    """The unchanged helper contract, with its state root and command bound inside the port."""

    def status(self) -> dict: ...  # wd_grok_helper.status(root, now)

    def consult(self, task_id: str, prompt: str) -> dict: ...  # wd_grok_helper.consult(root, task_id, prompt, command)

    def read_answer(self, report: dict) -> dict: ...  # {"text", "tool_calls", "report_sha256" of the bytes read}


class LedgerPort(Protocol):
    """The durable admission ledger (NOT BUILT). It serializes admissions across processes."""

    def observe(self) -> dict: ...  # {"open": [...], "last_admitted_utc"}

    def reserve(self, admission: dict) -> bool: ...  # True only for the one admission that wins

    def finish(self, admission: dict, outcome: dict) -> None: ...


def _result(verdict: str, reasons: list, **extra) -> dict:
    return {"schema": RESULT_SCHEMA, "verdict": verdict, "reasons": reasons, **extra,
            "execution_allowed": False, "authority": "none"}


def _hex40(value: Any) -> bool:
    return isinstance(value, str) and HEX40.fullmatch(value) is not None


def config_enabled(config: Any) -> bool:
    return isinstance(config, dict) and config.get("schema") == CONFIG_SCHEMA and config.get("enabled") is True


class GrokBroker:
    """Dormant. At most one helper call per consult(), and only after admission and a won ledger reservation."""

    def __init__(self, config: Any, *, clock: Any = None, snapshot: Any = None, activation: Any = None,
                 helper: Any = None, ledger: Any = None) -> None:
        self.config = config
        self.clock, self.snapshot, self.activation = clock, snapshot, activation
        self.helper, self.ledger = helper, ledger

    def _stamped(self, value: Any) -> dict | None:
        """A port read, stamped by the injected clock right after it. Any observed_utc the port gave is replaced."""
        stamp = utc_stamp(self.clock.now())
        return dict(value, observed_utc=stamp) if isinstance(value, dict) and stamp is not None else None

    def _finish(self, admission: dict, outcome: dict) -> dict:
        try:
            self.ledger.finish(admission, outcome)
        except Exception:  # noqa: BLE001 - the open ledger entry keeps later admissions refused (fail-closed)
            outcome = dict(outcome, reasons=outcome["reasons"] + ["ledger_finish_unknown"])
        return outcome

    def consult(self, intent: Any, prompt: Any) -> dict:
        if not config_enabled(self.config):
            return _result(DISABLED, ["admission_config_off"])
        missing = [reason for name, reason in PORTS.items() if getattr(self, name) is None]
        if missing:
            return _result(BLOCKED, missing)
        if not isinstance(intent, dict) or not isinstance(prompt, str) \
                or prompt_sha256(prompt) != intent.get("prompt_sha256") \
                or len(prompt.encode("utf-8")) != intent.get("prompt_bytes"):
            return _result(REFUSE, ["prompt_or_inputs_mismatch"])
        try:
            if utc_stamp(self.clock.now()) is None:
                return _result(BLOCKED, ["time_unknown"])
            snapshot = self._stamped(self.snapshot.observe())
            head, tree = (snapshot.get("head"), snapshot.get("tree")) if snapshot else (None, None)
            if not (snapshot and snapshot.get("readonly") is True and _hex40(head) and _hex40(tree)):
                return _result(BLOCKED, ["snapshot_unknown"])  # F0 would skip an absent head/tree check
            decision, policy = self.activation.evaluate(FEATURE, expected_head=head, expected_tree=tree)
            f0 = {"decision": decision, "evaluated_utc": utc_stamp(self.clock.now()), "head": head, "tree": tree}
            evidence = {"f0": f0, "policy": policy, "snapshot": snapshot,
                        "budget": self._stamped(self.helper.status()),
                        "admission_ledger": self._stamped(self.ledger.observe()),
                        "now_utc": utc_stamp(self.clock.now())}
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
            report = self.helper.consult(intent["task_id"], prompt)  # exactly one attempt; never retried
            answer = self.helper.read_answer(report) if isinstance(report, dict) \
                and report.get("status") == "answered" else None
        except Exception as exc:  # noqa: BLE001 - the helper keeps its durable reservation; no refund
            return self._finish(admission, _result(BLOCKED, ["helper_outcome_unknown:" + type(exc).__name__],
                                                   admission=admission))
        bound = bind_answer(intent, admission, report, answer)
        return self._finish(admission, _result(bound["verdict"], bound["reasons"], admission=admission,
                                               answer=bound))
