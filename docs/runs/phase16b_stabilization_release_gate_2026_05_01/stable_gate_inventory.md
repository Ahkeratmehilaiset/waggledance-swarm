# Phase 16B P1 — Stable gate inventory

**Stable target:** `v3.8.0`
**Prerelease fallback:** `v3.7.6-stabilization-alpha`
**Fail-closed rule:** v3.8.0 stable is allowed only if **every** required-for-stable gate is PASS on the post-merge `main` SHA. Unknown / partial / not-tested / local-only / tool-unavailable / branch-only blocks stable.

Machine-readable ledger: [`stable_gate_inventory.json`](stable_gate_inventory.json).

## Gate summary

| # | Gate | Required for stable | Required for prerelease | Status @ P1 |
|---|---|---|---|---|
| g01 | Docker end-to-end proof tested | yes | no | **pending** (P6) |
| g02 | Remote/fresh clone reproduction verified against post-merge main | yes | no | **pending** (P5 + P13.5) |
| g03 | Latest proof reproducible by copy-paste commands from repo root | yes | yes | **pass** |
| g04 | 100+ auto-promoted low-risk solvers in proof | yes | no | **pending** (P4; current 98) |
| g05 | Security self-audit completed | yes | yes | **pending** (P7) |
| g06 | CI green on PR head SHA | yes | yes | **pending** (P13) |
| g07 | README top section accurate | yes | yes | **pass** |
| g08 | RELEASE_READINESS latest-tag truth accurate | yes | yes | **pass** |
| g09 | No provider dependency in inner loop | yes | yes | **pass** |
| g10 | Stage-2 atomic flip remains unexecuted | yes | yes | **pass** |
| g11 | Phase 9 14-stage human-gated promotion ladder unchanged | yes | yes | **pass** |
| g12 | Six-family low-risk allowlist unchanged | yes | yes | **pass** |
| g13 | No consciousness / sentience / AGI / alive claim | yes | yes | **pass** |
| g14 | Full-corpus restart continuity proven | yes | no | **pending** (P2) |
| g15 | Proof soak / repeatability passed with no flakes | yes | no | **pending** (P3) |
| g16 | Install commands consistent across docs/CI | yes | yes | **pending** (P8) |
| g17 | HTTP/API-route stable requirement explicitly decided | yes | no | **pending** (P8) |
| g18 | Tag/release verification plan documented | yes | yes | **in_progress** (this doc + P14) |

## Stable eligibility @ P1

**Not yet eligible.** Multiple required-for-stable gates are pending P2–P10 work. Per the master prompt's fail-closed rule, the default release outcome is **`v3.7.6-stabilization-alpha`** unless every required-for-stable gate proves PASS by P11.

## Phase plan to reach a decision

| phase | gates addressed |
|---|---|
| P2 | g14 (full-corpus restart) |
| P3 | g15 (proof soak) |
| P4 | g04 (100+ corpus) |
| P5 | g02 (local + branch-ref reproduction; final at P13.5) |
| P6 | g01 (Docker) |
| P7 | g05 (security self-audit) |
| P8 | g16 (install truth), g17 (HTTP route stable decision) |
| P9 | g07, g08, g13 (docs truth refresh) |
| P10 | targeted tests + sanity for g03, g09, g11, g12 |
| P11 | release-decision aggregation |
| P13 | g06 (CI green on PR head) |
| P13.5 | g02 final (post-merge fresh-clone) |
| P14 | g18 (tag + release metadata verification) |

## Gates already PASS at P1

These six gates are already truthful from Phase 16A and require no new work:

* **g03** — both Phase 15 and Phase 16A proofs are reproducible from repo root.
* **g07** — README top section is concise (≤10 lines), distinguishes alpha vs not-implemented, and explicitly states `Consciousness? No`.
* **g08** — RELEASE_READINESS latest-tag truth was fixed in Phase 16A (`v3.7.4-runtime-hint-alpha` for Phase 15, `v3.7.5-upstream-runtime-alpha` recommended next).
* **g09** — both proofs record `provider_jobs_delta = builder_jobs_delta = 0`.
* **g10** — atomic flip remains preparation-only.
* **g11**, **g12** — promotion ladder and six-family allowlist verified intact.
* **g13** — no consciousness claim; verified across README / CHANGELOG / REPOSITORY_PRESENTATION / RELEASE_READINESS.

## Gates that almost certainly block stable v3.8.0

Based on the Phase 16A development environment and master-prompt rules:

* **g01 Docker** — Docker Engine has not been available in this development shell across multiple recent phases. Likely PARTIAL/UNAVAILABLE → blocks stable.
* **g02 Remote/fresh clone** — must be verified against `origin/main` after the Phase 16B PR merges. P5 may verify the branch ref, but final stable verification requires P13.5 against post-merge main.
* **g04 100+ corpus** — current 98 in Phase 16A proof. P4 may safely expand the seed library, but expansion must round-trip through real promotion to count.
* **g05 Security self-audit** — `bandit` / `pip-audit` may not be available; manual audit alone may be insufficient for stable.

If any one of g01 / g02 / g04 / g05 fails, stable is blocked and the correct successful outcome is `v3.7.6-stabilization-alpha`.
