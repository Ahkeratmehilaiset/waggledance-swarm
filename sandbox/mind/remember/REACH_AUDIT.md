# REMEMBER reachability audit (sandbox, read-only, advisory, never a gate)

Status: **sandbox dev, synthetic tests only.** Nothing here was run on real, sealed or frozen data, there is no blind
run, and nothing here is a release, a lock, a code-go or a Q4 candidate: Q4 stays on 6cf81dbb (REMEMBER v2.1.1, Lead's
selection), and the audit edits no engine, step-2 or Q4 file. Authored by fable-5 on 2026-10-18 under the operator's
order to polish the Mind in the sandbox (fable-5's diamond round D8, from RCO2's diamond round 4 part B and round 10;
fable-5 decided the design). Grok was not consulted in this round: Lead's Grok hold stands.

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
   the probe as its one query. L is `reachable` when it is a line of the card, else `unreachable`. The card is the
   verdict; the ranks below explain it.

An audited row carries: the probe's sha256, word count and qualifying count, never its text; the card's status and
line count, and L's line, head flag and score (the engine's own fused score; None off the card); L's rank in each view
(w, d, b); `fused`, L's reciprocal-rank fusion (k 60) of the three views as the engines fuse, and `fused_rank`,
L's place among every candidate by it, ties by id; `crowding`, how many records, lessons and events fuse before L, and
`crowders`, the first 10 of them (a file, or an event_ref); `topic_on_card`, whether a shown line other than L
is on L's topic (summary cosine at or above the engines' `TOPIC_COSINE`).

The report: one row per lesson (refs, statuses and numbers, never any record text), the counts per status, the
unreachable lessons by crowding (most first) then ref, and the near-duplicate clusters among the available lessons
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
  engine about what the joiner sees. The fused rank and the crowders say why.
* Crowders are counted in full and named up to 10, by ref only, so a report of a real memory carries no
  record text.
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

`test_reach_audit.py` (23 tests, 39 cases) builds small step-2 memories with a collision-free vocabulary
encoder (one dimension per word, so a cosine is exactly the word overlap), each memory once per run. Each fixture is a
measured precondition: the engines' own cards over a memory rebuilt with the probe span removed were measured before
the assertions were written (rounds x1 to x5 in fable-5's audit folder), so no expected status comes from the audit's
own code. The cases:

* the probe: the last qualifying sentence with its exact span (LF and CRLF), the exclusions, no section, a section
  that ends at the next marker, markers in any case, a sentence over several lines, a stop, a blank line or a list
  item that ends a sentence, and several sections;
* the masked view equal to a rebuilt memory in every arm (texts, vectors, BM25 scores, self-match on eight situations,
  the card), the encoder window, and a loaded memory unchanged after an audit;
* round 4's unmasked lesson, no candidate for its own probe (the measured precondition), reachable once masked;
* a reachable lesson that heads its topic in every arm, its fused score equal to the card's own score;
* a lesson crowded out by four lessons in every arm (fused rank 5, its crowders named), and by twelve (all twelve
  counted, 10 named);
* views that disagree (w 2, d 1, b 2) and still fuse first; a lesson shown under its twin's head, with its topic on the
  card; an event that fuses first, counted as an event by its event_ref;
* the unreachable lessons sorted by crowding (eight before six), then by ref on a tie;
* every other status named and counted; one row per lesson in ref order with no record text, and the constants;
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

Mutants: 42 named mutants (16 of the probe, 9 of the masked view and self-match, 15 of the
audit, 2 of the command line), each one exact text change of a scratch copy, KILLED only when pytest exits 1.
Run 1, on the committed blobs with a passing control (39 cases), killed all 41 mutants it executed and
then stopped at its harness's 50 MiB byte limit, before the last mutant
(C02_time_checked_late). Run 2 ran that mutant alone on the same blobs, with only the test that calls the command line
and a passing control of that test, and killed it.
The harnesses and their outputs are in fable-5's audit folder (`fable-5-reach-audit-20261018/mutants_r1.out.txt`,
`mutants_r2.out.txt`).

## Known limits (stated, not hidden)

* Synthetic memories only: the audit's own precision and recall are unknown. A reachable lesson means that one
  question in the lesson's own words reached it; a real situation words it otherwise (round 4's vocabulary gap is not
  measured here).
* One probe per lesson. A lesson without a qualifying How-to-apply sentence is never asked, and a How-to-apply past the
  stored text (the step-2 corpus keeps the first 4000 characters of a file, up to any guarded word) is `no_probe`.
* Records that hold the probe verbatim are excluded by rule v1 for that question, so they never count as crowders.
* One engine is built per lesson over the whole memory: slow on the real memory (not measured).
* Every run used fable-5's lane token, which is High (elevated). These are pure-Python synthetic tests with no
  privilege-dependent path, but no medium-token run was made, and no CI run (no PR: the diamond lands once).
