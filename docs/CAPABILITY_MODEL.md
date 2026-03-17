# Capability Model — WaggleDance v2.0

## Overview

Capabilities are the fundamental building blocks of the autonomy runtime.
Every action the system can take is registered as a capability with clear
metadata about what it does, what resources it needs, and how to verify
its results.

## Capability Registry

The `CapabilityRegistry` manages all available capabilities:

```python
from waggledance.core.capabilities.registry import CapabilityRegistry

registry = CapabilityRegistry()
registry.register(cap)
cap = registry.get("solve.math")
caps = registry.find_by_category("solve")
```

## Capability Structure

```python
@dataclass
class Capability:
    capability_id: str      # e.g., "solve.math", "retrieve.semantic_search"
    name: str               # Human-readable name
    category: str           # solve | retrieve | explain | act | observe
    layer: int = 3          # 3=authoritative, 2=specialist, 1=llm
    requires_approval: bool = False
    success_criteria: list  # Fields expected in result
```

## Categories

| Category | Description | Examples |
|----------|-------------|----------|
| `solve` | Deterministic computation | math, symbolic solver, constraint engine |
| `retrieve` | Data lookup | FAISS search, semantic memory, hot cache |
| `explain` | Natural language generation | LLM reasoning, template formatting |
| `act` | Side-effect actions | memory write, alert dispatch, model training |
| `observe` | Passive data collection | sensor reading, status check |

## Capability Selection

The `CapabilitySelector` picks the best capability for a query:

1. Filter by intent match
2. Score by layer priority (Layer 3 > Layer 2 > Layer 1)
3. Apply policy constraints (deny-by-default for `act` capabilities)
4. Return ranked selection with quality path

## Quality Path Determination

| Path | When |
|------|------|
| Gold | Layer 3 capability with verifier confirmation |
| Silver | Layer 2 specialist or partial solver match |
| Bronze | Layer 1 LLM-only response |

## Key Files

| File | Purpose |
|------|---------|
| `waggledance/core/domain/autonomy.py` | `Capability` dataclass |
| `waggledance/core/capabilities/registry.py` | `CapabilityRegistry` |
| `waggledance/core/capabilities/selector.py` | `CapabilitySelector` |
| `waggledance/core/reasoning/solver_router.py` | `SolverRouter` (3-tier) |
