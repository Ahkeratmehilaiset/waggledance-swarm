# SPDX-License-Identifier: BUSL-1.1
"""Plan-only Grok dispatcher twins (no call, no process, no network).

The CORPUS and ROUTE_CASES below are RCO1's executable spec (sandbox/mind_rco1/grok_dispatch_spec.py, fbc24158),
copied verbatim in content so this branch tests its own decide() and route() against them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.mind_rco2 import grok_dispatch_plan as gdp  # noqa: E402

MARK = "PRIV" + "MARK"  # RCO1's stand-in marker, built at run time


def spec_guard(text):
    return MARK.lower() not in text.lower()


OK_STATUS = {"local_availability": "available", "status": "answered", "eligible": True, "model_breaker": {"state": "closed"}}
PROMPT = "One narrow question about the hybrid lens: which single change reduces wrong facts most?"
ASK = {"requester": "claude-rco-2", "prompt": PROMPT, "request_id": "r1", "patch_id": None, "operator": False}
ROT = {"next": "claude-rco-2"}


def case(name, expect, ask=None, status=None, rotation=None, seen=None):
    return (name, expect, {**ASK, **(ask or {})}, {**OK_STATUS, **(status or {})}, {**ROT, **(rotation or {})}, seen or set())


CORPUS = [
    case("happy path", ("DISPATCH", "claude-rco-2")),
    case("unknown requester", ("REFUSE", "ask_malformed_or_unknown_requester"), ask={"requester": "mallory"}),
    case("prompt over 12 KB", ("REFUSE", "prompt_too_large"), ask={"prompt": "x" * (12 * 1024 + 1)}),
    case("empty prompt", ("REFUSE", "prompt_empty"), ask={"prompt": "   "}),
    case("github token shape", ("REFUSE", "secret_shape"), ask={"prompt": PROMPT + " ghp_" + "A" * 36}),
    case("aws key shape", ("REFUSE", "secret_shape"), ask={"prompt": PROMPT + " AKIA" + "B" * 16}),
    case("password assignment", ("REFUSE", "secret_shape"), ask={"prompt": PROMPT + " Password: hunter2"}),
    case("private marker plain", ("REFUSE", "privacy_guard"), ask={"prompt": PROMPT + " " + MARK}),
    case("private marker other case", ("REFUSE", "privacy_guard"), ask={"prompt": PROMPT + " " + MARK.lower()}),
    case("private marker json-escaped", ("REFUSE", "privacy_guard"),
         ask={"prompt": PROMPT + ' {"note": "\\u0050' + MARK[1:] + '"}'}),
    case("raw bridge rows pasted", ("REFUSE", "raw_bridge_rows_in_prompt"),
         ask={"prompt": PROMPT + ' {"ts_utc": "2026-10-13T10:00:00Z", "agent": "fable-5"}'}),
    case("second consultation for the same patch", ("REFUSE", "already_consulted_for_this_patch"),
         ask={"patch_id": "419523fc"}, seen={"419523fc"}),
    case("grok unavailable locally", ("QUEUE", "grok_unavailable"), status={"local_availability": "missing"}),
    case("single flight busy", ("QUEUE", "single_flight_busy"), status={"status": "reserved"}),
    case("model breaker open", ("QUEUE", "model_breaker_open"), status={"model_breaker": {"state": "open"}}),
    case("hourly budget not reset", ("QUEUE", "budget_wait"), status={"eligible": False}),
    case("not this lane's rotation turn", ("QUEUE", "not_this_lane_rotation_turn"), rotation={"next": "fable-5"}),
    case("operator ask skips the rotation", ("DISPATCH", "claude-rco-2"), ask={"operator": True}, rotation={"next": "fable-5"}),
]

ROUTE_CASES = [
    ("answer goes to its requester", {"request_id": "r1", "prompt_sha256": gdp.prompt_digest(PROMPT)}, "claude-rco-2"),
    ("same request id but another prompt is not routed", {"request_id": "r1", "prompt_sha256": "0" * 64}, None),
    ("unknown request id is not routed", {"request_id": "r9", "prompt_sha256": gdp.prompt_digest(PROMPT)}, None),
]


@pytest.mark.parametrize("name,expect,ask,status,rotation,seen", CORPUS, ids=[c[0] for c in CORPUS])
def test_rco1_dispatch_spec(name, expect, ask, status, rotation, seen):
    assert gdp.decide(ask, status, rotation, seen, spec_guard) == expect


@pytest.mark.parametrize("name,answer,expect", ROUTE_CASES, ids=[c[0] for c in ROUTE_CASES])
def test_rco1_route_spec(name, answer, expect):
    assert gdp.route(answer, [ASK]) == expect


def test_the_real_decoded_guard_also_refuses_an_escaped_marker():
    marker = "PRIVATE" + "_MARKER"
    escaped = PROMPT + ' {"note": "\\u0050' + marker[1:] + '"}'
    assert gdp.decide({**ASK, "prompt": escaped}, OK_STATUS, ROT, set(), gdp.workspace_guard) == ("REFUSE", "privacy_guard")


NOW = "2026-10-13T12:00:00Z"


def answered(requester, at, digest=None):
    return {"consultation_id": f"c-{requester}-{at}", "requested_by": requester, "task": f"{requester}/t",
            "started_utc": at, "start_ref": "a" * 64, "prompt_sha256": digest,
            "end": {"status": "answered", "utc": at, "ref": "b" * 64, "seconds": 100, "report_sha256": None}}


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_plan_starts_one_by_rotation_and_queues_the_rest(tmp_path):
    fold = {"as_of_utc": NOW, "grok": [answered("fable-5", "2026-10-13T11:00:00Z"),
                                        answered("codex-tools-1", "2026-10-13T09:00:00Z")]}
    candidates = [{"requested_by": "fable-5", "task_id": "f/t", "prompt_path": write(tmp_path, "a.md", "question one")},
                  {"requested_by": "codex-tools-1", "task_id": "t/t", "prompt_path": write(tmp_path, "b.md", "question two")}]
    result = gdp.plan(fold, OK_STATUS, candidates)
    assert result["rotation_next"] == "codex-tools-1" and result["calls_made"] == 0
    assert result["may_start"]["requested_by"] == "codex-tools-1"
    assert [(d["requested_by"], d["verdict"], d["reason"]) for d in result["decisions"]] == [
        ("fable-5", "QUEUE", "not_this_lane_rotation_turn"), ("codex-tools-1", "DISPATCH", "codex-tools-1")]


def test_plan_refuses_a_prompt_already_answered_and_a_missing_prompt(tmp_path):
    same = "a question already asked"
    fold = {"as_of_utc": NOW, "grok": [answered("fable-5", "2026-10-13T08:00:00Z", gdp.prompt_digest(same))]}
    result = gdp.plan(fold, OK_STATUS, [{"requested_by": "fable-5", "task_id": "1", "prompt_path": write(tmp_path, "d.md", same)},
                                        {"requested_by": "claude-rco-1", "task_id": "2", "prompt_path": None}])
    assert result["may_start"] is None
    assert [d["reason"] for d in result["decisions"]] == ["already_consulted_for_this_patch", "prompt_missing_or_unreadable"]


def test_plan_with_a_real_status_shape_open_breaker_queues(tmp_path):
    status = dict(OK_STATUS, model_breaker={"state": "open"})
    result = gdp.plan({"as_of_utc": NOW, "grok": []}, status,
                      [{"requested_by": "claude-rco-2", "task_id": "x", "prompt_path": write(tmp_path, "q.md", "one question")}])
    assert result["may_start"] is None and result["decisions"][0]["reason"] == "model_breaker_open"
