# Night Learning v2 — WaggleDance v3.2

> **Note:** The `route_classifier` uses real sklearn training. Other specialists
> use simulated training (grade-based accuracy estimation). v3.2 adds dream mode
> (counterfactual simulation), memory consolidation, and meta-optimizer.
> See [SIMULATED_TRAINING.md](SIMULATED_TRAINING.md).

## Overview

Night Learning v2 replaces the legacy Q&A-pair-based learning with a
**case trajectory** model. Every query/mission produces a case trajectory
that captures the full decision chain, not just the final answer.

## Pipeline

```
NightLearningPipeline.run_cycle()
├── 1. Grade cases (QualityGate)
│   ├── Gold → auto-promote
│   ├── Silver → specialist training
│   ├── Bronze → no promotion
│   └── Quarantine → anti-pattern
├── 2. Train specialists (SpecialistTrainer)
│   ├── Route classifier
│   ├── Capability selector
│   ├── Anomaly detector
│   └── 5 more specialist models
├── 3. Update procedural memory
│   ├── Gold → proven chains
│   └── Quarantine → anti-patterns
└── 4. Generate morning report
```

## Case Trajectory

```python
@dataclass
class CaseTrajectory:
    trajectory_id: str
    goal: Goal                                    # Goal dataclass (not query/intent strings)
    world_snapshot_before: WorldSnapshot
    selected_capabilities: List[CapabilityContract]
    actions: List[Action]
    world_snapshot_after: WorldSnapshot
    verifier_result: dict
    quality_grade: QualityGrade                   # gold/silver/bronze/quarantine
    canonical_id: str                             # agent/capability canonical ID
    profile: str
    created_at: datetime
```

### Chat-to-Case Funnel

Chat traffic (`/api/chat`) feeds into the learning funnel via
`CaseTrajectoryBuilder.build_from_legacy()`. This records the Q&A exchange
truthfully without fabricating autonomy execution data:

- **Source:** ChatService records every non-cached chat response (solver + LLM routes)
- **Fields:** query, response, confidence, source, route_type
- **Grade:** Auto-graded; high-confidence responses without corrections earn silver/gold
- **Persistence:** Cases are saved to `sqlite_case_store` with intent and latency metadata
- **Hot-cache hits** are excluded (already counted, not new reasoning)
- **Verified:** Soak test (120 queries, 100% OK) produced +93 new cases (solver + LLM routes)

### Case Store → Night Pipeline Ingestion

When the night learning pipeline runs (manually or via scheduler), it automatically
loads pending (unprocessed) cases from `SQLiteCaseStore`:

1. **Watermark:** A `learning_watermark` table tracks the `created_at` timestamp
   up to which cases have been processed
2. **Fetch:** `case_store.fetch_pending(limit=5000)` returns cases after the watermark
3. **Reconstruct:** `CaseTrajectory.from_stored_dict()` converts stored JSON back to
   domain objects for the pipeline
4. **Advance:** After a successful cycle, the watermark is advanced so those cases
   are never reprocessed

This ensures idempotent, auditable learning — repeated triggers process only new cases.

### Learning Scheduler

A background thread (`learning-scheduler`) automatically triggers learning cycles:

- **Interval:** Every 10 minutes
- **Conditions:** pending_cases >= 10 AND resource kernel is idle/light AND pipeline not running
- **Auto-start:** Starts when WaggleDance boots (if night pipeline is configured)
- **Observable:** `/api/autonomy/learning/status` shows scheduler state and pending count
- **Manual trigger:** `POST /api/autonomy/learning/run` always works regardless of scheduler

## Quality Gate

The `QualityGate` evaluates each case trajectory:

| Grade | Criteria | Promotion |
|-------|----------|-----------|
| Gold | Solver + verifier + no conflicts | Auto-promote to procedural memory |
| Silver | Partial solver or 48h+ canary pass | Feeds specialist training |
| Bronze | LLM-only | Never auto-promoted |
| Quarantine | Conflict or hallucination | Stored as anti-pattern |

## Specialist Models

8 specialist model types trained from case trajectories:

1. `route_classifier` — predict best routing layer
2. `capability_selector` — rank capabilities for intent
3. `anomaly_detector` — detect unusual patterns
4. `intent_classifier` — classify query intent
5. `quality_predictor` — predict response quality
6. `risk_scorer` — assess action risk level
7. `priority_estimator` — estimate task priority
8. `domain_classifier` — classify domain context

### Canary Lifecycle
- Train → Canary (10% traffic, 48h) → Promote or Rollback

## Procedural Memory

Stores proven action chains from gold-graded cases:

```python
procedure = ProceduralMemory.best_procedure("diagnose")
# Returns: Procedure(chain=["observe", "diagnose", "verify"],
#                     success_rate=0.95, success_count=12)
```

Anti-patterns from quarantined cases prevent repeating mistakes.

## Morning Report

Generated when night mode ends:

- Cases graded (gold/silver/bronze/quarantine counts)
- Specialists trained and their accuracy
- Procedures added/updated
- Coverage gaps identified
- Proactive goal suggestions

## Legacy Conversion

The `LegacyConverter` bridges old Q&A pairs to case trajectories:

```python
converter = LegacyConverter()
cases = converter.convert_training_pairs(qa_pairs)
```

## Key Files

| File | Purpose |
|------|---------|
| `waggledance/core/learning/night_learning_pipeline.py` | Main orchestrator |
| `waggledance/core/learning/quality_gate.py` | Case grading |
| `waggledance/core/learning/procedural_memory.py` | Proven chains |
| `waggledance/core/learning/case_builder.py` | Case trajectory builder |
| `waggledance/core/learning/legacy_converter.py` | Q&A → case conversion |
| `waggledance/core/learning/morning_report.py` | Report generator |
| `waggledance/core/specialist_models/specialist_trainer.py` | Model training |
| `waggledance/core/specialist_models/model_store.py` | Model persistence |
| `waggledance/adapters/persistence/sqlite_case_store.py` | Case store + watermark |
| `waggledance/application/services/autonomy_service.py` | Pipeline trigger + scheduler |
