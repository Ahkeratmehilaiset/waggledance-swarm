# REMEMBER v2.3 (sandbox, advisory, never a gate)

Status: **sandbox dev, synthetic tests only.** Nothing here was run on real, sealed or frozen data, no blind
acceptance was run, there is no v2.3 runner, and nothing here is a release, a lock or a code-go. It is NOT the Q4
revision: Q4 stays on 6cf81dbb (REMEMBER v2.1.1, Lead's selection), and v2.3 edits no v2, v2.1, v3 or v2.2 file.
Authored by fable-5 on 2026-10-18 under the operator's order to polish the Mind in the sandbox (fable-5's diamond
round D7, from RCO2's round-8 proposal with round 10's one attach rule; fable-5 decided the design). Grok was not
consulted in this round: Lead's Grok hold stands. RCO2's proposal itself drew on the Grok consultation 8b06f31d.

## What changed and why

| Defect (RCO2 round 8, measured read-only on the 934 lessons) | What the joining agent now sees |
| --- | --- |
| A card shows a lesson's head claim with no sign that the lesson's own body later withdraws or corrects it (strong-verb correction lines in 168 files, 283 lines); v2.2 shows none of them | The lesson's line carries `[CORRECTED <day or undated>[ HEAD][ (+n more)]: <note>]` right before its summary, and one gloss line says what it means |

The correction is attached by file, never retrieved: ranking cannot put a correction beside the claim it corrects.
`corrections.py` finds a lesson's own corrections in its stored text; `remember_v23.py` puts them on the card.

* What counts (RCO2's measured classifier): a LEAD line (an upper-case CORRECTED, CORRECTION, WITHDRAWN or SUPERSEDED
  at a line start) corrects the head claim. A strong PHRASE (was wrong, Lead caught it, withdrew, withdrawn,
  retracted) counts only beside SELF-deixis (this lesson/claim/note, the above/head/opening claim, I wrote, I
  claimed, earlier I/this). A paragraph with a `CORRECTION_STAMP` line is read from its exact stamps only. The
  frontmatter is never read. A lesson about mistakes (the model was wrong, a NEAR MISS) is not a correction.
* One mark per line: the last correction of the head in the file, else the last one, and `(+n more)` for the rest.
* The day is a label: the date right after the lead keyword, else the one date in the correction, else `undated`.
* A note is the writer's text: whitespace collapsed, brackets shown as parentheses (a note can never pose as a mark),
  and a note with a guarded word replaced by `(note withheld by the guard)`, so the claim it corrects is still shown.
* Packing is v2.2's (topics first, skip not break, hidden opponents), measured with the marks. When a card does not
  fit, every note is shortened to 40 characters, then to none; a group is skipped only when even the marks alone do
  not fit. A line is never shown without its mark, and the summary is never shortened.
* Round 10's one attach rule: a record's corrections attach when its text was available at or before the situation.
  A shown record is always a candidate, so the rule holds for every line; if it ever fails (only a time that does not
  parse can make it fail), the line is withheld and counted.
* A correction is no opposition edge: MAY_OPPOSE, the supersede claims and the hidden opponents stay v2.2's. An
  event's text is never read for corrections.

Telemetry: `corrections_shown`, `corrections_more`, `correction_stage` (0 full notes, 1 notes of 40 characters, 2
marks alone, None without a mark), `withheld_unproven`, `malformed_stamps`; `line_bytes` is measured with the marks.
Arms `v23a` and `v23ca`; a v2.2 arm name is refused; every answer names its arm and `remember-v2.3-dev.0`. The grader
contract (`RESULT_KEYS`) is unchanged: `correction` stays in the answer, as `may_oppose` does.

Measured on the synthetic fixtures (v2.2 then v2.3 card bytes, of 2048): the head card 867 then 997 (one full mark,
130 bytes); two wide corrected lines 1851 then 1991 (notes of 40 characters); three wide corrected lines 1851 then
1935 (marks alone); a corrected line that fits v2.2 by 10 bytes, 2038 then 1536 (its group skipped whole); a shown
extra 1688 then 1774.

## Where fable-5's design differs from the proposal

* Two or more dates and no lead date: the correction is kept, `undated`. R8 drops it, which hides it from the card,
  the very risk round 8 names.
* The date right after the lead keyword is the correction's own day, so a correction that also names the date of the
  claim it corrects keeps its day (R8 counts every date in the paragraph).
* A day that is not a calendar day, or that is later than the day its record became available, is no date.
* A phrase that names the head or opening claim corrects the head (R8 gives every phrase the line scope).
* One mark per line instead of every mark newest first: 198 of the 283 measured lines carry no date to sort by, and
  one mark bounds the cost at one note of at most 100 characters a line.
