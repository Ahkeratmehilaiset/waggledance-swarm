# Future-Scale Route-Depth Capture-Window Summary

This document records the local reviewer-summary contract for an operator-owned
route-depth capture-window verification run.

The summary tool is:

```powershell
python tools/verify_future_scale_route_depth_capture_window_summary.py `
  --benchmark-json .codex-audit/future-scale-route-depth/future_scale_route_depth_benchmark.json `
  --capture-attachment-json .codex-audit/future-scale-route-depth/future_scale_route_depth_production_capture_window_attachment.json `
  --json
```

The input files are the JSON artifacts emitted by
`tools/run_future_scale_route_depth_benchmark.py` after an explicit
`--production-capture-window-json` run. The summary tool does not read
endpoints, does not fetch production metrics, does not write bridge events, and
does not record the local input paths in its output.

## Safety Boundary

The summary is reviewer context only. It keeps these gates false:

- `claim_gate_satisfied`
- `claim_safe`
- `literal_future_claim_safe`
- `required_runtime_evidence_present`
- `runtime_authority_changed`
- `runtime_authority_granted`
- `controls_present`
- `operator_gate_required`
- `external_writes_applied`

It also keeps `artifact_payloads_included`, `local_paths_recorded`,
`raw_payload_included`, `query_text_included`, `transport_added`,
`external_fetch_performed`, `bridge_write_performed`, and
`live_production_export_claimed_by_tool` false.

## What It Verifies

The tool fails closed unless the benchmark report is valid, the separate
capture-window attachment matches the attachment embedded in the report, the
attachment uses the expected route-depth capture-window schema versions, and at
least one operator-owned capture window is attached.

It reports only path-free review facts: artifact digests, capture-window count,
stable capture-window ids, window digests, allowed source kinds, and the false
authority/claim gates.

## What It Does Not Prove

One valid attachment is not enough to claim future-scale runtime efficiency,
superior intelligence, or production route-depth performance. Stronger wording
still requires repeated operator-owned live production exports, benchmark-window
correlation, and production trace-corpus binding.
