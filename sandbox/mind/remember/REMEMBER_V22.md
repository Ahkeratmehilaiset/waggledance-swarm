# REMEMBER v2.2 (sandbox, advisory, never a gate)

Status: **sandbox dev, synthetic tests only.** Nothing here was run on real, sealed or frozen data, no blind
acceptance was run, there is no v2.2 runner, and nothing here is a release, a lock or a code-go. It is NOT the Q4
revision: Q4 stays on 6cf81dbb (REMEMBER v2.1.1, Lead's selection), and v2.2 edits no v2, v2.1 or v3 file.
Authored by fable-5 on 2026-10-18 under the operator's order to polish the Mind in the sandbox (fable-5's diamond
round D6, from RCO2's proposals R1, R1a and R1b; fable-5 decided the design).

## What changed and why

| Defect (measured on v2.1 before the code) | What the joining agent now sees |
| --- | --- |
| F1: an opposite inside the head's topic group that is not its head, earliest or latest record is hidden, and MAY_OPPOSE never marks it (it compares shown lines only) | One hidden opponent per group is shown beside the line it opposes, in the fence's time order, with v2.1's MAY_OPPOSE marks; when it does not fit, every line it opposes carries `[MAY_OPPOSE hidden]` and one gloss line |
| F3: packing stops at the first group that does not fit, so a later small topic is never tried | A group that does not fit is skipped whole and the next one is tried; `skipped_groups` names them |

Two records are opponents when one claims to supersede the other in this question (every claimant of the question's
own candidates, RCO1 round 7 and RCO2 1b) or when MAY_OPPOSE pairs them exactly as v2.1's `opposed` does. A shown line
is covered when an opponent of it is shown in its group. The rules, in order:

* Topics first: groups in fused order with exactly v2.1's lines, every trial measured as the joiner reads it. A topic
  is never dropped to make room for an extra or a mark.
* Then, per listed group in card order, the first uncovered line with a hidden opponent (the head first) gets that
  opponent shown, at most `MAX_EXTRAS = 1` per group. Opponents are tried in the fence's trust order (a record whose
  day the fence trusts first, then the higher fused score, then the id). One whose line the guard withholds is passed
  over; one that would break `CAP` or `CASE_BYTES` is not shown, and the next is tried.
* The extra takes its place in the fence's own time order: the claimed day of a record the fence trusts, else the time
  it became available. A BACKDATED or flooded opposite can never pose as the oldest line of its topic.
* Every shown line still uncovered while an opponent of it is hidden carries `[MAY_OPPOSE hidden]`, room permitting:
  fixed words, so a mark never shows a hidden record's text or name.

