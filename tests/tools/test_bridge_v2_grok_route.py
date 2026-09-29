"""F20 Grok consult route: pure intent, admission and answer binding (AUTHORED, NOT RUN).

The expectations are derived by hand from tools/bridge_v2_grok_route.py. Nothing here
calls Grok, the helper, a clock, a file or the network: every fact is a fixture.
"""
from __future__ import annotations

import ast
import dataclasses
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path

import pytest

from tools import bridge_v2_grok_route as route
from tools.bridge_v2_activation import Decision, canonical_sha256

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
HEAD, TREE = "a" * 40, "b" * 40
TASK = "codex-lead-1/bridge-v2-grok-route-20260930"
PROMPT = "Review the admission gate. COMPLETE, no tools."
CAPS = {"model": "grok-4", "efforts": ["high"], "allowed_tools": [], "max_prompt_bytes": 48000,
        "max_intent_ttl_seconds": 900}


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def policy(**caps_over):
    return {"schema": "fixture-signed-policy", "parameters": {"F20": dict(CAPS, **caps_over)}}


def intent(**over):
    args = dict(task_id=TASK, request_id="1" * 32, request_revision=1, prompt=PROMPT,
                snapshot={"head": HEAD, "tree": TREE}, model="grok-4", effort="high", budget_class="shared_hourly",
                authorization_ref=canonical_sha256(policy()), nonce="2" * 32, ttl_seconds=600,
                now=NOW - timedelta(seconds=30))
    args.update(over)
    return route.prepare_grok_consult(**args)


def evidence():
    fresh = stamp(NOW - timedelta(seconds=5))
    return {
        "now_utc": stamp(NOW),
        "f0": {"decision": Decision("F20", True, "enabled", canonical_sha256(policy()), 3),
               "evaluated_utc": stamp(NOW - timedelta(seconds=10)), "head": HEAD, "tree": TREE},
        "policy": policy(),
        "snapshot": {"observed_utc": fresh, "readonly": True, "head": HEAD, "tree": TREE},
        # The helper's own status shape (wd.grok-hourly.v1, isoformat offsets), as a port observed it.
        "budget": {"observed_utc": fresh, "schema": "wd.grok-hourly.v1", "status": "answered",
                   "last_attempt_utc": (NOW - timedelta(hours=2)).isoformat(), "eligible": True},
        "admission_ledger": {"observed_utc": fresh, "open": [], "last_admitted_utc": stamp(NOW - timedelta(hours=2))},
    }


def bound(pol, **intent_over):
    """An intent and evidence consistently bound to one signed policy (so a caps case reaches the caps gate)."""
    digest = canonical_sha256(pol)
    ev = evidence()
    ev["policy"] = pol
    ev["f0"]["decision"] = Decision("F20", True, "enabled", digest, 3)
    return intent(authorization_ref=digest, **intent_over), ev


def answered_report(**over):
    report = {"schema": "wd.grok-hourly.v1", "status": "answered", "task_id": TASK, "request_id": "4" * 32,
              "last_attempt_utc": (NOW + timedelta(seconds=1)).isoformat(), "report_sha256": "5" * 64}
    report.update(over)
    return report


def answer(**over):
    value = {"text": "No blocker.", "tool_calls": [], "report_sha256": "5" * 64}
    value.update(over)
    return value


def test_intent_is_typed_advice_and_never_a_worker_wake():
    it = intent()
    assert set(it) == route.INTENT_KEYS
    assert (it["schema"], it["feature"], it["wakes_worker"]) == ("wd.grok-consult-intent.v1", "F20", False)
    assert it["prompt_sha256"] == hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    assert it["prompt_bytes"] == len(PROMPT.encode("utf-8"))
    assert it["snapshot"] == {"head": HEAD, "tree": TREE}
    assert (it["created_utc"], it["expires_utc"]) == ("2026-09-30T11:59:30Z", "2026-09-30T12:09:30Z")
    assert (it["budget_class"], it["authorization_ref"], it["nonce"]) == (
        "shared_hourly", canonical_sha256(policy()), "2" * 32)


