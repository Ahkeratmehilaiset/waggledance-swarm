# Phase 16C P2 — Security audit (Bandit included)

**Scope:** all of `waggledance/` and `core/`, plus the dependency closure of the installed environment.
**Tools run:** Bandit (newly installed in this session), `pip-audit`, CI grep equivalent, manual grep on Phase 11+ files.
**Branch:** `phase16c/stable-gate-closure`.
**Bandit version:** 1.9.4.

This audit closes the Phase 16B `g05` partial outcome. Bandit is now installed and run. Findings are classified per the master prompt's classification rules.

## Tool availability

| tool | available in this session | result |
|---|---|---|
| Bandit | **yes (newly installed)** | ran on `waggledance/` and `core/`; report at `docs/runs/phase16c_stable_gate_closure_2026_05_02/bandit_report.json` |
| pip-audit | yes | ran against installed env; consistent with Phase 16B |
| CI security-scan grep equivalent (`eval()` outside `safe_eval`) | yes | **0 unauthorized hits** |
| manual grep on Phase 11+ autonomy_growth / autonomy_service files | yes | clean |

Bandit was installed via `python -m pip install bandit` as **audit tooling only**. It is **not** added to runtime requirements (`requirements.lock.txt` / `requirements.txt` / `requirements-ci.txt` unchanged). A future PR can add it to a dedicated `requirements-audit.txt` if the project wants to formalize audit tooling; Phase 16C does not introduce that file because it is out of stabilization scope.

## Bandit findings — overall

`python -m bandit -r waggledance/ core/ -f json -o docs/runs/phase16c_stable_gate_closure_2026_05_02/bandit_report.json`

Lines of code scanned: **74,639**.

| severity | count | classification |
|---|---|---|
| HIGH | **16** | all `B324 hashlib` weak-hash lints in cache / dedup / content-addressing code |
| MEDIUM | **28** | mostly model-loading lints (`B614 pytorch_load`, `B615 huggingface_unsafe_download`); a few `B307 eval` in `safe_eval`/`math_solver`/`constraint_engine`; `B608 hardcoded_sql_expressions`; `B310 urlopen` in installer/runtime; `B104 bind_all_interfaces` in start scripts |
| LOW | 226 | mostly `B110 try_except_pass`, `B311 random`, `B101 assert`, `B603/B607 subprocess`, `B105 hardcoded_password_string` (false-positive matches against literal status / threshold values like "OK", "green", "0.85") |

## Bandit findings — autonomy inner-loop reachability

The inner loop of the autonomy proof is:

```
AutonomyService.handle_query
  → CompatibilityLayer.handle_query  (compatibility_mode=False since Phase 9)
    → AutonomyRuntime.handle_query
      → SolverRouter.route
        → autonomy_consult / RuntimeQueryRouter.route
          → LowRiskSolverDispatcher / HotPathCache
```

Files that participate in the inner loop:

* `waggledance/application/services/autonomy_service.py`
* `waggledance/core/autonomy/runtime.py`, `compatibility.py`, related autonomy modules
* `waggledance/core/autonomy_growth/*`
* `waggledance/core/reasoning/solver_router.py`

Bandit findings inside this set:

| severity | count | breakdown |
|---|---|---|
| HIGH | **0** | none |
| MEDIUM | **0** | none |
| LOW | **9** | 4× `B110 try/except/pass` in autonomy_service / hot_path_cache (intentional defensive blocks); 2× `B101 assert` in hot_path_cache (defensive asserts); 3× `B105 hardcoded_password_string` false-positive matches against threshold value `0.85` and seed status labels `"OK"`, `"green"` |

**No HIGH or MEDIUM Bandit findings are reachable from the autonomy inner loop.**

## Classification of HIGH-severity findings

All 16 HIGH findings are Bandit test `B324 hashlib`: "Use of weak MD5/SHA1 hash for security. Consider `usedforsecurity=False`". Bandit flags any `hashlib.md5(...)` or `hashlib.sha1(...)` call as HIGH because *if* the hash were used for security (passwords, signatures, tamper detection), MD5/SHA1 would be unsafe.

