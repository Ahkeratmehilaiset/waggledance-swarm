# Phase 16D P1 — Stable gate ledger (initial)

**Stable target:** `v3.8.0`
**Prerelease fallback:** `v3.7.8-docker-gate-alpha`
**No-tag fallback:** no tag if no material progress / B324 cleanup reverted

**Fail-closed rule:** v3.8.0 stable is allowed only if **every** required-for-stable gate is PASS on the post-merge `main` SHA. Docker unavailable in this dev shell → stable BLOCKED regardless of other gates.

**P0 ENVIRONMENT PRE-CHECK decision:** `stable_v3_8_0_possible_in_this_session = false` (`docker_available = false`). Realistic outcome: B (v3.7.8-docker-gate-alpha) if B324 cleanup PASSES with persisted semantics preserved, OR C (no tag).

Machine-readable ledger: [`stable_gate_inventory.json`](stable_gate_inventory.json).

## Gate snapshot @ P1

| # | gate | required for stable | initial status | stable blocker |
|---|---|---|---|---|
| g01 | Docker end-to-end | yes | **FAIL_NOT_VERIFIED** | yes (carry-over from Phase 16B/16C) |
| g02 | Remote/fresh clone post-merge | yes | PARTIAL (PASS in Phase 16B + 16C; P9.5 + P10 will re-verify) | yes |
| g03 | README current truth | yes | PASS_CARRY (P6 will refresh) | yes |
| g04 | 100+ solver proof | yes | PASS_CARRY (corpus 104) | yes |
| g05 | Security audit / Bandit | yes | **PARTIAL_IMPROVED** (P2 will attempt B324 cleanup) | yes |
| g06 | Provider/builder Δ = 0 | yes | PASS_CARRY (P4 will re-confirm) | yes |
| g07 | Full-corpus restart proof | yes | PASS_CARRY (P4 will re-run) | yes |
| g08 | Proof soak no-flake | yes | PASS_CARRY (P4 will re-soak) | yes |
| g09 | Stage-2 atomic flip unexecuted | yes | PASS | yes |
| g10 | No HUMAN_APPROVAL | yes | PASS | yes |
| g11 | No allowlist widening | yes | PASS | yes |
| g12 | No high-risk auto-promotion | yes | PASS | yes |
| g13 | No actuator autonomy | yes | PASS | yes |
| g14 | Control-plane / MAGMA truth preserved | yes | PASS_CARRY (P2 verifies after B324) | yes |
| g15 | Release docs truth | yes | PENDING_P6 | yes |
| g16 | Tag target correctness | yes | PENDING_P12 | yes |
| g17 | GitHub release metadata correctness | yes | PENDING_P12 | yes |
| g18 | CI green on PR head | yes | PENDING_P9 | yes |
| g19 | Docker runtime no-network proof | yes | **FAIL_NOT_VERIFIED** (Docker unavailable) | yes (same as g01) |
| g20 | Fresh-clone tag fetch verification | yes | PASS_CARRY (P9.5 + P10 will re-verify with v3.7.7 tag) | yes |
| g21 | Persisted semantic fingerprint preservation across B324 cleanup | yes | PENDING_P2 | yes |

## Stable v3.8.0 disposition @ P1

**BLOCKED.** Two required-for-stable gates cannot be PASS in this session:

* **g01 / g19 Docker** — Docker CLI is unavailable; stable blocked regardless of other gates.

The Phase 16D session pursues:

| condition | outcome |
|---|---|
| g05 + g21 PASS (B324 cleanup completes, persisted semantics preserved, Bandit HIGH count = 0) AND g04, g06, g07, g08, g15, g18 confirmed PASS | **`v3.7.8-docker-gate-alpha` prerelease** |
| g21 FAILS (B324 cleanup altered persisted semantic fingerprint, fixes reverted) | **no tag** |
| g05 + g21 PASS but a regression in g04/g06/g07/g08 | **no tag** |

## Phase plan

| phase | gates addressed |
|---|---|
| P2 | g05, g21, g14 (Bandit + persisted semantics) |
| P3 | g01, g19 (documented as carry-over FAIL) |
| P4 | g04, g06, g07, g08 (re-run all proofs at corpus 104) |
| P5 | local clone diagnostic (no g02 update yet) |
| P6 | g03, g15 (docs truth) |
| P7 | targeted tests (sanity for g04, g06, g11, g12, g13, g14) |
| P8 | release decision aggregation (B or C) |
| P9 | g18 (CI green on PR head) |
| P9.5 | g02 (partial), g20 (branch-ref) |
| P10 | g02 (final), g16, g18 (final) |
| P11 | tag creation (B prerelease) |
| P12 | g16, g17 (post-tag verification) |