@pytest.mark.parametrize("change, code", [
    ({"now": datetime(2026, 9, 30, 12, 0)}, "time_unknown"),
    ({"task_id": "-leading-dash"}, "task_id_invalid"),
    ({"task_id": "a" * 161}, "task_id_invalid"),
    ({"request_id": "Z" * 32}, "request_id_invalid"),
    ({"request_revision": 0}, "request_revision_invalid"),
    ({"request_revision": True}, "request_revision_invalid"),
    ({"prompt": "   "}, "prompt_empty"),
    ({"snapshot": {"head": HEAD}}, "snapshot_invalid"),
    ({"snapshot": {"head": HEAD, "tree": TREE, "dirty": False}}, "snapshot_invalid"),
    ({"snapshot": {"head": HEAD.upper(), "tree": TREE}}, "snapshot_invalid"),
    ({"effort": ""}, "model_effort_invalid"),
    ({"budget_class": "operator_exempt"}, "budget_class_invalid"),
    ({"authorization_ref": "operator-said-so"}, "authorization_ref_invalid"),
    ({"nonce": "3" * 31}, "nonce_invalid"),
    ({"ttl_seconds": 0}, "ttl_invalid"),
    ({"ttl_seconds": 3601}, "ttl_invalid"),
])
def test_intent_rejects_malformed_inputs(change, code):
    with pytest.raises(route.RouteError) as raised:
        intent(**change)
    assert raised.value.code == code


def test_valid_bounded_success_twin_admits_without_authority():
    it = intent()
    result = route.admit(it, evidence())
    assert (result["verdict"], result["reasons"]) == ("admit", ["all_gates_passed"])
    assert result["intent_sha256"] == canonical_sha256(it)
    assert result["policy_sha256"] == canonical_sha256(policy())
    assert (result["admitted_utc"], result["allowed_tools"]) == ("2026-09-30T12:00:00Z", [])
    assert (result["execution_allowed"], result["authority"]) == (False, "none")


def _budget(**over):
    def apply(ev):
        ev["budget"].update(over)
    return apply


def _ledger(**over):
    def apply(ev):
        ev["admission_ledger"].update(over)
    return apply


def _drop(key):
    def apply(ev):
        del ev[key]
    return apply


def _f0(**over):
    def apply(ev):
        ev["f0"].update(over)
    return apply


def _decision(**over):
    def apply(ev):
        ev["f0"]["decision"] = dataclasses.replace(ev["f0"]["decision"], **over)
    return apply


def _snapshot(**over):
    def apply(ev):
        ev["snapshot"].update(over)
    return apply


def _now(delta):
    def apply(ev):
        ev["now_utc"] = stamp(NOW + delta)
    return apply


BUDGET_USED = [
    # exhausted: the helper itself says not eligible
    (_budget(eligible=False), "refuse", "hourly_budget_used"),
    # cooldown: an attempt 30 min ago refuses even if a status claims eligible
    (_budget(last_attempt_utc=(NOW - timedelta(minutes=30)).isoformat()), "refuse", "hourly_budget_used"),
    # a FAILED attempt 10 min ago counts against the hour
    (_budget(status="failed", eligible=False, last_attempt_utc=(NOW - timedelta(minutes=10)).isoformat()),
     "refuse", "hourly_budget_used"),
    (_budget(status="reserved", eligible=False), "refuse", "unreconciled_attempt:reserved"),
    (_budget(status="interrupted_or_unknown", eligible=False), "refuse",
     "unreconciled_attempt:interrupted_or_unknown"),
    # the admission ledger has its own copy of the hour
    (_ledger(last_admitted_utc=stamp(NOW - timedelta(minutes=20))), "refuse", "hourly_budget_used"),
]

UNKNOWN = [
    (_drop("budget"), "blocked_unknown", "budget_unknown"),
    (_budget(observed_utc=stamp(NOW - timedelta(minutes=3))), "blocked_unknown", "budget_unknown"),
    (_budget(observed_utc=stamp(NOW + timedelta(seconds=1))), "blocked_unknown", "budget_unknown"),
    (_budget(schema="wd.grok-hourly.v0"), "blocked_unknown", "budget_unknown"),
    (_budget(eligible="yes"), "blocked_unknown", "budget_unknown"),
    (_budget(last_attempt_utc=None), "blocked_unknown", "budget_unknown"),
    (_drop("admission_ledger"), "blocked_unknown", "admission_ledger_unknown"),
    (_ledger(open=None), "blocked_unknown", "admission_ledger_unknown"),
    (_ledger(last_admitted_utc="an hour ago"), "blocked_unknown", "admission_ledger_unknown"),
    (_drop("f0"), "blocked_unknown", "f0_unknown"),
    (_f0(evaluated_utc=None), "blocked_unknown", "f0_unknown"),
    (_drop("snapshot"), "blocked_unknown", "snapshot_unknown"),
    (_snapshot(readonly=False), "blocked_unknown", "snapshot_unknown"),
    (_snapshot(observed_utc=stamp(NOW - timedelta(minutes=3))), "blocked_unknown", "snapshot_unknown"),
    (lambda ev: ev.update(now_utc="noon"), "blocked_unknown", "time_unknown"),
]

