# REMEMBER reachability audit (sandbox, read-only, advisory, never a gate)

Status: **sandbox dev, synthetic tests only.** Nothing here was run on real, sealed or frozen data, there is no blind
run, and nothing here is a release, a lock, a code-go or a Q4 candidate: Q4 stays on 6cf81dbb (REMEMBER v2.1.1, Lead's
selection), and the audit edits no engine, step-2 or Q4 file. Authored by fable-5 on 2026-10-18 under the operator's
order to polish the Mind in the sandbox (fable-5's diamond round D8, from RCO2's diamond round 4 part B and round 10,
and round D8.1, the fixes from Lead's reconciliation of D8; fable-5 decided the design). Grok was not consulted in
either round: Lead's Grok hold stands. What it measures is own-word, masked-probe reachability on synthetic memories:
not a blind Q4 recall, not a cause of crowding, not parity with the real e5 encoder and not utility.

## What it answers

For every lesson L in a memory: does a question that asks for what L teaches reach L on the joiner's card, and when it
does not, who crowds it out? A lesson that no question reaches is memory the joiner never sees, and a blinded recall
number alone cannot say which lessons those are or what stands in front of them (round 4 part B). Round 10 orders this
audit first, read-only, on the current memory, run by a Medium non-author.

`reach_audit.py` builds and changes nothing: it reads a loaded step-2 memory, asks an engine arm's own shortlist one
question per lesson, and writes one report.

## How a lesson is audited

Per lesson L, in ref order:

1. **Availability.** `not_available`: L did not exist before the time (rule v1's guard band). `stored_text_cut`: the
   corpus cut L's stored text at a guarded line, so its sections may be gone.
2. **The probe**: the LAST sentence of L's How-to-apply section(s) that has at least 4 words and names
   neither L's file stem nor its frontmatter name (case-folded), nor a `[[link]]`, nor a `.md` file: the line a reader
   acts on, in L's own words. A section starts at a `**How to apply:**` marker (any case) at a line start after the
   frontmatter and runs to the next `**<label>:**` marker; a sentence ends at `.`, `!` or `?` before whitespace, at a
   blank line, or before a list item (whose mark is not part of it). No probe: `no_probe`. A probe that the decoded
   guard trips: `probe_guarded`, never asked.
3. **The mask.** The probe span is always cut out of L. The question is asked over a view of the loaded memory that
   equals a memory rebuilt with L's stored text without the span: L's w row encoded again and normalised as a build
   stores it (kept when the encoder's window shows that it reads the same text), BM25 rebuilt, rule v1's self-match
   recomputed for L alone; the loaded memory itself never changes. When the cut L still self-matches the probe (it
   says the same thing elsewhere), no such question can reach it: `probe_not_clean`.
4. **The question.** The arm's own engine, built over that view, answers the probe as the situation at the time, with
   the probe as its one query. A card that lists nothing is `card_empty` (status EMPTY) or `card_refused` (REFUSED),
   with the engine's reason, never `unreachable`; any other card status is refused. On a listed card, L is `reachable`
   when it is one of its lines, else `unreachable`. The card is the verdict; the ranks and the card's packing below
   describe it and do not establish a cause (Known limits).

An audited row carries: the probe's sha256, word count and qualifying count, never its text; the card's status, reason
(the engine's own; None on a listed card) and line count, L's line, head flag and score (the engine's own fused score;
None off the card), and the card's `packing`: the engine's own telemetry `stopped_by`, `skipped_groups` and
`withheld_by_guard` (None where the engine has no such key: v2 and v2.1 skip no group, v2.2 and v2.3 skip groups
instead of stopping, a refused card carries no telemetry; v2 and v2.1 also say None when nothing stopped); L's rank in
each view (w, d, b); `fused`, L's reciprocal-rank fusion (k 60) of the three views as the engines fuse, and
`fused_rank`, L's place among every candidate by it, ties by id; `crowding`, how many records, lessons and events fuse
before L, and `crowders`, the first 10 of them (a file, or an event_ref); `topic_on_card`, whether a shown line other
than L is on L's topic (summary cosine at or above the engines' `TOPIC_COSINE`).

The report: one row per lesson (refs, statuses and numbers, never any record text), the counts per status (every
status, the unasked and the card statuses included), the `unreachable` lessons (never a `card_empty` or
`card_refused` one) by crowding (most first) then ref, and the near-duplicate clusters among the available lessons
(union-find at summary cosine at or above `PAIR_COSINE`). It passes the decoded guard or is refused. The command line
adds the memory's sha256 and the code's git blobs, writes one new file (never overwrites one) and prints the counts.

## Design choices (fable-5's, with reasons)

* The probe is ALWAYS masked. Round 4 masks only a lesson with one qualifying sentence; left in, the probe is a
  verbatim span of L, and rule v1's self-match removes L from its own question. Measured on the fixtures: a lesson
  with two qualifying sentences is no candidate for its own unmasked probe, and it is reachable once masked.
* The last qualifying sentence, as the line a reader acts on (which sentence asks best is not measured). A sentence
  that names the lesson, a link or a file would find L by its name instead of by what it teaches, so it never
  qualifies.
* Reachable means on the arm's own card, never a rank threshold of the audit's own: the audit cannot disagree with the
  engine about what the joiner sees. The fused rank, the crowders and the card's packing describe what stood before L
  and what the card held back; they do not prove why L is off the card.
* A card that lists nothing has its own status (D8.1). D8 counted an EMPTY or REFUSED card as `unreachable`, so its
  counts mixed a card that showed nothing with a card that showed others; a row now keeps the engine's reason, and a
  card status the audit does not know is refused, never counted.
* Crowders are counted in full and named up to 10, by ref only, so a report of a real memory carries no
  record text. The refs still identify notes and events: such a report is not public-safe.
* `topic_on_card` separates two losses: L is gone but its topic is still on the card, or the topic is gone with it.
* Every lesson the audit does not ask gets a named status that says why, and the counts cover every lesson.
* Only the one-query arms are audited (v2a, v21a, v22a, v23a): the probe is the whole situation. Any other arm is
  refused before a lesson is read.

## Run

```
python -B sandbox/mind/remember/reach_audit.py --memory <memory dir> --arm v21a --time <aware UTC time> --out <new file>
```

In code: `audit(q4, arm, time_utc, passage, window)`, where `passage` encodes one cut lesson's text as the memory's w
view was encoded (`load_memory` gives the e5 one and its window); `probe_of(text, file)` gives one lesson's probe.
Who runs it on real data: round 10 names a Medium non-author. fable-5 wrote REMEMBER v2.1 to v2.3 and this audit, runs
it on synthetic data only, and this module never chooses a memory. A real run's report belongs in that runner's audit
folder, never in this public repo.

Tests: `python -B -m pytest sandbox/mind/remember/test_reach_audit.py -q -p no:cacheprovider`

## Tests (synthetic)

`test_reach_audit.py` (26 tests, 48 cases) builds small step-2 memories with a collision-free vocabulary
encoder (one dimension per word, so a cosine is exactly the word overlap), each memory once per run. Each fixture is a
measured precondition: the engines' own cards over a memory rebuilt with the probe span removed were measured before
the assertions were written (rounds x1 to x5 in fable-5's D8 audit folder, round x2 in its D8.1 one), so no expected
status comes from the audit's own code. The cases:

* the probe: the last qualifying sentence with its exact span (LF and CRLF), the exclusions, no section, a section
  that ends at the next marker, markers in any case, a sentence over several lines, a stop, a blank line or a list
  item that ends a sentence, and several sections;
* the masked view equal to a rebuilt memory in every arm (texts, vectors, BM25 scores, self-match on eight situations,
  the card), the encoder window, and a loaded memory unchanged after an audit;
* round 4's unmasked lesson, no candidate for its own probe (the measured precondition), reachable once masked;
* a reachable lesson that heads its topic in every arm, its fused score equal to the card's own score, with no card
  reason and the arm's own packing;
* a lesson crowded out by four lessons in every arm (fused rank 5, its crowders named), and by twelve (all twelve
  counted, 10 named);
* views that disagree (w 2, d 1, b 2) and still fuse first; a lesson shown under its twin's head, with its topic on the
  card; an event that fuses first, counted as an event by its event_ref;
* the unreachable lessons sorted by crowding (eight before six), then by ref on a tie;
* every other status named and counted (the two card statuses at 0); one row per lesson in ref order with no record
  text, and the constants;
* D8.1: a card that lists nothing (the lesson's line withheld by the guard, no other candidate at the time) is
  `card_empty` with reason `nothing_listable` in every arm, never unreachable; the same withheld line beside other
  candidates is `unreachable` with fused rank 1 and no crowding, and the packing counts the withheld line; a refused
  card (a fixed one: no fixture makes a real engine refuse after rule v1 admitted the probe) is `card_refused` with
  its reason, and an unknown card status is refused;
* an unknown arm or a time that is not an aware UTC time, refused before any lesson is read;
* near-duplicate clusters chained through a middle lesson, among the available lessons only;
* the command line: one report with the memory's hash and the code's blob, never overwritten, a bad time refused
  before the memory is loaded, an unknown arm refused by the parser.

Provenance: the tests were written before the code. Against a skeleton, RED run 1 failed all 31 cases with
NotImplementedError; three text-only probe cases were then added, before any code (a CRLF copy of the first test,
the sentence ends, several sections), and RED run 2 failed all 34. The first run against the code passed all 34
cases unchanged. After it, to tell apart planned mutants that the suite could not, 2 tests (5
cases: views that disagree, the twin's head) and assertions in earlier tests were added, each marked "added after GREEN
1" (or 2), with their fixtures measured first; two report fields, `fused` and the card's `score`, were added to the
code together with the assertions that read them, which therefore never ran RED. GREEN runs 2 and 3 passed all
39.

D8.1 (Lead's reconciliation of D8: a card that lists nothing was counted as unreachable, and a row could not show
what its card held back): the fixtures were measured first (round x2 of the D8.1 audit folder; its round x1 measured
nothing), and every test line marked "D8.1" was written before the code change. Against the D8 code, 14 cases
failed (8 on the missing card reason, 1 on the counts without the two card statuses, 5 on `unreachable` where a
card that lists nothing was expected) and the other 34 passed. The first run against the D8.1 code passed all 48.

Mutants, per run. Each mutant is one exact text change of a scratch copy, KILLED only when pytest exits 1, and every
run starts with a passing unmutated control. On the D8 blobs: 42 named mutants (16 of the probe, 9 of the masked view
and self-match, 15 of the audit, 2 of the command line). D8 run 1 (control 39 cases) killed all 41 mutants it
executed and then stopped at its harness's 50 MiB byte limit, before the last mutant (C02_time_checked_late); D8 run
2 ran that mutant alone, with only the test that calls the command line and a control of that test, and killed it.
Neither D8 run is an uninterrupted run of every mutant against the whole test file. On the D8.1 blobs: 14 named
mutants of the D8.1 change (the card status mapping, the unknown-status refusal, the reason, each packing key and
where it is read, the counted statuses, the unreachable list). D8.1 run 1 (control 48 cases) killed 14 of 14. The
combined run, one uninterrupted run of all 56 mutants (the 42 D8 and the 14 D8.1 ones), each against the whole
test file (control 48 cases), killed 56 of 56. The harnesses and their outputs are in fable-5's audit folders
(`fable-5-reach-audit-20261018/mutants_r1.out.txt` and `mutants_r2.out.txt`,
`fable-5-reach-audit-v01-20261018/mutants_d81_r1.out.txt`, `fable-5-reach-audit-v01m-20261018/mutants_all_r1.out.txt`).

## Known limits (stated, not hidden)

* Synthetic memories only: the audit's own precision and recall are unknown. A reachable lesson means that one
  question in the lesson's own words reached it; a real situation words it otherwise (round 4's vocabulary gap is not
  measured here).
* One probe per lesson. A lesson without a qualifying How-to-apply sentence is never asked, and a How-to-apply past the
  stored text (the step-2 corpus keeps the first 4000 characters of a file, up to any guarded word) is `no_probe`.
* Own-word, masked-probe reachability: not a blind Q4 recall, not parity with the real e5 encoder (the tests use a
  collision-free vocabulary encoder) and not utility.
* `crowding` and `crowders` are the records fused before L, not a cause: the card's packing (its cap, its card-line
  bytes, a skipped group), the topic-group show and the guard can each keep L off a card (a test shows an
  `unreachable` lesson with fused rank 1 and no crowding), so counts alone do not establish a retrieval loss; an
  `unreachable` row is read with its `fused_rank` and `packing`.
* Records that hold the probe verbatim are excluded by rule v1 for that question, so they never count as crowders.
* A report names lessons by file and events by event_ref: identifying metadata, so a report of a real memory is not
  public-safe even though it carries no record text.
* One engine is built per lesson over the whole memory: slow on the real memory (not measured).
* Every run used fable-5's lane token, which is High (elevated). These are pure-Python synthetic tests with no
  privilege-dependent path, but no medium-token run was made, and no CI run (no PR: the diamond lands once).
