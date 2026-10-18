# Hello Mind v0.1

One small, real integration path: a public synthetic fixture goes through the real JOIN chain, end to end.
Sandbox, advisory, never a gate. It is not Q4, not a utility measurement and not a production or native test.

Source: Lead round 9/10 (`R09_PROPOSAL.v1.md`, Grok consultation 3d7b96b4), decided by fable-5 as diamond
round D3 (ACCEPT). fable-5 wrote the code and the tests.

## What runs

```
python -B sandbox/mind/hello_mind.py --out <new dir inside an existing .codex-audit directory> [--pruned-capture]
```

| seam | real code | refuses when |
|---|---|---|
| pins | `live_pins()` against `PINS` | any live component version differs (before any output) |
| fixture | `write_fixture`: real core snapshot in its publication envelope, six cycle files, manifest | the manifest is not one Hello Mind cycle |
| load | `lane_view_frozen.load_frozen` | one byte differs from the manifest; a null or missing publication digest (before any cycle file is read) |
| turn | `join_turn.turn` (lane view, HEAR without receipts, card) | the stage's own fixed reason |
| write_turn | `join_turn.write_turn` into `<out>/turn` | the stage's own fixed reason |
| judge | `join_outcome_adapter.judge`, result in `outcome.json` | the adapter refuses (for example a changed card whose digest was fixed) |

The first refusing seam stops the chain. Later seams are `not_run`, and the CLI exits 2.

## The fixture

The fixture has one lane (`codex-tools-1`), one cycle (`hello-1`, as-of 2026-10-18T12:00:00Z) and one PR.

Events:
- a Lead request at head A;
- an RCO1 vote and a Lead vote at head B, which is the GitHub head.

So the bridge head lags GitHub, as it did in the PR1905 night. claude-rco-2 is a commit author, so it fills no slot. That leaves the Tools build vote as the only empty slot.

The card shows one source-referenced SEE item. The tests check it with a literal written from
`join_card.see_line` and `lane_view.action_line`, not one taken from the report.

## What the report says, and what it does not

`report.json` contains:
- the per-seam status;
- digests of the fixture, the turn outputs, the outcome and the code (`HELLO_CODE` = the import closure, tested);
- `card_utf8_bytes` (at most 2048);
- the explicit `frozen_as_of`;
- `authority: none`.

**Outcome.** The best outcome is `ACCEPTED_CARD_UTILITY_UNKNOWN`. A synthetic card has no real use event or receipt, so its utility is UNKNOWN. The report never says the action succeeded.

**Receipts.** HEAR is run without receipts, so every receipt is UNKNOWN `no_source`, never NONE.

**Captures.** A pruned capture (`--pruned-capture`) is never read, and the event inventory is then `UNKNOWN_capture_pruned`. A null capture and a null publication are different cases: the first is reported, the second refuses.

**Output directory.** It must be new, so a second run into it is refused before any byte is written and the first bytes stay. A refusal keeps the fixture and the report as evidence: nothing is removed, and there is no whole-directory or crash-atomicity claim.

Hello Mind does not do any of the following:
- read real or frozen fleet data;
- use a model, the network, a provider or a bridge write;
- import or call gate code;
- add a dependency;
- make a performance or recall claim.

## Tests

`test_hello_mind.py` has 22 tests, written RED-first against an interface skeleton (19 RED, then GREEN). They cover:
- pins and unknown pairs;
- the positive control line;
- receipts UNKNOWN `no_source`;
- that the digests are the disk bytes;
- the null-capture and null/missing-publication cases;
- a wrong fixture digest;
- a re-hashed card that is still refused;
- the same directory twice;
- a missing output root;
- a fixture outside the output directory;
- the guarded-word refusal;
- the import closure (no gate, provider or model module);
- the CLI.

A mutant run on a scratch copy, with a passing control, killed 20 of 20 mutants.
