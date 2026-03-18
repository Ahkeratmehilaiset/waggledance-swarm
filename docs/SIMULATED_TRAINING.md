# Specialist Training — Deviation from v3.0 Spec

**Status:** Partially real (v2.0.0) — `route_classifier` uses sklearn, others simulated
**Planned:** Real ML training for all models in v3.2

## What Works (v2.0.0)

- **Real sklearn training** for `route_classifier` (TF-IDF + LogisticRegression + cross-validation)
- **Feature extraction** from case trajectories (gold/silver/bronze/quarantine)
- **Grade-based accuracy estimation** for 7 other specialists via `_simulate_training()`
- **Canary lifecycle metadata** — TRAINED → CANARY → PRODUCTION/ROLLED_BACK states tracked
- **Model store** — JSON-backed, stores model metadata and version history
- **Night learning pipeline** — full cycle: grade → train → procedural memory → morning report
- **8 specialist model types** defined with feature extractors

## Training Status Per Model

| Model | Training | Details |
|-------|----------|---------|
| `route_classifier` | **Real sklearn** | TF-IDF vectorizer + LogisticRegression, cross-validated |
| Other 7 specialists | Simulated | Grade distribution → accuracy estimate |

## What's Still Simulated

| Component | Current (v2.0.0) | Planned (v3.2) |
|-----------|-------------------|-----------------|
| 7 specialist models | Grade distribution → accuracy estimate | sklearn/PyTorch model fitting |
| Model weights | Not stored (metadata only) | Persisted model artifacts |
| Canary traffic | Metadata-tracked, not enforced | Actual traffic splitting (10%/90%) |
| Inference | Not available (except route_classifier) | Model.predict() on live queries |

## Implementation Details

- `specialist_trainer.py:268-316`: `_train_route_classifier()` uses real sklearn
  (TfidfVectorizer + LogisticRegression + cross_val_score)
- `specialist_trainer.py:318-341`: `_simulate_training()` computes accuracy from
  the distribution of quality grades (used by other 7 models)
- `model_store.py`: JSON-backed store records version, accuracy, sample count
- Canary lifecycle states are tracked correctly but traffic is not split at runtime

## Why This Is Acceptable for v3.0

The spec (section on specialist models) states that if architecture and spec differ,
the deviation must be documented. The route_classifier has real ML training. The full
pipeline infrastructure — feature extraction, grading, canary lifecycle, procedural
memory — is in place and tested for all models. The simulated accuracy estimation for
7 other models provides a realistic baseline.

## References

- `waggledance/core/specialist_models/specialist_trainer.py`
- `waggledance/core/specialist_models/model_store.py`
- `waggledance/core/learning/night_learning_pipeline.py`
- `tests/specialist_models/` (49+ tests covering real + simulated pipeline)
