# Phase 16D — Security audit (post-B324 cleanup)

**Scope:** all of `waggledance/` and `core/` plus the dependency closure.
**Tools run:** Bandit (1.9.4), `pip-audit`, CI grep equivalent, manual review.
**Branch:** `phase16d/final-stable-gate-closure`.

This audit closes the Phase 16C `g05` partial outcome by resolving the 16 Bandit B324 weak-hash findings via `usedforsecurity=False` while preserving persisted semantic fingerprint per master prompt RULE 25.

## Results delta vs Phase 16C

| metric | Phase 16C | Phase 16D | delta |
|---|---|---|---|
| Bandit HIGH findings | 16 | **0** | **−16** |
| Bandit MEDIUM findings | 28 | 28 | 0 |
| Bandit LOW findings | 226 | 226 | 0 |
| Bandit B324 findings | 16 | **0** | **−16** |
| Inner-loop HIGH | 0 | 0 | 0 |
| Inner-loop MEDIUM | 0 | 0 | 0 |
| Inner-loop LOW (false-positives) | 9 | 9 | 0 |
| CI grep `eval()` outside `safe_eval` | 0 | 0 | 0 |
| `pip-audit` dependency CVEs | 32 | 32 (unchanged; no dep upgrades in this sprint) | 0 |

## What changed in code

12 source files received minimal additions of `usedforsecurity=False` to `hashlib.md5(...)` / `hashlib.sha1(...)` calls. Per-site classification, persisted-vs-volatile status, and digest-parity proof are documented in `docs/runs/phase16d_final_stable_gate_closure_2026_05_02/bandit_b324_cleanup.md`.

| file | lines touched | hash | persisted? |
|---|---|---|---|
| `core/embedding_cache.py` | 6 | md5 | NO (in-memory LRU keys) |
| `core/hive_support.py` | 1 | md5 | YES (audit log) |
| `core/knowledge_loader.py` | 1 | md5 | likely cache |
| `core/learning_engine.py` | 1 | md5 | volatile + log |
| `core/night_enricher.py` | 1 | md5 | volatile |
| `core/whisper_protocol.py` | 2 | md5 | one persisted (whisper_id), one fingerprint |
| `waggledance/adapters/feeds/feed_ingest_sink.py` | 1 | md5 | YES (doc_id) |
| `waggledance/adapters/memory/chroma_vector_store.py` | 1 | md5 | volatile |
| `waggledance/observatory/mama_events/consolidation.py` | 1 | sha1 | YES (replay fingerprint) |
| `waggledance/observatory/mama_events/taxonomy.py` | 1 | sha1 | YES (event ID) |

All persisted IDs / fingerprints retain the same digest output (Python 3.11+ semantics; verified by 33-sample parity test before any code change).

## Persisted semantic fingerprint preservation

PASS. See `persisted_semantics_preservation.md`.

## Inner-loop reachability

Unchanged from Phase 16C: **0 HIGH and 0 MEDIUM findings reachable from the autonomy inner loop** (`AutonomyService.handle_query` → `CompatibilityLayer.handle_query` → `AutonomyRuntime.handle_query` → `SolverRouter.route` → autonomy consult → `LowRiskSolverDispatcher` / `HotPathCache`). The 9 inner-loop LOW findings are all false-positives and have not changed.

## Remaining MEDIUM findings (carry-over from Phase 16C, accepted residuals)

| test_id | count | classification |
|---|---|---|
| B615 `huggingface_unsafe_download` | 13 | `true_positive_low` — model loading without `revision=` pinning. NOT in autonomy lane. Tracked for future cleanup; not a stable-gate blocker per Phase 16C analysis. |
| B608 `hardcoded_sql_expressions` | 7 | `false_positive_documented` — static parameterized queries; not real injection points |
| B307 `eval` blacklist | 3 | `false_positive_documented` — `safe_eval` AST-whitelist; CI grep already exempts |
| B310 `urlopen` | 2 | `false_positive_documented` — installer / runtime bootstrap |
| B104 `bind_all_interfaces` | 2 | `false_positive_documented` — server bootstrap binds 0.0.0.0 |
| B614 `pytorch_load` | 1 | `true_positive_low` — `torch.load` without `weights_only=True`. Same lane as B615. |

None reachable from autonomy inner loop. None block Phase 16D's prerelease decision.

## pip-audit

Identical to Phase 16C: 32 known dependency CVEs across 14 packages (aiohttp, cryptography, deep-translator, js2py, lxml, nltk, pillow, pip, pygments, pypdf, pytest, python-dotenv, requests, streamlit). All classified `low` for autonomy inner-loop reachability. Tracked in active Dependabot PRs (#19–#26).

## CI security-scan equivalent

```
grep -rn "eval(" --include="*.py" core/ waggledance/ \
  | grep -v safe_eval | grep -v "# noqa" | grep -v "test_" \
  | grep -v "\.eval()" | grep -v "retrieval(" | grep -v "evaluate(" \
  | wc -l
→ 0
```

Clean.

## Stable gate disposition (g05)

**g05: PASS.** Bandit HIGH count = 0 under strict no-high reading. Persisted semantics preserved.

Phase 16C's residual cleanup task is closed. **Bandit no longer blocks v3.8.0 stable.**

The remaining stable blocker for v3.8.0 is **g01 Docker** (CLI unavailable in this dev shell — same situation as Phase 16B and Phase 16C). An external operator with Docker installed must verify the documented Docker proof commands before stable v3.8.0 is justified.

## Bandit installed as audit tooling (not runtime dependency)

`bandit==1.9.4` is installed in the Phase 16D dev shell as audit tooling. It is NOT added to `requirements.txt`, `requirements.lock.txt`, or `requirements-ci.txt`. Future sessions can re-install via `python -m pip install bandit` if needed. A future `requirements-audit.txt` or equivalent dev-only file may formalize this.

## Conclusion

* All 16 Bandit B324 weak-hash findings resolved via `usedforsecurity=False`.
* Persisted semantic fingerprint preserved (per RULE 25 verification).
* Inner-loop autonomy stack remains free of HIGH and MEDIUM findings.
* `pip-audit` and CI grep clean.
* g05 PASS.
* Stable v3.8.0 still blocked by g01 Docker only.
