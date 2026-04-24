# Honeycomb 400h campaign — plan

- **Target:** 400.0 h total
- **Checkpoint cadence:** status every 2h, validation pack every 12h, commit every 24h
- **Stop conditions:** see `STOP_CONDITIONS` in `tools/run_honeycomb_400h_campaign.py`

## Segment allocation

| segment | hours | description |
|---|---|---|
| `HOT` | 100.0 | chat/UI gauntlet rotation, context recycling, adversarial corpus |
| `WARM` | 60.0 | tab switching, feed panel, chat panel, hologram state checks |
| `COLD` | 80.0 | health/ready/status/auth/cookie checks |
| `SOLVER` | 32.0 | cell_manifest + solver_dedupe + propose_solver dry-run |
| `COMPOSITION` | 32.0 | composition graph recompute + bridge stability |
| `LEARNING` | 32.0 | dream_mode + quality_gate dry-run (safe mode, no writes) |
| `CI` | 32.0 | recurring small test subset (phase7 + phase8 + contracts) |
| `FULL` | 32.0 | nightly full pytest when machine budget allows |

## Invocation

This harness never auto-starts. Either

- **Dry-run plan only:** `python tools/run_honeycomb_400h_campaign.py --target-hours 400`
- **Live run:**         `python tools/run_honeycomb_400h_campaign.py --target-hours 400 --confirm-start`

HOT/WARM/COLD segments delegate to the existing
`tests/e2e/ui_gauntlet_400h.py` harness by default. The honeycomb
harness only owns the SOLVER / COMPOSITION / LEARNING / CI / FULL
segments directly.
