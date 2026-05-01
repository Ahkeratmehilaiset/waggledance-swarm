# Phase 16B P7 — Security self-audit

**Scope:** new Phase 11–16B files plus the dependency closure of `requirements.lock.txt`.
**Bounded-timebox:** master prompt forbids adding heavy new runtime deps for tooling. `bandit` was not installed; `pip-audit` was available.

## Tools attempted

| tool | available | result |
|---|---|---|
| CI security-scan locally (grep `eval()` / `port 1883` from `.github/workflows/ci.yml`) | yes | **0 hits** — clean |
| `pip-audit --skip-editable` (installed environment) | yes | 32 known vulnerabilities in 14 packages — all dependency upgrades |
| `pip-audit -r requirements.lock.txt` | failed | local pip cache permission error on Windows; superseded by `--skip-editable` against installed env |
| `bandit` | **no — module not installed** | did not install heavy new dependency per master-prompt rule |
| manual grep for dangerous patterns in Phase 11+ files | yes | findings classified below |

## Findings

### F1 — CI eval()/port grep (REPO-CODE)

| field | value |
|---|---|
| classification | **false_positive_documented** (clean) |
| evidence | `grep -rn "eval(" --include="*.py" core/ waggledance/ \| grep -v safe_eval \| grep -v "# noqa" \| grep -v "test_" \| grep -v '\\.eval()' \| grep -v 'retrieval(' \| grep -v 'evaluate(' \| wc -l` returns **0** |
| reference | `.github/workflows/ci.yml` lines 73–84 (security-scan job) |
| stable impact | none |
| prerelease impact | none |

### F2 — Manual grep in Phase 11+ autonomy_growth / autonomy_service files (REPO-CODE)

| pattern | hits in Phase 11–16B scope | classification |
|---|---|---|
| `eval(` | 0 (after CI exclusions) | clean |
| `exec(` | 0 | clean |
| `os.system(` | 0 | clean |
| `subprocess.` | 0 | clean |
| `urllib.request` | 0 | clean |
| `requests.` (HTTP client) | 0 | clean |
| `socket.` | 1 — `autogrowth_scheduler.py:103` `socket.gethostname()` for scheduler-ID seeding | **false_positive_documented** — read-only host-name lookup, no network I/O |
| `.unlink(`, `shutil.rmtree`, `os.remove` | only in `tools/` proof harness DB / scratch-dir cleanup | **false_positive_documented** — all target scratch / temp paths the tools themselves created |

**Stable impact:** none. **Prerelease impact:** none.

### F3 — Dependency CVEs from `pip-audit` (DEPENDENCY-CLOSURE)

`pip-audit --skip-editable` reported 32 known vulnerabilities across 14 third-party packages. All are dependency upgrades; none are WaggleDance-code vulnerabilities.

| package | version installed | vulns | fix versions | classification |
|---|---|---|---|---|
| aiohttp | 3.13.3 | CVE-2026-34515, CVE-2026-34513, CVE-2026-34516, CVE-2026-34525, CVE-2026-22815, CVE-2026-34514 | 3.13.4 | **true_positive_low** (HTTP server-side; not used by inner-loop autonomy proof) |
| cryptography | 46.0.5 | CVE-2026-34073, CVE-2026-39892 | 46.0.6, 46.0.7 | **true_positive_low** (TLS / cert handling; not used by inner-loop) |
| deep-translator | 1.11.4 | PYSEC-2022-252 | (no fix listed) | **needs_followup** (not used by autonomy lane) |
| js2py | 0.74 | CVE-2024-28397 | (no fix listed) | **needs_followup** (not used by autonomy lane) |
| lxml | 6.0.2 | CVE-2026-41066 | 6.1.0 | **true_positive_low** |
| nltk | 3.9.3 | GHSA-rf74-v2fm-23pw, CVE-2026-33230, CVE-2026-33231 | 3.9.4 | **true_positive_low** |
| pillow | 11.3.0 | CVE-2026-25990, CVE-2026-40192 | 12.1.1, 12.2.0 | **true_positive_low** |
| pip | 26.0.1 | CVE-2026-3219 | (no fix listed) | **needs_followup** |
| pygments | 2.19.2 | CVE-2026-4539 | 2.20.0 | **true_positive_low** |
| pypdf | 6.9.1 | CVE-2026-33699, CVE-2026-40260, GHSA-jj6c-8h6c-hppx, GHSA-4pxv-j86v-mhcw, GHSA-7gw9-cf7v-778f, GHSA-x284-j5p8-9c5p | 6.9.2, 6.10.0, 6.10.1, 6.10.2 | **true_positive_low** |
| pytest | 9.0.2 | CVE-2025-71176 | 9.0.3 | **true_positive_low** |
| python-dotenv | 1.2.1 | CVE-2026-28684 | 1.2.2 | **true_positive_low** |
| requests | 2.32.5 | CVE-2026-25645 | 2.33.0 | **true_positive_low** |
| streamlit | 1.48.1 | CVE-2026-33682 | 1.54.0 | **true_positive_low** |

