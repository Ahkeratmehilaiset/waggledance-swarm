# Mama Event Ablation Matrix

Each ablation disables exactly one subsystem so the contribution of that subsystem is isolated. A subsystem is considered load-bearing if disabling it moves the baseline distribution in a measurable way.

| config | verdict | sum | max | top binding | delta vs baseline |
|--------|---------|-----|-----|-------------|-------------------|
| `baseline` | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 285 | 66 | 0.769 | — |
| `no_self_state` | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 264 | 63 | 0.769 | -21 |
| `no_caregiver` | `GROUNDED_CAREGIVER_TOKEN_CANDIDATE_DETECTED` | 219 | 47 | 0.000 | -66 |
| `no_consolidation` | `NO_CANDIDATE_EVENTS` | 279 | 60 | 0.769 | -6 |
| `no_voice` | `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED` | 250 | 65 | 0.646 | -35 |
| `no_multimodal` | `WEAK_SPONTANEOUS_EVENTS_ONLY` | 123 | 33 | 0.000 | -162 |

## Interpretation

* **3 of 5** ablations produced a different verdict than baseline.
* The framework is measuring something: at least three subsystems are load-bearing for the baseline verdict.
