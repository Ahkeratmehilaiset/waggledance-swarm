# SPDX-License-Identifier: BUSL-1.1
"""Conformance test for the bridge-consensus verifier (fail-closed Rule 9a) using a locked, versioned corpus.

This test loads tests/tools/verify_bridge_consensus_conformance_corpus.json and drives
the REAL verify_bridge_consensus (from tools/idle_consensus_auto_merge) over every
refuse_case and allow_case using keyword args (events=, task_id=, head_sha=).

- For every refuse_case: asserts result['ok'] is False and result['decision'] matches expected.
- For every allow_case: asserts result['ok'] is True and result['decision'] == "bridge_consensus_verified".

The corpus enumerates the exact REFUSE set (2-of-3 missing any identity; duplicate/self-approving;
RCO author-as-own-reviewer; build-author waiver without the other build peer; lead/tools not
head-bound via absent sha in message; stale/different head; wrong agent identity; rco veto
changes_requested/finding/blocked from claude-rco-1; build status not in BUILD_CONSENSUS_STATUSES)
and ALLOW set (head-bound build peer plus a waived build-author slot when applicable,
correct statuses, correct identities, no later veto).

This locks the autonomy safety property of the head-bound bridge-consensus (CLAUDE.md Rule 9a)
against regression. Any future change that weakens verify_bridge_consensus (or its callers in
idle_consensus_auto_merge.py) such that a refuse_case now returns ok=True or an allow_case is refused
will cause this test to fail deterministically.

Synthetic fixtures only; deterministic append order for "latest"; offline, no network, no wallclock
in verdicts. No CLI subprocess here (the core verifier is exercised directly); see sibling
test_bridge_consensus_approver.py and test_idle_consensus_auto_merge.py for integration.

All claim gates are asserted false in the corpus artifact (per hard rule, mirroring rco/ leak_policy
conformance). The verify result itself is not required to carry claim gates.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.idle_consensus_auto_merge import (  # noqa: E402
    verify_bridge_consensus,
)
from waggledance.core.leak_policy import CLAIM_GATES  # noqa: E402

CORPUS_PATH = Path(__file__).parent / "verify_bridge_consensus_conformance_corpus.json"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}

# Explicit required case name sets. These make the regression lock strict:
# deleting ANY listed refuse or allow case will cause the set-equality assert to fail.
REQUIRED_REFUSE_CASE_NAMES = {
    "missing_rco_only_2_of_3",
    "missing_tools_only_2_of_3",
    "missing_lead_only_2_of_3",
    "duplicate_self_approving_identity_set",
    "rco_pass_present_but_lead_tools_not_head_bound",
    "approval_at_different_stale_head",
    "wrong_agent_identity_posting_a_role",
    "rco_veto_changes_requested_after_pass",
    "rco_veto_blocked_type_after_pass",
    "build_status_not_in_BUILD_CONSENSUS_STATUSES",
    "author_lead_waiver_missing_tools_peer_rejected",
    "author_tools_waiver_missing_lead_peer_rejected",
    "author_rco_self_pass_rejected",
    "other_recognized_rco_veto_blocks_pass",
}
REQUIRED_ALLOW_CASE_NAMES = {
    "three_distinct_head_bound_identities",
    "allow_various_build_statuses_and_rco_review_type",
    "fresh_pass_after_earlier_rco_veto_still_allows",
    "backup_rco_only_pass_satisfies_rco_slot",
    "author_lead_build_slot_waived_with_tools_peer",
    "author_tools_build_slot_waived_with_lead_peer",
}


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


def _events_with_agent_uuids(events: list[dict]) -> list[dict]:
    enriched: list[dict] = []
    for event in events:
        copy = dict(event)
        agent = str(copy.get("agent", ""))
        if agent in AGENT_UUIDS and "agent_uuid" not in copy:
            copy["agent_uuid"] = AGENT_UUIDS[agent]
        enriched.append(copy)
    return enriched


@pytest.fixture(scope="module")
def corpus() -> dict:
    return _load_corpus()


def test_corpus_is_versioned_and_complete(corpus: dict):
    """Lock the corpus shape, version prefix, case counts, and that it declares all gates false."""
    assert corpus["corpus_version"].startswith(
        "wd.bridge_consensus_verifier.conformance_corpus.v"
    )
    assert (
        isinstance(corpus.get("refuse_cases"), list)
        and len(corpus["refuse_cases"]) >= 7
    )
    assert (
        isinstance(corpus.get("allow_cases"), list) and len(corpus["allow_cases"]) >= 2
    )
    # provenance is deterministic label, no wallclock/random
    prov = corpus.get("provenance", "").lower()
    assert "hand-authored" in prov or "stable event shapes" in prov
    # task/head stable synthetic
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
def test_refuse_case_is_refused_by_verify(case: dict):
    """Every refuse_case must produce REFUSE verdict (ok=false, correct decision) from the real verifier."""
    events = _events_with_agent_uuids(case["events"])
    task_id = case["task_id"]
    head_sha = case["head"]
    author_agent = case.get("author_agent", "fable-5")
    expected = case["expected"]

    # Drive the REAL verify_bridge_consensus with keyword args only (no FS, no network, deterministic)
    result = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        author_agent=author_agent,
    )
    assert result["ok"] is expected["ok"]
    assert result["decision"] == expected["decision"]
    # Note: verify result does not emit claim gates; they are enforced only on the corpus artifact itself.


@pytest.mark.parametrize("case", _load_corpus()["allow_cases"], ids=lambda c: c["name"])
def test_allow_case_is_allowed_by_verify(case: dict):
    """Every allow_case must produce ALLOW verdict (ok=true, decision=bridge_consensus_verified)."""
    events = _events_with_agent_uuids(case["events"])
    task_id = case["task_id"]
    head_sha = case["head"]
    author_agent = case.get("author_agent", "fable-5")
    expected = case["expected"]

    # Drive the REAL verify_bridge_consensus with keyword args only
    result = verify_bridge_consensus(
        events=events,
        task_id=task_id,
        head_sha=head_sha,
        author_agent=author_agent,
    )
    assert result["ok"] is True
    assert result["decision"] == "bridge_consensus_verified"
    assert result["ok"] is expected["ok"]
    assert result["decision"] == expected["decision"]


def test_corpus_events_exercise_head_binding_veto_and_identity_logic(corpus: dict):
    """Sanity: corpus exercises head-in-message binding, stale head, later-veto, 2-of-3, duplicate, wrong-id, out-of-set status, and fresh-after-veto paths."""
    # Strict name sets ensure deleting ANY required case fails the test (category lock).
    refuse_names = {c["name"] for c in corpus["refuse_cases"]}
    allow_names = {c["name"] for c in corpus["allow_cases"]}
    assert refuse_names == REQUIRED_REFUSE_CASE_NAMES
    assert allow_names == REQUIRED_ALLOW_CASE_NAMES

    heads = set()
    has_stale = False
    has_veto_after = False
    has_missing = False
    has_duplicate = False
    has_wrong_id = False
    has_bad_status = False
    has_fresh_after_veto = False
    for c in corpus["refuse_cases"] + corpus["allow_cases"]:
        heads.add(c["head"])
        evs = c["events"]
        agents = [e.get("agent") for e in evs]
        statuses = [str(e.get("status", "")).lower() for e in evs]
        msgs = " ".join(str(e.get("message", "")) for e in evs)
        if (
            c["head"] == "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"
            and "0000000000000000000000000000000000000000" in msgs
        ):
            has_stale = True
        if "changes_requested" in statuses or "blocked" in statuses:
            # veto after would be in refuse
            if c["expected"]["ok"] is False:
                has_veto_after = True
        if len(set(a for a in agents if a)) < 3 and c["expected"]["ok"] is False:
            has_missing = True
        if (
            agents.count("codex-lead-1") > 1
            and "codex-tools-1" not in agents
            and c["expected"]["ok"] is False
        ):
            has_duplicate = True
        # NOTE: the has_wrong_id heuristic is intentionally removed here; see name-bound guard below.
        if "test:pass" in statuses and c["expected"]["ok"] is False:
            has_bad_status = True
        if (
            "initial review veto" in msgs.lower()
            or "earlier" in c.get("description", "").lower()
        ):
            if c["expected"]["ok"] is True:
                has_fresh_after_veto = True

    # For the wrong-agent guard specifically: look up the case BY NAME and assert its disqualifier
    # is a NON-RCO agent posting status=rco_pass (i.e. an rco_pass event whose agent != claude-rco-1),
    # so the property is bound to that named case rather than a corpus-wide any().
    wrong_agent_case = next(
        (
            c
            for c in corpus["refuse_cases"]
            if c["name"] == "wrong_agent_identity_posting_a_role"
        ),
        None,
    )
    assert wrong_agent_case is not None
    wa_evs = wrong_agent_case["events"]
    non_rco_rco_pass = any(
        str(e.get("status", "")).lower() == "rco_pass"
        and e.get("agent") != "claude-rco-1"
        for e in wa_evs
    )
    assert non_rco_rco_pass
    assert wrong_agent_case["expected"]["ok"] is False
    has_wrong_id = True

    assert len(heads) >= 1
    assert has_stale
    assert has_veto_after
    assert has_missing
    assert has_duplicate
    assert has_wrong_id
    assert has_bad_status
    assert has_fresh_after_veto
