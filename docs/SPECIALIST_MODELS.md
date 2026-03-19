# Specialist Models — WaggleDance v3.2

> **All 8 specialist models now use real sklearn training.** Simulated training
> (`_simulate_training`) is retained only as a fallback when sklearn is unavailable
> or when a model has insufficient class diversity for cross-validation.
> See [SIMULATED_TRAINING.md](SIMULATED_TRAINING.md) for fallback details.

## Overview

Specialist models are small, locally-trained models that handle specific
tasks better than the general-purpose LLM. They sit at Layer 2 in the
3-tier architecture (between authoritative solvers and LLM fallback).

## Model Types

| Model | Purpose | Algorithm | Training Source |
|-------|---------|-----------|----------------|
| `route_classifier` | Predict best routing layer | TF-IDF + LogisticRegression | Route telemetry data |
| `capability_selector` | Rank capabilities for intent | LogisticRegression | Case trajectories |
| `anomaly_detector` | Detect unusual patterns | IsolationForest | Gold/quarantine cases |
| `baseline_scorer` | Score baseline quality | DecisionTreeClassifier | Quality grades |
| `approval_predictor` | Predict approval likelihood | LogisticRegression | Policy decisions |
| `missing_var_predictor` | Predict missing variables | DecisionTreeClassifier | Case trajectories |
| `verifier_prior` | Predict verification outcome | LogisticRegression | Verifier results |
| `domain_language_adapter` | Adapt to domain language | LogisticRegression | Profile data |

## Training Pipeline

```
CaseTrajectories → SpecialistTrainer.train(model_name)
  → Feature extraction (per model type)
  → Train on gold/silver cases
  → Generate TrainingResult
  → Store in ModelStore
```

## Canary Lifecycle

All specialist models follow a strict promotion path:

```
TRAINED → CANARY (10% traffic, 48h) → PRODUCTION
                                    → ROLLED_BACK (if accuracy drops)
```

### Canary Evaluation
- Model serves 10% of traffic during canary period (metadata-tracked, not enforced)
- Accuracy tracked against production model
- Auto-promote if accuracy >= threshold after 48h
- Auto-rollback if accuracy drops below baseline

## Model Store

```python
from waggledance.core.specialist_models.model_store import ModelStore

store = ModelStore()
store.save("route_classifier", model_data, metadata)
model = store.load("route_classifier")
versions = store.list_versions("route_classifier")
store.rollback("route_classifier")  # Restore previous version
```

## Integration with Route Telemetry

The `RouteTelemetry.feed_specialist_trainer()` method exports route-level
success/failure data for training the `route_classifier` specialist:

```python
telemetry.feed_specialist_trainer(specialist_trainer)
```

## Key Files

| File | Purpose |
|------|---------|
| `waggledance/core/specialist_models/specialist_trainer.py` | Training logic |
| `waggledance/core/specialist_models/model_store.py` | Model persistence |
| `waggledance/core/learning/quality_gate.py` | Grade cases for training |
| `waggledance/core/learning/night_learning_pipeline.py` | Training orchestration |
