# Phase 16D P8 — Release decision (pre-PR)

## Decision

**Release outcome: `v3.7.8-docker-gate-alpha` (PRERELEASE).**

Stable v3.8.0 is **blocked solely by g01 Docker** under the master prompt's fail-closed rule and the P0 ENVIRONMENT PRE-CHECK result (`docker_available = false`). Phase 16D made significant material progress: the Bandit B324 cleanup is fully closed (16 → 0 HIGH findings) with persisted semantic fingerprint preservation verified per RULE 25.

## Final gate ledger snapshot @ P8

| # | gate | required for stable | status @ Phase 16D pre-PR |
|---|---|---|---|
| g01 | Docker end-to-end | yes | **FAIL_NOT_VERIFIED** — Docker CLI unavailable in dev shell (same as Phase 16B + 16C) |
| g02 | Remote/fresh clone post-merge | yes | PASS_CARRY (P9.5 + P10 will re-verify) |
| g03 | README current truth | yes | **PASS** (refreshed in P6) |
| g04 | 100+ solver proof | yes | **PASS** (corpus 104; `auto_promotions_total = 104` in all 4 Phase 16D re-runs) |
| g05 | Security audit / Bandit | yes | **PASS** — Bandit HIGH count: **16 → 0**. Phase 16C residual cleanup task fully closed. |
| g06 | Provider/builder Δ = 0 | yes | **PASS** (all 4 Phase 16D re-runs) |
| g07 | Full-corpus restart proof | yes | **PASS** (104/104 pre/post restart, all invariants true, 0/0 across restart) |
| g08 | Proof soak no-flake | yes | **PASS** (Phase 16D 9/9 at corpus 104, no flakes) |
| g09 | Stage-2 atomic flip unexecuted | yes | **PASS** |
| g10 | No HUMAN_APPROVAL collected | yes | **PASS** |
| g11 | No allowlist widening | yes | **PASS** (no seed library changes in Phase 16D) |
| g12 | No high-risk auto-promotion | yes | **PASS** |
| g13 | No actuator autonomy | yes | **PASS** |
| g14 | Control-plane / MAGMA truth preserved | yes | **PASS** (verified by g21 persisted semantic fingerprint preservation) |
| g15 | Release docs truth | yes | **PASS** (refreshed in P6) |
| g16 | Tag target correctness | yes | PENDING_P12 |
| g17 | GitHub release metadata correctness | yes | PENDING_P12 |
| g18 | CI green on PR head | yes | PENDING_P9 |
| g19 | Docker runtime no-network proof | yes | **FAIL_NOT_VERIFIED** (Docker unavailable; same root cause as g01) |
| g20 | Fresh-clone tag fetch verification | yes | PASS_CARRY (P9.5 + P10 will re-verify with v3.7.7 tag) |
| g21 | Persisted semantic fingerprint preservation across B324 cleanup | yes | **PASS** — 14 scalar fields + 7 restart invariants + 6 per-operation served counts identical pre/post (RULE 25 fully satisfied) |

## Stable-eligibility evaluation

The master prompt's fail-closed rule:

> v3.8.0 stable is allowed only if every required-for-stable gate is PASS on the post-merge main SHA.

* **g01 / g19 Docker:** FAIL_NOT_VERIFIED → stable blocker.

Therefore, **stable v3.8.0 is BLOCKED.**

## Prerelease eligibility evaluation (RELEASE DECISION RULE outcome B)

> Allowed if stable is blocked but material release-readiness progress was made.

Phase 16D achieved:

* **Bandit B324 cleanup completed** with persisted semantics preserved — closes the Phase 16C residual cleanup task entirely. Bandit HIGH count goes from 16 to 0.
* **Persisted semantic fingerprint preservation verified per RULE 25** — 14 scalar fields + 7 restart invariants + 6 per-operation served counts identical pre and post the B324 cleanup. This is the strongest semantic-preservation evidence the project has shipped.
* **All four canonical proofs re-run on Phase 16D branch** at corpus 104 with `auto_promotions_total = 104`, `provider_jobs_delta = builder_jobs_delta = 0`, no flakes.
* **3-iteration soak: 9/9 PASS, no flakes** (consistent with Phase 16C 9/9 and Phase 16B 15/15).
* **349/349 targeted tests PASS** on first run (no flakes).
* **Release docs refreshed** with Phase 16D truth: README, CURRENT_STATUS, CHANGELOG, RELEASE_READINESS, PHASE16D_SECURITY_AUDIT.
* **Blocker set narrowed** from Phase 16C's "Docker + Bandit" (1 substantive + 1 residual cleanup) to **Phase 16D's "Docker only"** (1 substantive). v3.8.0 is now one external Docker verification away.

This is unambiguous **material release-readiness progress**.

Therefore, **prerelease `v3.7.8-docker-gate-alpha` is JUSTIFIED**.

## Why not no-tag (RELEASE DECISION RULE outcome C)

Outcome C requires:

* no material progress was made — does not apply (16 → 0 HIGH Bandit findings is material progress);
* B324 cleanup reverted due to persisted semantics failure — does not apply (g21 PASS);
* post-branch-level critical failure — does not apply.

**Outcome C is rejected.**

## Why not v3.8.0 (RELEASE DECISION RULE outcome A)

Outcome A requires Docker available + build succeeds + in-container proof passes. Phase 16D environment pre-check determined `docker_available = false`. Per master prompt RULE 13:

> If Docker is unavailable, stable is blocked no matter what else passes.

**Outcome A is unavailable in this session.**

## What an external operator must do to unblock v3.8.0

There is now exactly **one** substantive blocker. On a machine with Docker Engine 24+ or Docker Desktop 4.29+:

```bash
git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git
cd waggledance-swarm
git checkout main  # post-Phase-16D
docker version
docker compose version
docker build -t waggledance:phase16d .

# Inner-loop autonomy proofs with no runtime network
docker run --rm --network none waggledance:phase16d python tools/run_full_restart_continuity_proof.py
docker run --rm --network none waggledance:phase16d python tools/run_upstream_structured_request_proof.py
docker run --rm --network none waggledance:phase16d python tools/run_automatic_runtime_hint_proof.py

# Targeted smoke tests
docker run --rm --network none waggledance:phase16d python -m pytest \
  tests/autonomy_growth/test_full_restart_continuity_smoke.py \
  tests/autonomy_growth/test_upstream_structured_request_proof_smoke.py \
  tests/autonomy_growth/test_automatic_runtime_hint_proof_smoke.py \
  tests/autonomy_growth/test_seed_library.py \
  -q
```

If all PASS, update `RELEASE_READINESS.md` to mark g01 / g19 PASS, then create v3.8.0 stable from main.

## Phase 16D outcome

* **Branch:** `phase16d/final-stable-gate-closure`
* **Release candidate:** `v3.7.8-docker-gate-alpha`
* **Release nature:** prerelease (alpha)
* **Stable v3.8.0:** BLOCKED solely by g01 Docker. **One external verification away.**
