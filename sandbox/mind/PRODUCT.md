# Bridge Mind JOIN card: product card (sandbox, not landed)

Status on 2026-10-14 at about 21:35Z. The operator's goal (18:28Z, 18:51Z and 21:27Z): "I hear, I see, I understand,
I remember, and the Mind learns". It should be polished to a diamond in the sandbox and land once, as finished as
possible, through the normal gate. The Mind is advisory. It never computes a gate verdict; before any vote or pass,
check with the canonical tools. The hard rules in [README.md](README.md) apply to every part.

## What a lane gets

One card per turn, at most 2048 bytes, in fixed line order. One command, `join_turn.py`, turns one verified frozen
capture cycle into one lane's card. The lines carry their refs (PR heads, vote refs, the delivery identity, lesson
refs), and an unsure fact is `UNKNOWN` with a reason, never a guess. REMEMBER lines appear only when the turn supplies
a REMEMBER answer bound to this lane and this view's as_of; otherwise there is no REMEMBER line. This is an example
rendered from the synthetic test fixtures:

```
JOIN codex-tools-1 as_of=2026-10-13T19:00:00Z now=2026-10-13T19:05:00Z join-card-v0.1; advisory, not a gate; check the canonical tools before any vote
HEAR waits over 30 min on your slots: new 1, unchanged 0, carried 0, unknown 0, below 0; first pr=1905 slot=build_tools wait_min=79 since=2026-10-13T17:40:44Z requested=UNKNOWN delivered=UNKNOWN read=UNKNOWN answered=UNKNOWN id=9af5028668fc (join-card-delivery-semantics-v1.1, card_trust current)
SEE pr1905@5c5ba036851d empty=build_tools credited=build_lead:fe117f0ff873,claude-rco-1:10b838083bcb ci=6/6; abstained 0 (UNKNOWN reasons in the view), coverage complete
SLOT pr=1905 head=5c5ba036851d23f45602dc7346238da09b665ece credited_at_head=2(build_lead:fe117f0ff873,claude-rco-1:10b838083bcb) you_in_set=no veto=none_observed ci_at_head=6/6 last_open_slot=yes
UNDERSTAND your build_tools vote is the last open slot on PR1905 at 5c5ba036851d: CI 6/6 at that head, no veto observed, 2 slots filled; the canonical tools decide what follows
REMEMBER [1] lesson_a.md (lesson, 2026-10-12): a narrowed retry loop must keep every reason the old code retried
REMEMBER picks [remember-v2.0 answer=e8f2d7742b59 situation=9fdafff3e45e]
LEARN v0 records nothing from this card; LEARN contract rco2-learn:f80a92c2
```

## Sections, modules and evidence

