# Solver Candidate Lab

**Version:** v3.5.0
**Status:** Lab only — does NOT auto-modify production routing

## Purpose

The Solver Candidate Lab analyzes failure patterns (route misses, LLM-heavy clusters, verifier rejections) and generates structured solver candidate specs. These candidates are reviewable artifacts only — they never automatically enter production routing.

## Architecture

```
Failure Cases → SolverCandidateLab.analyze_failures()
                    ↓
              CandidateRegistry (isolated, in-memory)
                    ↓
              TemplateCompiler (AST-validated)
                    ↓
              Structured Candidate Specs (review only)
```

## Key Components

### SolverCandidateLab
- Input: failure cases, route misses, verifier rejections, LLM-heavy clusters
- Output: structured `SolverCandidate` specs stored in isolated `CandidateRegistry`
- Deterministic pattern detection (no LLM required)
- Optional local LLM integration for rationale generation (graceful degradation if unavailable)

### SolverCandidate
Structured spec with:
- `candidate_id`: Deterministic SHA256-based ID
- `domain`: Intent domain (math, chat, thermal, etc.)
- `source_cases`: Trajectory IDs that triggered this candidate
- `rationale`: Why this candidate was proposed
- `expected_inputs` / `expected_outputs`: Interface specification
- `proposed_rules`: Deterministic rules for the solver
- `confidence`: 0.0-0.8 based on cluster size
- `state`: proposed → compiled → ready_for_canary (or failed_validation / rejected)

### TemplateCompiler
Converts candidate specs into bounded deterministic solver templates:
- **AST validation mandatory**: All generated code passes Python AST validation
- **Strict allowlist**: No imports, no filesystem/network/process side effects
- **Forbidden**: exec, eval, compile, open, __import__, getattr, setattr, delattr, globals, locals, breakpoint, system, popen, remove, rmdir, unlink, write, read
- **Forbidden AST nodes**: Import, ImportFrom, Global, Nonlocal, Async*, Yield*
- **Allowed builtins**: abs, round, min, max, sum, len, int, float, str, bool, list, dict, tuple, set, range, enumerate, zip, sorted, reversed, map, filter, any, all, isinstance

### CandidateRegistry
In-memory registry with JSON serialization:
- States: PROPOSED, COMPILED, FAILED_VALIDATION, READY_FOR_CANARY, REJECTED
- Methods: add, get, list_all, transition, count, stats, to_json

## API Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /api/candidate_lab/status` | Yes | Lab status and registry stats |
| `GET /api/candidate_lab/recent` | Yes | Recent candidates (`?limit=10`) |

## Feature Flags

- No feature flag needed — lab is isolated by design
- `learning.gpu_enabled` controls GPU acceleration (default: false)

## Safety Guarantees

1. Candidates NEVER auto-load into production routing
2. All generated content is AST-validated
3. No imports, no side effects, no arbitrary code execution
4. Registry is isolated from production solver folders
5. All candidate generation is auditable via registry
