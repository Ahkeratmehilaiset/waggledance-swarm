# Future-scale composite path benchmark

Status: local/offline artifact producer for the `useful_composite_paths` axis.

`tools/run_future_scale_composite_path_benchmark.py` emits a deterministic JSON
artifact with schema version `future_scale.composite_path_benchmark.v1`. The
tool reads only the axiom library and the existing typed composition graph; it
does not call network services, mutate runtime routing, update manifests, or
grant authority.

The artifact is intentionally claim-safe:

- `claim_gate_satisfied=false`
- `claim_safe=false`
- `literal_future_claim_safe=false`
- `required_runtime_evidence_present=false`
- `runtime_authority_changed=false`
- `runtime_authority_granted=false`
- `operator_gate_required=false`
- `external_writes_applied=false`

The measured value is the count of bridge candidates whose composition score is
at least the configured threshold. This is local evidence for one future-scale
axis, not proof of unlimited scalability. Manifest aggregation is deferred until
the sibling `contradiction_rate` and `insight_score` benchmark artifacts land on
their own disjoint paths.

Example:

```powershell
.\.venv\Scripts\python.exe tools\run_future_scale_composite_path_benchmark.py --json
```
