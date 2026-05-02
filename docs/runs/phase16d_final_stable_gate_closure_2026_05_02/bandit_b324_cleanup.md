# Phase 16D P2 — Bandit B324 weak-hash cleanup

## Goal

Resolve the 16 Bandit B324 weak-hash findings inherited from Phase 16C (all in non-inner-loop cache / dedup / content-addressing code) by adding `usedforsecurity=False` to every affected `hashlib.md5(...)` and `hashlib.sha1(...)` call. Verify persisted semantic fingerprint preservation per master prompt RULE 25 before keeping any change.

## Pre-change parity sanity check

`hashlib.md5(payload, usedforsecurity=False)` and `hashlib.sha1(payload, usedforsecurity=False)` produce **byte-identical digests** to their default-flag counterparts on Python 3.11+. This was verified in-session against 11 representative input groups (33 total samples) drawn from each of the 16 call sites' input shapes:

```
Python: 3.13.7
ALL DIGESTS IDENTICAL: True
```

Therefore adding `usedforsecurity=False` cannot alter persisted IDs / fingerprints / cache keys — it only sets a metadata flag that informs OpenSSL FIPS mode and Bandit's lint heuristic.

## Per-finding classification and fix

| # | file:line | hash | purpose | persisted? | fix | digest preserved? |
|---|---|---|---|---|---|---|
| 1 | `core/embedding_cache.py:115` | md5 | LRU dict cache key | NO (in-memory) | `usedforsecurity=False` | ✓ verified |
| 2 | `core/embedding_cache.py:154` | md5 | LRU dict cache key (batch) | NO | `usedforsecurity=False` | ✓ verified |
| 3 | `core/embedding_cache.py:190` | md5 | LRU dict cache write key (batch) | NO | `usedforsecurity=False` | ✓ verified |
| 4 | `core/embedding_cache.py:319` | md5 | LRU dict cache key (eval embed) | NO | `usedforsecurity=False` | ✓ verified |
| 5 | `core/embedding_cache.py:342` | md5 | LRU dict cache key (eval batch) | NO | `usedforsecurity=False` | ✓ verified |
| 6 | `core/embedding_cache.py:377` | md5 | LRU dict cache write key (eval batch) | NO | `usedforsecurity=False` | ✓ verified |
| 7 | `core/hive_support.py:103` | md5 | `query_hash` field in JSONL audit log (truncated to 12 hex chars) | YES (log) | `usedforsecurity=False` | ✓ verified |
| 8 | `core/knowledge_loader.py:364` | md5 | file fingerprint `path:size:mtime` for knowledge cache | likely cache | `usedforsecurity=False` | ✓ verified |
| 9 | `core/learning_engine.py:645` | md5 | response dedup hash in `_seen_hashes` set + JSONL log (truncated to 12) | volatile + log | `usedforsecurity=False` | ✓ verified |
| 10 | `core/night_enricher.py:624` | md5 | exact-text dedup in `_seen_hashes` set | volatile | `usedforsecurity=False` | ✓ verified |
| 11 | `core/whisper_protocol.py:245` | md5 | content fingerprint → hieroglyph visual encoding | possibly persisted | `usedforsecurity=False` | ✓ verified |
| 12 | `core/whisper_protocol.py:298` | md5 | `whisper_id` (truncated to 12 hex chars) used as record ID | YES (persisted) | `usedforsecurity=False` (multi-line form) | ✓ verified |
| 13 | `waggledance/adapters/feeds/feed_ingest_sink.py:144` | md5 | `doc_id` segment (truncated to 8 hex chars) | YES (persisted) | `usedforsecurity=False` | ✓ verified |
| 14 | `waggledance/adapters/memory/chroma_vector_store.py:171` | md5 | embedding cache key | volatile | `usedforsecurity=False` | ✓ verified |
| 15 | `waggledance/observatory/mama_events/consolidation.py:159` | sha1 | `fingerprint()` for replay determinism (truncated to 16 hex chars) | YES (replay test) | `usedforsecurity=False` | ✓ verified |
| 16 | `waggledance/observatory/mama_events/taxonomy.py:103` | sha1 | event ID (truncated to 16 hex chars) | YES (persisted) | `usedforsecurity=False` (object-form constructor) | ✓ verified |

**Total fixed: 16 / 16.** All fixes preserve digest output.

## Bandit re-run after cleanup

```
python -m bandit -r waggledance/ core/ -f json -o docs/runs/phase16d_final_stable_gate_closure_2026_05_02/bandit_report_after.json
```

| severity | count BEFORE (Phase 16C) | count AFTER (Phase 16D) |
|---|---|---|
| HIGH | **16** | **0** |
| MEDIUM | 28 | 28 |
| LOW | 226 | 226 |

**B324 findings: 16 → 0. HIGH severity findings: 16 → 0.**

The 28 MEDIUM and 226 LOW findings are unchanged — they were classified in Phase 16C and remain accepted residuals (mostly `B615 huggingface_unsafe_download` model-loading lints in NLP/translation lanes, `B110 try/except/pass`, `B105` false-positive matches against threshold values like `"OK"`, `"green"`, `"0.85"`).

Inner-loop reachability for the remaining MEDIUM and LOW findings is identical to Phase 16C: **0 HIGH and 0 MEDIUM reachable from autonomy inner loop**; the 9 LOW inner-loop findings are all false-positives.

## CI grep equivalent re-run

```
grep -rn "eval(" --include="*.py" core/ waggledance/ \
  | grep -v safe_eval | grep -v "# noqa" | grep -v "test_" \
  | grep -v "\.eval()" | grep -v "retrieval(" | grep -v "evaluate(" \
  | wc -l
→ 0
```

Clean.

## Stable gate disposition

* **g05 Security audit / Bandit:** **PASS.** Bandit HIGH count = 0 under strict no-high reading. Inner-loop HIGH/MEDIUM = 0. Phase 16C's residual cleanup task is closed.
* **g21 Persisted semantic fingerprint preservation:** see `persisted_semantics_preservation.md`.

## Conclusion

All 16 Bandit B324 weak-hash lint findings are resolved. Bandit HIGH count is now 0. The fixes are minimal (one parameter added per call site) and provably digest-output-preserving on Python 3.11+. No persisted ID, cache key, or fingerprint changed semantically.
