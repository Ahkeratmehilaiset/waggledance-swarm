# MAGMA 100h Competitive Synthesis

Generated: 2026-05-24

## Current WD State

WaggleDance is currently strongest where the repo has local, repeatable
proofs: MAGMA receipts, adversarial policy evaluation, solver provenance,
autogrowth receipt durability, and evidence-only hex-cell competition.

Current local evidence from this worktree:

- `python tools/show_v12_proof.py --json` reports `ok=true`.
- A3 counterfactual axis is `MEASURED_LOCAL_PARTIAL`.
- A4 solver-growth axis is `MEASURED_LOCAL_SYNTHETIC`.
- Adversarial corpus is `42/42` with gate, verdict, and reason-code accuracy
  `1.0`.
- Rival local checks remain `1/4`; `consensus_grade=false`.
- Hex-cell competition is still `non_authority_contract`; it ranks shadow
  candidates but does not route traffic or mutate candidate state.

## What Has Been Done

- WriteRCOGate execution binding was hardened so stale or mismatched approval
  evidence fail-closes before effects.
- AutoPromotionEngine and SolverProvenance receipt emission were moved behind
  durable state transitions so receipt heads do not advance on failed durable
  writes.
- The adversarial corpus expanded to 42 cases and now covers overclaim,
  payload leakage, evidence spoofing, tool argument abuse, and consensus-grade
  traps.
- Idle protocol steering now points dream rounds toward operator-gated
  competitor evidence refresh instead of ungated implementation work.
- Dependabot lockfile-only PRs have been reviewed with resolver/security
  gates, while native dependency bumps remain held when CI is stale.

## What Is Still Missing

- Rival local evidence is incomplete. Microsoft AGT passes; JamJet and Preloop
  are still not passed; Asqav remains cloud-dependent for its receipt headline.
- A3 is still partial and local. It needs stored-consensus replay against a
  candidate diff before it can become a broader counterfactual-eval claim.
- A4 is still synthetic. It proves six-family solver-growth dispatch locally,
  but not production authority or long-running promotion behavior.
- Hex-cell competition is evidence-only. Operator-gated authority promotion
  must be a separate patch with receipts, rollback behavior, and no duplicate
  event emission.
- Governance throughput has insufficient data; the bridge needs measured
  non-idle progress rather than heartbeat-only confidence.

## Competitor Situation

The current competitor landscape is strong in orchestration, stateful
workflows, and multi-agent composition:

- OpenAI Agents SDK documents agent handoffs/tools, guardrails, and tracing for
  LLM generations, tool calls, handoffs, and guardrail spans.
- LangGraph publicly emphasizes durable execution, checkpoint/resume, and
  human-in-the-loop workflow control.
- Microsoft Agent Framework combines agent abstractions, typed workflows,
  middleware, telemetry, and graph-based orchestration.
- Google ADK documents multi-agent composition with parent/sub-agent
  hierarchy, workflow agents, shared session state, and fan-out/gather
  patterns.
- CrewAI Flows documents event-driven workflows, state management, persistence,
  and multi-crew orchestration.

WD should not claim superiority over these systems from public docs alone.
The technical opening is narrower and more defensible: receipt-bound local
evidence, adversarial no-overclaim gates, offline replay, and hex-cell
candidate competition with a clear authority boundary.

## Implemented Slice

This sprint adds `tools/run_v12_competitive_triad_simulation.py`, a guarded
local proof report that:

- models three rival capability profiles without installing or executing rival
  SDKs;
- reuses current WD proof surfaces and the rival local-check matrix;
- runs a non-authority hex-cell competition probe;
- keeps `consensus_grade=false`;
- reports capability gaps as simulation evidence, not as a live competitor
  benchmark.

Reproduce:

```powershell
C:\Python\project2-master\.venv\Scripts\python.exe tools\run_v12_competitive_triad_simulation.py --json
C:\Python\project2-master\.venv\Scripts\python.exe -m pytest tests\tools\test_v12_competitive_triad_simulation.py -q
```

## Next 100h Sequence

1. Keep consensus-grade false until all four rival local manifests pass.
2. Convert JamJet and Preloop from `not_passed` to pinned local smoke evidence,
   or keep them blocked with exact reasons.
3. Add receipt-bound replay of a stored consensus against a candidate diff.
4. Add operator-gated authority promotion after hex-cell competition, with
   receipt ordering and duplicate-retry behavior tested.
5. Only then add performance comparisons on the same host, same Python, same
   offline/network policy, and same artifact schema.

## Guardrails

- No rival SDK execution in CI.
- No `consensus_grade=true` until all required local evidence passes.
- No "beats competitors" language.
- No production authority for hex-cell winners without a separate
  operator-gated promotion patch.
- No intelligence claim without paired benchmark methodology and raw artifacts.
