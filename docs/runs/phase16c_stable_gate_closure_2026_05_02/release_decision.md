# Phase 16C P8 — Release decision

## Decision

**Release outcome: `v3.7.7-stable-gate-alpha` (PRERELEASE).**

Stable v3.8.0 is **blocked** by **g01 Docker** under the master prompt's fail-closed rule. Phase 16C made material progress (Bandit installed and run for the first time; full proof re-verification at corpus 104; soak 9/9 no flakes) but the deciding blocker remains.

## Final gate ledger

| # | gate | required for stable | status @ Phase 16C |
|---|---|---|---|
| g01 | Docker end-to-end | yes | **FAIL / NOT VERIFIED** — `docker` CLI unavailable in dev shell (same as Phase 16B) |
| g02 | Remote/fresh clone reproduction vs post-merge main | yes | **PASS** (Phase 16B P13.5 verified vs `bada64c`; Phase 16C P10 will re-verify vs post-Phase-16C-merge SHA) |
| g03 | README top-section truth | yes | **PASS** (refreshed in P6) |
| g04 | Provider/builder delta = 0 | yes | **PASS** (re-confirmed in 4 Phase 16C re-runs; 0/0 in all) |
| g05 | Security audit including Bandit | yes | **PARTIAL_IMPROVED** — Bandit installed and run; zero HIGH/MEDIUM in inner loop; 16 HIGH `B324` weak-hash lints in non-inner-loop cache code documented for follow-up. Strict no-high reading: still blocks v3.8.0. Reachability-qualified reading: passes. Conservative call: PARTIAL. |
| g06 | 100+ solver proof | yes | **PASS** (corpus 104; `auto_promotions_total = 104`) |
| g07 | Full-corpus restart continuity | yes | **PASS** (re-confirmed: 104/104 served pre/post restart, persisted state identical, 0/0 across restart) |
| g08 | Proof soak no-flake | yes | **PASS** (Phase 16C 3-iteration soak: 9/9 across 3 proofs, no flakes) |
| g09 | Stage-2 atomic flip unexecuted | yes | **PASS** |
| g10 | Six-family low-risk allowlist unchanged | yes | **PASS** (no seed library changes in Phase 16C) |
| g11 | No actuator-side autonomy | yes | **PASS** |
| g12 | No high-risk family auto-promotion | yes | **PASS** |
| g13 | Release docs truth | yes | **PASS** (refreshed in P6) |
| g14 | GitHub release metadata verification | yes | **PENDING P12** (after tag creation) |
| g15 | Tag target SHA verification | yes | **PENDING P12** |
| g16 | CI green on PR head SHA | yes | **PENDING P9** |
| g17 | Docker docs truth | yes | **PASS** (DOCKER_QUICKSTART.md "not tested in this session" remains truthful) |
| g18 | No consciousness claim | yes | **PASS** |

## Stable-eligibility evaluation

The master prompt's fail-closed rule:

> v3.8.0 stable is allowed only if every required-for-stable gate is PASS on the post-merge main SHA.
> UNKNOWN, NOT TESTED, PARTIAL, LOCAL-ONLY, TOOL-UNAVAILABLE, BRANCH-ONLY, or NOT VERIFIED are stable blockers.

* **g01 Docker:** NOT VERIFIED → stable blocker.
* **g05 Security audit:** PARTIAL (under conservative interpretation) → stable blocker.

Therefore, **stable v3.8.0 is BLOCKED**.

## Prerelease eligibility evaluation

> Otherwise create `v3.7.7-stable-gate-alpha` ONLY if the session makes material release-readiness progress.

Phase 16C improvements over Phase 16B:

* **Bandit installed and run for the first time** (Phase 16B: "Bandit unavailable; not added per heavy-deps rule"). Real Bandit findings now classified per severity and reachability.
* **All four canonical proofs re-run on Phase 16C branch** at corpus 104 with output written into the Phase 16C session folder so Phase 11–16B canonical artifacts remain untouched.
* **3-iteration soak repeated**: 9/9 pass, no flakes (Phase 16B was 5-iter / 15/15; Phase 16C re-confirms repeatability at 3-iter scope).
* **Local-clone reproduction at branch state** verified before push.
* **Release docs refreshed** with Phase 16C truth: README, CURRENT_STATUS, CHANGELOG, RELEASE_READINESS, DOCKER_QUICKSTART (no change needed — truth preserved), PHASE16C_SECURITY_AUDIT.

This is **material release-readiness progress** even though the deciding stable blocker (Docker) is unchanged.

Therefore, **prerelease `v3.7.7-stable-gate-alpha` is JUSTIFIED**.

## Why no v3.8.0

Per master prompt:

> If release decision is stable: ... NOT prerelease.

v3.8.0 stable would require:

1. Docker tested end-to-end (blocked — `docker` not installed in dev shell);
2. Bandit no-high under strict reading (blocked — 16 HIGH B324 lints exist; see follow-up plan);

Neither is satisfied. **No v3.8.0 in this session.**

## Release notes scope

Per master-prompt P8 + P11:

> Do not prepare both as final release outcome.

This decision document is the final release-outcome decision. Release notes are prepared only for `v3.7.7-stable-gate-alpha`. No release notes for v3.8.0 will be created in this session.

## What an external operator must do to unblock v3.8.0 after Phase 16C

1. **Docker** — on a machine with Docker Engine 24+:
   ```bash
   git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
   cd waggledance-swarm
   git checkout main
   docker build -t waggledance:phase16c .
   docker run --rm waggledance:phase16c python tools/run_full_restart_continuity_proof.py
   docker run --rm waggledance:phase16c python tools/run_upstream_structured_request_proof.py
   docker run --rm waggledance:phase16c python tools/run_automatic_runtime_hint_proof.py
   ```
2. **Bandit B324 cleanup** (only required under strict no-high reading):
   ```bash
   # in waggledance-swarm clone
   # apply usedforsecurity=False to all hashlib.md5(...) and hashlib.sha1(...) calls
   # listed in docs/security/PHASE16C_SECURITY_AUDIT.md
   python -m bandit -r waggledance/ core/
   # expect: 0 HIGH findings
   ```
3. Update `RELEASE_READINESS.md` to record the verifications.

If both succeed and `RELEASE_READINESS.md` is updated, v3.8.0 stable is justifiable in the next release session.

## Phase 16C final outcome

* **Branch:** `phase16c/stable-gate-closure`
* **Release candidate:** `v3.7.7-stable-gate-alpha`
* **Release nature:** prerelease (alpha)
* **Stable v3.8.0:** BLOCKED. One substantive blocker (Docker) + one residual cleanup task (Bandit B324). Down from three Phase-16B blockers to one substantive + one cleanup.
