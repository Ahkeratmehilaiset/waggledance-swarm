# RCO_PASS_GATE_CONFORMANCE (versioned offline corpus + locked test)

Status: producer slice (grok-scout-1). Disjoint paths only. NEW FILES ONLY. Does not modify `tools/check_rco_pass_present.py` or `tests/tools/test_check_rco_pass_present.py`.

## Purpose

This conformance asset locks the fail-closed RCO-pass-presence merge gate (`tools/check_rco_pass_present.py`, merged in #837) against regression. The gate enforces CLAUDE.md Rule 9a: **"RCO absence = NO merge"**. A valid `claude-rco-1` (or --rco-agent) `RCO_PASS` (`type` in {decision, rco_review}, `status=rco_pass`) whose *message* contains the exact --head SHA must be present for the --task-id at the *exact* --head; any later veto from the rco-agent (changes_requested / finding / blocked / rco_block* etc.) supersedes and refuses.

The corpus enumerates the exact must-REFUSE and must-ALLOW sets so that any future change that weakens the gate (e.g. default-allowing silence, accepting stale head, ignoring non-decision types with rco_pass status, counting wrong-agent passes, or failing to detect later veto after pass) will cause `tests/tools/test_check_rco_pass_present_conformance.py` to fail deterministically.

All operations are offline, deterministic, no network, no model pulls, no wallclock or random values in the committed artifacts (beyond the static "provenance" label).

## Files (exact allowed set for this slice)

- `tests/tools/check_rco_pass_present_conformance_corpus.json` — the versioned fixture (refuse_cases + allow_cases arrays + claim gate declarations + stable task/head)
- `tests/tools/test_check_rco_pass_present_conformance.py` — the loader/assert test (imports only the public `check_rco_pass_present` + `CLAIM_GATES`; seeds synthetic events.jsonl for CLI; asserts exit/verdict REFUSE for refuse, ALLOW for allow)
- `docs/architecture/RCO_PASS_GATE_CONFORMANCE.md` — this document

No other files may be created or edited in this round (no manifests, no aggregation scripts, no capability files, no changes to existing tests/docs, no edits to check_rco_pass_present.py or its unit test).

## Corpus shape

```json
{
  "corpus_version": "wd.rco_pass_gate.conformance_corpus.v1",
  "title": "WaggleDance RCO Pass Presence Gate Conformance Corpus",
  "description": "... enumerating REFUSE cases (no rco_pass event / silence; rco_pass at a DIFFERENT head than --head; a later changes_requested/finding/blocked veto after a pass; type=message or type=handoff with status=rco_pass; wrong rco-agent identity posting the pass) and ALLOW cases (type=decision status=rco_pass with the exact --head SHA in the message, no later veto, correct rco-agent) ...",
  "provenance": "hand-authored stable event shapes per RCO_PASS_PRESENCE_GATE spec...; deterministic by event list order only",
  "task_id": "waggledance/grok-scout-1/rco-pass-gate-conformance-corpus-20260603",
  "head": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
  "other_head": "0000000000000000000000000000000000000000",
  "rco_agent": "claude-rco-1",
  "refuse_cases": [ /* 9 cases covering all listed REFUSE shapes + variants */ ],
  "allow_cases": [ /* 4 cases covering decision + rco_review, with/without prior veto then fresh pass, pass+non-veto-later */ ],
  "claim_gates": {
    "claim_gate_satisfied": false,
    "claim_safe": false,
    "literal_future_claim_safe": false,
    "controls_present": false,
    "runtime_authority_granted": false,
    "external_writes_applied": false,
    "required_runtime_evidence_present": false
  }
}
```

The test loads the corpus at collection/runtime and parametrizes assertions:
- for every item in refuse_cases: `check_rco_pass_present(...)["ok"] is False`, decision matches, `has_qualifying...` per spec, CLI returncode != 0
- for every item in allow_cases: `check_rco_pass_present(...)["ok"] is True` and `decision == "rco_pass_present"`, CLI returncode == 0

It also asserts that the corpus itself carries every gate as the literal boolean `false`, and that results emitted by the gate carry them false.

## Claim gates (strict)

This is a pure test asset. All claim gates are N/A and are emitted as literal `false` in the JSON corpus and in every gate result exercised by the test. The test hard-asserts their presence and falsity in both places. There are no carve-outs, no "future" relaxation, and no consumer may read this corpus and set any gate to true.

See `tools/check_rco_pass_present.py:CLAIM_GATES` (and the identical list in leak_policy) and the sibling `docs/architecture/RCO_PASS_PRESENCE_GATE.md`.

## Invariants locked by this corpus + test

- Silence / absence of any qualifying RCO_PASS for the task from the rco-agent -> REFUSE (no_rco_events_for_task or no_qualifying_pass).
- RCO_PASS whose message does not contain the exact --head (different/stale head) -> REFUSE (no_qualifying_pass).
- Any later veto from the rco-agent after a qualifying pass (changes_requested, finding, blocked, rco_block* shapes) -> vetoed_after_pass REFUSE, even if a pass existed earlier. latest_rco_is_veto also covers most-recent-is-veto case.
- Non-qualifying types (message, handoff, etc.) carrying status=rco_pass are ignored for the presence check -> no_qualifying_pass REFUSE (only decision/rco_review count).
- Wrong rco-agent identity (e.g. codex-lead-1 or tools) posting a pass is never counted for the default claude-rco-1 gate -> REFUSE.
- Valid ALLOW requires: exact head string in the message of a type=decision or rco_review + status=rco_pass from the correct rco-agent, and no veto after it in the append-order log. A fresh pass after an earlier veto allows (re-review semantics).
- "Latest" is strictly by event index in the list / jsonl append order (no wallclock ts used for ordering).
- Head binding is substring containment in the "message" field only (payload does not count).
- All claim gates remain literal false in every artifact.
- Deterministic + offline only. No non-finite numbers, no random, no external I/O beyond loading the sibling JSON and temp synthetic jsonl for CLI subprocess.

## Usage

The conformance test is intended to be run as part of the tools test suite (and in CI):

```
python -m pytest tests/tools/test_check_rco_pass_present_conformance.py -q
```

It can also be used by future contract authors or bridge consumers as a machine-readable list of the exact event shapes that must continue to classify as REFUSE vs ALLOW. Import the corpus JSON directly if a contract needs the authoritative list of cases (still call the shared `check_rco_pass_present` rather than reimplementing the gate logic).

## Relation to RCO_PASS_PRESENCE_GATE.md and the original locked test

- `docs/architecture/RCO_PASS_PRESENCE_GATE.md` + `tests/tools/test_check_rco_pass_present.py` remain the single source of truth for the implementation and the full enumerated cases (including the full veto shape logic in _is_rco_veto_event, _has_blocking_shape, interaction with check_bridge_changes_requested.py, and integration in idle_consensus_auto_merge / bridge_loop_tick).
- This conformance corpus + test is an additional regression brake focused on the core "presence at exact head with no later veto" contract and the minimal set of must-refuse / must-allow cases that protect the autonomy safety property. It is deliberately a disjoint producer slice (NEW FILES ONLY).

## Anti-drift contract

Changing the DECISION_TYPES_FOR_PASS, RCO_PASS_STATUSES, BLOCKING_* sets, the _is_qualifying_rco_pass / _is_rco_veto_event logic, the head-in-message requirement, the rco-agent filter, or the latest-by-index + later-veto supersede rule such that a refuse_case now yields ok=True (or exit 0) or an allow_case yields ok=False will break this test. That is the intended outcome.

When the gate legitimately tightens or a new veto shape is added to the blocking set, the corpus must be updated in the same change that updates the implementation (still only touching the three allowed paths for a future slice of this type). Never weaken the fail-closed behavior.

All claim gates remain false in every artifact produced by or consuming this conformance material.

## Related

- `tools/check_rco_pass_present.py`
- `tests/tools/test_check_rco_pass_present.py`
- `docs/architecture/RCO_PASS_PRESENCE_GATE.md`
- `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`
- `docs/architecture/LEAK_POLICY_CONFORMANCE.md` (pattern mirrored by this slice)
- MAGMA / bridge consensus / RCO docs (this slice is build-consensus + RCO review gated; producer does not merge)