* The summary is never shortened (R8 shortens it first): it is the claim that matched, and MAY_OPPOSE reads it.
* A third packing stage, the mark alone (R8 drops the group after the 40-character stage).
* One attach rule, the record's available time. R8 also attaches on the correction's own date, which is the writer's
  claim, and keeps separate live and replay rules; rule v1 already admits a text only when it existed before the
  situation.
* Brackets in a note shown as parentheses, a guarded note replaced by fixed words, and an unclosed frontmatter read
  as body (R8 does not say).

## Run

```
python -B -m pytest sandbox/mind/remember/test_corrections.py sandbox/mind/remember/test_remember_v23.py -q -p no:cacheprovider
```

In code: `RememberV23(q4, "v23a").shortlist(situation, situation_time_utc, [situation])`, then
`render_shortlist(case_id, situation, listed)` for the joiner and `apply_picks(listed, picks)` for the answer. The
finder alone: `corrections.corrections(text, available)` gives a text's corrections and its malformed stamp count.

## Tests (synthetic)

`test_corrections.py` (20 tests, 60 cases) pins every finder rule: the lead forms and its note, upper case and line
start only, a lesson about mistakes (R8 test 3), phrase and self-deixis, the frontmatter skipped and an unclosed one
read, line ends, exact stamps, a stamp winning over legacy text (R8 test 4), malformed stamps without a legacy
fallback (R8 test 8), the lead's own day, two dates kept undated (R8 test 5, changed), one date, days that are not
real or come after the record, one correction per paragraph, the note, the selection and the attach rule.

`test_remember_v23.py` (18 tests, 22 cases) builds each card twice. Each fixture is a measured precondition: the
v2.2 card of the same memory is built first, shows the defect, and every asserted mark was measured to fit in its
stage before the test was written. The cases:

* a corrected head claim with its full mark, every other line byte for byte v2.2's;
* a lesson about mistakes, and three step-2 situations in both arms: v2.2's card byte for byte;
* notes shortened to 40 characters, then marks alone, with no topic dropped;
* a group that does not fit even with marks alone, skipped whole;
* an extra that carries its own correction, with MAY_OPPOSE unchanged; an opposite note that makes no MAY_OPPOSE;
* a guarded note withheld while its line is shown; a note that cannot forge a mark;
* a record written after the situation, which shows nothing (R8 tests 1 and 7);
* a record whose correction cannot be proven prior, which is withheld;
* the last head correction with `(+n more)`, and a line's undated correction without HEAD;
* a malformed stamp counted; CAP binding while the bytes would allow more;
* the arm check and the answer's version and correction;
* a crowded memory in both arms where every shown lesson with a correction carries its mark (R8's safety gate);
* only lesson lines read for corrections.

Provenance: the tests were written before the code. Against a skeleton, 79 cases failed and 1 passed: the record
written after the situation, which v2.2 already shows nothing of. The first run against the code passed all 80 cases
unchanged. Added after that run, for rules no RED test pinned on a card: one test (the malformed-stamp count), one
assertion and one case (a line's undated correction without HEAD). Mutants: 31 named mutants, 17 of the finder and 14
of the engine. Run 1 killed 30; the survivor removed v2.3's CAP check, and no fixture let CAP bind. One test was added
for it, its fixture measured first. Run 2 ran on the committed bytes with a passing control (82 cases) and killed it.
The 30 kills of run 1 ran on the same module bytes; the test files have only gained that test and docstring text
since, and a kill stays a kill when tests are only added. The harness and outputs are in fable-5's audit folder
(`fable-5-corrections-20261018/mutants_r1.out.txt`, `mutants_r2.out.txt`).

## Known limits (stated, not hidden)

* The finder's precision is unmeasured. Round 8's next step is a report over the store and 40 files labelled by a
  non-author with pre-registered labels; neither was run here (no real data in this round).
* A correction beyond the stored text is never seen: the step-2 corpus keeps the first 4000 characters of a file, up
  to any guarded word. RCO2 counted 83 files whose first correction marker lies beyond 4000 characters. Raising that
  limit is a corpus decision (Lead), not this module's.
* HEAD for a legacy lead is R8's convention, not verified; a note shows only the correction's first words; the day is
  the writer's claim. A correction in a markdown heading or block quote is not a lead, and an MM-DD range or version
  number can be read as a day.
* Room: one full mark costs about 30 bytes plus its note (130 for a full note of 100 ASCII characters), so a crowded
  card shows marks without notes, and a corrected line that only just fitted v2.2 can lose its group.
* One engine answers one question at a time: its claims, candidates, fence groups and situation time live on the
  instance, as in v2, v2.1 and v2.2.
* No runner, manifests or freeze exist for v2.3, it has never seen a blind set, and it is not wired into the join
  card. Whether it helps the joining agent is UNKNOWN until a non-author measures it.
* Every run used fable-5's lane token, which is High (elevated). These are pure-Python synthetic tests with no
  privilege-dependent path, but no medium-token run was made.
