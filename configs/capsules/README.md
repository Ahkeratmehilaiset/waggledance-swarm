# Capsule Configs

This directory contains `DomainCapsule` profile definitions. The production
consumer is `core.domain_capsule.DomainCapsule`, which is loaded by
`waggledance.core.autonomy.runtime.AutonomyRuntime` to add capsule decision
context and result metadata for a profile.

These files are not `CapabilityRegistry` YAML configs. Capability registry
YAML discovery uses `configs/capabilities/*.yaml`.

`waggledance.adapters.capabilities.constraint_engine_adapter.ConstraintEngineAdapter`
has a `load_capsule_rules()` adapter method, but these capsule files are not
automatically loaded into that adapter today. Future work that wants constraint
engine capsule rules should add an explicit bootstrap/runtime wiring step that
converts these profile `rules` entries into the adapter's expected rule shape.
