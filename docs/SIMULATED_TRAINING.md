# Simulated Training — Deviation from v3.0 Spec

**Status:** Documented deviation (v2.0.0)
**Planned:** Real ML training in v3.2

## What Works (v2.0.0)

- **Feature extraction** from case trajectories (gold/silver/bronze/quarantine)
- **Grade-based accuracy estimation** via `SpecialistTrainer._simulate_training()`
- **Canary lifecycle metadata** — TRAINED → CANARY → PRODUCTION/ROLLED_BACK states tracked
- **Model store** — JSON-backed, stores model metadata and version history
- **Night learning pipeline** — full cycle: grade → train → procedural memory → morning report
- **8 specialist model types** defined with feature extractors

## What's Simulated

| Component | Current (v2.0.0) | Planned (v3.2) |
|-----------|-------------------|-----------------|
| Training | Grade distribution → accuracy estimate | sklearn/PyTorch model fitting |
| Model weights | Not stored (metadata only) | Persisted model artifacts |
| Canary traffic | Metadata-tracked, not enforced | Actual traffic splitting (10%/90%) |
| Inference | Not available | Model.predict() on live queries |

## Implementation Details

- `specialist_trainer.py:253-276`: `_simulate_training()` computes accuracy from
  the distribution of quality grades in training cases
- `model_store.py`: JSON-backed store records version, accuracy, sample count — no
  actual model binary
- Canary lifecycle states are tracked correctly but traffic is not split at runtime

## Why This Is Acceptable for v3.0

The spec (section on specialist models) states that if architecture and spec differ,
the deviation must be documented. The full pipeline infrastructure — feature
extraction, grading, canary lifecycle, procedural memory — is in place and tested.
Only the actual ML fitting step is simulated.

## References

- `waggledance/core/specialist_models/specialist_trainer.py`
- `waggledance/core/specialist_models/model_store.py`
- `waggledance/core/learning/night_learning_pipeline.py`
- `tests/specialist_models/` (41 tests covering the simulated pipeline)