In this codebase, all 16 occurrences are used for **non-security purposes** — cache keys, content addressing, deduplication. Specifically:

| file | line(s) | purpose | reachable from autonomy inner loop? |
|---|---|---|---|
| `core/embedding_cache.py` | 115, 154, 190, 319, 342, 377 | embedding-cache key | no (memory/RAG lane) |
| `core/hive_support.py` | 103 | legacy hive-lane support | no (legacy lane; `compatibility_mode=False` since Phase 9) |
| `core/knowledge_loader.py` | 364 | knowledge-base content addressing | no |
| `core/learning_engine.py` | 645 | night-learning case dedup | no |
| `core/night_enricher.py` | 624 | night-enrichment dedup | no |
| `core/whisper_protocol.py` | 245, 298 | message ID generation | no |
| `waggledance/adapters/feeds/feed_ingest_sink.py` | 144 | feed-content dedup | no |
| `waggledance/adapters/memory/chroma_vector_store.py` | 171 | vector-store key | no |
| `waggledance/observatory/mama_events/consolidation.py` | 159 | event ID (SHA1) | no |
| `waggledance/observatory/mama_events/taxonomy.py` | 103 | taxonomy key (SHA1) | no |

**Disposition:** all 16 HIGH findings are `false_positive_documented` for the autonomy inner-loop release gate. They are real B324 lint findings and should be cleaned up in a follow-up PR by adding `usedforsecurity=False` to the affected `hashlib.md5(...)` and `hashlib.sha1(...)` calls. Phase 16C does **not** apply that cleanup because:

* the cleanup touches 12 files in non-autonomy lanes (memory, knowledge, learning, observatory, feeds);
* Phase 16C is a stabilization sprint, not a Bandit-cleanup sprint;
* the cleanup does not affect the v3.8.0 stable-gate decision (Docker is the deciding blocker, see P3);
* the master prompt explicitly forbids broad scope creep ("Do not chase scope").

A separate PR titled `chore(security): silence Bandit B324 weak-hash lints by passing usedforsecurity=False` is the right vehicle for this cleanup.

## Classification of MEDIUM-severity findings

| test_id | count | examples | classification |
|---|---|---|---|
| B615 `huggingface_unsafe_download` | 13 | `core/micro_model.py`, `core/opus_mt_adapter.py`, `core/translation_proxy.py` | `true_positive_low` — model loading without revision pinning. Real supply-chain concern but in optional NLP / translation lanes, not autonomy inner loop. Pinning `revision=...` in `from_pretrained()` calls is the recommended fix. Tracked for follow-up. |
| B608 `hardcoded_sql_expressions` | 7 | various | `false_positive_documented` — these are static parameterized queries. Not real injection points. |
| B307 `eval` blacklist | 3 | `core/safe_eval.py`, `core/math_solver.py`, `core/constraint_engine.py` | `false_positive_documented` — `safe_eval` is the project's controlled evaluator with explicit AST whitelist; `math_solver` and `constraint_engine` route through `safe_eval`. CI grep workflow already exempts these. |
| B310 `urlopen` blacklist | 2 | `core/auto_install.py`, `waggledance/adapters/cli/start_runtime.py` | `false_positive_documented` — installer / runtime bootstrap. Not autonomy inner loop. |
| B104 `hardcoded_bind_all_interfaces` | 2 | `waggledance/adapters/cli/start_runtime.py` | `false_positive_documented` — server bootstrap binds to `0.0.0.0`. Standard. |
| B614 `pytorch_load` | 1 | `core/micro_model.py` | `true_positive_low` — `torch.load` without `weights_only=True`. Same lane as B615. Tracked for follow-up. |

**No MEDIUM findings reachable from autonomy inner loop.**

## Classification of LOW-severity findings

