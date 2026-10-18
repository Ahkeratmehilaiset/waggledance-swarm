# REMEMBER v2.1 and LEARN v1 (sandbox, advisory, never a gate)

Status: **sandbox dev product, synthetic tests only.** Nothing here was run on real, sealed or frozen data, no blind
acceptance was run, and nothing here is a release, a lock or a code-go for REMEMBER v2.1. Authored by fable-5 on
2026-10-18 under the operator's direct order in fable-5's session ("finish REMEMBER and LEARN in the sandbox").

## What changed and why

| Part | Defect it answers (measured before the code) | What the agent now sees |
| --- | --- | --- |
| `MAY_OPPOSE` marks (`remember_v21.py`) | RCO1: on fresh pairs the true lesson was shown with its opposite UNMARKED in 80/80 v2a observations | `[MAY_OPPOSE <ref> reason=... cues=... shared=... ov=... pol=...]` on both lines, plus one gloss line; never a verdict |
| Date fence (`remember_v21.py`, `fence`) | Probe: a forged-date flood hid the true lesson in 8/20 v2 shortlists (13/20 code-only) | Group slots only from records that pass the fence; `[TOPIC g: n trusted, a..b; +u untrusted] FLOOD k from ...` and per-line `[FLOOD]`, `[CLAIMED_AFTER_AVAILABLE]`, `[BACKDATED]`, `[DATE_UNVERIFIED]` |
| LEARN v1 (`../learn_step.py`) | Nothing the Mind did changed with what happened after a card was read | `LEARN: similar <kind> wrong w of taken t (...; rows h1, h2)`, a correct-count line, lesson warnings ending `association only`, CANDIDATE drafts |

Every design came from one direct Grok xhigh consultation per part (answers in fable-5's audit folder: A opposites
050b9f27..., B dates 3580b6f3..., C LEARN acc2c4ae...). Those three calls were bare CLI and concurrent, not the
controlled helper; Lead recorded that as an admitted bypass, and all later consultations go through
`C:/Python/Invoke-WdGrok.ps1` only. fable-5 adapted each design; every adaptation is named in the module docstrings.

## Run

```
# REMEMBER v2.1 (blind.jsonl from `remember_v2.py prepare`; needs the e5 encoder and a built step-2 memory)
python sandbox/mind/remember/remember_v21.py shortlists --memory <memory> --rewrites <rewrites.json> --out <run>
python sandbox/mind/remember/remember_v21.py compose --memory <memory> --picks-v21a <picks.json> --out <run>

# LEARN v1 (RCO2's outcome result files; picks file {"picks": [{card_sha256, lane, shown, picked}]})
python sandbox/mind/learn_step.py fold --results <r1.json> <r2.json> ... [--picks <picks.json>] --out <state.json>
python sandbox/mind/learn_step.py notes --state <state.json> --lane <lane> [--card <card sha256>]
python sandbox/mind/learn_step.py order --state <state.json> --lane <lane> --shown A.md B.md C.md
```

Arms: `v21a` (the situation only) and `v21ca` (the situation and the joiner's rewrite); the arm is checked from the
queries. A state file is never overwritten and every read checks its `state_hash` first.

## Tests (synthetic, run before each commit)

* `sandbox/mind/remember/test_remember_v21.py`: the 22-row opposite table both ways, the 16 date-fence cases in both
  input orders, a built memory where v2 hides the true lesson behind a forged flood and v2.1 shows it, two forged notes
  below the flood size, the byte cap with marks, the runner.
* `sandbox/mind/test_learn_step.py`: no evidence from UNKNOWN or PRE_READ, min-n silence, harm before praise, medians,
  card-independent counts, rotation, lane isolation, one count per event, the daily cap, order-free replay and
  tamper refusal, line conflicts, CANDIDATE drafts, decay by window, lesson warnings that never promote, partial
  coverage, pick conflicts, the guard, the runner.
* Mutants: 18 LEARN and 11 fence mutants in memory, all killed by these tests (one test was strengthened after a
  survivor; harness and outputs in fable-5's audit folder, `finish/mind_mutants*.out.txt`).

## Review after the first push (bee845a6)

* Lead boundary probe: a fence row without `available` or `claimed` raised KeyError, and 9999-12-31 overflowed the
  far check. Fixed, with regression tests.
* Lead and Tools peer probe: a returned LEARN state shared the module's PARAMS dict. Fixed: every state is a fresh
  object tree; regression test.
* Controlled-helper Grok review (consultation 2af02293, six proposals, advisory). Each premise was checked against
  RCO2's source and the date-fence spec first. Adopted: a taken row outside RCO2's `[read, read + 7200 s]` window is
  no evidence, and a PRE_READ line no longer voids a card for lesson evidence. Not adopted: rewind-inside-a-wave as
  BACKDATED (it breaks spec case 14, and a name date before a later edit is normal here), and counting one event
  judged two ways (RCO2's aggregate makes that a conflict). Pinned as stated trade-offs: the same-hour lesson flood
  and the edited-old-lesson BACKDATED; their fixes would reopen the measured flood.

## Known limits (stated, not hidden)

* MAY_OPPOSE misses opposites with no shared content word and a summary cosine below 0.90, implicit conflicts with
  no cue or listed antonym, paraphrased negations outside the cue lists and Finnish outside the gloss table.
* The date fence trusts what this memory has: lesson `available_utc` is the file mtime (writer-controlled), the
  batch is the UTC hour of it, and every lesson shares one identity because the corpus keeps only a file name. Three
  real notes on one topic written in one hour therefore show as a FLOOD with one line, and an old lesson edited after
  a newer record on its topic is BACKDATED (a spare line at most). A writer who forges mtimes and stays under three
  notes can still bracket a note (the consultation's pinned residual). How often these false positives hit real
  memory is UNKNOWN until someone authorized measures it.
* LEARN trusts RCO2's proofs and cannot re-verify identities or heads; OTHER and NONE still rest on agent labels;
  a lesson warning is an association, not a cause; the thresholds are the consultation's, not calibrated on real
  outcomes. Drafts never enter memory without a review record.
* Not wired into the join card: the card frame belongs to its owners; LEARN notes and lesson order are offered as a
  pure interface (`notes`, `lesson_order`) for that later change.
