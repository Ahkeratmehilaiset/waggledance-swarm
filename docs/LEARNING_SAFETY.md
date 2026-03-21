# Learning Safety

WaggleDance implements multiple safety layers to prevent harmful or incorrect learning.

## Canary Gate

Every prompt evolution and micromodel update passes through a canary gate before production deployment:

- **Minimum samples**: At least 50 evaluation samples before promotion
- **Minimum improvement**: Must improve by at least 5% over baseline
- **Auto-rollback**: If post-deployment quality drops > 0.05 below baseline, automatic rollback
- **Config disable**: Set `enabled: false` to disable canary entirely

## Hallucination Detection

The `HallucinationChecker` (`core/hallucination_checker.py`) flags suspicious LLM answers:

- **Embedding similarity** (weight 0.3): cosine similarity between question and answer vectors
- **Keyword overlap** (weight 0.7): fraction of question keywords found in the answer
- **Hard gate**: zero keyword overlap + low embedding similarity = always suspicious
- **Threshold**: combined score < 0.45 = suspicious

## Bounded Self-Tuning

Route telemetry (`core/route_telemetry.py`) adjusts routing thresholds automatically:

- **Maximum adjustment**: ±0.05 per tuning cycle
- **Floor/ceiling**: thresholds clamped to [0.1, 0.95]
- **Minimum samples**: 10+ observations required before any adjustment
- **Config disable**: `tuning_enabled=False` to freeze thresholds

## Trust Store

Agent trust scores use 6 weighted signals (see `waggledance/core/domain/trust_score.py`):

- (1 - hallucination_rate) * 0.25
- validation_rate * 0.20
- consensus_agreement * 0.20
- (1 - correction_rate) * 0.15
- fact_production_rate * 0.10
- freshness_score * 0.10

New agents start at composite_score = 0.5 and must earn trust through accurate responses.

## English Source Learning

`EnglishSourceLearner` enforces:

- **Strict allowlist**: only pre-configured URLs are accepted
- **Budget**: max N facts per learning cycle
- **Provenance**: every fact records its source URL
- **Translation**: English facts are translated to Finnish before storage
