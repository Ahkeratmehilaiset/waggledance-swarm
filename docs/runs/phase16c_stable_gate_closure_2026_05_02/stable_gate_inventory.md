# Phase 16C P1 — Stable gate ledger (initial)

**Stable target:** `v3.8.0`
**Prerelease fallback:** `v3.7.7-stable-gate-alpha`
**No-tag fallback:** no tag if no material progress

**Fail-closed rule:** v3.8.0 stable is allowed only if **every** required-for-stable gate is PASS on the post-merge `main` SHA.

Machine-readable ledger: [`stable_gate_inventory.json`](stable_gate_inventory.json).

## Inherited from Phase 16B

| status | gates |
|---|---|
| PASS (carry forward) | g02 (remote/fresh-clone vs post-merge main, P13.5), g03 (README), g04 (provider/builder Δ), g06 (100+ corpus = 104), g07 (full-corpus restart), g08 (proof soak no-flake), g09 (Stage-2 unexec), g10 (allowlist), g11 (no actuator), g12 (no high-risk), g13 (release docs), g16 (CI green for PR #64), g17 (Docker docs truthful), g18 (no consciousness claim), g14/g15 (release/tag verification — passed for v3.7.6) |
| PARTIAL — Bandit unavailable | g05 |
| FAIL — Docker not tested | g01 |

## Phase 16C target

Close g01 (Docker) and g05 (Bandit), or document why they remain blocked. Re-verify all carry-forward gates to make sure nothing regressed.

## Decision logic at P8

| condition | outcome |
|---|---|
| g01 PASS AND g05 PASS AND all carry-forward PASS AND P9-P10 verifications PASS | **`v3.8.0` stable** |
| g01 or g05 still blocked, but Phase 16C made material progress (e.g. Bandit run, even if Docker still unavailable) | **`v3.7.7-stable-gate-alpha` prerelease** |
| no material progress beyond Phase 16B | **no tag**; final report only |

## Phase plan

| phase | gates addressed |
|---|---|
| P2 | g05 (Bandit) |
| P3 | g01 (Docker) |
| P4 | g04, g06, g07, g08 (re-verify proofs) |
| P5 | g02 (local/branch-ref reproduction; final remote/fresh in P10) |
| P6 | g03, g13, g17, g18 (release docs truth) |
| P7 | (targeted tests sanity for g04, g10–g12) |
| P8 | release decision aggregation |
| P9 | g16 (CI green on PR head) |
| P10 | g02 final (post-merge fresh-clone) |
| P11 | tag |
| P12 | g14, g15 (post-tag verification) |

## Gate count

* required for stable: **18**
* required for prerelease: **15** (subset; g01, g02, g06, g07, g08 are stable-only)