Telemetry: `skipped_groups` (in place of v2.1's `stopped_by`), `conflict_extras`, `conflict_withheld`,
`hidden_marks`, `hidden_unmarked`; `may_oppose_pairs` and `line_bytes` as in v2.1. Arms `v22a` and `v22ca`; a v2.1
arm name is refused; every answer names its arm and `remember-v2.2-dev.0`. The grader contract (`RESULT_KEYS`) is
unchanged: `hidden_opposed` stays in the answer, as `may_oppose` does.

## Not adopted from the proposals, and why

* Head-only groups (V22-1's capacity part): the earliest and latest lines are v2's structural defense against an
  opposite flood whenever MAY_OPPOSE misses an opposite. v2.2 keeps every v2.1 line and adds; it never trades a line
  for room.
* Skipping a group whose conflict line the guard withholds (V22-2's second clause): a planted guarded opposite could
  then remove the true lesson from the card. The withheld opponent is passed over and the lines it opposes keep the
  hidden mark.
* The bounded closure over extras (R1a) and two extras per group: with every v2.1 line kept, an extra always has a
  shown opponent (its anchor), so a closure step adds nothing; one extra bounds a group at `GROUP_LINES + 1` lines.
* Two extra topics from depth 5 (V22-3): more text for the joiner on negative cases; it needs a negatives measurement
  by a non-author first.

## Run

```
python -B -m pytest sandbox/mind/remember/test_remember_v22.py -q -p no:cacheprovider
```

In code: `RememberV22(q4, "v22a").shortlist(situation, situation_time_utc, [situation])`, then
`render_shortlist(case_id, situation, listed)` for the joiner and `apply_picks(listed, picks)` for the answer.

## Tests (synthetic, `test_remember_v22.py`)

Each fixture is a measured precondition: the v2.1 card of the same memory is built first and shows the defect.

* RCO2 R1 test 2 (F1), the head's group: the opposite is shown beside the head and marked both ways.
* R1 test 3, adapted: a group without an opponent keeps exactly its v2.1 lines.
* R1 test 8: v2.2 equals v2.1 byte for byte on cards with nothing hidden or skipped (3 situations, both arms).
* R1 test 1 (F3): the wide group is skipped and the later small topics are listed.
* R1 test 4: a forged-date flood keeps the fence lines byte for byte.
* R1a test 10: a hidden flood opposite is one extra, marked FLOOD, placed by the time it became available.
* R1a test 11, adapted: one extra per group however many opponents hide in it; and two hidden claimants of the head
  where a second extra would fit: one is shown (this pins `MAX_EXTRAS`).
* R1a open point 225A7D63: an opposite of a shown line other than the head is found.
* The room rule: an opponent that does not fit leaves the fixed mark on the three lines it opposes; no topic dropped.
* The guard-skip rejection: a guarded opponent is passed over and never removes its group.
* R1b test 12: every claimant of this question is an opponent, not only `superseded_by`.
* The fence time order both ways: a BACKDATED extra goes by its available time, an eligible DATE_UNVERIFIED extra by
  its claimed day; and a trusted opponent is shown before a higher-ranked flood one.
* R1 test 6, adapted: cards never break `CAP` or `CASE_BYTES`, and marks name shown lines only (both arms).
* The arm check, and an answer that names its arm, its version and the hidden mark.

Provenance: 16 test cases were written before the code (11 failed and 5 passed against a skeleton). After the first
run against the code, two fixtures were corrected to a memory without the step-2 records, because beside them the
extra had no room (measured 2211 and 2075 of 2048 bytes; that case is the room rule's own test); no assertion
changed. Three tests were added for stated rules no earlier test pinned (trust order, claimed-day place,
`MAX_EXTRAS` with room). Mutants: 16 named mutants of `remember_v22.py` (break instead of skip, coverage ignored,
head-only anchors, no fit check for extras, an extra that drops a later topic, the guard-skip clause,
`superseded_by` only, no hidden mark, placement by the claimed day, `MAX_EXTRAS = 2`, no trust order,
DATE_UNVERIFIED as untrusted, the guard off, any hidden member as an opponent, no gloss, the head's mark only). Run
1 killed 15 (`MAX_EXTRAS = 2` survived: no fixture had room for a second extra); run 2, with a passing control and on
the committed bytes, killed 16 of 16 (harness and outputs in fable-5's audit folder,
`fable-5-remember-v22-20261018/mutants_r2.out.txt`).

## Known limits (stated, not hidden)

* MAY_OPPOSE's known misses (`REMEMBER_V21.md`) stay misses: an opposite it misses is still hidden and unmarked.
* Opponents are looked for in a line's own topic group only; an opposite grouped under an earlier head is not found.
* Room: an extra that opposes all three fence lines costs about 1.1 KB of the 2 KB card (its line and six MAY_OPPOSE
  marks; the head-opposite test card went from 867 to 2003 bytes), so on a full card the hidden mark is the common
  outcome. A shorter MAY_OPPOSE form would change every marked card; it needs a measurement by a non-author first.
* A supersede claim stays a writer-controlled claim (the claimant's availability is its file mtime).
* One engine answers one question at a time: its claims, candidates and fence groups live on the instance, as in v2
  and v2.1.
* No runner, manifests or freeze exist for v2.2, it has never seen a blind set, and it is not wired into the join
  card. Whether it helps the joining agent is UNKNOWN until a non-author measures it.
* Every run used fable-5's lane token, which is High (elevated). These are pure-Python synthetic tests with no
  privilege-dependent path, but no medium-token run was made.
