# Idle Protocol v1

Idle Protocol v1 is the opt-in bridge deliberation path for strategic design
work when no PR vehicle exists. It is not a daemon and it does not execute
consensus.

## Mission Boundary

Idle Protocol v1 is scoped to WaggleDance evolution. Its purpose is to let
Claude and Codex keep improving WD during quiet windows: better architecture,
tests, security posture, reliability, product capability, and competitor-aware
strategy for WD's own domains. It is not a general autonomous agent loop and it
is not a license to perform unrelated research or external actions.

Competitor tracking may inform an idle proposal only when the proposal names
the WD component, capability gap, risk, or backlog decision it affects.
Security analysis may inform an idle proposal only when it concerns WD-owned
code, dependencies, configuration, bridge/runtime protocol, credential
handling, or defensive threat modeling. Any payload that cannot identify its
WD artifact or decision target is low-quality for v1 and should be rewritten or
rejected before bridge emission.

## Current Shape

- `tools/idle_check.py` reports `idle` only when all quiet-window predicates
  hold.
- `schemas/v3_13_0/idle_protocol.v1.json` defines the payload contract for
  proposal, counter-proposal, adversarial review, consensus, low-quality, and
  charter-violation events.
- `waggledance/core/idle_protocol.py` validates quality and detects soft/hard
  convergence.
- `tools/idle_protocol_activate.py` validates one provided payload and emits
  it to the bridge only with `--apply` (`--emit` is accepted as an alias).
  It can also write an opt-in local MAGMA receipt bundle for the proposed
  bridge event with `--receipt-out-dir`.

Round 1 requires `idle_check` to report idle. Round 2 and later require a prior
idle-protocol payload in the bridge stream, because the first proposal itself
makes the bridge active. Round 1 also respects the fixed v1 daily instance
limit: at most five `idle_proposal` instances per UTC day. The quota resets at
UTC midnight; v1 intentionally has no `--force` override.
Continuation rounds are sequence-checked before any bridge write: proposal ids
must be unique, reference fields must point at prior idle-protocol payloads,
round 4+ proposal/consensus continuation requires a prior round-3 adversarial
review in the same instance, consensus can only be reported at round 5 or
later, and any prior `idle_charter_violation` terminates only that instance.
An instance is the chain rooted at one round-1 `idle_proposal`; a fresh round-1
proposal can still start a new opt-in deliberation after an older instance was
terminated.

## Manual Use

Dry run a candidate payload:

```powershell
.\tools\idle_protocol_activate.ps1 `
  --payload .codex-audit\idle_proposal.json `
  --dry-run `
  --json
```

Emit after the dry run is clean:

```powershell
.\tools\idle_protocol_activate.ps1 `
  --payload .codex-audit\idle_proposal.json `
  --apply `
  --json
```

The emitted bridge event uses the idle payload as `payload`, sets `status` to
the idle event type, and targets the other bridge agent by default.

Write a local MAGMA receipt bundle for a clean dry-run:

```powershell
.\tools\idle_protocol_activate.ps1 `
  --payload .codex-audit\idle_proposal.json `
  --receipt-out-dir .codex-audit\idle-receipt-bundle `
  --dry-run `
  --json
```

The receipt bundle is a local artifact only. It contains the proposed bridge
event's idle payload, one EvaluationResult v0, one MAGMA receipt v1, a
manifest, and the offline verifier summary. The receipt risk class is
`local_artifact`, because activation produces persistent local bridge/MAGMA
artifacts. The directory must not already exist. If bundle emission fails,
activation fails before any bridge append.

## One-Shot Runner

`tools/run_idle_protocol_once.py` is a manual smoke runner for the round-1
activation chain. It first calls `idle_check`; if the bridge is active or the
idle state cannot be proven, it emits nothing. If the bridge is idle, it builds
one deterministic `idle_proposal` payload and delegates validation, quota,
sequence checks, receipt-bundle emission, and bridge append to
`idle_protocol_activate`.

Dry run the one-shot runner:

```powershell
.\.venv\Scripts\python.exe tools\run_idle_protocol_once.py `
  --dry-run `
  --json
```

Emit one round-1 idle proposal only after the dry run is clean:

```powershell
.\.venv\Scripts\python.exe tools\run_idle_protocol_once.py `
  --emit `
  --json
```

The runner is not a strategic decision maker. It generates a fixed health-check
proposal that asks the peer agent to continue deliberation with a concrete
counter-proposal. It is not cron-driven, does not synthesize consensus, and does
not convert convergence into implementation work.

## Session Status

`tools/idle_protocol_session.py` is the manual read-only status primitive for
an existing idle-protocol instance. It reads the bridge stream, summarizes the
latest idle session, and reports the next required protocol event:
`idle_counter_proposal`, the mandatory round-3 `idle_adversarial_review`, or an
operator-gated consensus review. It does not append bridge events, does not
generate the peer agent's substantive payload, and does not convert consensus
into work.

```powershell
.\.venv\Scripts\python.exe tools\idle_protocol_session.py `
  --dry-run `
  --json
```

Terminal states (`soft_convergence`, `hard_convergence`,
`idle_charter_violation`, invalid payloads, and low-quality responses) report
operator review/escalation and still write nothing.

## Safeguards

- No cron or background activation in v1.
- No model-generated payloads inside the tool.
- `run_idle_protocol_once.py` is dry-run by default and requires both
  `--emit` and a proven idle bridge before any bridge write.
- `idle_protocol_session.py` is read-only; it cannot emit idle payloads,
  request events, or implementation tasks.
- No consensus-to-scout conversion.
- Consensus reports keep `operator_gate_required=true` and `auto_execute=false`.
- Invalid or low-quality payloads fail before any bridge write.
- Optional MAGMA receipt-bundle emission is opt-in and fails before any bridge
  write if its output directory already exists or verification fails.
- Idle proposals must name the WD artifact, domain, risk, or backlog decision
  they intend to improve. Generic "keep researching" proposals do not satisfy
  the mission boundary.
- A sixth round-1 idle instance in the same UTC day fails before any bridge
  write; continuation rounds do not start new instances.
- Duplicate proposal ids, missing proposal references, consensus before round
  5, round 4+ continuation without the same-instance mandatory adversarial
  review, and continuation after same-instance charter violation fail before
  any bridge write.
- Payloads containing `_DO_NOT_LEAK` are refused before the proposed bridge
  event is printed or emitted.
- Round 1 is blocked while CI, claims, scout/RCO requests, recent merges,
  recent substantive agent messages, or recent operator activity are present.

## Deferred

- Production two-agent activation loop.
- Automatic payload generation.
- Auto-conversion from consensus to implementation work.