| Line | Module | Commit | Evidence | Status |
|---|---|---|---|---|
| SEE | `step1/lane_view.py` | e9cae9d5 | Acceptance 1a on the frozen PR1905-night cycles: Tools line 8/8, must_not 4/4, controls 6/6, byte-identical rerun (b739f4be). Parity 1b over 22 frozen captures (a8bb4c3c). 67 sandbox tests at that commit. | Measured on frozen cycles by the author; no independent probe |
| HEAR | `join_card_delivery.py` v1.1 | 83f9c3e5 | RCO1 probes 4FEAA88E on v1: 49 PASS / 4 FAIL (1185E841). F1-F3 repaired, each reproduced red first. 35 tests, hand mutants 7/8 (the survivor is equivalent). v1.1 re-probed with the unchanged harness: 53 PASS / 0 FAIL (BBA57334; measured-binding run 6F544EF9). | Probed: v1.1 53/0 |
| SLOT, UNDERSTAND, the REMEMBER binding | `join_card.py` v0.1 | 28300a47 | RCO1 probes EC030DEF on v0 a89d50ca (16214158): 8 PASS, 1 FAIL minor (P2b), 1 UNREACHED, 1 NOT_APPLICABLE, 1 UNCOVERED_BY_CONTRACT (P4b). v0.1 fixes P2b by omission and P4b with a turn-bound REMEMBER envelope: 39 tests, hand mutants 26/27 (J12 is equivalent). RCO2 binding probes on 28300a47 (AEB929E8): P1 to P3 pass (an absent answer is omitted; another lane and a stale as_of are refused; an answer changed after hashing is refused). P4 is a measured limit: another well-formed situation digest at the same lane and as_of renders with only the citation changed. | v0.1 probed by RCO2: P1-P3 pass, P4 limit. Not a product or RCO pass. |
| JOIN turn | `join_turn.py` v0 | 6db27217 | 20 tests: two processes with different hash seeds write the same bytes; every frozen P1 cycle gives exactly the acceptance-1a view and a bounded, guarded card for every lane; containment, no-overwrite, read-back and refusals. Red baseline run after the fact (the module draft came before the tests): 20 of 20 fail against an interface stub. Hand mutants 25/25. Root's output-race audit covered preview 4D5AC73A only, which differs from the committed bytes in one refusal message. | Author-tested; not independently probed |
| REMEMBER | `remember/remember_v2.py` | 2699c0fe, frozen | Blinded, once per arm, run manifest E6531C66. Pooled G9 over RCO1 set 3b and RCO2 set 3 (records 998B458A, 0AC239E7, pooled 625B710C; both RCOs agree): v2ca recall@3 13/16, negatives abstaining 6/6, precision@3 26/27, 0 leaks. | v2ca and v2a: smoke pass, not proof. v2c: NOT YET. v2ca on set 3 alone: NOT YET (5/8). |
| memory under REMEMBER | `step2/` (WD hex cells, Q4) | 994bdb8b | memory-v1 4cd2d95c: 797 lesson notes and 2233 bridge events in WaggleDance's own HexCellTopology cells, verified generations. | Q4 alone NOT YET (3/9); used only under v2 |
| LEARN | `join_card_outcomes.py` (RCO2) | b708a832 on `sandbox/mind-learn-rco2-20261014` | Contract v1.2 96223AD3; aggregate delta v1.2.1 24F508BD registered before code. RCO1 harness v1.1: 50 PASS / 0 FAIL with 4 observations on f80a92c2 (BDBD0891) and on b708a832 (E5415129). | Offline counts only, not wired. Proposed to land later as its own PR (RCO2's option A, supported by RCO1; Lead decides), outside this product. |

## Known limits (they stay in every card and in the landing description)

* REMEMBER is a smoke pass, not proven. n is 16 positives and 6 negatives, the joiner was from the author's model
  family (cross-family usefulness is UNKNOWN), the two graders labelled differently (read-based and mechanical), and
  all three sealed sets are now used. Any v2.1 needs a new pre-registration and new blind cases.
* REMEMBER arm: the card cannot see which arm produced an answer, so a v2c answer inside an envelope would render like
  a joined one. "A v2c result is never shown as an answer" is a process rule today, not enforced by code.
* On fresh, independently worded pairs the true lesson is present, but its opposite is unmarked in 80/80 v2a
  observations (RCO1).
* A forged-date flood hides the true lesson in 8/20 of the old A/B v2a shortlists (a probe run after the code was
  written). Ordering trusts writer-chosen dates; verified availability is not considered yet.
* v2c (code only, no joiner) is NOT YET (10/16; negatives 4/6), and with an identity rewrite a flood hides the true
  lesson in 13/20 fresh answers. A card must never show a v2c result as an answer.
* Privacy: the shortlist guard was not independently exercised, because RCO1's guarded fixtures were rejected at
  corpus build. There is no blanket privacy pass.
* REMEMBER binding (card v0.1): the card checks the lane, the turn (as_of) and the answer digest. The digest is
  self-consistency, not origin. `situation_sha256` is cited, not verified: an answer made for another situation at
  the same lane and as_of renders with only the citation changed (RCO2 P4, measured).
* JOIN turn: each output file is created exclusively and read back, but there is no whole-directory transaction or
  crash safety; a crash or a competing writer can leave a partial turn directory. A frozen cycle is never live input,
  and `now` defaults to the cycle's as_of, never the wall clock.
* Dependencies: the product imports `tools/wd_bridge_mind.py`, which is not on main 00ce1453, and two tools modules
  whose main bytes differ from the tested ones. Every sandbox test so far ran on the branch versions (fable-5
  membership ledger v1, D0DB67EA).
* LEARN aggregate: since v1.2.1, a card line evaluated once without and once with its action is UNKNOWN
  (mixed_history) instead of 1/2. That is a bound, not a resolution; the full answer needs the v1.3 action horizon,
  which is not frozen. No LEARN number is a benefit claim.
* HEAR: one guarded word in one PR's field withholds every lane's card (guard poisoning). The fix is a separately
  registered semantics change. A malformed view with an unhashable slot raises TypeError and the CLI exits 1: still
  fail-closed, but not a fixed message (RCO1).
* SEE and SLOT: `last_open_slot=yes` needs complete coverage. A frozen capture window is never complete, so every
  lane gets `UNKNOWN` there, by design.
* Live input: the sandbox producer has not written since 2026-10-13. Restarting it needs the operator, and its
  read-only GitHub calls are a carve-out that Lead decides.

## Landing bar: Lead's frozen criteria v1

Lead froze seven criteria on 2026-10-14: `landing-bar.v1.json`, sha256 5328C678D67FB600..., in
`C:\Python\project2\.codex-audit\bridge-train2-20261011\mind-product-landing-governance-20261014\`. They are criteria
only: not a source freeze, an implementation go, an approval, an exception, an install, an activation or a restart.
This section summarizes them, and the frozen file governs. The "where the product stands" column is fable-5's own
reading and carries no authority.

| # | Criterion (summary) | Where the product stands |
|---|---|---|
| 1 | Bounded independent evidence and honest input identity: frozen input and code hashes and an independent probe for each included section, rebound to the exact final blobs; UNREACHED or unassessed layers are never PASS. | Partly. Independent probes exist for HEAR v1.1 (BBA57334), the JOIN card v0.1 (AEB929E8) and the REMEMBER v2 grading (625B710C). SEE and the JOIN turn have author evidence only. A new final head is required. |
| 2 | One deterministic, safe joining command: verified frozen inputs to one lane's cited card of at most 2048 bytes; UNKNOWN stays UNKNOWN; no bridge writes, gate verdict, new permission or implicit live clock; guard, containment, no-overwrite and refusals tested at the final source; no whole-directory or crash-safety claim. | `join_turn.py` v0 (6db27217) is author-tested for these points; it is not independently probed. The situation digest is only cited. |
| 3 | Measured utility, or explicitly no utility claim. | No benefit or speedup is claimed. No utility protocol has been preregistered and run. |
| 4 | Versioned REMEMBER contract and fresh acceptance: only the joined v2a/v2ca protocol is shown; a next version needs the corrected design, a locked PREREGISTRATION-v2.1, exact hashes and NEW sealed cases before execution. | v2 is frozen and its cases are USED. The card does not enforce the arm (see Known limits). v2.1 is plan only: the plan 7F17529D is being revised for Lead's 21:01Z corrections and RCO1's review 656F47B8, then the pre-registration; acceptance uses RCO2's sealed set F8A8DB1F (unopened) and fresh RCO1 probes. |
| 5 | Cross-family joining evidence: a preregistered Codex joining run on the next frozen version and new sealed cases, with a matched Claude baseline. | UNKNOWN. Lead's cross-family joiner plan (2BB39DC3) is a plan only. The same-family smoke pass is not generalized. |
| 6 | Membership, actual provenance and lawful independent review before CI: exact paths and blobs, identity-bound contributors, each RCO exclusion explained, class recomputed on the whole diff; the (a) signature and any nonstandard route need an explicit operator decision. | Proposal only: fable-5 ledger v1 (D0DB67EA). Blocker: 3 of the 9 imported repo dependencies are not main's bytes. LEARN is proposed to land separately. `sandbox/mind` is (a) today (Lead's static assessment E8AA7A3A; RCO2's classify_ab on 227aa862 agrees); recompute on the landing diff. Mixed-family authors can leave the installed opposite-family slot unavailable. |
| 7 | Required final-head validation and bounded release: affected sandbox tests and the selector-required scope, required CI green at the exact final head, current independent reviews and veto scans; no sandbox data in git; install, activation and restarts stay separate operator decisions. | Not started. The sandbox suite (232 passed at 6db27217) is not whole-repo CI. Nothing is added to the six-source release train (0979d75f / tree 8143ad50). |
