# Self-model data provenance — Phase 8.5

## Per-field provenance schema

`data_provenance.json` carries `entries[]` with this shape:

```
{
  "field_path": "scorecard.metacognition_maturity",
  "source": "real" | "fixture" | "default",
  "source_artifact": "curiosity_log + cells aggregate",
  "confidence": 0.0..1.0,
  "weight": float,
  "is_real": bool
}
```

Per spec §B8:
- `source = "real"` — value came from actual pinned repo/runtime artifacts
- `source = "fixture"` — value came from test or fallback fixture data
- `source = "default"` — value used a neutral fallback because no real or fixture provided one

`is_real` is `true` only for `source="real"`.

## Weighting

```
scorecard dimensions       → 3.0
attention_focus items      → 2.0
blind_spots                → 2.0
continuity_anchors         → 1.0
all other major fields     → 1.0
```

Spec rationale: scorecard signals are the highest-stakes claims, so a real-data ratio is dragged most by their grounding. Attention and blind_spots carry moderate weight. Continuity anchors are always present so they get baseline weight only.

## Real-data coverage ratio

```
real_data_coverage_ratio = sum(weight × is_real) / sum(weight)
```

A trivial real field contributes nothing meaningful to the ratio because the denominator includes its weight too. Inflating the ratio by adding many trivial fields is therefore counterproductive.

Acceptance threshold: `>= 0.7` per spec §ACCEPTANCE CRITERIA #20. Below that, a documented blocker is required.

## Source semantics

| Field | Default source when… |
|---|---|
| Scorecard dimension score | "real" if any evidence list is non-empty, else "default" |
| Attention focus item | "real" (curiosity_log feeds it) |
| Blind spot | "real" (detector grounded) |
| Cell classification | "real" if `fallback_rate is not None`, else "default" |
| Continuity anchor | "real" if pinned_inputs is non-empty, else "default" |

## Audit trail

The combination of `data_provenance.json` + `continuity_anchor` + `pin_hash` + the chained `HISTORY.jsonl` lets a future reviewer:

1. Identify the exact pin and branch this snapshot was built from.
2. Resolve every emitted field back to its source artifact.
3. Verify the source artifact has not been mutated since the pin.
4. Trace the snapshot's place in the rolling chain of selves.
