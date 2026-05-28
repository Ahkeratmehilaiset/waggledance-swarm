# MAGMA Share Manifest Contract

`magma.share_manifest.v0` is the contract-first boundary for future
cross-instance MAGMA sharing. It is intentionally schema, docs, and tests only;
runtime export remains disabled until a later operator-gated PR.

The manifest is for peers that need to review or replay MAGMA evidence without
receiving private prompts, raw payloads, raw solver outputs, replacement maps,
or deterministic raw-query digests. It can name receipt and EvaluationResult
digests, counts, gates, verdicts, and sanitization facts. It must not include
payload files or payload digests.

## Required Boundary

- `runtime_export_enabled` is always `false` in v0.
- `export_policy.contract` is `sanitization_v0`.
- `export_policy.payload_visibility` is `no_payload`.
- `allow_payload_digests`, `allow_raw_payloads`, `allow_replacement_maps`,
  `allow_raw_context`, `allow_raw_solver_outputs`, and
  `allow_deterministic_query_digests` are all `false`.
- `artifact_counts.payload_files` is `0`.
- `forbidden_material_absent` and each entry's
  `sanitization.raw_material_removed` must list:
  `raw_payload`, `replacement_map`, `raw_context`, `raw_solver_output`, and
  `raw_query_digest`.

Allowed entry fields are limited to stable MAGMA references and review state:
entry id, receipt digest, EvaluationResult digest, subject type, risk class,
expected/actual gate, verdict, and a sanitization summary. A future runtime
exporter must validate against this schema before writing any share artifact.

## Non-Goals

This contract does not make MAGMA append-only by default, does not export
receipt bundles, does not add cross-instance transport, and does not grant any
runtime authority. It only defines the artifact shape that future export code
must satisfy.

## Validation

Run:

```powershell
python -m pytest tests\contracts\test_magma_share_manifest_schema.py -q
```

The regression tests prove the good fixture validates and that raw payloads,
payload digests, replacement maps, raw context, raw solver output, raw-query
digests, payload files, and runtime export enablement are rejected.
