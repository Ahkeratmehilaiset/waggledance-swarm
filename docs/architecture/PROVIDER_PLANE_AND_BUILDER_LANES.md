# Provider Plane and Builder Lanes

**Status:** new in Phase 10 (P3). Adds an execution layer on top of Phase 9 routing scaffolds. Subprocess invocations honour RULE 17.

## What Phase 9 already shipped

Phase 9 introduced the *types and routing* scaffolds for the provider plane and builder lane:

- `waggledance/core/provider_plane/` — `ProviderRegistry`, `ProviderRouter`, `RequestPackRouter`, `ProviderBudgetEngine`, `ResponseNormalizer`, `AgentPoolRegistry`. Pure dataclasses + decision functions.
- `waggledance/core/builder_lane/` — `BuilderRequest` / `BuilderArtifact` / `BuilderResult` packs, `MentorPrompt`, `RepairContext`, `WorktreeAllocation`, an invocation-log helper, plus the routing function.
- `schemas/provider_request.schema.json` and `schemas/provider_response.schema.json` — versioned request/response contracts with the 4-provider chain (`claude_code_builder_lane`, `anthropic_api`, `gpt_api`, `local_model_service`) and the 6-layer trust gate enum.

What Phase 9 did not ship: the actual *call*. Routing decisions returned a chosen provider id; nothing in the runtime invoked Claude Code, Anthropic, GPT, or a local model. There was no control-plane persistence of the calls.

## What Phase 10 P3 adds

A new package, `waggledance/core/providers/`, that is the execution layer on top of the Phase 9 scaffolds plus the Phase 10 control plane (P2):

| Module | Role |
|---|---|
| `provider_contracts.py` | JSON-schema-validated request/response packs. Loads `schemas/provider_*.schema.json`. Uses `jsonschema` if available; falls back to a structural validator. Fail-loud (RULE 14). |
| `provider_registry.py` (`ProviderPlaneRegistry`) | Composes the Phase 9 in-memory `ProviderRegistry` with the control plane. Tracks `ProviderConfig` (enabled, has_credentials, daily budget). Persists each call to `provider_jobs`. |
| `provider_plane.py` (`ProviderPlane`) | Top-level orchestrator. Validates → routes → dispatches → persists → re-validates. Always has a `dry_run_stub` adapter so the plane is exercisable without credentials. |
| `claude_code_builder.py` (`ClaudeCodeBuilder`) | The only authorised subprocess in this prompt (RULE 17). Bounded timeout, isolated worktree, JSONL invocation log via the Phase 9 helper. CLI absent → dry-run fallback (RULE 0/9: missing keys must not block unrelated work). |
| `builder_job_queue.py` (`BuilderJobQueue`) | Persistent queue over `builder_jobs`. Submit → `queued`; lifecycle transitions → `running` / `completed` / `failed` / `timed_out` / `dry_run`. |
| `builder_lane_router.py` (`BuilderLaneRouter`) | Calls the Phase 9 router and persists the routing decision as a `provider_jobs` row. |
| `mentor_forge.py` (`MentorForge`) | Façade that enforces the mentor-output advisory boundary (RULE: mentor notes are IR objects of type `learning_suggestion`, `lifecycle_status='advisory'`, `is_advisory_only=True`). |
| `repair_forge.py` (`RepairForge`) | Glue around the Phase 9 repair forge with control-plane persistence. |

All seven new modules are protected under BUSL-1.1 with `# BUSL-Change-Date: 2030-12-31` per RULE 6 and listed in `LICENSE-CORE.md`.

## Provider chain (Phase 9, unchanged)

```
code_or_repair       : claude_code_builder_lane → anthropic_api → gpt_api → local_model_service
spec_or_critique     : anthropic_api → claude_code_builder_lane → gpt_api → local_model_service
bulk_classification  : local_model_service → anthropic_api → gpt_api → claude_code_builder_lane
```

Phase 10 adds a fifth provider type — `dry_run_stub` — used internally as the safety fallback when no warm provider is available.

## Credential policy (RULE 9 honoured)

- Use environment / config when present.
- Missing keys disable **only** that provider, not the plane.
- Tests stay in dry-run / stub mode when keys are absent.
- The plane never asks the operator mid-session.

