# M0-lite over existing objects: specification v1

Author fable-5 (S14 2b Source GO, 2026-10-27; fix round of the same day after RCO2 F1/F2 and four advisories, RCO1
concurring). Base main ab22e4a0. A specification only: no case, gold, memory, model,
run or grade was read, built, executed or scored while writing it. The Mind stays advisory; nothing here computes a gate verdict.

## 1. Purpose and limits
- Purpose: ONE small, fixed command graph over the EXISTING frozen hex-memory objects and ONE deterministic hash judge, so
  that a future, separately authorized run can show whether the memory returns the right existing record for a situation.
- Limits: no new component, adapter or framework; no model transport, network, native code, runtime, data or writer; no
  REMEMBER arm; no grader lane. A grep, a document lookup or a toy self-test is NOT a Mind Q4 benefit and is never reported as one.
- Where the existing objects cannot carry a stage, that stage is marked MISSING with an owner. Nothing is invented to fill it.

## 2. Measured revision and object graph (public git objects only; blob ids are the git blob SHA-1 at that revision)
- Revision: 6cf81dbbefe2b0e19a4c8306100beb30784ac4e4 (sandbox/mind, 2026-10-18; NOT an ancestor of main; the frozen Mind head).
- sandbox/mind/step2/corpus.py  9aa057465f95c12b72ff3a10f7e32215c8b93228: build(lessons_dir, capture_paths, cutoff, out_dir)
  -> corpus (schema wd.bridge-mind-hex-corpus.v2); record = {record_id, kind lesson|event, time_utc, text, summary}; ref =
  lesson basename or ts_utc|agent|type|task_id.
- sandbox/mind/step2/encode.py  5d9cfad31b861e32f0ac8cb14e46aeefb588ef09: build(corpus_dir, out_dir) -> vectors
  (wd.bridge-mind-hex-vectors.v2; E5 small, views w and d); model_identity() = HF revision + model.safetensors sha256 (file hash).
- sandbox/mind/step2/memory.py  dbe927e874d0b42d53c06cdee465d878e4f79dbd: build(corpus_dir, vectors_dir, out_dir) -> memory
  (wd.bridge-mind-hex-memory-step2.v1) over waggledance/core/hex_cell_topology.py 40b5b3a72bb95f6f46e870532c749f6422dd9298;
  memory_sha256(out_dir).
- sandbox/mind/step2/cell.py  8e52cc70cd7926a823c736b734382c5fe2b6c81d: generation files, publish, verify_generation, CellView.search.
- sandbox/mind/step2/q4.py  eb457381658dce37b6742ef55f99d35593af7f97: Q4.ask(situation, situation_time_utc) -> {status, abstain,
  degraded, results[<=3]{ref, score, doc_id, kind, cell, time_utc, tier, fallback}}; RULE q4-rule-v1 (views w, d, b; rrf_k 60).
- sandbox/mind/step2/run_q4.py  5fa8f06c12256ef3073e432bae8d262586db4ade: run(memory_dir, cases_path, out_dir, kind) reads only
  id, situation and time per case; writes outputs.jsonl (RCO2 grader v1.2 contract), diagnostics.jsonl, run-manifest.json
  (case file sha256, output sha256s, memory sha256, rule, git blob id of every step-2 source). Blind by construction.
- Graph (literal, fixed, in this order): corpus.build -> encode.build -> memory.build -> run_q4.run -> judge (section 4).
  Every stage records its command line, input sha256s, output sha256s, exit code, wall seconds and bytes written.
- Fact that bounds the graph: run_q4.py imports encode at module load, and run_q4.run and q4.main construct the E5 encoder
  and the Memory unconditionally and hand them to Q4, so the graph runs only where the frozen encoder cache and a built memory
  exist. No public entry runs q4.Lexical alone; a lexical-only path over existing objects is MISSING (section 5, row 2).

## 3. Cases and gold (GOLD_SCHEMA_UNBOUND)
- The run reads cases in run_q4's "fresh" shape: cases[]{id, situation, situation_time_utc}. Expected answers live in a separate
  sealed file the run never opens: expected[]{id, expected_ref | null, expect_abstain}. The sealed file's sha256 is recorded
  in the lock before the run and the judge reads it only after outputs.jsonl exists.
- GOLD_SCHEMA_UNBOUND: no public case schema at the measured revision binds expected_ref or expect_abstain; until one does,
  the expected file is this spec's private shape and no claim about "gold" validity is made beyond its sha256 and author.
- No case is constructed, selected, read or tuned by this spec. The case source is Root's to select and lock (row 1).
- Proposed repo-side variant (PROPOSAL only, no construction): a public failure-record schema
  failure_record.v0 = {id, observed_utc, lane, pr, head, defect_name, situation_text, lesson_ref, source_event_ref, sha256}
  built by a future deterministic extractor from the public compact bridge dumps of 2026-10-26 (the five defects of that day:
  hex index abbreviation, API-vs-git diff comparator, PS5-vs-PS7 rendering, git dubious-ownership exit 128, dormant v2 port
  import). The extractor does not exist; it is MISSING with owner Root. Nothing of it was built here.

## 4. The hash judge (deterministic; reads two files; no model, no transport, no network)
- Inputs: outputs.jsonl from the run and the sealed expected file. Output: judge.json plus judge.sha256.
- Expected rows, classified before any output is read: positives = rows with expect_abstain false and a non-null expected_ref;
  negatives = rows with expect_abstain true and expected_ref null; every other expected row (null ref with expect_abstain false,
  non-null ref with expect_abstain true, a missing or malformed field) is a file ERROR for that id. The counts of positives
  and negatives are fixed by the expected file alone and written to judge.json before any output row is scored.
