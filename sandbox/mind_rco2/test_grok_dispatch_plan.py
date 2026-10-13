# SPDX-License-Identifier: BUSL-1.1
"""Plan-only Grok dispatcher twins (no call, no process, no network)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from sandbox.mind_rco2 import grok_dispatch_plan as gdp  # noqa: E402

NOW = "2026-10-13T12:00:00Z"
IDLE = {"status": "answered", "eligible": True, "next_eligible_utc": None, "model_holds": [], "model_breaker": {}}


def fold(consultations=()):
    return {"as_of_utc": NOW, "grok": list(consultations), "asks": [], "replies": {}, "ends": {}}


def prompt(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def answered(requester, at, digest=None):
    return {"consultation_id": f"c-{requester}-{at}", "requested_by": requester, "task": f"{requester}/t",
            "started_utc": at, "start_ref": "a" * 64, "prompt_sha256": digest,
            "end": {"status": "answered", "utc": at, "ref": "b" * 64, "seconds": 100, "report_sha256": None}}


def test_one_item_may_start_and_rotation_picks_the_oldest_answer(tmp_path):
    candidates = [{"requested_by": "fable-5", "task_id": "f/t1", "prompt_path": prompt(tmp_path, "a.md", "one narrow question")},
                  {"requested_by": "codex-tools-1", "task_id": "t/t2", "prompt_path": prompt(tmp_path, "b.md", "another question")}]
    history = [answered("fable-5", "2026-10-13T11:00:00Z"), answered("codex-tools-1", "2026-10-13T09:00:00Z")]
    result = gdp.plan(fold(history), IDLE, candidates)
    assert result["may_start"]["requested_by"] == "codex-tools-1" and result["calls_made"] == 0
    assert [w["reasons"] for w in result["waiting"]] == [["single_flight_one_per_plan"]]


def test_a_busy_helper_lets_nothing_start(tmp_path):
    busy = dict(IDLE, status="reserved")
    result = gdp.plan(fold(), busy, [{"requested_by": "fable-5", "task_id": "f/t", "prompt_path": prompt(tmp_path, "a.md", "q")}])
    assert result["may_start"] is None and result["waiting"][0]["reasons"] == ["helper_reserved"]


def test_size_guard_missing_and_duplicate_prompts_are_refused(tmp_path):
    marker = "PRIVATE" + "_MARKER"  # built at run time so the file itself carries no guarded word
    same = "a question already asked"
    candidates = [{"requested_by": "a", "task_id": "1", "prompt_path": prompt(tmp_path, "big.md", "x" * (12 * 1024 + 1))},
                  {"requested_by": "b", "task_id": "2", "prompt_path": prompt(tmp_path, "leak.md", f"see {marker} here")},
                  {"requested_by": "c", "task_id": "3", "prompt_path": None},
                  {"requested_by": "d", "task_id": "4", "prompt_path": prompt(tmp_path, "dup.md", same)}]
    history = [answered("d", "2026-10-13T08:00:00Z", hashlib.sha256(same.encode()).hexdigest())]
    result = gdp.plan(fold(history), IDLE, candidates)
    reasons = {w["requested_by"]: w["reasons"] for w in result["waiting"]}
    assert result["may_start"] is None
    assert reasons == {"a": ["prompt_over_12kb"], "b": ["prompt_guard_hit"], "c": ["prompt_missing"],
                       "d": ["same_prompt_already_answered"]}
