# RCO_PASS Presence Gate (Rule 9a)

**Status:** 2026-06-03 (grok-scout-1 producer slice; disjoint new files only)

This document describes the fail-closed verifier `tools/check_rco_pass_present.py`
that enforces CLAUDE.md Rule 9a: **"RCO absence = NO merge"**.

## Purpose

Per `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md` §4:

> RCO absence = NO merge — if no explicit recognized RCO `RCO_PASS` at the
> exact head is present, the gate refuses even when build-consensus and every
> charter condition pass. Silence blocks; it does not default-allow.

A separate tool `tools/check_bridge_changes_requested.py` already provides the
RCO-veto preflight (absolute veto on any later `changes_requested`/`finding`/`blocked`
from a recognized RCO agent).

This tool (`check_rco_pass_present.py`) is the **presence** side of the RCO gate.
It must be **AND-ed** with the veto preflight in any merge decision path:

- Merge only if:
  - `check_bridge_changes_requested.py` reports clear (no-block), **AND**
  - `check_rco_pass_present.py` reports a valid `RCO_PASS` present at the **exact**
    head for the canonical `--task-id` (branch name) from any configured
    recognized `--rco-agent` that is not `--author-agent`.

Any missing, stale (different head), wrong-identity, non-qualifying-type/status,
or later-veto RCO signal fails closed to refusal (never default-allows).

## CLI contract (fail-closed)

```
python tools/check_rco_pass_present.py \
    --task-id <canonical-branch-name> \
    --head <40-char-sha> \
    [--events .agent-bridge/shared/events.jsonl] \
    [--rco-agent claude-rco-1] [--rco-agent claude-rco-2] \
    --author-agent <bridge-agent-that-authored-pr> \
    [--json]
```

- `--rco-agent` is repeatable. If omitted, the default recognized RCO set is
  `{claude-rco-1, claude-rco-2}`.
- `--author-agent` is required. A recognized RCO cannot satisfy the RCO slot
  for a PR it authored.
- Scans only events where `agent` is in the recognized RCO set **and**
  `task_id == --task-id`.
- A qualifying PASS requires:
  - `type` in {"decision", "rco_review"}
  - `status` in {"rco_pass"}
  - `message` contains the exact `--head` string (head-exact binding)
- Veto rule (most-recent-wins by append order in events.jsonl):
  - If the *most recent* event from any recognized RCO on the task is a veto
    (status changes_requested / blocked* or type finding/blocked), refuse
    regardless of any earlier PASS.
  - If one recognized RCO passes while another recognized RCO holds an
    unretracted veto, refuse. A later pass by the same RCO clears only that
    RCO's earlier veto; it cannot out-vote a different RCO's veto.
- Exit codes:
  - 0 : valid head-bound RCO_PASS present and not superseded by later veto
  - 3 : RCO_PASS absent / stale / vetoed / silence (the core fail-closed case)
  - 2 : argument / events-file parse error
- Always emits claim gates as literal `false` (per leak policy / hard rules):
  `claim_gate_satisfied=false`, `claim_safe=false`,
  `literal_future_claim_safe=false`, `controls_present=false`,
  `runtime_authority_granted=false`, `external_writes_applied=false`,
  `required_runtime_evidence_present=false`.

## Integration note

In an autonomous merge step the two RCO checks are conjunctive:

```
python tools/check_bridge_changes_requested.py --task-id "$TASK" --from-agent "$MERGER" ...
# and
python tools/check_rco_pass_present.py --task-id "$TASK" --head "$HEAD" ...
# only if both return 0, and other charter conditions (CI, receipts, build-consensus
# from lead+tools, head match, etc.) hold, may the merge proceed.
```

This pair together with build-consensus identities satisfies the three-distinct-identity
bridge-consensus contract while keeping RCO as the independent absolute veto + presence
authority.

## Self-modification / bootstrap

Like its sibling, this file and the tool it describes are subject to the charter
denylist and PR-only landing rules. The producer slice that added it (grok-scout-1)
followed the "NEW FILES ONLY" constraint for the round.

Offline / deterministic by design. No network, no wallclock in verdicts beyond
event order.
