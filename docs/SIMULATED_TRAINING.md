# Specialist Training — Simulation as Fallback Only

**Status:** All 8 models use **real sklearn training** (v3.2)
**Fallback:** `_simulate_training()` retained for when sklearn is unavailable or single-class data

## Training Status Per Model

| Model | Algorithm | X (features) | y (target) |
|-------|-----------|--------------|------------|
| `route_classifier` | TF-IDF + LogisticRegression | goal_type + profile (text) | primary capability_id |
| `capability_selector` | LogisticRegression | goal_type (encoded) | primary capability_id |
| `anomaly_detector` | IsolationForest | residuals (numeric) | unsupervised (outlier score) |
| `baseline_scorer` | DecisionTreeClassifier | goal_type + profile (encoded) | grade |
| `approval_predictor` | LogisticRegression | goal_type + profile (encoded) | approved (gold/silver → True) |
| `missing_var_predictor` | DecisionTreeClassifier | goal_type + profile (encoded) | grade |
| `verifier_prior` | LogisticRegression | capabilities (encoded) | verifier_passed (bool) |
| `domain_language_adapter` | LogisticRegression | profile (encoded) | goal_type |

## When Simulation Still Activates

`_simulate_training()` is called as a fallback in these cases:

1. **sklearn not installed** — ImportError caught, falls back gracefully
2. **Single class in labels** — cross-validation requires ≥2 classes
3. **Training error** — any unexpected exception during sklearn training

The simulation estimates accuracy from quality grade distribution:
`accuracy = 0.7 + (gold_ratio × 0.2) + (silver_ratio × 0.1)`, capped at 0.99.

## References

- `waggledance/core/specialist_models/specialist_trainer.py`
- `waggledance/core/specialist_models/model_store.py`
- `tests/specialist_models/test_real_sklearn_training.py` (11 tests)
- `tests/specialist_models/test_real_training.py` (existing route_classifier tests)
