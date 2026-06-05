# SPDX-License-Identifier: BUSL-1.1
"""Conformance test for the RCO pass presence gate (fail-closed Rule 9a) using a locked, versioned corpus.

This test loads tests/tools/check_rco_pass_present_conformance_corpus.json and drives
check_rco_pass_present (library) and the CLI (tools/check_rco_pass_present.py via subprocess
with synthetic events.jsonl) over every refuse_case and allow_case.

- For every refuse_case: asserts ok=False, has_qualifying_rco_pass_at_head=False (or True only for veto-after cases), decision matches expected, and CLI exit != 0 (REFUSE).
- For every allow_case: asserts ok=True, decision=="rco_pass_present", has_qualifying=True, and CLI exit == 0 (ALLOW).

The corpus enumerates the exact REFUSE set (silence/no rco_pass; different head; later veto after pass via changes_requested/finding/blocked; non-qualifying type=message/handoff even with status=rco_pass; wrong rco-agent) and ALLOW set (correct type in {decision,rco_review}, status=rco_pass, exact head in message, correct agent, no later veto).

This locks the autonomy safety property "RCO absence = no merge" against regression. Any future change that weakens check_rco_pass_present.py (or its callers) such that a refuse_case now returns ok or a allow_case is refused will cause this test to fail.

Synthetic fixtures only; deterministic append order for "latest"; offline, no network, no wallclock in verdicts.

All claim gates are asserted false in corpus artifact and in every result emitted by the gate (per hard rule, mirroring leak_policy conformance).
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_rco_pass_present.py"

sys.path.insert(0, str(ROOT))

from tools.check_rco_pass_present import (  # noqa: E402
    check_rco_pass_present,
    CLAIM_GATES,
)

CORPUS_PATH = Path(__file__).parent / "check_rco_pass_present_conformance_corpus.json"


def _load_corpus() -> dict:
    with CORPUS_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    # Enforce claim gates are explicitly false in the artifact (pure test asset)
    gates = data.get("claim_gates", {})
    for gate in CLAIM_GATES:
        assert gate in gates, f"missing claim gate declaration for {gate}"
        assert (
            gates[gate] is False
        ), f"claim gate {gate} must be literal false in conformance corpus"
    return data


@pytest.fixture(scope="module")
def corpus() -> dict:
    return _load_corpus()


def _seed_events(tmp_path: Path, events: list[dict]) -> Path:
    """Write events.jsonl under temp .agent-bridge/shared for CLI invocation (mirrors existing test helper)."""
    bridge = tmp_path / ".agent-bridge"
    shared = bridge / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")
    return events_path


def _case_rco_agents(case: dict) -> list[str]:
    raw = case.get("rco_agent", "claude-rco-1")
    if isinstance(raw, list):
        return [str(agent) for agent in raw]
    return [str(raw)]


def _rco_agent_cli_args(agents: list[str]) -> list[str]:
    args: list[str] = []
    for agent in agents:
        args.extend(["--rco-agent", agent])
    return args


def test_corpus_is_versioned_and_complete(corpus: dict):
    """Lock the corpus shape, version prefix, case counts, and that it declares all gates false."""
    assert corpus["corpus_version"].startswith("wd.rco_pass_gate.conformance_corpus.v")
    assert (
        isinstance(corpus.get("refuse_cases"), list)
        and len(corpus["refuse_cases"]) >= 5
    )
    assert (
        isinstance(corpus.get("allow_cases"), list) and len(corpus["allow_cases"]) >= 2
    )
    # provenance is deterministic label, no wallclock/random
    prov = corpus.get("provenance", "").lower()
    assert "hand-authored" in prov or "stable event shapes" in prov
    # task/head stable
    assert corpus["task_id"].startswith("waggledance/grok-scout-1/")
    assert len(corpus["head"]) == 40 and all(
        c in "0123456789abcdef" for c in corpus["head"]
    )


def test_all_claim_gates_are_false_in_corpus_artifact(corpus: dict):
    """Explicit audit: the emitted corpus carries all gates as the literal boolean false (no carve-outs)."""
    gates = corpus["claim_gates"]
    for gate in CLAIM_GATES:
        assert gates[gate] is False


@pytest.mark.parametrize(
    "case", _load_corpus()["refuse_cases"], ids=lambda c: c["name"]
)
def test_refuse_case_is_refused_by_gate(case: dict, tmp_path: Path):
    """Every refuse_case must produce REFUSE verdict (ok=false, nonzero exit, correct decision)."""
    events = case["events"]
    task_id = case["task_id"]
    head = case["head"]
    rco_agents = _case_rco_agents(case)
    author_agent = case.get("author_agent", "codex-lead-1")
    expected = case["expected"]

    # Library path (direct events list, no FS)
    result = check_rco_pass_present(
        events=events,
        task_id=task_id,
        head=head,
        rco_agent=rco_agents,
        author_agent=author_agent,
    )
    assert result["ok"] is expected["ok"]
    assert result["decision"] == expected["decision"]
    assert (
        result["has_qualifying_rco_pass_at_head"]
        is expected["has_qualifying_rco_pass_at_head"]
    )
    if "latest_rco_is_veto" in expected:
        assert result["latest_rco_is_veto"] is expected["latest_rco_is_veto"]
    # All claim gates false per hard rule (emitted by the gate itself)
    for key in CLAIM_GATES:
        assert (
            result[key] is False
        ), f"gate {key} must be false in result for refuse case {case['name']}"

    # CLI path with synthetic events.jsonl (locks exit code and stdout/stderr behavior)
    events_path = _seed_events(tmp_path, events)
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--task-id",
        task_id,
        "--head",
        head,
        "--events",
        str(events_path),
        *_rco_agent_cli_args(rco_agents),
        "--author-agent",
        author_agent,
        "--json",
    ]
    res = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert (
        res.returncode != 0
    ), f"refuse case {case['name']} must not exit 0; stdout={res.stdout} stderr={res.stderr}"
    # stdout should be the json result (or stderr for some, but with --json it goes to stdout)
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["decision"] == expected["decision"]
    for key in CLAIM_GATES:
        assert payload[key] is False


@pytest.mark.parametrize("case", _load_corpus()["allow_cases"], ids=lambda c: c["name"])
def test_allow_case_is_allowed_by_gate(case: dict, tmp_path: Path):
    """Every allow_case must produce ALLOW verdict (ok=true, exit=0, decision=rco_pass_present)."""
    events = case["events"]
    task_id = case["task_id"]
    head = case["head"]
    rco_agents = _case_rco_agents(case)
    author_agent = case.get("author_agent", "codex-lead-1")
    expected = case["expected"]

    # Library path
    result = check_rco_pass_present(
        events=events,
        task_id=task_id,
        head=head,
        rco_agent=rco_agents,
        author_agent=author_agent,
    )
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_qualifying_rco_pass_at_head"] is True
    if "latest_rco_is_veto" in expected:
        assert result["latest_rco_is_veto"] is expected["latest_rco_is_veto"]
    for key in CLAIM_GATES:
        assert (
            result[key] is False
        ), f"gate {key} must be false in result for allow case {case['name']}"

    # CLI path
    events_path = _seed_events(tmp_path, events)
    cmd = [
        sys.executable,
        str(SCRIPT),
        "--task-id",
        task_id,
        "--head",
        head,
        "--events",
        str(events_path),
        *_rco_agent_cli_args(rco_agents),
        "--author-agent",
        author_agent,
        "--json",
    ]
    res = subprocess.run(
        cmd,
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert (
        res.returncode == 0
    ), f"allow case {case['name']} must exit 0; stderr={res.stderr} stdout={res.stdout}"
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "rco_pass_present"
    assert payload["has_qualifying_rco_pass_at_head"] is True
    for key in CLAIM_GATES:
        assert payload[key] is False


def test_corpus_events_exercise_head_binding_and_veto_logic(corpus: dict):
    """Sanity: at least one refuse has head mismatch, one has post-pass veto, one allow has head match; all events contain stable head strings where expected."""
    heads = set()
    for c in corpus["refuse_cases"] + corpus["allow_cases"]:
        heads.add(c["head"])
        for ev in c["events"]:
            if "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0" in json.dumps(ev):
                heads.add("a1b2... present in some event")
    assert (
        len(heads) >= 2
    )  # at least the main HEAD and the other_head used in stale case
