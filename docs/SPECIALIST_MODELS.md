# Specialist Models — WaggleDance v2.0

> **Note:** The `route_classifier` uses **real sklearn training** (TF-IDF +
> LogisticRegression with cross-validation). The remaining 7 specialists use
> **simulated training** — accuracy estimated from quality grade distributions.
> The full pipeline infrastructure (feature extraction, grading, canary lifecycle)
> is in place and tested. Real ML training for all models is planned for v3.2.
> See [SIMULATED_TRAINING.md](SIMULATED_TRAINING.md) for details.

## Overview

Specialist models are small, locally-trained models that handle specific
tasks better than the general-purpose LLM. They sit at Layer 2 in the
3-tier architecture (between authoritative solvers and LLM fallback).

## Model Types

| Model | Purpose | Training Source |
|-------|---------|----------------|
| `route_classifier` | Predict best routing layer | Route telemetry data |
| `capability_selector` | Rank capabilities for intent | Case trajectories |
| `anomaly_detector` | Detect unusual patterns | Gold/quarantine cases |
| `intent_classifier` | Classify query intent | Labeled intents |
| `quality_predictor` | Predict response quality | Quality grades |
| `risk_scorer` | Assess action risk level | Policy decisions |
| `priority_estimator` | Estimate task priority | Goal priorities |
| `domain_classifier` | Classify domain context | Profile data |

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
