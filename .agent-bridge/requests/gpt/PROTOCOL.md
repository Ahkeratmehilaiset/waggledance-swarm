# GPT consensus gate — bridge-loop release-review protocol

**Added:** 2026-05-08 by operator (autonomous bridge-loop hardening)
**Updated:** 2026-05-09 — pre-merge finding gate + GPT post-fix-review clarification
**Scope:** advisory-but-binding release gate for the autonomous Claude+Codex loop.

## Pre-merge finding gate (added 2026-05-09)

In addition to CLAUDE.md rule 9, **before any autonomous merge**, the
acting agent MUST verify that no Codex finding event remains open
against the PR's branch or original `task_id`. The contract:

- A `finding/open` event from Codex with `to: claude` blocks merge of
  the PR it targets until a matching `done/approved` event is published
  with the **same `task_id`**.
- BRIDGE_PROTOCOL rule 3.6 (replies reuse the request's exact
  `task_id`) is the mechanism that makes this checkable:
  `Read-AgentBridge.ps1 -Agent claude` prints `OPEN` vs `answered`
  status per `task_id`, so the acting agent can verify the gate
  without grepping the raw stream.
- This gate is **stricter** than the pure-test exception below. Even
  a pure-test PR with an open Codex finding must wait — the finding
  may flag a covertly-broken assertion that would otherwise hide a
  real bug.
- The 2026-05-09 PR #116 incident (validators property-gate
  promotion bug) and the PR #111 / PR #112 follow-ups all came from
  merging with open findings. This gate exists so that pattern
  cannot recur silently.

## GPT release-review is post-fix, not pre-design (clarified 2026-05-09)

The original framing of GPT as a pre-implementation `block_release`
gate produced a deadlock: GPT's actual operating profile is
"review the diff that exists now," not "approve a plan before
coding." GPT does not poll the bridge; it answers when an artifact
is pasted into its UI.

Operating model:

1. **Codex prompt-review** (architect / security / reliability) is
   the **pre-implementation** consensus gate. Path X / Path A+ /
   etc. design decisions live here.
2. **Claude implements** after Codex 3-role consensus.
3. **GPT release-review request** is written when the fix branch is
   ready to merge. It is the **post-fix** sanity check on the diff,
   not a plan-approval port.
4. **Operator pastes** the request to GPT, gets a verdict, commits
   the reply alongside the request as `<NNNN>_..._gpt_reply.md`.

`block_release: true` still applies until GPT's verdict lands or the
operator overrides — but it now serves a coherent role
(post-implementation safety net), not a pre-implementation deadlock.

The 2-min defer rule for doc/test/minor-contract-fixes remains in
force, since those classes are unlikely to need a third-model review
post-fix.

## When a GPT review request MUST be written

Claude (or Codex) writes a GPT review request artifact under
`.agent-bridge/requests/gpt/<NNNN>_<slug>.md` whenever the loop produces:

1. A **medium or high Codex finding** on a Claude-authored PR.
2. A **core runtime change** in product code (anything that changes program
   behavior at runtime, not test additions).
3. Any change touching **MAGMA**, **solver synthesis**, **security**, or
   **persistence** subsystems.
4. A **release merge** — autonomous squash-merge of any PR that contains
   anything other than a pure test addition.

Pure test-addition PRs (no source-code edit, no schema edit, no doc edit
that changes a contract) MAY be merged without a GPT request, since they
cannot change runtime behavior. The autonomous-merge guardrails of
CLAUDE.md rule 9 still apply unchanged.

## What the request MUST contain

The artifact is **self-contained**: GPT must be able to answer from the
artifact alone. Required sections:

1. **Task** — what was the loop doing and why.
2. **Diff summary** — files changed, insertions / deletions, key
   load-bearing edits quoted.
3. **Tests** — list of new / modified tests, pass/fail counts.
4. **Codex findings** — every finding from the latest Codex review
   round, severity, and how it was addressed. Verbatim Codex quotes
   where short enough.
5. **Claude response** — Claude's reasoning for the implementation
   choices, especially anywhere Claude disagreed with Codex.
6. **Explicit question to GPT** — what decision is the loop asking
   GPT to make? Default questions:
   - "Is this safe to merge to main?"
   - "Are the load-bearing invariants preserved?"
   - "Is there a risk vector Claude or Codex missed?"
7. **Block-release default** — until GPT replies, the loop treats the
   artifact as `block_release: true` for the listed PR(s).

## How GPT replies

The operator pastes the request to GPT (out-of-band — GPT does not poll
the bridge). GPT replies with a single block of structured output. The
operator commits GPT's reply alongside the request as
`<NNNN>_<slug>.gpt_reply.md`. Required reply fields:

- `verdict`: one of `approve`, `approve_with_conditions`, `block`,
  `defer_to_operator`.
- `rationale_per_role`: at minimum architect, security, reliability,
  release_safety. Each role gets one short paragraph.
- `conditions`: if `approve_with_conditions`, the explicit conditions
  that must be met before merge.
- `flagged_risks`: any risks GPT wants the operator to see, even if
  the verdict is `approve`.

## Binding semantics

- `approve` → Claude proceeds with autonomous merge once CLAUDE.md
  rule 9 guardrails are also met.
- `approve_with_conditions` → Claude implements the conditions in a
  follow-up commit on the same branch, re-pushes, and only then merges.
  GPT does NOT need to re-review unless the conditions are non-trivial.
- `block` → Claude must NOT merge. The block is binding regardless of
  CI / mergeable / Codex state. Only the operator can override with an
  explicit "override block" message in this conversation.
- `defer_to_operator` → Claude pauses the merge and asks the operator
  in this conversation. Loop continues with other independent work.

## Anti-patterns (what NOT to ask GPT)

- Open-ended brainstorming during active implementation. GPT is the
  release-gate, not the architect of in-flight code.
- "What should we build next?" — that is operator + scout territory.
- "Is the test naming convention right?" — that is review-cycle nit
  territory and goes through Codex.

## Numbering

Numeric prefix (`0001_`, `0002_`, ...) preserves chronological order
in `ls` output. The slug describes the PR or task in 4–8 words.

## Storage

`.agent-bridge/requests/gpt/` is **committed** to the repo. The bridge
events directory `.agent-bridge/shared/events.jsonl` remains gitignored
runtime state, but the GPT consensus artifacts are durable evidence of
release decisions and live with the code.