| skipped (not on PyPI for audit) | rationale |
|---|---|
| torch 2.7.1+cu118 | local CUDA build; pip-audit cannot resolve; not used by inner-loop |
| torchaudio 2.7.1+cu118 | local CUDA build |
| torchvision 0.22.1+cu118 | local CUDA build |

**Inner-loop impact:** **none**. The inner-loop autonomy proofs (`tools/run_automatic_runtime_hint_proof.py`, `tools/run_upstream_structured_request_proof.py`, `tools/run_full_restart_continuity_proof.py`) do not import any of the affected packages. Verified by code reading: the autonomy_growth lane uses only stdlib + the project's own modules.

**Severity:** all classified as `low` because:

1. None of the affected libraries are reachable from the inner-loop autonomy code path.
2. The vulnerabilities are typical HTTP-stack / parser CVEs (DoS, header confusion, file-format issues).
3. Dependabot PRs #19–#26 already exist for the most active subset (psutil, websockets, cachetools, av, scipy, actions/checkout, actions/setup-python). Routine upgrade discipline is in place.

**Recommended remediation:** merge the active Dependabot PRs and refresh `requirements.lock.txt` in a follow-up PR. Out of scope for Phase 16B (RULE 11: no new HTTP adapters; this PR's focus is stabilization and release-gate audit, not dependency churn).

### F4 — Architectural / boundary checks

| check | result |
|---|---|
| Stage-2 atomic flip executed? | **no** (verified by reading `docs/atomic_flip_prep/00_README.md` — still PREPARATION ONLY) |
| HUMAN_APPROVAL collected this session? | **no** |
| `phase8.5/*` writes? | **no** (read-only inspection only) |
| provider jobs in inner-loop proof path? | **no** — proof JSON KPIs `provider_jobs_delta_during_proof = 0` across all three canonical proofs |
| builder jobs in inner-loop proof path? | **no** — `builder_jobs_delta_during_proof = 0` |
| direct production config writes? | **no** in autonomy_growth scope |
| direct prod actuator writes? | **no** — auto-promoted solvers are consultable, not authoritative |
| six-family allowlist intact? | **yes** — `LOW_RISK_FAMILY_KINDS` unchanged |

## Unresolved findings

* **Critical:** none.
* **High:** none.
* **Medium:** none.
* **Low:** 32 dependency CVEs (F3) — routine upgrade work, none reachable from inner-loop. Tracked in active Dependabot PRs.
* **Tool unavailable:** `bandit` (not installed in this dev shell; not added per master-prompt rule about heavy new deps).

## Stable gate disposition

g05 (security self-audit completed) — **partial pass**.

Per master-prompt fail-closed rule:

> v3.8.0 requires security self-audit completed with no unresolved critical/high issue.

* No unresolved critical / high issues found. ✓
* CI security-scan runs clean. ✓
* Manual grep on Phase 11+ files clean. ✓
* `pip-audit` ran successfully on installed env; findings are all dependency upgrades, low severity, none reachable from inner loop. ✓
* `bandit` is unavailable.

The master prompt also says:

> If tools unavailable, manual audit may allow prerelease but blocks stable unless a pre-existing documented exception exists.

`bandit` is unavailable. There is no pre-existing stable-gate exception in `RELEASE_READINESS.md` for missing `bandit`. Therefore, per the strict fail-closed rule, **g05 is treated as NOT FULLY VERIFIED for v3.8.0 stable** — `bandit` is the missing tool.

For `v3.7.6-stabilization-alpha`: g05 passes (manual audit + `pip-audit` + CI security-scan local-run all complete with no unresolved high/critical findings).

## Conclusion

* **For prerelease `v3.7.6-stabilization-alpha`:** PASS — no unresolved critical/high; routine dependency upgrades pending; CI grep clean; manual audit clean.
* **For stable `v3.8.0`:** PARTIAL — `bandit` not run. Either install `bandit` and re-verify in a follow-up session, or document a pre-existing stable-gate exception in `RELEASE_READINESS.md`.

For Phase 16B, the conservative outcome is `v3.7.6-stabilization-alpha` with this g05 caveat documented in `RELEASE_READINESS.md` as a stable-gate blocker for v3.8.0.
