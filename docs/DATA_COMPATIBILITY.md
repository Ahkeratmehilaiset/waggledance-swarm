# Data Compatibility — WaggleDance v2.0

## Overview

The autonomy runtime maintains full backward compatibility with all
existing data. The strategy is "wrap, don't rewrite" — existing data
stores remain accessible through compatibility adapters.

## Canonical ID System

All entities (agents, profiles, capabilities, models) use a canonical ID:

```python
@dataclass
class CanonicalId:
    canonical_id: str   # e.g., "agent:cottage_beekeeper"
    entity_type: str    # "agent", "profile", "capability", "model"
```

### Alias Registry

Legacy identifiers map to canonical IDs through the `AliasRegistry`:

```python
registry = AliasRegistry()
registry.register(AgentAlias(
    legacy_id="beekeeper",
    canonical_id="agent:cottage_beekeeper",
    aliases=("mehiläishoitaja", "bee_keeper"),
    profiles=("cottage",),
))

# Lookup works with any known identifier
canonical = registry.resolve("mehiläishoitaja")
# → "agent:cottage_beekeeper"
```

## Data Stores

| Store | Format | Compatible? | Adapter |
|-------|--------|-------------|---------|
| ChromaDB (`data/chroma_db/`) | Vector embeddings | Yes | `MemoryStore` unchanged |
| SQLite (`data/waggle_dance.db`) | Relational | Yes | `MemoryStore` unchanged |
| SQLite (`data/audit_log.db`) | Audit trail | Yes | Extended with mission-level |
| JSONL (`data/finetune_live.jsonl`) | Training pairs | Yes | `LegacyConverter` bridges |
| JSONL (`data/morning_reports.jsonl`) | Reports | Yes | Extended format |
| FAISS (`data/faiss/`) | Dense vectors | Yes | `FaissRegistry` unchanged |
| YAML (`configs/capsules/`) | Capsule configs | Yes | Loaded by `CapabilityRegistry` |
| YAML (`configs/axioms/`) | Axiom models | Yes | Loaded by `SymbolicSolver` |
| YAML (`knowledge/`) | Agent definitions | Yes | `AliasRegistry` resolves |

## Legacy Converter

Bridges Q&A training pairs to case trajectories:

```python
from waggledance.core.learning.legacy_converter import LegacyConverter

converter = LegacyConverter()
# From training_collector pairs
cases = converter.convert_training_pairs(qa_pairs)
# From dict format
cases = converter.convert_dicts([{"question": "...", "answer": "..."}])
```

## Compatibility Layer

Routes queries to legacy or autonomy path based on mode:

```python
from waggledance.core.autonomy.compatibility import CompatibilityLayer

compat = CompatibilityLayer(
    runtime=autonomy_runtime,
    legacy=hivemind,
    compatibility_mode=True,  # Legacy primary
)

# When compatibility_mode=True: legacy handles, autonomy shadows
# When compatibility_mode=False: autonomy handles, no legacy fallback
result = compat.handle_query("What is the temperature?")
```

### Format Adaptation

Results can be converted between legacy and autonomy formats:

```python
# Legacy → Autonomy
autonomy_result = compat.adapt_legacy_to_autonomy(legacy_result)

# Autonomy → Legacy
legacy_result = compat.adapt_autonomy_to_legacy(autonomy_result)
```

## Key Files

| File | Purpose |
|------|---------|
| `waggledance/core/domain/autonomy.py` | `CanonicalId`, `AgentAlias` |
| `waggledance/core/domain/alias_registry.py` | `AliasRegistry` |
| `waggledance/core/learning/legacy_converter.py` | Q&A → case trajectory |
| `waggledance/core/autonomy/compatibility.py` | Legacy/autonomy bridge |
| `core/training_collector.py` | `feed_case_builder()` bridge |
