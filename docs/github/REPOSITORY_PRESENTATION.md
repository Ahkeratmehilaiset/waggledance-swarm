# Repository Presentation Surface - WaggleDance

This file tracks the repository's external-facing GitHub presentation:
the GitHub "About" text, topic tags, README positioning, and the short
truth boundary for release readers.

This document is descriptive. Applying GitHub repository metadata is an
operator action. This PR does not run `gh repo edit` and does not change
the currently applied GitHub About text.

## Current GitHub About Text

Leave the currently applied repository description unchanged unless the
operator explicitly asks for a metadata update:

> Local-first AI runtime that routes 10k+ solver descriptors at warm p99 0.05 ms (1000/1000 hits, zero fallback) and spawns 1004 live agent clones in 120 s. Hex-mesh routing, MAGMA-audited provenance, agent-bridge autonomy fabric. Profile S (fully offline) or Profile L (opt-in cloud). BUSL.

The wording above is intentionally retained per operator instruction.

## Suggested Topics / Tags

Current GitHub topics are aligned with the R22/R23 surface:

* `multi-agent`
* `ollama`
* `ai-runtime`
* `iot`
* `local-first`
* `solver-first`
* `autonomous-learning`
* `chromadb`
* `finnish-nlp`
* `mqtt`
* `opentelemetry`
* `prometheus`
* `pytorch`
* `sklearn`
* `smart-home`
* `agent-bridge`
* `busl-license`
* `autogrowth`
* `autonomy-fabric`
* `hexagonal-topology`

Avoid topic or About wording that implies AGI, consciousness, raw model
superiority, or cross-vendor ranking.

## One-Paragraph External Summary

WaggleDance is a local-first Python runtime for routing structured work
through deterministic solvers before learned fallback. It ships a bounded
six-family auto-growth lane, capability-aware routing through a SQLite
control plane, MAGMA audit/provenance surfaces, and a two-agent bridge
that lets Claude Code and Codex coordinate implementation, review, and
release work without operator paste-relay. Profile S stays fully offline;
Profile L can opt into cloud LLM calls with PII redaction and fail-closed
provider behavior.

## What Is Real Now

* Solver-first routing and six-family deterministic solver execution.
* Runtime-gap mining and replay into allowlisted low-risk solver specs.
* Capability-aware dispatch with synthetic 10k and 50k descriptor scale
  measurements.
* MAGMA event/provenance/trust adapters and replay surfaces.
* R22/R23 bridge substrate:
  * wake-on-event coordination,
  * heartbeat for long tasks,
  * stale-claim release,
  * per-agent worktree bootstrap,
  * process-isolated role review helpers.
* Profile S offline mode and Profile L opt-in cloud path with redaction.
* Group-call language pipeline for packing multiple specialist agent
  slots into one structured English LLM call while keeping Finnish as an
  edge-language option.

## Current Measured Claims

These are the strongest public claims currently supported by local
evidence:

* 10,000 synthetic solver descriptors: 1,000/1,000 capability hits,
  zero FIFO fallback, zero miss, warm p99 0.0497 ms.
* 50,000 synthetic solver descriptors: 2,000/2,000 capability hits,
  zero FIFO fallback, zero miss, warm p99 0.2198 ms.
* Mined solver dispatch: 30 runtime signals -> 14 candidates -> 6
  allowlisted registered solvers; 18/18 dispatch cases passed across
  the six low-risk families.
* 2D branch isolation is measured, not solved: single-hot cross-branch
  load caused 2.806x p99 degradation; adversarial cold-flood load caused
  12.217x p99 degradation in the latest local audit.
* Live agent object/runtime capacity audit: 81 templates and 1004 live
  agent clones in 120 s. This is not a claim of 1004 simultaneous live
  LLM calls.

## What Is Alpha / Pending

* v3.11.0-r20-axis-b-activated-alpha is a prerelease. It is not promoted
  to v3.11.0 stable.
* v3.12.0 stable is targeted for 2026-05-24 or later after the R22.5
  soak and promotion gates.
* Profile L cloud LLM use is opt-in and must pass the redaction and
  budget gates.
* 2D topology is the current release shape. R25 3D / per-cell sharding
  remains a scout/measurement topic and is not part of the current
  release surface.
* Docker is supported as a reproducibility/runtime vehicle, but the
  stable image/tag policy remains tied to v3.8.0 until R22.5 promotion.

## What Is Not Claimed

* No consciousness, sentience, AGI, or human-like understanding claim.
* No raw-intelligence superiority claim.
* No "world's fastest" or cross-vendor ranking claim.
* No claim that 50k cold lookup is low-latency; the measured 50k cold
  p99 was 354.8117 ms.
* No claim that branch isolation is solved; it is the next measured
  scaling bottleneck.
* No claim that the live-agent clone measurement equals 1004 concurrent
  LLM completions.

## README / Release Mapping

* `README.md` is the GitHub landing page and currently carries the
  R22/R23 public positioning.
* `CHANGELOG.md` is the chronological release and sprint ledger.
* `docs/release/RELEASE_READINESS.md` is the release-state and gate
  summary.
* `docs/deployment/DOCKER_QUICKSTART.md` is the Docker reproduction and
  deployment-status guide.
* `iterations/codex_scout_tasks/r22_2d_branch_isolation_baseline_2026_05_10.md`
  records the R22 branch-isolation measurement.
* Latest local 10k/50k solver and group-call claim audit was run on 2026-05-10; the measured numbers are repeated in this document because `.codex-audit/` is scratch state, not a public release artifact.

## Apply Metadata

Only the operator should update GitHub repository metadata. Suggested
commands, if explicitly requested later:

```powershell
gh repo edit Ahkeratmehilaiset/waggledance-swarm `
  --description "<operator-approved text>"
```

Do not apply social preview or topic edits from an autonomous docs PR
unless the operator asks for that exact action.