| test_id | count | classification |
|---|---|---|
| B110 `try_except_pass` | 158 | `false_positive_documented` — defensive try/except/pass in metrics / telemetry / optional features |
| B311 `random` | 18 | `false_positive_documented` — `random` used for seed shuffling / sample generation, not crypto |
| B101 `assert_used` | 14 | `false_positive_documented` — defensive asserts; project does not run with `python -O` |
| B603 `subprocess_without_shell_equals_true` | 10 | `false_positive_documented` — explicit list-form subprocess calls in tools (proof harnesses, watchdog) |
| B607 `start_process_with_partial_path` | 8 | `false_positive_documented` — same as B603 context |
| B105 `hardcoded_password_string` | 8 | `false_positive_documented` — false-positive matches against threshold values like `"0.85"`, `"OK"`, `"green"`, `"warm"` etc. that pattern-match Bandit's password heuristic |
| B404 `subprocess` blacklist | 7 | `false_positive_documented` — same as B603 |
| B112 `try_except_continue` | 3 | `false_positive_documented` — defensive |

**9 LOW findings reachable from autonomy inner loop**, all false positives (3× B110 in autonomy_service, 1× B110 in hot_path_cache, 2× B101 in hot_path_cache, 1× B105 `0.85` confidence threshold, 2× B105 `"OK"`/`"green"` seed labels). None require code change.

## pip-audit — dependency CVEs

Consistent with Phase 16B: 32 known vulnerabilities across 14 packages.

* No change since Phase 16B in this session.
* All classified `low` for the autonomy inner loop — the affected packages (aiohttp, cryptography, deep-translator, js2py, lxml, nltk, pillow, pip, pygments, pypdf, pytest, python-dotenv, requests, streamlit) are not imported by the autonomy_growth lane.
* Routine upgrade work tracked in active Dependabot PRs.

## CI security-scan equivalent (local re-run)

```
grep -rn "eval(" --include="*.py" core/ waggledance/ \
  | grep -v safe_eval | grep -v "# noqa" | grep -v "test_" \
  | grep -v "\.eval()" | grep -v "retrieval(" | grep -v "evaluate(" \
  | wc -l
→ 0
```

**Clean.** No unauthorized `eval()` outside `safe_eval`.

## Stable gate disposition (g05)

Per master prompt:

> Bandit audit PASS or acceptable documented no-high/no-critical result.

* **Bandit ran successfully** for the first time in any release-gate session — material progress over Phase 16B's "Bandit unavailable" partial.
* **No HIGH or MEDIUM findings reachable from the autonomy inner loop.** All inner-loop findings are LOW false-positives.
* **16 HIGH findings exist outside the autonomy inner loop**, all `B324` weak-hash lints in cache / dedup / content-addressing code. They are real Bandit findings (not "no-high result") but are documented as not reachable from the inner loop.

**Two valid interpretations of the master prompt's strict gate language:**

1. **Strict no-high reading**: any HIGH finding blocks stable. → g05 `PARTIAL` (improved from Phase 16B; still blocks v3.8.0 stable until B324 lints are silenced via the follow-up PR).
2. **Reachability-qualified reading** (per the per-finding classification rule "High/critical reachable from inner loop => stable blocked; Medium/low unreachable or documented safe => may pass if justified"): HIGH-but-unreachable + documented could pass. → g05 `PASS_WITH_DOCUMENTED_RESIDUAL`.

For Phase 16C the more conservative interpretation applies: g05 status is `PARTIAL_IMPROVED`. This is consistent with the fail-closed rule and with the spirit of the master prompt that demands explicit truth.

g05 disposition for **prerelease v3.7.7-stable-gate-alpha**: **PASS** — Bandit ran, no high/medium findings reachable from inner loop, residual HIGH findings documented as B324 weak-hash lints with planned follow-up cleanup.

g05 disposition for **stable v3.8.0**: **BLOCKED** under the strict no-high-finding reading. To unblock, a follow-up PR titled `chore(security): silence Bandit B324 weak-hash lints` should add `usedforsecurity=False` to the 16 affected `hashlib.md5(...)` / `hashlib.sha1(...)` calls.

## Conclusion

* Bandit installed and run for the first time. Major improvement over Phase 16B.
* No HIGH or MEDIUM findings reachable from autonomy inner loop.
* 16 HIGH B324 lints in non-inner-loop cache code; documented for follow-up cleanup.
* `pip-audit` and CI grep both clean for inner-loop reachability.
* g05 PARTIAL → still blocks v3.8.0 stable per strict reading; PASSES the prerelease bar.
