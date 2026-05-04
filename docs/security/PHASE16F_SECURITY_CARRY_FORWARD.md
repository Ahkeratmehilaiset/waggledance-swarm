# Phase 16F — Security Carry-Forward (Bandit + pip-audit)

**Date:** 2026-05-04
**Branch:** `phase16f/docker-stable-gate`
**Tooling:** Bandit 1.9.4, pip-audit 2.10.0 — both audit-only; not added to runtime requirements.

## Result: PASS — g05 + g21 carry forward

| metric | Phase 16D baseline | Phase 16F result | match |
|---|---|---|---|
| LOC scanned (`waggledance/` + `core/`) | 74,639 | 74,640 | ✅ (1 line drift in this Phase 16F worktree's edits to nothing-in-scope) |
| Bandit HIGH count | 0 | **0** | ✅ |
| Bandit B324 weak-hash count | 0 | **0** | ✅ (cleanup intact) |
| Bandit MEDIUM count | 28 | **28** | ✅ (carry-forward, all `B615 huggingface_unsafe_download`) |
| Bandit LOW count | 226 | **226** | ✅ (carry-forward, all defensive try/except/pass etc.) |
| pip-audit total CVEs | 32 across 14 pkgs | **32 across 14 pkgs** | ✅ (carry-forward) |
| autonomy inner-loop HIGH/MEDIUM reachability | 0 / 0 | 0 / 0 | ✅ (carry-forward by source-grep) |

## Bandit details

**Command:**
```bash
python -m bandit -r waggledance/ core/ -f json -o docs/runs/phase16f_docker_stable_gate_2026_05_03/bandit_report.json
```

**Totals:**
* HIGH: 0 ✅
* MEDIUM: 28 (all `B615 huggingface_unsafe_download` — carry-forward from 16C analysis as out-of-inner-loop NLP / translation lane work; explicit per-call comment is a follow-up but not a stable blocker)
* LOW: 226 (defensive try/except, asserts, B105 false-positives against threshold/status-string literals — carry-forward classification from 16C)
* UNDEFINED: 0

**B324 verification:** zero findings, confirming Phase 16D's `usedforsecurity=False` additions across 12 files remain intact under `phase16f/docker-stable-gate` (no autonomy code touched).

## pip-audit details

**Command:**
```bash
python -m pip_audit --skip-editable -f json -o docs/runs/phase16f_docker_stable_gate_2026_05_03/pip_audit_report.json
```

**Result: 32 known vulnerabilities in 14 packages.** All carry-forward from 16B/16C/16D. Dependabot PRs are open for the highest-volume ones.

| package | version | CVEs | fix version | Dependabot PR |
|---|---|---|---|---|
| aiohttp | 3.13.3 | 10 (CVE-2026-34513..34525, CVE-2026-22815) | 3.13.4 | tracked |
| cryptography | 46.0.5 | 2 (CVE-2026-34073, CVE-2026-39892) | 46.0.6 / 46.0.7 | tracked |
| deep-translator | 1.11.4 | 1 (PYSEC-2022-252) | (no fix yet) | — |
| js2py | 0.74 | 1 (CVE-2024-28397) | (no fix yet) | — |
| lxml | 6.0.2 | 1 (CVE-2026-41066) | 6.1.0 | tracked |
| nltk | 3.9.3 | 3 (GHSA-rf74-v2fm-23pw, CVE-2026-33230, CVE-2026-33231) | 3.9.4 (partial) | tracked |
| pillow | 11.3.0 | 2 (CVE-2026-25990, CVE-2026-40192) | 12.1.1 / 12.2.0 | tracked |
| pip | 26.0.1 | 1 (CVE-2026-3219) | (no fix yet) | — |
| pygments | 2.19.2 | 1 (CVE-2026-4539) | 2.20.0 | tracked |
| pypdf | 6.9.1 | 6 (CVE-2026-33699, CVE-2026-40260, GHSA-*) | 6.10.2 | tracked |
| pytest | 9.0.2 | 1 (CVE-2025-71176) | 9.0.3 | tracked |
| python-dotenv | 1.2.1 | 1 (CVE-2026-28684) | 1.2.2 | tracked |
| requests | 2.32.5 | 1 (CVE-2026-25645) | 2.33.0 | tracked |
| streamlit | 1.48.1 | 1 (CVE-2026-33682) | 1.54.0 | tracked |

**Inner-loop reachability:** none of these 14 packages is on the autonomy inner-loop hot path. The autonomy lane uses `aiosqlite` (no CVEs) for the control plane and pure-Python `pydantic` / `dataclasses` for IR. Phase 16B already documented that the 32 CVEs are all classified `low` and not reachable.

## Stable gate ledger updates

* **g05 Security audit / Bandit**: PASS (carry-forward — HIGH=0, B324=0, MEDIUM=28 carry-forward classification, LOW carry-forward; pip-audit carry-forward)
* **g21 Bandit B324 cleanup carry-forward**: PASS (B324 count = 0)

## Bandit / pip-audit are NOT added to runtime requirements

Per CLAUDE.md / Phase 16C policy, Bandit and pip-audit are dev / CI audit tooling only. They are not in `requirements.txt`, `requirements.lock.txt`, or `requirements-ci.txt`. No new dependency is shipped by Phase 16F.