- Status map (complete, fixed before any run; the run emits exactly these five statuses, q4 and run_q4 blobs in section 2):
| status | positive case | negative case |
| ANSWER | HIT if expected_ref is among the first k refs, else MISS | MISS |
| ABSTAIN | MISS | ABSTAIN_OK |
| DEGRADED | DEGRADED_MISS (never HIT; q4 rule 5: never a clean ANSWER) | DEGRADED_MISS (never ABSTAIN_OK) |
| REFUSED | ERROR (a harness or case defect, never an ordinary MISS) | ERROR |
| CRASH | ERROR | ERROR |
  Any other status, a missing status, or crash true with a status other than CRASH is ERROR. An id present in one file and
  absent in the other, a duplicate id in either file, or a missing required field is ERROR for that id.
- Canonicalization: every JSON object is re-serialized with sorted keys, no whitespace, UTF-8, ensure_ascii false. Refs are
  compared as exact bytes: no NFC, no CRLF rewriting, no case folding (refs are basenames or ts|agent|type|task_id strings
  from one corpus; normalizing could only merge distinct refs). Score is never compared, only used for ordering.
- Ordering: results sorted by (score descending, ref ascending) before truncation to k = 3; a missing score sorts last;
  results with an equal ref keep the first after ordering.
- Timestamps: one canonical representation, corpus.utc_text (%Y-%m-%dT%H:%M:%S.%fZ, six fractional digits, UTC). The judge
  parses situation_time_utc and every result time_utc as aware UTC instants and compares instants, never strings; a case
  time that does not parse is ERROR. A result instant later than the case instant is a LEAK and the case is MISS regardless
  of ref and status, unless the case is already ERROR.
- Per case exactly one of HIT, MISS, DEGRADED_MISS, ABSTAIN_OK, ERROR. judge.json holds the per-case table, the fixed counts,
  the sha256 of both inputs and of this spec's blob. Two runs of the judge on identical inputs must be byte-identical.
- Metrics over the fixed pre-output denominators (ERROR stays in both denominators, so a refusal or crash can only lower a
  metric, never raise it): recall_at_3 = HIT / positives; abstain_specificity = ABSTAIN_OK / negatives (abstention recall,
  not precision); degraded_count; leak_count; error_count. Division by zero -> null.

## 5. Seven-binding table (DONE only with an existing path, hash or revision; otherwise MISSING with owner)
| # | binding | state | owner | what would fill it |
| 1 | selected frozen case builder | MISSING | codex-lead-1 selects | a locked case source in the section-3 shape; or the proposed extractor |
| 2 | literal complete command graph | MISSING (proposed in section 2) | codex-lead-1 locks; Tools CLI facts | private paths, interpreter and executor identity bound to the section-2 blobs |
| 3 | memory identity and encoder receipt | MISSING | the custodian Lead binds | encode.model_identity() plus memory_sha256 produced read-only by the custodian |
| 4 | sealed sample receipts | MISSING | case custodian after row 1 | sha256 of the case file and of the expected file, written before the run |
| 5 | resource peak and enforceable allowance | MISSING (NOT_MEASURED) | executor measures, Root adjudicates | no guessed allowance here; a cap, if Root sets one before the lock, is a PROPOSAL, never DONE or observed until the run receipt |
| 6 | judge transport | MISSING at 6cf81dbb; specified in section 4 | this spec (author fable-5); Root adjudicates | the section-4 judge as a file reader; no transport |
| 7 | environment digest | UNKNOWN (10-26 tooling failure) | codex-lead-1 | python version, platform, blob ids and memory sha256 in run-manifest.json |

## 6. Hypothesis, metric, bar, sample, ending rule, snapshot, lock, roles
- Hypothesis H1 (prospective, not a prediction): on the locked case set, over the fixed denominators of section 4,
  recall_at_3 >= 0.60 and abstain_specificity >= 0.50 with leak_count = 0 and error_count = 0 (an error or refusal can never
  improve the result). Context: Q4 alone was 3/9 on the older sealed set; the bar is set above that on purpose.
- Metric: section-4 metrics only. Sample: every case the locked source yields; no selection after any result is seen.
- Ending rule: one run of the graph and one run of the judge; no rerun after a result; a refusal, a hook refusal or a crash is
  the result and is reported as such with its records.
- Snapshot and lock (before the first run): this spec's blob id, the section-2 blob ids, the case file sha256, the expected file
  sha256, the memory sha256 and the encoder identity, written to one lock file whose sha256 is posted on the bridge. Any
  change after the lock is a new pre-registration (dated addendum), never an edit.
- Roles: author fable-5 (never votes, never executes, never grades, recused from case selection); Root (codex-lead-1)
  adjudicates and locks any actual measurement; an independent executor and the judge script run only under roles Root accepts;
  both recognized RCOs read the lock, the receipts and judge.json.
- What a result means: a passing H1 is evidence about the frozen memory on the locked cases only; it is not a Mind activation,
  not a Stage-2 input and not a claim about any lane's recall in practice.

## 7. What this specification does not do
No file other than this one. No case or gold access. No run. No score. No new object. No runtime, data or writer. The seven
rows above stay MISSING or UNKNOWN until their owners post the named receipts; this spec creates no receipt by itself.
