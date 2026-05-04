# Local AI Runtime — comparison framework

**Date:** 2026-05-04
**Status:** Phase 17A. Engineering reference, not marketing.

This document describes how to compare WaggleDance with other local-first AI runtimes on a per-axis evidence basis. It does **not** publish such a comparison here, because we have not run paired benchmarks against named rivals in this session. What it does instead:

1. Defines the comparison axes that matter for a local-first cognitive runtime.
2. Records WaggleDance's per-axis evidence (drawn from `COMPETITIVE_EVIDENCE_MATRIX_2026.md`).
3. Identifies what evidence a fair comparison would need from any rival.
4. Calls out the failure modes of unfair comparisons (cherry-picked tasks, missing methodology, unverifiable LLM-fallback rates).

WaggleDance does not claim to be conscious, sentient, aware, alive, or AGI. The forbidden vocabulary listed in the Phase 17A master prompt rule 15 is excluded from this document.

## Comparison axes

For each axis a fair comparison must collect: **what** the runtime does, **how** it was measured, **what was held constant**, and **what was variable**. Axes are clustered into "deployment" (D) and "behaviour" (B).

### Deployment axes

#### D1. Image / install size

| metric | how to measure |
|---|---|
| Container image | `docker images <runtime>` after `docker build`. |
| Cold-install pip / npm cost | wall-clock time for the documented `install` step on a clean Python 3.13 / Node 20 environment. |
| Runtime memory | RSS after a smoke proof completes. |

**WaggleDance evidence:** Phase 16F image is 3.09 GB (`python:3.13-slim` + `requirements-ci.txt`). Cold install is ~3.5 min on the measured host. `tools/run_full_restart_continuity_proof.py` runs to completion within ~80 MB extra RSS over the import baseline.

#### D2. Provider dependency

| metric | how to measure |
|---|---|
| Inner-loop provider calls during a documented proof | runtime metric `provider_jobs_delta_during_proof`. |
| Provider credentials required at install time | `requirements-ci.txt` + Dockerfile inspection. |
| Network reachability required at runtime | `docker run --rm --network none` proofs. |

**WaggleDance evidence:** `provider_jobs_delta_during_proof = 0` across every Phase 11–17A canonical proof. No provider credential is required at install or runtime. `--network none` proofs pass. Documented in `docs/runs/phase16f_docker_stable_gate_2026_05_03/docker_runtime_proofs.md` and Phase 17A `producer_fabric_proof.json` + `solver_scale_proof.json`.

#### D3. Reproducibility from clean clone

| metric | how to measure |
|---|---|
| Fresh-clone proof | clone from public GitHub HTTPS URL into a tmp dir; run the full proof suite; compare counts. |
| Determinism across two runs | hash the proof JSON; compare. |

**WaggleDance evidence:** Phase 16F `fresh_clone_reproduction.md`; Phase 17A `test_proof_deterministic_across_two_runs` asserts identical `pinned_input_manifest_sha256` and identical `curiosity_log` byte content across two consecutive runs.

#### D4. Edge-class fitness

| metric | how to measure |
|---|---|
| ARM build (Apple Silicon, Pi 4 / Pi 5) | `docker buildx build --platform linux/arm64`. |
| Inference latency on edge hardware | run the canonical proofs on a Pi 5 / Apple M-class box; record p50/p95. |

**WaggleDance evidence:** **MISSING.** No ARM build was published in this session. This is a strengthening path; not claimed.

### Behaviour axes

#### B1. Deterministic routing depth

| metric | how to measure |
|---|---|
| Fraction of inner-loop queries served by deterministic solver | `coverage` = `served_total / total_queries`. |
| Capability-lookup hit rate (vs FIFO fallback / miss) | `lookup_capability_hits_total / lookup_pass_count`. |

**WaggleDance evidence:** 100 % capability-lookup hits at 10k descriptors / 1k lookups in Phase 17A scale proof; 0 FIFO fallback; 0 miss.

#### B2. Audit / replay completeness

| metric | how to measure |
|---|---|
| Fraction of solver calls with a MAGMA / control-plane event row | replay the proof and compare event-row count against call counter. |
| Restart continuity | close + reopen the DB; assert byte-stable persisted counts. |

**WaggleDance evidence:** Phase 16B P2 / Phase 16D / Phase 17A `full_restart_continuity_proof.json`. Pre/post reopen `solver_count` and `capability_features` byte-identical.

