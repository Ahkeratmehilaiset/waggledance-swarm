# Bridge Mind JOIN card: product card (sandbox, not landed)

Status on 2026-10-14 at about 21:00Z. The operator's goal (18:28Z and 18:51Z): "I hear, I see, I understand, I
remember, and the Mind learns". It should be polished to a diamond in the sandbox and land once, through the normal
gate. The Mind is advisory. It never computes a gate verdict; before any vote or pass, check with the canonical
tools. The hard rules in [README.md](README.md) apply to every part.

## What a lane gets

One card per turn, at most 2048 bytes, in fixed line order. The lines carry their refs (PR heads, vote refs, the
delivery identity, lesson refs), and an unsure fact is `UNKNOWN` with a reason, never a guess. This is an example
rendered from the synthetic test fixtures:

```
JOIN codex-tools-1 as_of=2026-10-13T19:00:00Z now=2026-10-13T19:05:00Z join-card-v0; advisory, not a gate; check the canonical tools before any vote
HEAR waits over 30 min on your slots: new 1, unchanged 0, carried 0, unknown 0, below 0; first pr=1905 slot=build_tools wait_min=79 since=2026-10-13T17:40:44Z requested=UNKNOWN delivered=UNKNOWN read=UNKNOWN answered=UNKNOWN id=9af5028668fc (join-card-delivery-semantics-v1.1, card_trust current)
SEE pr1905@5c5ba036851d empty=build_tools credited=build_lead:fe117f0ff873,claude-rco-1:10b838083bcb ci=6/6; abstained 0 (UNKNOWN reasons in the view), coverage complete
SLOT pr=1905 head=5c5ba036851d23f45602dc7346238da09b665ece credited_at_head=2(build_lead:fe117f0ff873,claude-rco-1:10b838083bcb) you_in_set=no veto=none_observed ci_at_head=6/6 last_open_slot=yes
UNDERSTAND your build_tools vote is the last open slot on PR1905 at 5c5ba036851d: CI 6/6 at that head, no veto observed, 2 slots filled; the canonical tools decide what follows
REMEMBER [1] lesson_a.md (lesson, 2026-10-12): a narrowed retry loop must keep every reason the old code retried
LEARN v0 records nothing from this card; LEARN contract rco2-learn:f80a92c2
```

## Sections, modules and evidence

| Line | Module | Commit | Evidence | Status |
|---|---|---|---|---|
| SEE | `step1/lane_view.py` | e9cae9d5 | Acceptance 1a on the frozen PR1905-night cycles: Tools line 8/8, must_not 4/4, controls 6/6, byte-identical rerun (b739f4be). Parity 1b over 22 frozen captures (a8bb4c3c). 67 sandbox tests at that commit. | Measured on frozen cycles |
| HEAR | `join_card_delivery.py` v1.1 | 83f9c3e5 | RCO1 probes 4FEAA88E on v1: 49 PASS / 4 FAIL (1185E841). F1-F3 repaired, each reproduced red first. 35 tests, hand mutants 7/8 (the survivor is equivalent). | Probed v1; v1.1 not re-probed yet |
| SLOT, UNDERSTAND | `join_card.py` v0 | a89d50ca | 27 tests on the real core, lane view and delivery fixtures; hand mutants 17/18 (J12 is equivalent). | RCO1 probe plan EC030DEF registered before the code; not run yet |
| REMEMBER | `remember/remember_v2.py` | 2699c0fe, frozen | Blinded, once per arm, run manifest E6531C66. Pooled G9 over RCO1 set 3b and RCO2 set 3 (records 998B458A, 0AC239E7, pooled 625B710C; both RCOs agree): v2ca recall@3 13/16, negatives abstaining 6/6, precision@3 26/27, 0 leaks. | v2ca and v2a: smoke pass. v2c: NOT YET. v2ca on set 3 alone: NOT YET (5/8). |
| memory under REMEMBER | `step2/` (WD hex cells, Q4) | 994bdb8b | memory-v1 4cd2d95c: 797 lesson notes and 2233 bridge events in WaggleDance's own HexCellTopology cells, verified generations. | Q4 alone NOT YET (3/9); used only under v2 |
| LEARN | `join_card_outcomes.py` (RCO2) | f80a92c2 on `sandbox/mind-learn-rco2-20261014` | Contract v1.2 96223AD3 (Lead's audit 8A9D94EE matched v1.1). RCO1 harness v1.1 8e39f085: 50 PASS / 0 FAIL on c4486586; a rerun on f80a92c2 was asked for on 10-14. | Offline counts only, not wired |

## Known limits (they stay in every card and in the landing description)

* REMEMBER is a smoke pass, not proven. n is 16 positives and 6 negatives, the joiner was from the author's model
  family (cross-family is untested), the two graders labelled differently (read-based and mechanical), and all three
  sealed sets are now used. Any v2.1 needs a new pre-registration and new blind cases.
* On fresh, independently worded pairs the true lesson is present, but its opposite is unmarked in 80/80 v2a
  observations (RCO1).
* A forged-date flood hides the true lesson in 8/20 of the old A/B v2a shortlists (a probe run after the code was
  written). Ordering trusts writer-chosen dates; verified availability is not considered yet.
* v2c (code only, no joiner) is NOT YET (10/16; negatives 4/6), and with an identity rewrite a flood hides the true
  lesson in 13/20 fresh answers. A card must never show a v2c result as an answer.
* Privacy: the shortlist guard was not independently exercised, because RCO1's guarded fixtures were rejected at
  corpus build. There is no blanket privacy pass.
* HEAR: one guarded word in one PR's field withholds every lane's card (guard poisoning). The fix is a separately
  registered semantics change.
* SEE and SLOT: `last_open_slot=yes` needs complete coverage. A frozen capture window is never complete, so every
  lane gets `UNKNOWN` there, by design.
* Live input: the sandbox producer has not written since 2026-10-13. Restarting it needs the operator, and its
  read-only GitHub calls are a carve-out that Lead decides.

## Proposed landing bar (for Lead to amend, then freeze)

1. Every section has measured evidence on frozen inputs and an RCO probe result at the landing head: the JOIN card
   (EC030DEF), delivery v1.1, and LEARN at f80a92c2.
2. One command turns a capture cycle into a lane's card: deterministic, bounded, guarded (`join_turn.py`, planned).
3. Benefit: either RCO2's unit plan is frozen and measured on frozen captures, or the landing claims no benefit.
4. REMEMBER reaches a card only through the joiner protocol (v2ca or v2a); a v2c result is never shown as an answer.
5. A cross-family joiner check (Lead's and Tools' lane), or the limit stays stated.
6. Governance is settled before the PR. `sandbox/mind` classified as (a) via unrecognized (RCO2, classify_ab on a
   full main 227aa862 archive; recompute it on the landing diff), so the landing needs the operator's explicit
   signature and both RCOs. LEARN was authored by RCO2, so Lead decides how the RCO slots are filled for a landing
   that contains it.
7. The whole `sandbox/mind` suite is green, nothing imports gate code, no sandbox data is in git, and the decoded
   guard runs on every output.
