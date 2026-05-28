# MAGMA Share Manifest Contract

`magma.share_manifest.v0` is the contract-first boundary for cross-instance
MAGMA sharing. Runtime export remains disabled by default; the only writer in
this repo is an explicit operator-gated local exporter.

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
expected/actual gate, verdict, and a sanitization summary. The local exporter
validates this schema with date-time format checks and count-consistency checks
before writing any share artifact.

## Operator-Gated Exporter

`tools/export_magma_share_manifest.py` converts an already verified local
MAGMA receipt-bundle `manifest.json` into a payload-free `share_manifest.json`.
It requires an explicit `--operator-approval-id`; the approval ref is checked
for shape but is not written to the export report. The tool writes only:

- `share_manifest.json`
- `share_export_report.json`

It does not copy payload files, does not export `canonical_payload_digest`, does
not export replacement maps, and does not enable default runtime receipt
emission.

Example:

```powershell
python tools\export_magma_share_manifest.py `
  --source-manifest <receipt-bundle>\manifest.json `
  --out-dir <new-share-export-dir> `
  --operator-approval-id operator:approval:example `
  --share-id magma:share:example:001 `
  --producer-agent codex-lead-1 `
  --producer-role lead `
  --bridge-event-ref bridge:example `
  --purpose cross_instance_replay `
  --json
```

## Non-Goals

This contract does not make MAGMA append-only by default, does not export full
receipt bundles, does not add cross-instance transport, and does not grant any
runtime authority. The exporter emits a local metadata artifact only.

## Validation

Run:

```powershell
python -m pytest tests\contracts\test_magma_share_manifest_schema.py -q
python -m pytest tests\tools\test_magma_share_manifest_exporter.py -q
```

The regression tests prove the good fixture validates and that raw payloads,
payload digests, replacement maps, raw context, raw solver output, raw-query
digests, payload files, invalid timestamps, and runtime export enablement are
rejected. The exporter tests prove that source receipt bundles are verified
fail-closed, an operator approval ref is required, artifact counts must match,
and private payload markers do not appear in exported JSON.