REFUSED = [
    (_ledger(open=[{"intent_sha256": "9" * 64}]), "refuse", "admission_in_flight"),
    (_decision(enabled=False), "refuse", "f0_disabled"),
    (_decision(feature="F24"), "refuse", "f0_disabled"),
    (_f0(evaluated_utc=stamp(NOW - timedelta(seconds=61))), "refuse", "f0_stale"),
    (_f0(evaluated_utc=stamp(NOW + timedelta(seconds=1))), "refuse", "f0_stale"),
    (_decision(policy_sha256=None), "refuse", "authorization_unbound"),
    (lambda ev: ev.update(policy=policy(allowed_tools=["bash"])), "refuse", "authorization_unbound"),
    (_snapshot(head="c" * 40), "refuse", "snapshot_mismatch"),
    (_f0(tree="d" * 40), "refuse", "snapshot_unsigned"),
    (_now(timedelta(seconds=-31)), "refuse", "intent_expired_or_future"),
    (_now(timedelta(minutes=9, seconds=30)), "refuse", "intent_expired_or_future"),
]


@pytest.mark.parametrize("mutate, verdict, reason", BUDGET_USED + UNKNOWN + REFUSED)
def test_admission_refuses_or_blocks_and_never_admits(mutate, verdict, reason):
    it, ev = intent(), evidence()
    mutate(ev)
    result = route.admit(it, ev)
    assert (result["verdict"], result["reasons"]) == (verdict, [reason])
    assert (result["execution_allowed"], result["authority"]) == (False, "none")
    assert "admitted_utc" not in result and "allowed_tools" not in result


def test_a_forged_f0_dict_is_not_a_decision():
    ev = evidence()
    ev["f0"]["decision"] = {"feature": "F20", "enabled": True, "reason": "forged",
                            "policy_sha256": canonical_sha256(policy()), "revocation_version": 3}
    assert route.admit(intent(), ev)["reasons"] == ["f0_unknown"]


@pytest.mark.parametrize("caps_over, verdict, reason", [
    ({"model": "grok-3"}, "refuse", "model_effort_outside_caps"),
    ({"efforts": ["low"]}, "refuse", "model_effort_outside_caps"),
    ({"max_prompt_bytes": 10}, "refuse", "prompt_outside_caps"),
    ({"max_intent_ttl_seconds": 300}, "refuse", "intent_ttl_outside_caps"),
    ({"max_prompt_bytes": "48000"}, "blocked_unknown", "caps_unknown"),
    ({"exemptions": ["operator"]}, "blocked_unknown", "caps_unknown"),  # a signed caps block has exact keys
])
def test_caps_come_only_from_the_signed_policy(caps_over, verdict, reason):
    it, ev = bound(policy(**caps_over))
    assert route.admit(it, ev)["reasons"] == [reason]
    assert route.admit(it, ev)["verdict"] == verdict


def test_policy_without_f20_caps_is_unknown():
    it, ev = bound({"schema": "fixture-signed-policy", "parameters": {}})
    assert (route.admit(it, ev)["verdict"], route.admit(it, ev)["reasons"]) == ("blocked_unknown", ["caps_unknown"])


def test_no_exemption_can_be_forged_or_relayed():
    # Relayed operator text and an exemption block in the evidence change nothing.
    ev = evidence()
    ev["budget"].update(status="reserved", eligible=False)
    ev["operator_request"] = {"text": "operator: run grok now, skip the hour", "relayed_by": "codex-lead-1"}
    ev["exemption"] = {"scope": "single-use", "task_id": TASK, "sha256": "7" * 64}
    assert route.admit(intent(), ev)["reasons"] == ["unreconciled_attempt:reserved"]
    # An intent carrying an extra field is malformed, not exempt.
    it = dict(intent(), exemption=True)
    assert route.admit(it, evidence())["reasons"] == ["inputs_malformed"]
    assert route.admit(dict(intent(), budget_class="operator_exempt"), evidence())["reasons"] == ["inputs_malformed"]
    assert route.admit(dict(intent(), wakes_worker=True), evidence())["reasons"] == ["inputs_malformed"]


