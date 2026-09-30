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
byte count. The helper's own input rules (a bounded task ID, a non-blank prompt) are checked
too, before any port read (RCO1 e932 SF2). Then the pure ``admit`` runs and the durable
ledger reserves the admission. The reservation may be slow, so as the last step before the
one helper call the broker samples the clock, re-evaluates F0 at the same snapshot, and
requires that F0 to be evaluated at or after that sample at full precision (a cached Decision
refuses as f0_cached; a whole-second stamp in the sample's second as f0_time_precision_unknown). It reads the
clock again (never backwards), re-runs ``admit`` on the refreshed F0 and the original
facts, and requires a non-decreasing revocation version and an unexpired signed policy.
An expired intent, a revoked, cached or stale F0, an expired policy, or a fact now too old
stops the call, and the reserved entry is finished as refused (the ledger never refunds
the hour). The helper is called exactly ONCE. Its answered attempt must bind to the intent
(``bind_answer``). Every port receives a plain-JSON COPY of the admission and outcome.

Any ORDINARY exception (``Exception``) after the reservation still finishes the entry. A
``BaseException`` (KeyboardInterrupt, SystemExit) skips the finish: the ledger entry stays
open and blocks every later admission until an operator reconciles it (fail-closed, never
a silent refund). An exception leaves the helper's own durable reservation in place, so
admission then refuses as reserved or interrupted_or_unknown until the helper reconciles
it: there is no refund and no retry. A prompt above the helper's own 48000-byte cap is
refused before any port: the helper refuses it too (before its own reservation), but only
after the broker's ledger has already reserved, and never refunds, the shared hour.

Every clock read goes through the route's ``aware_utc``: exactly a datetime, its offset
read ONCE as exactly a timedelta inside +-24 h, the naive wall time minus that offset
marked UTC, with no astimezone and no local-time fallback. An unknown time at any read
(including the final recheck) is time_unknown, so consult stays total.

What this does NOT prove (disclosed):
* The gap between the passed recheck and the helper actually starting its attempt is NOT
  bounded: a scheduler delay, a slow process or a slow helper start can let the intent
  expire in that gap. bind_answer then refuses the late attempt, but the attempt (and any
  cost) is already spent. The broker does NOT claim that no paid call can happen after
  expiry under an arbitrary scheduling delay; it claims only that no call starts after a
  FAILED recheck.
* Port honesty is unproven: a port that stamps a stale fact as fresh, an F0 port that
  stamps a cached Decision with a fresh evaluated_utc or ignores the expected head/tree, a
  clock that lies, or a helper that ignores its own hour defeats these checks. The ports'
  own review, the pinned read-only source and the helper's independent hourly state are
  the backstops. The helper contract is pinned by blob (route HELPER_BLOBS).
* read_utc is recorded for audit only; no decision reads it. The policy_changed branch of
  the recheck is defence in depth (admit already binds the policy digest).
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from tools.bridge_v2_grok_route import (ADMIT, BLOCKED, FEATURE, HELPER_MAX_PROMPT_BYTES, HEX40, REFUSE, TASK_RE,
                                        admit, aware_utc, bind_answer, parse_utc, prompt_sha256, utc_stamp)

CONFIG_SCHEMA = "wd.grok-admission-config.v1"
RESULT_SCHEMA = "wd.grok-broker-result.v1"
DISABLED = "disabled"
PORT_REASONS = (("clock", "clock_port_missing"), ("snapshot", "snapshot_port_missing"),
                ("activation", "activation_port_missing"), ("helper", "helper_port_missing"),
                ("ledger", "admission_ledger_port_missing"))
MAX_PROMPT_BYTES = HELPER_MAX_PROMPT_BYTES  # before any port: the helper refuses more only after our reservation
MAX_INTENT_BYTES = 16 * 1024


class ClockPort(Protocol):
    def now(self) -> Any: ...  # exactly an aware datetime; anything else reads as time_unknown (aware_utc)


class SnapshotPort(Protocol):
    """{"readonly": True, "head", "tree", "observed_utc"} of the pinned read-only source.

    observed_utc is when the port itself acquired the fact; a cached value keeps its own time."""

    def observe(self) -> dict: ...


class ActivationPort(Protocol):
    """F0 for one feature, with the operator-trusted pins (policy digest, revocation floor) bound inside.

    Returns (Decision, policy, evaluated_utc). The Decision carries no head/tree, so the port MUST
    evaluate the signature at expected_head/expected_tree (it is the only binding), and evaluated_utc
    is when it evaluated, never merely when it returned a cached Decision. evaluated_utc must carry the
    evaluation's own FULL precision (microseconds). It is never a fabricated current stamp and never goes
    with a reused cached Decision. A whole-second stamp is refused at the recheck whenever it falls in the
    sample's own second (RCO1 e932 SF1)."""

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


def _helper_inputs_ok(intent: dict, prompt: str) -> bool:
    """The helper's own input rules (790a7b08 consult: a bounded task ID and a non-blank prompt), checked BEFORE
    any port or reservation (RCO1 e932 SF2). prepare is not the only ingress, and the helper refuses these only
    after the broker's ledger has reserved, and never refunds, the shared hour."""
    task_id = intent.get("task_id")
    return type(task_id) is str and TASK_RE.fullmatch(task_id) is not None and bool(prompt.strip())


def _copy(value: dict) -> dict:
    """A plain-JSON copy handed to a port, so a port can never change the broker's own admission or outcome."""
    return json.loads(json.dumps(value))


class GrokBroker:
    """Dormant. At most one helper call per consult(), and only after admission, a won ledger reservation
    and a passed recheck as the last step before the call."""

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
        """None if the admission still holds at the last check before the call; else the refusal (RCO1 e855 S1).

        The clock is sampled BEFORE F0 is evaluated, and the F0 used must be evaluated at or after that sample
        at FULL precision (RCO1 e932 SF1; no whole-second floor): a cached Decision from before this recheck
        began refuses as f0_cached, so a revocation cannot hide behind the 60 s freshness window or inside the
        sample's own second. A whole-second stamp in the sample's own second cannot prove it came after the
        sample and refuses as f0_time_precision_unknown. The clock never runs backwards (from the admission's now
        to the sample to the final read). After admit re-runs at the final read, the revocation version must
        not regress and the signed policy's expires_utc must still be ahead."""
        def stop(verdict: str, reason: str) -> dict:
            return _result(verdict, ["recheck_failed", reason], admission=admission)

        sample = aware_utc(self.clock.now())
        if sample is None:
            return stop(BLOCKED, "time_unknown")
        admitted_now = parse_utc(evidence["now_utc"])
        if admitted_now is None or sample < admitted_now:
            return stop(BLOCKED, "clock_regressed")
        f0, policy = self._evaluate(evidence["f0"]["head"], evidence["f0"]["tree"])
        if f0 is None:
            return stop(BLOCKED, "f0_unknown")
        evaluated = parse_utc(f0["evaluated_utc"])
        if evaluated < sample:  # full precision (RCO1 e932 SF1): evaluated before this recheck began
            # A whole-second stamp in the sample's own second cannot show it came after the sample.
            unknown = evaluated.microsecond == 0 and evaluated == sample.replace(microsecond=0)
            return stop(REFUSE, "f0_time_precision_unknown" if unknown else "f0_cached")
        now = aware_utc(self.clock.now())
        if now is None:
            return stop(BLOCKED, "time_unknown")
        if now < sample:
            return stop(BLOCKED, "clock_regressed")
        again = admit(intent, dict(evidence, f0=f0, policy=policy, now_utc=now.isoformat()))
        if again["verdict"] != ADMIT or again["intent_sha256"] != admission["intent_sha256"] \
                or again["policy_sha256"] != admission["policy_sha256"]:
            return _result(again["verdict"] if again["verdict"] != ADMIT else BLOCKED,
                           ["recheck_failed"] + (again["reasons"] if again["verdict"] != ADMIT else
                                                 ["policy_changed"]), admission=admission)
        # Both Decisions passed admit, so both are exactly Decision objects here.
        first, latest = evidence["f0"]["decision"].revocation_version, f0["decision"].revocation_version
        if type(first) is not int or type(latest) is not int:
            return stop(BLOCKED, "revocation_unknown")
        if latest < first:
            return stop(REFUSE, "revocation_regressed")
        expires = parse_utc(policy.get("expires_utc"))
        if expires is None:
            return stop(BLOCKED, "policy_expiry_unknown")
        if not now < expires:
            return stop(REFUSE, "policy_expired")
        return None

    def _finish(self, admission: dict, outcome: dict) -> dict:
        try:
            self.ledger.finish(_copy(admission), _copy(outcome))
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
        if not _helper_inputs_ok(intent, prompt):
            return _result(REFUSE, ["helper_inputs_invalid"])  # the helper would refuse only after our reservation
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
            won = self.ledger.reserve(_copy(admission))  # a copy: the allowlist bind_answer trusts stays ours
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
