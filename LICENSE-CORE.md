# WaggleDance Protected Modules Map

This repository is dual-licensed.

- **Apache License 2.0** applies to the open core by default.
- **Business Source License 1.1 (BUSL-1.1)** applies only to the protected files and directories listed below.

For the full BUSL-1.1 legal text and project-specific parameters, see `LICENSE-BUSL.txt`.
For the Apache-2.0 text, see `LICENSE`.

## Protected files and directories

### Existing protected legacy/core files
- `core/audit_log.py`
- `core/replay_engine.py`
- `core/memory_overlay.py`
- `core/provenance.py`
- `core/trust_engine.py`
- `core/cognitive_graph.py`

### Existing protected WaggleDance MAGMA files
- `waggledance/core/magma/audit_projector.py`
- `waggledance/core/magma/event_log_adapter.py`
- `waggledance/core/magma/provenance.py`
- `waggledance/core/magma/replay_engine.py`
- `waggledance/core/magma/trust_adapter.py`
- `waggledance/core/magma/confidence_decay.py`

### Existing protected learning files
- `waggledance/core/learning/case_builder.py`
- `waggledance/core/learning/quality_gate.py`

### New v3.2 protected files
- `waggledance/core/learning/dream_mode.py`
- `waggledance/core/learning/consolidator.py`
- `waggledance/core/specialist_models/meta_optimizer.py`
- `waggledance/core/projections/narrative_projector.py`
- `waggledance/core/projections/introspection_view.py`
- `waggledance/core/projections/autobiographical_index.py`
- `waggledance/core/projections/projection_validator.py`
- `waggledance/core/projections/__init__.py`

### v3.6.x / Phase 10 protected files (Change Date 2030-12-31)

The files below are protected under BUSL-1.1 with a per-file Change
Date of **2030-12-31**, as defined per Phase 10 RULE 6
(`NEW_CORE_CHANGE_DATE = 2030-12-31`). Each file carries an explicit
`# BUSL-Change-Date: 2030-12-31` header. The Change Date in
`LICENSE-BUSL.txt` (2030-03-19) remains the project-wide default for
v3.2-era files; per-file dates take precedence when present.

Storage substrate (P2 — control plane and data plane foundation):
- `waggledance/core/storage/__init__.py`
- `waggledance/core/storage/control_plane_schema.py`
- `waggledance/core/storage/control_plane.py`
- `waggledance/core/storage/path_resolver.py`
- `waggledance/core/storage/registry_queries.py`

## Default rule

Any source file not listed above is licensed under Apache License 2.0 unless that file itself carries a BUSL-1.1 SPDX header.

When a file carries a `# BUSL-Change-Date: YYYY-MM-DD` header, that date
overrides the default Change Date in `LICENSE-BUSL.txt` for that file.

## Practical summary

You may:
- use the Apache-licensed open core freely under Apache-2.0;
- use the BUSL-licensed protected modules for non-production purposes such as development, CI, testing, review, education, and research;
- use the BUSL-licensed protected modules in production for personal, non-commercial use.

You may not use the BUSL-licensed protected modules in production as part of a commercial product, commercial service, managed service, hosted offering, or competitive offering without a separate commercial license from the Licensor.