#### B3. Producer / observer separation

| metric | how to measure |
|---|---|
| Producer plane(s) emit IR objects | run the producer fabric proof; count IR objects per kind. |
| Consumer (IR adapter) accepts producer output without modification | inspect adapter source vs producer output JSON. |

**WaggleDance evidence:** Phase 17A producer fabric proof emits 68 IR objects across 6 kinds (curiosity, self_model, dream_curriculum, dream_meta_proposal, hive_proposals, review_bundle); the existing main IR adapters consume them without code changes.

#### B4. Capability-aware dispatch at scale

| metric | how to measure |
|---|---|
| Synthetic descriptor scale ceiling | `tools/run_solver_scale_proof.py --descriptors N` until lookup latency or hit-rate degrades. |
| Lookup p50 / p95 / p99 at scale | from `solver_scale_proof.json`. |

**WaggleDance evidence:** at 10,000 synthetic descriptors: build 147 s, p50 4.2 ms, p95 10.8 ms, p99 14.1 ms, 1000/1000 capability hits. A higher ceiling has not been measured this session.

#### B5. Autonomous learning bounded by safety gate

| metric | how to measure |
|---|---|
| Auto-promotion within the six-family allowlist | `auto_promotions_total` from a canonical proof. |
| High-risk family auto-promotion attempt | runtime invariant test must reject. |

**WaggleDance evidence:** `auto_promotions_total = 128` at corpus 128. High-risk variants are explicitly blocked and documented in `HIGH_RISK_VARIANTS_DEFERRED.md`; Phase 17A producer-fabric negative cases assert HUMAN_APPROVAL collection and Stage-2 flip requests are rejected in build/proof sessions.

#### B6. LLM-fallback hybrid

| metric | how to measure |
|---|---|
| Inner-loop coverage with LLM disabled | `coverage` from a documented benchmark. |
| Joint accuracy with LLM fallback enabled | rerun same benchmark with provider lane on. |

**WaggleDance evidence:** **NOT MEASURED this session.** The architecture supports both modes; the hybrid has not been benchmarked here. Strengthening path noted.

#### B7. Raw reasoning quality on free-form benchmarks

| metric | how to measure |
|---|---|
| Score on BBH, MMLU, GSM8K, HumanEval, ARC, GPQA, etc. | a proper benchmark harness with documented prompt format and full inputs. |

**WaggleDance evidence:** **NOT CLAIMED.** No external reasoning benchmark was run in this session. Any claim that WaggleDance beats / matches / loses to a frontier MoE model on raw reasoning is unsupported by this repo.

## Failure modes of unfair comparisons

Avoid these when reading or writing comparison docs:

* **Cherry-picked tasks.** Picking only tasks the runtime wins on. Counter: report `coverage` and full task-set with no exclusions.
* **Missing methodology.** Not stating prompt format, sampling parameters, retry policy, fallback policy. Counter: include the exact `tools/run_*` invocation that produced the score.
* **Unverifiable LLM-fallback rates.** Reporting a hybrid score without saying how often the deterministic path won and how often the LLM was consulted. Counter: always publish per-query `route_source`.
* **Image-size sleight of hand.** Comparing a stripped-down image vs a full development environment. Counter: compare `requirements-ci.txt` vs the rival's documented production install.
* **Single-machine scaling claims.** Calling a 1-host benchmark "production scale." Counter: explicitly label as `synthetic-scale, NOT canonical proof corpus` (Phase 17A scale proof) or "single-machine" (everything else).
* **Consciousness / sentience claims.** None should appear. WaggleDance has none, and this document does not invite any.

## Reproducing this comparison framework against a rival

A fair comparison would proceed as:

1. Pick the axes you want to compare on. Record evidence requirements per axis from this document.
2. Run WaggleDance's proofs on a fixed host. Save artifacts.
3. Run the rival's documented proofs on the **same** host with the **same** OS. Save artifacts.
4. Compute one number per axis per runtime, with confidence intervals if the proof is non-deterministic.
5. Publish both artifact sets and the host spec.
6. Do not extrapolate — if you didn't measure axis Z, leave Z blank.

This document is the framework. The actual numbers, when collected, belong in `docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md` (per-axis label) and a follow-up `docs/benchmarks/<rival>_vs_waggledance_<date>.md` (paired comparison).
