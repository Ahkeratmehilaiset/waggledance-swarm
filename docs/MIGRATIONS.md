# WaggleDance Data & Migrations

*Tietokannat ja varmuuskopiot — Database schema and backup*

## Data Directory Layout

```
data/
├── chroma_db/              # ChromaDB vector store (bilingual FI+EN)
├── waggle_dance.db         # SQLite: agent levels, training pairs, metadata
├── audit_log.db            # SQLite: MAGMA audit log, provenance, consensus
├── cognitive_graph.json    # NetworkX graph (nodes + edges)
├── replay_store.jsonl      # MAGMA replay events
├── learning_metrics.jsonl  # Structured learning metrics
├── learning_progress.json  # Night mode progress state
├── weekly_report.json      # Latest weekly report
├── finetune_live.jsonl     # All Q&A pairs (raw)
├── finetune_curated.jsonl  # High-quality pairs (7+/10)
├── finetune_rejected.jsonl # Low-quality pairs (<7/10)
├── learning_audit.jsonl    # Prompt evolution audit trail
├── meta_reports.jsonl      # Meta-learning report history
└── morning_reports.jsonl   # Night shift morning reports
```

## SQLite Schemas

### waggle_dance.db

Created by `core/memory_engine.py`. Key tables:
- Agent levels and trust scores
- Training data pairs (Q&A with source, confidence)
- MicroModel promotion records

### audit_log.db

Created by `core/audit_log.py` (MAGMA L1). Tables:
- `audit_entries` — append-only write log (agent, action, data, timestamp)
- `validations` — provenance validation records
- `consensus_records` — Round Table consensus results

## ChromaDB Collections

Managed by `core/memory_engine.py`:
- Bilingual FI+EN embeddings (nomic-embed-text, 768 dimensions)
- Collections auto-created on first use
- **Warning:** Do not mix embedding models — 768d (nomic) and 384d (all-minilm) are incompatible

## Backup & Restore

### Create Backup

```bash
python tools/waggle_backup.py
```

Creates a timestamped ZIP in `backups/` containing:
- All `data/` files including ChromaDB
- `agents/` YAML definitions
- `knowledge/` bases
- `configs/` settings

### Restore

```bash
python tools/waggle_restore.py
```

Validates environment (13 checks):
- Python version, required packages
- Voikko DLL + dictionary files
- Ollama connectivity + required models
- Data directory structure

### Run Tests Only

```bash
python tools/waggle_backup.py --tests-only
```

Runs all 43 test suites without creating a backup.

## Schema Versioning

Every SQLite database has a `schema_version` table tracking its current version:

```sql
CREATE TABLE schema_version (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    version     INTEGER NOT NULL DEFAULT 1,
    updated_at  REAL    NOT NULL
);
```

### Current Schema Versions

| Database | Latest Version | Changes in V2 |
|----------|---------------|----------------|
| audit_log.db | v2 | `content_preview` column on audit table |
| waggle_dance.db | v2 | Performance indexes on memories, events, messages, tasks |

### Migration Tool

```bash
# Check current versions
python tools/migrate_db.py --check

# Apply pending migrations
python tools/migrate_db.py --migrate
```

Migrations are idempotent — safe to run multiple times. The tool auto-detects
legacy databases (no schema_version table) as v1 and bootstraps from there.

## Migration Notes

- **ChromaDB** — collections are auto-created; embedding dimension must match model
- **cognitive_graph.json** — auto-created empty if missing; uses atomic writes to prevent corruption
- **JSONL files** — append-only; corrupted lines are skipped and logged (ReplayStore resilience)
