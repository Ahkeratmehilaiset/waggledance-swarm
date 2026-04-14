# 400h Campaign Runbook

## Prerequisites

- Server running on port 8002 (`docs/runs/ui_gauntlet_20260412/_launch_gauntlet_server.py`)
- Ollama running on 11434
- `.venv` junction pointing to `C:\WaggleDance_venv` (Python 3.13.7)
- Ephemeral API key in `%TEMP%\waggle_gauntlet_8002.key`

## Starting a Segment

```bash
cd C:\Python\project2
.venv/Scripts/python.exe tests/e2e/ui_gauntlet_400h.py \
  --mode HOT --segment-hours 8 \
  --campaign-dir docs/runs/ui_gauntlet_400h_20260413_092800
```

## Resuming After Crash

The harness reads `campaign_state.json` and `chat_ui_results.jsonl` to determine:
- Last completed segment
- Last completed query_id
- Cumulative green hours per mode

It skips already-completed work and continues from the first missing entry.

## Mode Quick Reference

### HOT Mode
- Cycle through all 7 corpus buckets
- Recycle browser context every 50 queries
- Backend health check on any failure
- Fresh-context retry before classifying as PRODUCT

### WARM Mode
- Every cycle: overview, feeds, hologram, chat, auth check
- Every 3rd cycle: send one chat
- Every 10th: mini-burst 3-5 chats
- Every 20th: recycle browser context
- Every 30th: fresh auth bootstrap

### COLD Mode
- Every 2 min: health, ready, status, feeds, hologram/state
- Every 30 min: cookie bootstrap check
- Every 60 min: one authenticated chat

## Incident Classification

| Category | When to use |
|---|---|
| PRODUCT | Backend returns wrong data, 5xx, auth bypass, XSS |
| HARNESS | Playwright timeout in stale context, tab switch fail, DOM buildup |
| CI/WORKFLOW | GitHub Actions failure, test portability issue |
| INFRA/ENV | DNS resolution, Ollama timeout, host suspend |
| OPERATOR | Human error, wrong config |

## Stopping

- Clean stop: let current segment finish, it writes final metrics
- Emergency stop: Ctrl+C, harness writes partial state to JSONL
- Resume: re-run with same `--campaign-dir`, it picks up from durable state
