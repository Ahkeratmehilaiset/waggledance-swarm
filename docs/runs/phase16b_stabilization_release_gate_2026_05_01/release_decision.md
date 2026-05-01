# Phase 16B P11 — Release decision

## Decision

**Release outcome: `v3.7.6-stabilization-alpha` (PRERELEASE).**

Stable v3.8.0 is **blocked** under the master prompt's fail-closed rule. Phase 16B's correct successful outcome is the prerelease, with explicit blockers documented for the next session.

## Final gate ledger

| # | gate | required for stable | status |
|---|---|---|---|
| g01 | Docker end-to-end proof tested | yes | **FAIL / NOT VERIFIED** — `docker` CLI not installed; no build, no in-container proof |
| g02 | Remote/fresh clone reproduction verified against post-merge main | yes | **PARTIAL** — local-clone passed at v3.7.5 SHA; remote/fresh against post-merge main pending P13.5 |
| g03 | Latest proof reproducible by copy-paste commands | yes | **PASS** |
| g04 | 100+ auto-promoted low-risk solvers in proof | yes | **PASS** — corpus expanded 98 → 104; all three canonical proofs report `auto_promotions_total = 104` |
| g05 | Security self-audit completed | yes | **PARTIAL** — CI grep clean; manual grep on Phase 11+ clean; `pip-audit` reports 32 dependency CVEs all classified low and not reachable from inner loop; `bandit` not installed |
| g06 | CI green on PR head SHA | yes | **PENDING (P13)** |
| g07 | README top section accurate | yes | **PASS** — Phase 16B refresh keeps top section concise + alpha vs not-implemented + consciousness=No |
| g08 | RELEASE_READINESS latest-tag truth accurate | yes | **PASS** — updated to v3.7.5 latest, v3.7.6-stabilization-alpha recommended next |
| g09 | No provider dependency in inner loop | yes | **PASS** — all three canonical proofs record `provider_jobs_delta = builder_jobs_delta = 0` |
| g10 | Stage-2 atomic flip remains unexecuted | yes | **PASS** |
| g11 | Phase 9 14-stage human-gated promotion ladder unchanged | yes | **PASS** |
| g12 | Six-family low-risk allowlist unchanged | yes | **PASS** — +6 seeds inside existing six families; allowlist unchanged |
| g13 | No consciousness / sentience / AGI / alive claim | yes | **PASS** |
| g14 | Full-corpus restart continuity proven | yes | **PASS** — 104 / 104 served pre/post restart; persisted state identical; provider/builder Δ = 0 across restart |
| g15 | Proof soak / repeatability passed with no flakes | yes | **PASS** — 15 / 15 iterations across 3 proofs; no flakes |
| g16 | Install commands consistent across docs/CI | yes | **PASS** — three install sources documented as deliberate layering |
| g17 | HTTP/API-route stable requirement explicitly decided | yes | **PASS** — v3.8.0 is library/service-layer-stable, not HTTP-API-stable |
| g18 | Tag/release verification plan documented | yes | **PASS** (this section + P14 procedure) |

## Stable-eligibility evaluation

The master prompt's STABLE RELEASE FAIL-CLOSED RULE:

> v3.8.0 stable is allowed only if every stable gate is PASS on the post-merge main SHA.
> UNKNOWN, NOT TESTED, PARTIAL, LOCAL-ONLY, TOOL-UNAVAILABLE, BRANCH-ONLY, or NOT VERIFIED are stable blockers.

Three required-for-stable gates are not PASS:

* **g01 Docker:** FAIL / NOT VERIFIED → stable blocker.
* **g02 Remote/fresh-clone:** PARTIAL (local-clone-only at v3.7.5 SHA; remote/fresh-against-post-merge-main is required and is pending P13.5; even if P13.5 succeeds, the master prompt's "no new stable-gate exception may be invented during this non-interactive session" rule applies — and there is no pre-existing exception for the bandit absence) → stable blocker.
* **g05 Security audit:** PARTIAL — `bandit` is not installed; `pip-audit` and CI grep + manual grep are complete; per master-prompt rule, "If tools unavailable, manual audit may allow prerelease but blocks stable unless a pre-existing documented exception exists." No pre-existing exception exists → stable blocker.

Therefore, **stable v3.8.0 is BLOCKED**.

## Prerelease eligibility evaluation

Master prompt:

> "Prerelease stabilization alpha" means the stabilization work improved truth/reproducibility/tests/docs but at least one stable gate remains unsatisfied; blockers are explicit and actionable.

Phase 16B improved:

* truth: stable-gate ledger, install-source layering doc, HTTP-route stable decision, security audit doc, Docker truth doc.
* reproducibility: full-corpus restart proof, proof soak driver, local-clone reproduction.
* tests: +12 full restart smoke tests, +1 release-gate seed-library test, +6 canonical seeds. 349 / 349 targeted tests pass.
* docs: README, CURRENT_STATUS, CHANGELOG, RELEASE_READINESS, DOCKER_QUICKSTART, RUNTIME_ENTRYPOINT_TRUTH_MAP all refreshed.

Blockers for stable are explicit and actionable (see "Final gate ledger" above and `docs/release/RELEASE_READINESS.md` "Release blockers — for stable v3.8.0 (still blocked)" section).

Therefore, **prerelease `v3.7.6-stabilization-alpha` is JUSTIFIED**.

## Release notes scope

Per master-prompt P9 / P14 rule:

> Do not prepare both as final release outcome; final tag decision happens after P10/P12.5.

This decision document is the final release-outcome decision. Release notes are prepared only for `v3.7.6-stabilization-alpha`. No release notes for v3.8.0 will be created in this session.

## What an external operator must do to unblock v3.8.0

1. On a machine with Docker Engine 24+:
   ```
   git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
   cd waggledance-swarm
   git checkout main
   docker build -t waggledance:phase16b .
   docker run --rm waggledance:phase16b python tools/run_upstream_structured_request_proof.py
   docker run --rm waggledance:phase16b python tools/run_full_restart_continuity_proof.py
   ```
2. From a fresh remote clone of `origin/main` post-Phase-16B-merge:
   ```
   git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git wd-fresh
   cd wd-fresh
   git rev-parse HEAD  # must equal post-Phase-16B-merge main SHA
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.lock.txt
   python -m pytest tests/autonomy_growth/ -q
   python tools/run_upstream_structured_request_proof.py
   python tools/run_full_restart_continuity_proof.py
   ```
3. Install `bandit` and run:
   ```
   pip install bandit
   bandit -r waggledance/ core/
   ```
   then add a Phase 16C / Phase 17 release-readiness section documenting the result.

If all three succeed and `RELEASE_READINESS.md` is updated to record the verification, v3.8.0 stable is justifiable in the next release session.

## Phase 16B final outcome

* **Branch:** `phase16b/stabilization-release-gate`
* **Release candidate:** `v3.7.6-stabilization-alpha`
* **Release nature:** prerelease (alpha)
* **Stable v3.8.0:** BLOCKED. Three documented blockers.
