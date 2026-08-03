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
              TemplateCompiler (fixed inert skeleton + static AST policy)
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

`COMPILED` has a deliberately narrow meaning: bounded candidate metadata was
accepted and the fixed, inert Python skeleton passed static syntax/policy and
exact-shape checks. It does **not** mean the candidate has an implementation,
is behaviorally correct, is safe to execute, has passed a sandbox, or is
eligible for registry, canary, promotion, or routing. State transitions in this
in-memory registry do not grant any of those authorities.

### TemplateCompiler
Converts candidate specs into one bounded, inert solver skeleton:

- Candidate ID, domain, rationale, rules, and other caller-controlled metadata
  remain data on `SolverCandidate`; none of their bytes are interpolated into
  Python source.
- Candidate strings, list shapes/counts, confidence, source length, and AST
  node count are bounded before `COMPILED` can be assigned.
- The emitted source must match the byte-exact and AST-exact inert v1 shape. It
  contains one fixed `solve_candidate` function that returns an empty dict and
  contains no calls, attributes, subscripts, imports, classes, or candidate
  logic.
- The public static AST checker rejects imports, classes, async/yield/global
  constructs, attributes, dunder names, and every computed call target. Direct
  calls are accepted only when their name is in the explicit builtin allowlist.
- The Candidate Lab never imports, evaluates, compiles with Python's
  `compile()`, or executes the emitted source.

The static checker is defense in depth for a non-executed artifact. Passing it
is not proof that arbitrary Python is side-effect-free; Python operations can
invoke user-defined behavior. A real coding sandbox still requires an external
isolated execution backend, resource/network/filesystem policy, and independent
verification. Candidate Lab supplies none of those.

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
2. Candidate-controlled metadata is not emitted as executable Python source
3. The emitted inert skeleton is statically validated and never executed here
4. Registry is isolated from production solver folders
5. All candidate generation is reviewable through the in-memory registry

These guarantees do not claim a runtime sandbox, side-effect freedom after
external execution, solver correctness, provenance, signing, persistence,
BuilderHost integration, automatic code generation, canary eligibility,
promotion, or activation.
