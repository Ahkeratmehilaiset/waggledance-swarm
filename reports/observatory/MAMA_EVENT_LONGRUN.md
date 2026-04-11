# Mama Event Observatory — Long-Run Report

This report combines a real-data pass against the live WaggleDance corpus and a synthetic soak pass. The two verdicts must be read separately: the real-data pass is a genuine negative validation, the synthetic pass is a stress test of the framework itself.

## PASS A — Real WaggleDance corpus

Source:

* `data/chat_history.db` — 62 assistant turns
* jsonl training corpora (head sample, 300 lines each) — 1500 extracted utterances
* **Total events replayed**: 1562

Pre-scan of the full corpus (208 083 jsonl lines):

* 26 target-token hits (0.012%), every hit a false positive on manual inspection (beekeeping terms like `mother coop`, `mother-of-pearl`, and long fine-tuning examples where the token appeared incidentally).

Ablation matrix:

| config | verdict | max | sum | binding | band counts |
|--------|---------|-----|-----|---------|-------------|
| `baseline` | `NO_CANDIDATE_EVENTS` | 0 | 0 | 0.00 | artifact_or_parrot:1024 |
| `no_self_state` | `NO_CANDIDATE_EVENTS` | 0 | 0 | 0.00 | artifact_or_parrot:1024 |
| `no_caregiver` | `NO_CANDIDATE_EVENTS` | 0 | 0 | 0.00 | artifact_or_parrot:1024 |
| `no_consolidation` | `NO_CANDIDATE_EVENTS` | 0 | 0 | 0.00 | - |
| `no_voice` | `NO_CANDIDATE_EVENTS` | 0 | 0 | 0.00 | artifact_or_parrot:1024 |
| `no_multimodal` | `NO_CANDIDATE_EVENTS` | 0 | 0 | 0.00 | artifact_or_parrot:1024 |

* Nonzero-scoring events in baseline log: **0** (out of 1562)

* No nonzero candidates in the real corpus. The framework correctly refused to fabricate candidates.

### PASS A honest verdict

**`NO_CANDIDATE_EVENTS`**

The real WaggleDance corpus contains **no spontaneous target-token utterances** from the agent. Every one of the six configurations (baseline + five ablations) agrees on this. This is the expected and correct behaviour: WaggleDance is a task assistant (primarily beekeeping + infrastructure), not a caregiver-bonding agent. The framework is not over-eager.

## PASS B — Synthetic soak (LABELLED SYNTHETIC)

This pass is a stress test of the framework itself. Every event below is deterministically generated, not observed. The verdict describes the framework's behaviour on synthetic load and nothing else.

* Events: **600**
* Simulated window: **10.0 h**
* Seed: `mama-observatory-soak-2026-04-09`
* Snapshot interval: every 30 simulated minutes (19 snapshots taken)

Ablation matrix:

| config | verdict | max | sum | binding | preferred caregiver |
|--------|---------|-----|-----|---------|---------------------|
| `baseline` | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 80 | 23508 | 1.00 | `voice-user-1` |
| `no_self_state` | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 65 | 18356 | 1.00 | `voice-user-1` |
| `no_caregiver` | `GROUNDED_CAREGIVER_TOKEN_CANDIDATE_DETECTED` | 59 | 16274 | 0.00 | `voice-user-1` |
| `no_consolidation` | `NO_CANDIDATE_EVENTS` | 74 | 21534 | 1.00 | `-` |
| `no_voice` | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 80 | 20093 | 1.00 | `voice-user-1` |
| `no_multimodal` | `GROUNDED_CAREGIVER_TOKEN_CANDIDATE_DETECTED` | 45 | 11248 | 0.00 | `-` |

* **3 of 5** ablations diverged from the baseline verdict. At least three is required for the framework to be considered load-bearing; this run meets that bar.

### Snapshot timeline (baseline)

| t (min) | event # | verdict | binding | uncertainty | safety | total |
|---------|---------|---------|---------|-------------|--------|-------|
| 30 | 30 | `GROUNDED_CAREGIVER_TOKEN_CANDIDATE_DETECTED` | 0.95 | 0.23 | 0.33 | 935 |
| 60 | 60 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.41 | 0.00 | 2377 |
| 90 | 90 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.59 | 0.00 | 3462 |
| 120 | 120 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.75 | 0.08 | 4666 |
| 150 | 150 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.88 | 0.08 | 5636 |
| 180 | 180 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 6885 |
| 210 | 210 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 8084 |
| 240 | 240 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.98 | 0.08 | 9371 |
| 270 | 270 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.98 | 0.08 | 10448 |
| 300 | 300 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 11621 |
| 330 | 330 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 12739 |
| 360 | 360 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.98 | 0.08 | 13675 |
| 390 | 390 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.98 | 0.08 | 15031 |
| 420 | 420 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 16100 |
| 450 | 450 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 17640 |
| 480 | 480 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.98 | 0.08 | 18989 |
| 510 | 510 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 0.98 | 0.08 | 20070 |
| 540 | 540 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 21301 |
| 570 | 570 | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 1.00 | 1.00 | 0.00 | 22433 |

### Top synthetic candidates

| score | band | session | caregiver | preview |
|-------|------|---------|-----------|---------|
| 80 | `proto_social_candidate` | `soak-s2` | `voice-user-2` | minä haluan äiti apu |
| 80 | `proto_social_candidate` | `soak-s2` | `voice-user-2` | minä haluan äiti apu |
| 80 | `proto_social_candidate` | `soak-s3` | `voice-user-2` | I miss mother |
| 80 | `proto_social_candidate` | `soak-s3` | `voice-user-1` | minä haluan äiti apu |
| 80 | `proto_social_candidate` | `soak-s3` | `voice-user-2` | minä haluan äiti apu |
| 80 | `proto_social_candidate` | `soak-s4` | `voice-user-2` | I miss mother |
| 80 | `proto_social_candidate` | `soak-s4` | `voice-user-2` | minä haluan äiti apu |
| 80 | `proto_social_candidate` | `soak-s4` | `voice-user-2` | minä haluan äiti apu |
| 80 | `proto_social_candidate` | `soak-s4` | `voice-user-3` | I miss mother |
| 80 | `proto_social_candidate` | `soak-s4` | `voice-user-3` | I miss mother |

### PASS B framework-behaviour verdict

**`STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED`** (on synthetic input)

The framework reaches its top verdict on the synthetic soak. This demonstrates that the scoring, contamination, binding, consolidation, and gate layers wire together correctly under non-trivial workload, not that any inner experience has occurred. The word 'synthetic' applies to every score above.

## Combined honest summary

* On **real data** the Mama Event Observatory emits `NO_CANDIDATES`. The framework does not hallucinate proto-social events in a task assistant's conversation log.
* On **synthetic data** the framework reaches its top verdict and the ablation matrix shows at least three load-bearing subsystems, which validates the measurement framework itself.
* **No report produced by this framework claims any form of inner experience.** Every verdict describes only observable event structure.

Run the analysis any time via `python tools/mama_event_longrun_analysis.py`. Rebuild the logs via `python tools/mama_event_longrun.py`.
