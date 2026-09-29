#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2 F20: the DORMANT Grok broker (Tools fb59267c slice B-Grok-F20).

Nothing calls this module, and it has no default ports. It reads no file, has no
retry path, and changes no provider, payment, auth, account or credit setting. The
unchanged hourly-budgeted helper (``tools/wd_grok_helper.py``, RCO1 7da35242) is
reached only through an INJECTED helper port, at one authorized execution boundary:
``GrokBroker.consult``.

Order: the admission config must be on (default OFF), both ports must exist, and
the prompt must match the intent's digest. The budget and the admission ledger are
observed through the ports and replace any caller-supplied value. Then the pure ``admit``
runs, the durable ledger reserves the admission (serialized), and the helper is
called exactly ONCE. Its answered attempt must bind to the intent (``bind_answer``).
An exception leaves the helper's own durable reservation in place. Admission then
refuses as reserved or interrupted_or_unknown until the helper reconciles it:
there is no refund and no retry. A missing port is blocked_unknown, never a
simulated readiness.
"""
from __future__ import annotations

from typing import Any, Protocol

from tools.bridge_v2_grok_route import ADMIT, BLOCKED, REFUSE, admit, bind_answer, prompt_sha256

CONFIG_SCHEMA = "wd.grok-admission-config.v1"
RESULT_SCHEMA = "wd.grok-broker-result.v1"
DISABLED = "disabled"


class HelperPort(Protocol):
    """The unchanged helper contract, with its state root and command bound inside the port."""

    def status(self) -> dict: ...  # wd_grok_helper.status(root, now) plus the port's observed_utc of that read

    def consult(self, task_id: str, prompt: str) -> dict: ...  # wd_grok_helper.consult(root, task_id, prompt, command)

    def read_answer(self, report: dict) -> dict: ...  # {"text", "tool_calls", "report_sha256" of the bytes read}


class LedgerPort(Protocol):
    """The durable admission ledger (NOT BUILT). It serializes admissions across processes."""

    def observe(self) -> dict: ...  # {"observed_utc", "open": [...], "last_admitted_utc"}

    def reserve(self, admission: dict) -> bool: ...  # True only for the one admission that wins

    def finish(self, admission: dict, outcome: dict) -> None: ...


def _result(verdict: str, reasons: list, **extra) -> dict:
    return {"schema": RESULT_SCHEMA, "verdict": verdict, "reasons": reasons, **extra,
            "execution_allowed": False, "authority": "none"}


def config_enabled(config: Any) -> bool:
    return isinstance(config, dict) and config.get("schema") == CONFIG_SCHEMA and config.get("enabled") is True


class GrokBroker:
    """Dormant. At most one helper call per consult(), and only after admission and a won ledger reservation."""

    def __init__(self, config: Any, helper: Any = None, ledger: Any = None) -> None:
        self.config, self.helper, self.ledger = config, helper, ledger

    def _finish(self, admission: dict, outcome: dict) -> dict:
        try:
            self.ledger.finish(admission, outcome)
        except Exception:  # noqa: BLE001 - the open ledger entry keeps later admissions refused (fail-closed)
            outcome = dict(outcome, reasons=outcome["reasons"] + ["ledger_finish_unknown"])
        return outcome

    def consult(self, intent: Any, evidence: Any, prompt: Any) -> dict:
        if not config_enabled(self.config):
            return _result(DISABLED, ["admission_config_off"])
        if self.helper is None:
            return _result(BLOCKED, ["helper_port_missing"])
        if self.ledger is None:
            return _result(BLOCKED, ["admission_ledger_port_missing"])
        if not isinstance(intent, dict) or not isinstance(evidence, dict) or not isinstance(prompt, str) \
                or prompt_sha256(prompt) != intent.get("prompt_sha256"):
            return _result(REFUSE, ["prompt_or_inputs_mismatch"])
        try:
            observed = dict(evidence, budget=self.helper.status(), admission_ledger=self.ledger.observe())
        except Exception:  # noqa: BLE001 - an unreadable port is unknown, never ready
            return _result(BLOCKED, ["port_observation_unknown"])
        admission = admit(intent, observed)
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