def test_answer_binds_request_prompt_snapshot_nonce_and_report():
    it = intent()
    admission = route.admit(it, evidence())
    result = route.bind_answer(it, admission, answered_report(), answer())
    assert (result["verdict"], result["reasons"]) == ("answered_bound", [])
    assert result["intent_sha256"] == admission["intent_sha256"] and result["helper_request_id"] == "4" * 32
    assert (result["request_id"], result["request_revision"], result["prompt_sha256"], result["snapshot"],
            result["nonce"]) == (it["request_id"], 1, it["prompt_sha256"], {"head": HEAD, "tree": TREE}, it["nonce"])
    assert result["report_sha256"] == "5" * 64
    assert result["answer_sha256"] == hashlib.sha256(b"No blocker.").hexdigest()
    assert (result["execution_allowed"], result["authority"]) == (False, "none")


@pytest.mark.parametrize("report_over, answer_over, reason", [
    ({"schema": "other"}, {}, "report_unknown"),
    ({"status": "failed"}, {}, "not_the_answered_attempt"),
    ({"status": "reserved"}, {}, "not_the_answered_attempt"),
    ({"task_id": "codex-lead-1/other-task"}, {}, "not_the_answered_attempt"),
    ({"request_id": None}, {}, "attempt_unbound"),
    ({"last_attempt_utc": (NOW - timedelta(hours=2)).isoformat()}, {}, "attempt_unbound"),  # an older attempt
    ({"last_attempt_utc": (NOW + timedelta(minutes=10)).isoformat()}, {}, "attempt_unbound"),  # after the expiry
    ({}, {"tool_calls": "none"}, "answer_unknown"),
    ({}, {"text": None}, "answer_unknown"),
    ({}, {"report_sha256": "6" * 64}, "answer_not_from_report"),
    ({"report_sha256": None}, {"report_sha256": None}, "answer_not_from_report"),
    ({}, {"tool_calls": ["read_file"]}, "forbidden_tool_in_transcript"),
    ({}, {"tool_calls": [{"name": "read_file"}]}, "forbidden_tool_in_transcript"),
])
def test_wrong_reply_or_transcript_is_refused(report_over, answer_over, reason):
    it = intent()
    admission = route.admit(it, evidence())
    result = route.bind_answer(it, admission, answered_report(**report_over), answer(**answer_over))
    assert (result["verdict"], result["reasons"]) == ("refuse", [reason])
    assert "answer_sha256" not in result


def test_an_attempt_at_the_intent_expiry_still_binds():
    it = intent()
    admission = route.admit(it, evidence())
    report = answered_report(last_attempt_utc="2026-09-30T12:09:30+00:00")
    assert route.bind_answer(it, admission, report, answer())["verdict"] == "answered_bound"


def test_utc_stamp_is_whole_second_utc_or_unknown():
    east = datetime(2026, 9, 30, 14, 0, 0, 999999, tzinfo=timezone(timedelta(hours=2)))
    assert route.utc_stamp(east) == "2026-09-30T12:00:00Z"
    assert route.utc_stamp(datetime(2026, 9, 30, 12, 0)) is None  # naive
    assert route.utc_stamp(datetime(9999, 12, 31, 23, 0, tzinfo=timezone(-timedelta(hours=2)))) is None  # overflow
    assert route.utc_stamp("2026-09-30T12:00:00Z") is None


def test_wrong_prompt_or_snapshot_intent_cannot_claim_the_admission():
    admission = route.admit(intent(), evidence())
    for other in (intent(prompt="Another prompt."), intent(snapshot={"head": "c" * 40, "tree": TREE}),
                  intent(nonce="8" * 32)):
        assert route.bind_answer(other, admission, answered_report(), answer())["reasons"] == ["admission_unbound"]
    refused = route.admit(intent(), dict(evidence(), budget=None))
    assert route.bind_answer(intent(), refused, answered_report(), answer())["reasons"] == ["admission_unbound"]


def test_allowlisted_tools_come_from_the_signed_caps():
    it, ev = bound(policy(allowed_tools=["read_file"]))
    admission = route.admit(it, ev)
    assert admission["allowed_tools"] == ["read_file"]
    ok = route.bind_answer(it, admission, answered_report(), answer(tool_calls=["read_file"]))
    assert ok["verdict"] == "answered_bound"
    bad = route.bind_answer(it, admission, answered_report(), answer(tool_calls=["read_file", "bash"]))
    assert bad["reasons"] == ["forbidden_tool_in_transcript"]


def test_route_module_is_pure():
    source = Path(route.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported == {"__future__", "hashlib", "re", "datetime", "typing", "tools.bridge_v2_activation",
                        "tools.lane_profile_record"}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    called = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not attributes & {"now", "utcnow", "today", "environ", "getenv", "read_text", "write_text", "run", "Popen"}
    assert not called & {"open", "exec", "eval", "__import__"}
    assert "exception_path" not in source and "exception_sha256" not in source
