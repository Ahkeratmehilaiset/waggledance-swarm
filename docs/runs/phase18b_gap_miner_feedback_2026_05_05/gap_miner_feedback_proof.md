# Phase 18B - Runtime Gap Miner + Solver Feedback Loop Proof

**Benchmark version:** phase18b.v1
**Git SHA:** 2d32b9b2267d271508d689f94f4631e2965f3be2
**Python:** 3.13.7
**Platform:** Windows-11-10.0.22631-SP0
**Started UTC:** 2026-05-05T14:05:57Z
**Finished UTC:** 2026-05-05T14:05:57Z

## Honesty declarations

* No cloud API calls were made.
* No model was pulled or downloaded.
* No live builder execution.
* No Stage-2 atomic flip.
* No HUMAN_APPROVAL collected.
* Six-family low-risk allowlist unchanged.

## Counters

| metric | value |
| --- | ---: |
| signals_total | 30 |
| candidates_total | 14 |
| ALLOWLISTED_SOLVER_SPEC | 6 |
| INSUFFICIENT_EVIDENCE | 3 |
| OUT_OF_FAMILY_REJECTED | 2 |
| HIGH_RISK_REJECTED | 1 |
| BUILDER_HANDOFF_QUARANTINED | 1 |
| DUPLICATE_SUPPRESSED | 1 |
| solver_specs_total | 6 |
| capability_lookup_status | NOT_RUN_OUT_OF_PHASE18B_SCOPE |

## Allowlist + provider/builder invariants

* `allowlist_unchanged`: **True**
* `provider_jobs_delta`: 0
* `builder_jobs_delta`: 0
* `no_stage2_flip`: True
* `no_human_approval`: True
* `is_synthetic_fixture`: True

## Per-family allowlisted candidates

| family_kind | spec_id | confidence | signal_count |
| --- | --- | ---: | ---: |
| `scalar_unit_conversion` | `154e99e6e8c230ed` | 0.800 | 3 |
| `lookup_table` | `affb3e81ededf350` | 0.800 | 3 |
| `threshold_rule` | `df8967a3d288be07` | 0.800 | 3 |
| `interval_bucket_classifier` | `cc1a87367301deb5` | 0.800 | 3 |
| `linear_arithmetic` | `926ece2941dd8f37` | 0.800 | 3 |
| `bounded_interpolation` | `1599768241387bc9` | 0.780 | 2 |

## Release gate

* `release_gate_pass`: **True**
* `forbidden_claims_absent`: **True**

## What this proves

* Runtime gap signals can be mined into structured, auditable
  candidates with deterministic SHA-256-derived IDs.
* Six-family low-risk allowlist policy is enforced fail-closed:
  out-of-family inputs are rejected; high-risk inputs are
  rejected; builder-handoff is quarantined with
  no_auto_promotion=true.
* Allowlisted candidates produce deterministic solver specs
  ready for the existing six-family low-risk solver bootstrap
  path. The proof harness emits the specs but does not promote
  them through the runtime path - that is the existing
  Phase 9 14-stage promotion ladder's job.
* No provider call, no builder call, no cloud API, no model
  pull, no Stage-2 flip, no HUMAN_APPROVAL.

## What this does NOT prove

* Does NOT prove every produced spec compiles end-to-end through
  the existing solver-bootstrap API. That is a follow-up
  integration step (see `capability_lookup_status` field).
* Does NOT prove anything about raw-intelligence quality.
  Phase 18B emits structured candidates only; no model is
  consulted.
* Does NOT make any cross-vendor ranking claim.

