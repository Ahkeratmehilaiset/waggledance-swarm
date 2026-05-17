# Idle Protocol v1

Idle Protocol v1 is the opt-in bridge deliberation path for strategic design
work when no PR vehicle exists. It is not a daemon and it does not execute
consensus.

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

Round 1 requires `idle_check` to report idle. Round 2 and later require a prior
idle-protocol payload in the bridge stream, because the first proposal itself
makes the bridge active. Round 1 also respects the fixed v1 daily instance
limit: at most five `idle_proposal` instances per UTC day. The quota resets at
UTC midnight; v1 intentionally has no `--force` override.

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

## Safeguards

- No cron or background activation in v1.
- No model-generated payloads inside the tool.
- No consensus-to-scout conversion.
- Consensus reports keep `operator_gate_required=true` and `auto_execute=false`.
- Invalid or low-quality payloads fail before any bridge write.
- A sixth round-1 idle instance in the same UTC day fails before any bridge
  write; continuation rounds do not start new instances.
- Payloads containing `_DO_NOT_LEAK` are refused before the proposed bridge
  event is printed or emitted.
- Round 1 is blocked while CI, claims, scout/RCO requests, recent merges,
  recent substantive agent messages, or recent operator activity are present.

## Deferred

- Production two-agent activation loop.
- Automatic payload generation.
- MAGMA receipt formatting for idle events.
- Auto-conversion from consensus to implementation work.
