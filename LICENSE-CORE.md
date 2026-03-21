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

## Default rule

Any source file not listed above is licensed under Apache License 2.0 unless that file itself carries a BUSL-1.1 SPDX header.

## Practical summary

You may:
- use the Apache-licensed open core freely under Apache-2.0;
- use the BUSL-licensed protected modules for non-production purposes such as development, CI, testing, review, education, and research;
- use the BUSL-licensed protected modules in production for personal, non-commercial use.

You may not use the BUSL-licensed protected modules in production as part of a commercial product, commercial service, managed service, hosted offering, or competitive offering without a separate commercial license from the Licensor.