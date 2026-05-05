# Phase 18A Claim Evidence Ledger

| claim_id | label | evidence_artifact | scope |
| --- | --- | --- | --- |
| `docker_offline_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` | WaggleDance autonomy + producer fabric + 10k synthetic capability lookup paths. |
| `producer_fabric_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` | Offline producer fabric; deterministic fixtures. |
| `capability_lookup_10k_measured` | **MEASURED** | `phase17b_local_efficiency_benchmark.sanitized.json` | WaggleDance RuntimeQueryRouter.route() capability-aware path; six-family allowlist. |
| `canonical_corpus_128_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` | Six-family low-risk allowlist seed corpus. |
| `local_efficiency_harness_proven` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` | WaggleDance core proofs aggregated into a single artifact. |
| `local_ollama_one_model_measured` | **MEASURED_LOCAL_OLLAMA_ONE_MODEL** | `phase17c_local_ollama_baseline.sanitized.json` | Local Ollama daemon, gemma4:e4b, ollama 0.22.1, 30 prompts. |
| `local_ollama_panel_measured` | **MEASURED_LOCAL_OLLAMA_PANEL** | `phase17d_local_model_sweep.sanitized.json` | Local Ollama daemon, 4-model panel, 360 prompts on this host. |
| `raw_intelligence_vs_frontier_moe_not_claimed` | **NOT_CLAIMED** | `phase17d_local_model_sweep.sanitized.json` | Public competitive comparison. |
| `cross_vendor_ranking_not_claimed` | **NOT_CLAIMED** | `phase17d_local_model_sweep.sanitized.json` | Phase 17D 4-model panel. |
| `no_model_pull_or_download` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` | All three benchmark harnesses. |
| `no_cloud_api_calls` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` | All three benchmark harnesses. |
| `provider_builder_delta_zero` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` | WaggleDance autonomy hot-path. |
| `no_stage2_flip` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` | Per-session honesty invariants. |
| `no_human_approval_collected` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` | Per-session honesty invariants. |
| `no_allowlist_widening` | **PROVEN** | `phase17b_local_efficiency_benchmark.sanitized.json` | WaggleDance autonomy growth allowlist. |
| `benchmark_artifact_externalization` | **PROVEN** | `phase17d_local_model_sweep.sanitized.json` | Phase 18A bundle export + validation contract. |

## Negative claims

* `raw_intelligence_vs_frontier_moe_not_claimed` - WaggleDance does not assert raw-intelligence superiority over any frontier MoE model.
* `cross_vendor_ranking_not_claimed` - the Phase 17D 4-model panel reports per-model numbers in selection order; no rank ordering is implied.

