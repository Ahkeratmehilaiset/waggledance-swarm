# Night Pipeline Ingestion Blocker

**Status:** Identified 2026-03-28, fix in progress
**Branch:** `fix/night-pipeline-case-ingest-and-scheduler`

## Problem

The night learning pipeline trigger (`POST /api/autonomy/learning/run`) executes
successfully but processes **zero cases** every time.

## Root Cause

`autonomy_service.run_learning_cycle()` calls `night_pipeline.run_cycle()` with
`day_cases=None`. Nobody reads accumulated cases from `SQLiteCaseStore` and passes
them to the pipeline.

```
ChatService → case_store.save_case() → SQLite (15,019+ cases)
                                         ↓
POST /autonomy/learning/run → run_cycle(day_cases=None) → 0 processed
```

## Required Fix

1. `run_learning_cycle()` must load pending cases from `case_store`
2. A watermark/cursor must track processed cases to prevent reprocessing
3. An automatic trigger must fire when pending cases accumulate and runtime is idle

## Evidence

- 3h soak: 299 queries, 293 OK, +257 cases accumulated, 0 trained
- Manual trigger: HTTP 200, `cases_built: 0`, `models_trained: 0`
- No scheduler or idle trigger exists in codebase
