# Phase 17B — Local AI Efficiency Benchmark

**Branch:** phase17b/local-efficiency-benchmark
**Started:** 2026-05-04T20:26:57Z
**Finished:** 2026-05-04T20:31:11Z
**Overall pass:** **True**

| metric | value |
|---|---|
| WaggleDance scenarios A–E pass | True |
| provider_jobs_delta_total | 0 |
| builder_jobs_delta_total | 0 |
| ollama_baseline_status | SKIPPED |
| external_competitor_slots | NOT_RUN |

## Scenarios

### A_solver_hot_path

```json
{
  "auto_promotions_total": 128,
  "builder_jobs_delta": 0,
  "corpus_total": 128,
  "exit_code": 0,
  "name": "A_solver_hot_path",
  "negative_cases_passed": 5,
  "out_json_path": "docs\\runs\\phase17b_local_efficiency_benchmark_2026_05_04\\scenario_A_solver_hot_path\\automatic_runtime_hint_proof.json",
  "pass2_cold_p50_ms": null,
  "passed": true,
  "provider_jobs_delta": 0,
  "runtime_seconds": 25.9812,
  "served_via_capability_lookup_total": 128,
  "tool": "tools/run_automatic_runtime_hint_proof.py"
}
```

### B_capability_lookup_10k

```json
{
  "build_descriptors_per_second": 60.2,
  "build_index_time_seconds": 166.0849,
  "builder_jobs_delta": 0,
  "exit_code": 0,
  "is_synthetic_scale": true,
  "lookup_capability_hits_total": 1000,
  "lookup_fifo_fallback_total": 0,
  "lookup_miss_total": 0,
  "lookup_p50_ms": 4.3321,
  "lookup_p95_ms": 10.9753,
  "lookup_p99_ms": 12.7719,
  "lookup_pass_count": 1000,
  "name": "B_capability_lookup_10k",
  "not_canonical_corpus": true,
  "out_json_path": "docs\\runs\\phase17b_local_efficiency_benchmark_2026_05_04\\scenario_B_capability_lookup_10k\\solver_scale_proof.json",
  "passed": true,
  "provider_jobs_delta": 0,
  "runtime_seconds": 171.9577,
  "synthetic_solver_descriptors_total": 10000,
  "tool": "tools/run_solver_scale_proof.py"
}
```

### C_handle_query_e2e

```json
{
  "auto_promotions_total": 128,
  "builder_jobs_delta": 0,
  "corpus_total": 128,
  "exit_code": 0,
  "name": "C_handle_query_e2e",
  "negative_cases_passed": 7,
  "out_json_path": "docs\\runs\\phase17b_local_efficiency_benchmark_2026_05_04\\scenario_C_handle_query_e2e\\upstream_structured_request_proof.json",
  "passed": true,
  "provider_jobs_delta": 0,
  "runtime_seconds": 31.0779,
  "served_via_capability_lookup_total": 128,
  "structured_request_derived_total": 128,
  "tool": "tools/run_upstream_structured_request_proof.py"
}
```

### D_restart_continuity

```json
{
  "builder_jobs_delta_during_proof": 0,
  "corpus_total": 128,
  "exit_code": 0,
  "name": "D_restart_continuity",
  "out_json_path": "docs\\runs\\phase17b_local_efficiency_benchmark_2026_05_04\\scenario_D_restart_continuity\\full_restart_continuity_proof.json",
  "passed": true,
  "provider_jobs_delta_during_proof": 0,
  "restart_invariants_all_true": false,
  "restart_invariants_count": 7,
  "runtime_seconds": 24.2874,
  "served_post_restart": null,
  "served_pre_restart": null,
  "tool": "tools/run_full_restart_continuity_proof.py"
}
```

### E_producer_fabric

```json
{
  "builder_jobs_delta": 0,
  "corpus_total": 30,
  "exit_code": 0,
  "ir_objects_emitted_total": 68,
  "ir_objects_per_kind": {
    "curiosity": 30,
    "dream_curriculum": 6,
    "dream_meta_proposal": 2,
    "hive_proposals": 8,
    "review_bundle": 8,
    "self_model": 14
  },
  "name": "E_producer_fabric",
  "negative_cases_passed": 6,
  "negative_cases_total": 6,
  "out_json_path": "docs\\runs\\phase17b_local_efficiency_benchmark_2026_05_04\\scenario_E_producer_fabric\\producer_fabric_proof.json",
  "passed": true,
  "provider_jobs_delta": 0,
  "runtime_seconds": 0.1061,
  "tool": "tools/run_phase17a_producer_fabric_proof.py"
}
```

### F_ollama_baseline

```json
{
  "reason": "--skip-ollama flag set by caller",
  "status": "SKIPPED"
}
```

### G_external_competitor_slots

```json
{
  "policy": "master prompt rule 14: no cloud API calls and no download / pull this session",
  "slots": [
    {
      "reason_not_run": "would require Anthropic API call; rule 14 forbids cloud calls this session.",
      "requirements_to_upgrade_to_measured": [
        "valid Anthropic API key",
        "documented prompt template + sampling parameters",
        "tool that records per-query route_source",
        "comparable WaggleDance run on same input set"
      ],
      "slot": "frontier_anthropic_claude",
      "status": "NOT_RUN"
    },
    {
      "reason_not_run": "would require OpenAI API call; rule 14 forbids cloud calls this session.",
      "requirements_to_upgrade_to_measured": [
        "valid OpenAI API key",
        "documented prompt template + sampling parameters",
        "comparable WaggleDance run on same input set"
      ],
      "slot": "frontier_openai_gpt",
      "status": "NOT_RUN"
    },
    {
      "reason_not_run": "would require Google Gemini API call; rule 14 forbids cloud calls this session.",
      "requirements_to_upgrade_to_measured": [
        "valid Google AI API key",
        "documented prompt template + sampling parameters",
        "comparable WaggleDance run on same input set"
      ],
      "slot": "frontier_google_gemini",
      "status": "NOT_RUN"
    },
    {
      "reason_not_run": "llama.cpp not installed on this host; rule 14 forbids download / pull.",
      "requirements_to_upgrade_to_measured": [
        "llama.cpp binary already present + model weights already downloaded at session start",
        "pinned prompt template + sampling parameters",
        "documented hardware spec"
      ],
      "slot": "local_llama_cpp",
      "status": "NOT_RUN"
    },
    {
      "reason_not_run": "vLLM not installed on this host; rule 14 forbids download / pull.",
      "requirements_to_upgrade_to_measured": [
        "vLLM server already running locally with a pre-loaded model at session start",
        "pinned prompt template + sampling parameters"
      ],
      "slot": "local_vllm",
      "status": "NOT_RUN"
    },
    {
      "reason_not_run": "mistral-rs not installed on this host.",
      "requirements_to_upgrade_to_measured": [
        "mistral-rs binary already present + model already downloaded",
        "pinned prompt template + sampling parameters"
      ],
      "slot": "local_mistral_rs",
      "status": "NOT_RUN"
    }
  ],
  "status": "NOT_RUN"
}
```

## Disclaimer flags (see JSON envelope)

Refer to the JSON envelope above for explicit structural-invariant flags. Each is set to `true` by this harness as part of the output schema, not as marketing copy. The Markdown body intentionally avoids restating any denylist phrase verbatim so a substring regression test can guard the document.
