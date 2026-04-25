# Self-model formulas — Phase 8.5 v0.1

All formulas are deterministic and offline. This document is the single source of truth for thresholds; if code disagrees, code is wrong.

## Constants

| Constant | Value | Source |
|---|---|---|
| `HISTORY_WINDOW` | `10` | spec §B2 |
| `GENESIS_PREV_SHA` | `"0" * 64` | spec §B2 |
| `CALIBRATION_DRIFT_THRESHOLD` | `0.20` | spec §B3 |
| `CALIBRATION_OSCILLATION_DAMPEN_AT` | `3` corrections in window | spec §B3 |
| `CALIBRATION_OSCILLATION_FREEZE_AT` | `5` corrections in window | spec §B3 |
| `EVIDENCE_HIGH_MIN` | `8` (cluster size) | derived |
| `EVIDENCE_MEDIUM_MIN` | `3` (cluster size) | derived |

## Scorecard dimensions (v0.1)

```
self_model_maturity                = artifact_grounded_field_ratio × non_default_field_ratio
metacognition_maturity             = blind_spots_detected / expected_domain_count
                                       (+0.1 if calibration_evidence exists; capped at 1.0)
unified_workspace_readiness        = 0.5·(tensions_detectable) + 0.5·(attention_priorities_derivable)
dream_consolidation_readiness      = 0.5·(attention_focus_present) + 0.5·(self_model_delta_derivable)
identity_continuity_strength       = 0.5·(continuity_anchors_present) + 0.5·(invariants_or_ruptures_derivable)
autonomous_self_improvement_readiness =
                                     0.5·(curiosity → attention map exists)
                                     + 0.5·(meta_curiosity is non-canonical when data permits)
value_layer_readiness              = 0.1   # intentional Phase 8.5 floor; reason="phase_8_5_intentional_floor"
```

`value_layer_readiness` is excluded from automatic calibration_corrections.jsonl emission per spec §B3 even if calibration evidence diverges.

## Calibration adjustment

```
abs(prior - evidence_implied) > CALIBRATION_DRIFT_THRESHOLD  → emit correction record
correction_count_in_window < 3   → adjusted = 0.7·evidence + 0.3·prior   (standard)
correction_count_in_window 3..4  → adjusted = 0.5·evidence + 0.5·prior   (dampened)
correction_count_in_window >= 5  → adjusted = prior                       (frozen + tension)
```

**Critical rule:** the adjustment is applied to the NEXT snapshot only. Never mutate the current snapshot's score retroactively.

## Cell classification

```
strong         = fallback_rate <= 0.10 AND clusters <= 1 AND contradiction_rate <= 0.05
weak           = fallback_rate >= 0.30 OR clusters >= 4 OR contradiction_rate >= 0.15
under_pressure = subdivision_pressure_hint >= 1.0 OR clusters >= 3
                 OR (proposal_rejection_pressure >= 3 if derivable)
unknown        = no rule fired
```

Conflict order: `under_pressure > weak > strong > unknown`.

## Blind spot detectors

```
coverage_negative_space:
    flagged when expected_capability_signals reference paths
    that produced no artifact evidence
curiosity_silence:
    flagged when a related cell is classified `strong`
    AND attributed_curiosity_clusters in that cell == 0
```

Severity:
- `high` — both detectors flagged
- `medium` — one detector flagged AND structural evidence exists for the domain
- `low` — one detector flagged only

## Tension detection

```
tension_id = sha256(type + claim_canonical + observation_canonical)[:12]
```

`claim_canonical` and `observation_canonical` strip evidence_ref details. Adding/removing evidence_refs does NOT change the id.

Heuristic:
```
scorecard dimension drift   → if calibration_evidence.evidence_implied differs from
                              dimension.score by >= CALIBRATION_DRIFT_THRESHOLD
cell classification drift   → cell classified strong but fallback_rate >= 0.30
```

`resolution_path` ∈ {`calibration_correction`, `blind_spot_promotion`, `deferred_to_dream`, `requires_human_review`}.
`lifecycle_status` ∈ {`new`, `persisting`, `resolved`}.

## Stability under perturbation

```
max_change = 0.15 × (1 + 1 / sqrt(max(1, curiosity_item_count)))
```

Use the perturbation fixture at `tests/fixtures/perturbation_item.json`.

## Invariant detection

A property qualifies as an invariant if it holds across the last `min(5, history_length)` snapshots. Every invariant carries: `property_id`, `description`, `held_for_snapshots`, `evidence_refs`.

A rupture is a previously invariant property that broke in the current snapshot. Same shape.

## History path defaults

```
no --history-path, no --output-dir   → docs/runs/self_model/HISTORY.jsonl
no --history-path, --output-dir X    → X/HISTORY.jsonl
--history-path Y                      → Y
```

## Default output-dir

```
docs/runs/self_model/<pinned_input_manifest_sha12>/
```

Derived from pin_hash. NOT from datetime.now(). Two reproductions of the same pin land at the same path.

## Determinism contract

| Artifact | Byte-identical across same-pin reruns? |
|---|---|
| `self_model_snapshot.json` | yes |
| `self_model_snapshot.md` | yes |
| `scorecard.json` | yes |
| `continuity_anchors.json` | yes |
| `self_model_delta.json` | yes |
| `data_provenance.json` | yes |
| `HISTORY.jsonl` | append-only, structurally valid (NOT byte-identical because ts grows) |
| `calibration_corrections.jsonl` | append-only, structurally valid |

## why_it_matters regex

```
^Affects: [^,]+, Improves when: [^,]+, Degrades when: [^.]+\.$
```

`Affects:` and `Improves when:` cannot contain commas. `Degrades when:` can contain commas but must end at the final period.