A `ProviderConfig` carries an explicit `has_credentials` flag. The Phase 9 registry's `warm` flag is computed as `enabled and has_credentials and warm_hint`. The router only routes to warm providers when `require_warm=True`.

## Mentor-output boundary

- Mentor notes are IR objects of type `learning_suggestion`.
- `lifecycle_status='advisory'`, `promotion_state='supportive'`, `is_advisory_only=True`.
- They cannot directly mutate runtime, the world model, or the architecture.
- Promotion to a meta-proposal happens through the existing reviewed pipeline (Session D / proposal compiler / promotion ladder).
- `MentorForge.compile_advisory_payload(...)` belt-and-braces: even if a caller hand-edits the dict, the dataclass enforces `is_advisory_only=True` and `lifecycle_status='advisory'`.

## Subprocess discipline (RULE 17)

`ClaudeCodeBuilder` is the only module in this prompt that may launch a subprocess. The contract:

1. The caller passes `isolated_worktree_path` and `isolated_branch_name` — the builder does **not** create them. (`tools/savepoint.ps1` and the Phase 9 worktree allocator are the supported creation paths.)
2. `max_wall_seconds` is bounded; the absolute ceiling is 7200 s.
3. Every invocation is appended to `data/builder_invocation_log.jsonl` (Phase 9 path) with `request_id`, `outcome` (`completed` / `failed` / `timed_out` / `dry_run`), and a rationale.
4. The CLI is detected via `shutil.which("claude")` / `shutil.which("claude-code")`. Absent → dry-run fallback returns a synthetic response in `raw_quarantine` and writes a `dry_run` log entry.
5. Every call counts against the per-section provider budget recorded in `docs/runs/provider_invocations.jsonl` and the control plane.
6. Output never directly mutates the runtime. Returned response is in `raw_quarantine`. Promotion through the trust gate happens elsewhere.

## Schema contract (unchanged from Phase 9)

A request payload validates against `schemas/provider_request.schema.json` and a response against `schemas/provider_response.schema.json`. The two key invariants:

- `request.no_runtime_mutation === true` (asserted by the structural validator)
- `response.no_direct_mutation === true` (asserted by the structural validator)

Any payload missing either flag, or with the flag set to `false`, raises `ProviderContractError`.

## How a typical call flows

```
caller -> ProviderPlane.dispatch(payload)
       1. validate_request(payload)              # schema-checked
       2. _pick_provider_type(...)               # request priority list, Phase 9 router fallback, dry_run_stub last
       3. ProviderPlaneRegistry.record_job_started(...)   # control-plane row, status='started'
       4. adapter.dispatch(request)              # Claude Code subprocess / dry-run stub / future Anthropic adapter
       5. validate_response(response.to_dict())  # schema-checked again
       6. ProviderPlaneRegistry.record_job_completed(..., status='completed' | 'dry_run')
       7. return ProviderDispatchResult(request, validated_response, provider_id_used, ...)
```

A failure at step 4 records the row with `status='failed'` and re-raises. A failure at step 1 or 5 raises `ProviderContractError` before any persistence.

## What Phase 10 P3 does NOT do

- **No real Anthropic / OpenAI HTTP adapters.** The shape is in place; concrete adapters are follow-up work that needs credentials and rate-limit handling. The `dry_run_stub` adapter is the only one shipped to keep tests deterministic and prompt budgets at zero.
- **No actual mentor-to-proposal pipeline.** That belongs to Session D / proposal compiler.
- **No autonomy-runtime call sites.** The autonomy runtime continues to operate without consulting the provider plane. The plane is the lane WD can be *taught* through; it is not WD's irreducible inner cognition.

## Cross-references

- `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md` — control-plane / data-plane substrate (P2).
- `docs/journal/2026-04-28_storage_runtime_truth.md` — the truth audit that frames why the substrate matters.
- `waggledance/core/provider_plane/` — Phase 9 routing scaffold (still the source of provider chains and routing decisions).
- `waggledance/core/builder_lane/` — Phase 9 request/result packs and worktree allocator.
- `LICENSE-CORE.md` — protected-modules map (Phase 10 entries with Change Date 2030-12-31).
