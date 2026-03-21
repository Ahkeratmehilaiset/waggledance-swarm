# Memory Proxy Implementation Guide v1.1

## Architecture Overview
MAGMA (Multi-Agent Generative Memory Architecture) provides append-only memory with role-based access control, audit logging, and agent rollback.

## Components

### AuditLog (`core/audit_log.py`)
Append-only SQLite audit trail. No update/delete operations.
```python
from core.audit_log import AuditLog
audit = AuditLog("data/audit_log.db")
audit.record("new", "doc_123", agent_id="bee_agent", session_id="s1")
```

### ChromaDBAdapter (`core/chromadb_adapter.py`)
Thin wrapper implementing `StoreAdapter` protocol over existing ChromaDB.
```python
from core.chromadb_adapter import ChromaDBAdapter
adapter = ChromaDBAdapter(memory_store=existing_store)
adapter.add("doc1", "text", embedding, {"key": "val"})
```

### MemoryWriteProxy (`core/memory_proxy.py`)
Central write guard with role enforcement.
```python
from core.memory_proxy import MemoryWriteProxy
proxy = MemoryWriteProxy(adapter, audit, role="worker", agent_id="a1")
proxy.write("doc1", "text", embedding, mode="new")
```

### AgentRollback (`core/agent_rollback.py`)
Undo agent writes respecting layer safety.
```python
from core.agent_rollback import AgentRollback
rb = AgentRollback(adapter, audit)
rb.preview("agent_id", "session_id")  # dry run
rb.rollback("agent_id", "session_id")  # execute
```

## Write Modes
- `new` — append new document (working layer)
- `correction` — create correction pointing to original (correction layer)
- `invalidate_range` — mark document as invalidated via metadata flag

## Safety Rules
1. No overwrite mode exists — prevents silent data loss
2. Original layer documents are NEVER deleted or modified
3. Every write creates an audit record
4. Corrections chain via `_corrects` metadata field
5. Rollback follows spawn_chain for recursive undo

## Metadata Convention
Internal fields prefixed with `_`:
- `_layer`: working | correction | original
- `_content_hash`: SHA-256 prefix (16 chars)
- `_created_at`: Unix timestamp
- `_agent_id`, `_session_id`: provenance
- `_corrects`: doc_id this correction targets
- `_invalidated`: boolean flag
