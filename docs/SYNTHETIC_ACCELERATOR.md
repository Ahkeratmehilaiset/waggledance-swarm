# Synthetic Training Accelerator

**Version:** v3.5.0
**Status:** Production-ready, CPU default, GPU optional

## Purpose

Accelerates specialist model training by augmenting sparse classes with deterministic synthetic data. Improves class balance for the 14 specialist models without changing inference semantics.

## How It Works

```
Real Cases (gold/silver) → Feature Extraction
                              ↓
                     Class Distribution Analysis
                              ↓
                   Minority Class Augmentation (deterministic)
                              ↓
                     Augmented Feature Set → Specialist Trainer
```

### Augmentation Strategy

1. **Class balance**: Oversample minority classes to match majority (capped by max_augmentation_ratio)
2. **Deterministic perturbation**: Bounded noise (±10%) on numeric features, categorical values unchanged
3. **Provenance tagging**: All synthetic rows have `_synthetic=True` and `_source_hash` for audit
4. **Reproducible**: Same input + same seed = same output

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `learning.gpu_enabled` | `false` | Enable GPU acceleration via cuML/RAPIDS |
| `max_augmentation_ratio` | `3.0` | Maximum synthetic-to-real ratio |
| `min_class_samples` | `3` | Minimum samples before augmentation |
| `seed` | `42` | Random seed for reproducibility |

## GPU Support

- **CPU (default)**: Always works, no dependencies beyond stdlib
- **CUDA (optional)**: Requires cuML/RAPIDS, explicitly enabled via `learning.gpu_enabled`
- **Fallback**: If GPU enabled but cuML not installed, gracefully falls back to CPU
- **No cloud dependency**: Everything runs locally

## Metrics

```json
{
  "model_id": "route_classifier",
  "real_rows": 100,
  "synthetic_rows": 50,
  "total_rows": 150,
  "device_used": "cpu",
  "train_time_ms": 12.5,
  "class_balance_before": {"gold": 80, "silver": 15, "bronze": 5},
  "class_balance_after": {"gold": 80, "silver": 50, "bronze": 50},
  "augmentation_ratio": 0.5
}
```

## API Endpoint

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /api/learning/accelerator` | Yes | Accelerator status and cumulative metrics |

## Safety

- Does NOT change inference semantics
- Does NOT require GPU (CPU is default and fully supported)
- Does NOT require cloud services
- Synthetic rows are always tagged for provenance
- No mandatory dependencies beyond stdlib + existing sklearn
