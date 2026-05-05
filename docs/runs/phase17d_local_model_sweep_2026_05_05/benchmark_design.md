# Phase 17D — Benchmark Design (BEFORE Coding)

**Status:** Authoritative for Phase 17D. Code in `tools/run_phase17d_local_model_sweep.py` MUST conform to this document.

This document specifies what the Phase 17D harness measures, what it does NOT measure, and what it must produce. It exists per Phase 17D master-prompt rule "Do not code until this exists."

---

## 1. Purpose

Extend the Phase 17C single-model probe (`gemma4:e4b`, MEASURED, 30 prompts) to a multi-model sweep with repeatability statistics across N already-installed local models. Produce a single canonical artifact (`phase17d_local_model_sweep.{json,md}`) that:

* enumerates the selected models;
* runs each model R times across the same P-prompt manifest;
* reports per-model latency + variance + correctness statistics;
* never pulls or downloads a model;
* never calls a cloud LLM API;
* never claims raw-intelligence superiority;
* never produces cross-vendor rankings.

Phase 17D does not change the WaggleDance autonomy code, the six-family allowlist, the canonical 128-seed corpus, the 10k synthetic-scale ceiling, or the Phase 17C single-model artifact.

## 2. Scope and invariants

### In scope

1. Detecting `ollama` on PATH; reading `ollama list` output to build a local model inventory.
2. Selecting up to `--max-models` (default 4) already-installed models per the Phase 17D preference order (see §6).
3. Issuing a fixed P-prompt deterministic probe (default P=30, same prompts as Phase 17C) to each selected model R times (default R=3).
4. Aborting if the harness detects any pull/download attempt in subprocess output (rule 7 of master prompt).
5. Producing `phase17d_local_model_sweep.{json,md}` artifacts under `docs/runs/phase17d_local_model_sweep_2026_05_05/`.
6. Claim-label upgrade restricted to `MEASURED-LOCAL-OLLAMA-PANEL` for axis J — never beyond.

### Out of scope (hard fail-closed)

1. Pulling, downloading, or modifying any Ollama model.
2. Calling any cloud LLM API.
3. Modifying the Phase 17C aggregator behaviour or its committed artifact.
4. Changing the canonical corpus size, allowlist, or autonomy code.
5. Issuing a stable tag — Phase 17D may at most produce a PRERELEASE (`v3.9.3-local-model-sweep-alpha`).
6. Stage-2 atomic flip; HUMAN_APPROVAL.yaml collection.
7. Cross-vendor ranking (no "Gemma > Llama" claims, etc.).
8. Raw-intelligence superiority claim of any kind.
9. Forbidden vocabulary substrings outside JSON disclaimer fields.
10. Editing `CURRENT_STATE.md` manually (master prompt rule 17).

## 3. Output JSON schema (top-level fields)

```json
{
  "benchmark_version": "phase17d.v1",
  "git_sha": "<full hex of current HEAD>",
  "python_version": "<sys.version>",
  "platform": "<platform.platform()>",
  "started_utc": "<iso8601>",
  "finished_utc": "<iso8601>",
  "duration_seconds": <float>,

  "selected_models": ["gemma4:e4b", "gemma3:4b", "llama3.2:3b", "phi4-mini:latest"],
  "deferred_too_large_by_default": ["gemma4:26b", "qwen2.5:32b", "osoderholm/poro:latest"],
  "model_results": {
    "gemma4:e4b": {
      "model_name": "gemma4:e4b",
      "model_id": "c6eb396dbd59",
      "model_size_bytes": <int>,
      "ollama_version": "ollama version is 0.22.1",

      "prompt_count": 30,
      "repeat_count": 3,
      "total_prompts_attempted": 90,
      "prompts_succeeded": <int>,
      "prompts_failed": <int>,

      "correctness_count": <int>,
      "correctness_total": <int>,
      "correctness_rate": <float>,
      "parse_success_count": <int>,
      "parse_success_rate": <float>,

      "latency_ms_min": <float>,
      "latency_ms_p50": <float>,
      "latency_ms_p95": <float>,
      "latency_ms_p99": <float>,
      "mean_latency_ms": <float>,
      "stddev_latency_ms": <float>,
      "coefficient_of_variation": <float>,

      "total_seconds": <float>,
      "throughput_prompts_per_second": <float>,
      "hash_chain_sha256": "<sha256>",

      "no_model_pull_or_download": true,
      "no_cloud_api_calls": true,

      "claim_label": "MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET",

      "per_repeat": [
        {"repeat_index": 0, "prompts_succeeded": 30, "prompts_failed": 0,
         "median_latency_seconds": 0.79, "p95_latency_seconds": 17.55,
         "total_seconds": 76.62, "hash_chain_sha256": "..."},
        {"repeat_index": 1, ...},
        {"repeat_index": 2, ...}
      ]
    },
    "gemma3:4b": { ... },
    "llama3.2:3b": { ... },
    "phi4-mini:latest": { ... }
  },

  "panel_summary": {
    "models_measured_count": 4,
    "models_succeeded_count": 4,
    "min_median_latency_seconds_across_panel": <float>,
    "max_median_latency_seconds_across_panel": <float>,
    "panel_coefficient_of_variation_max": <float>
  },

  "claim_labels": {
    "ollama_local_baseline": "MEASURED-LOCAL-OLLAMA-PANEL",
    "competitive_evidence_axis_J": "MEASURED-LOCAL-OLLAMA-PANEL",
    "no_cross_model_ranking": true,
    "no_cross_vendor_ranking": true,
    "no_cloud_api_comparison": true,
    "raw_intelligence_vs_frontier_moe": "NOT_CLAIMED"
  },

  "not_claimed": [
    "no_consciousness",
    "no_sentience",
    "no_human_like_mind",
    "no_beats_all_competitors",
    "no_world_best",
    "no_world_fastest",
    "no_raw_intelligence_superiority",
    "no_cross_vendor_ranking"
  ],

  "provider_jobs_delta": 0,
  "builder_jobs_delta": 0,
  "no_model_pull_or_download": true,
  "no_cloud_api_calls": true,
  "release_gate_pass": <bool>,
  "forbidden_claims_absent": <bool>,

  "release_gates": {
    "at_least_two_models_measured": <bool>,
    "no_pull_download_detected": <bool>,
    "no_cloud_api_call_detected": <bool>,
    "all_selected_models_completed": <bool>,
    "no_forbidden_substring_in_json_or_md": <bool>,
    "no_provider_jobs_added": true,
    "no_builder_jobs_added": true,
    "no_allowlist_widened": true,
    "no_stable_release_in_phase17d": true,
    "no_cross_vendor_ranking": true,
    "no_raw_intelligence_superiority_claim": true
  }
}
```

## 4. Markdown rendering rules

The `.md` sibling MUST:

1. List the selected models with size + id.
2. Render per-model summary metrics (median/p95/mean/stddev/CoV, prompts succeeded/failed, hash chain head).
3. Include a "What this measures" / "What this does NOT measure" pair of sections in plain prose.
4. NEVER include any forbidden vocabulary substring. Substring-checked at the bytes level after rendering.
5. Include the literal string "**No cloud API calls were made.**" and "**No model was pulled or downloaded.**".
6. Include the literal string "**No cross-vendor ranking is implied.**".
7. NOT contain table comparisons that imply ranking ("Gemma is faster than Llama" → forbidden). Numbers are reported per model, side-by-side at most, never with rank ordering or qualitative comparison.

## 5. Model selection (rule 6, 7 of master prompt)

Preference order, picked first to fourth match present in `ollama list`:

1. `gemma4:e4b` (9.6 GB)
2. `gemma3:4b` (3.3 GB)
3. `llama3.2:3b` (2.0 GB)
4. `phi4-mini:latest` (2.5 GB)
5. `qwen2.5:7b` (4.7 GB) — held in spillover slot for `--max-models 5`

Models > 10 GB are NOT selected by default. They appear in `deferred_too_large_by_default[]` of the JSON output. The CLI override `--models a,b,c,...` accepts any explicit comma-separated list (including the deferred large ones), but the harness still asserts every named model is already locally present (no pull).

If fewer than 2 of the preference-order models are installed AND no override is given, the harness fails closed with `release_gate_pass=false`.

## 6. Prompt manifest (re-used from Phase 17C)

The 30 deterministic prompts from `tools/run_phase17c_local_ollama_baseline.py::PROBE_PROMPTS` are imported as-is. 5 prompts × 6 low-risk allowlist families. SHA-256 hashes per prompt + a chained SHA-256 over all stdouts per repeat.

The harness imports the prompt list directly from the Phase 17C module so a future change to one stays in one place.

## 7. Repeat policy (master prompt P1: repeat policy)

* Default `--repeat-count = 3`.
* Each repeat issues all P prompts to one model in sequence.
* Cold-start outliers are NOT stripped; every per-prompt latency is preserved verbatim in `per_repeat[i].per_prompt[]`. Repeatability is reported via `coefficient_of_variation` over the per-repeat median latencies.
* Between repeats the harness does NOT unload the model — Ollama keeps it warm in its own daemon for ~5 minutes by default. The first repeat captures cold-start overhead; subsequent repeats capture warm-state overhead. Both are reported.
* The order of (model × repeat) is fixed: model[0]·repeat[0], model[0]·repeat[1], model[0]·repeat[2], model[1]·repeat[0], ..., so each model is fully exercised before moving to the next. This avoids cross-model ordering bias inside one repeat.

## 8. Pull/download abort gate (master prompt rule 6, 7)

The harness scans every Ollama subprocess stdout AND stderr for these substrings (case-insensitive), and aborts with non-zero exit if found:

```
pulling manifest
downloading
pulling
verifying sha256
writing manifest
```

If any one is present in any subprocess output, the harness sets `no_pull_download_detected=false`, sets `release_gate_pass=false`, raises a hard error, and refuses to emit a MEASURED claim for that model.

This is in addition to the basic rule that the harness never invokes `ollama pull`.

## 9. Probe execution rules

* Spawn `ollama run <model>` with the prompt on stdin (or as argv if the installed Ollama version requires that — Phase 17C used argv).
* 60-second per-prompt timeout. On timeout, record `exit_code=124` and continue; do NOT abort the whole probe. A model with timeouts gets `claim_label="FAILED-FOR-THIS-MODEL"` and is not counted toward the panel-MEASURED claim.
* Read subprocess output in bytes mode; decode UTF-8 with `errors="replace"`. (Phase 17C established this; reuse `_decode_safely`.)
* Hash both prompt and stdout with SHA-256.
* If at least one prompt fails (exit_code != 0 or timeout), still emit the partial track but flag that model's `claim_label="FAILED-FOR-THIS-MODEL"`.

## 10. CLI surface

```
python tools/run_phase17d_local_model_sweep.py \
    [--out-dir <dir>] \
    [--models auto | <a,b,c>] \
    [--repeat-count 3] \
    [--prompt-count 30] \
    [--max-models 4] \
    [--allow-no-ollama-track] \
    [--prefer-larger-models]
```

Flags:

* `--out-dir` — output directory. Defaults to `docs/runs/phase17d_local_model_sweep_2026_05_05/`.
* `--models auto` (default) — walk preference order, pick first `--max-models` matches.
* `--models a,b,c` — explicit comma-separated list. Must all be locally present.
* `--repeat-count` — repeats per model. Default 3.
* `--prompt-count` — prompts per repeat. Default 30 (full Phase 17C manifest).
* `--max-models` — cap on number of models. Default 4.
* `--allow-no-ollama-track` — exit 0 even if Ollama is unavailable (Docker `--network none` proof uses this).
* `--prefer-larger-models` — opt-in to include `gemma4:26b` / `qwen2.5:32b` / `osoderholm/poro:latest` in the auto list (default OFF).

## 11. Forbidden vocabulary check

After rendering both JSON and MD, the harness lower-cases their bytes and asserts that NONE of the following substrings is present anywhere outside the `not_claimed` array of disclaimer flags:

```
conscious, sentient, aware, alive, agi, revolutionary, magical,
human-like mind, self-aware, explosive intelligence, emergent,
beats all competitors, world's best, world's fastest
```

Plus Phase 17D-specific guard substrings (case-insensitive):

```
"is faster than", "is slower than", "outperforms",
"beats", "ranks higher", "ranked first", "best of breed",
"better than"
```

If any substring is found anywhere outside `not_claimed[]` or the panel summary's neutral min/max, the harness exits non-zero with `forbidden_claims_absent=false`.

## 12. Test plan (P17D-6)

`tests/autonomy_growth/test_phase17d_local_model_sweep.py` covers:

1. **MEASURED-PANEL path** — fake `ollama` shim on PATH that emits deterministic stdout for any prompt. Assert JSON has `selected_models` of length 4, `model_results` keyed by all 4, every `claim_label="MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET"`, `release_gate_pass=true`, `forbidden_claims_absent=true`.
2. **NOT_AVAILABLE_NOT_RUN path** — empty PATH (no `ollama` shim). With `--allow-no-ollama-track`, harness exits 0 and JSON has empty `selected_models`, `release_gate_pass=true`. Without the flag: non-zero exit.
3. **TOO_FEW_MODELS path** — fake ollama with only 1 model in `ollama list`. Without `--allow-no-ollama-track`, harness fails (`release_gate_pass=false`).
4. **PULL_DETECTED path** — fake ollama whose stderr contains `"pulling manifest"`. Harness aborts with `release_gate_pass=false`, `no_pull_download_detected=false`.
5. **FAILED-FOR-MODEL path** — fake ollama that exits 1 for half the prompts of one model. That model's `claim_label="FAILED-FOR-THIS-MODEL"`; the panel-level claim downgrades but other models are still MEASURED.
6. **Forbidden substring path** — patch the MD renderer to inject `"is faster than"`. Harness exits non-zero, `forbidden_claims_absent=false`.
7. **Override --models flag** — explicit list with one absent model fails closed.
8. **Repeat-count semantics** — with `--repeat-count 2`, every model has exactly 2 entries in `per_repeat[]`.

## 13. Docker `--network none` (P4)

```
docker build -t waggledance:phase17d -f Dockerfile .
docker run --rm --network none waggledance:phase17d \
    python tools/run_phase17c_local_ollama_baseline.py \
        --skip-ollama --allow-no-ollama-track \
        --output /tmp/phase17d_docker.json
```

Phase 17D's contribution to Docker proof is to confirm the WaggleDance carry-forward path (the Phase 17C harness, running its WaggleDance Tracks A–E pass-through) still runs `--network none`. The Phase 17D harness itself targets local Ollama which does NOT run inside the container by design (the Ollama daemon is on the host).

`.dockerignore` carve-out: `!tools/run_phase17d_local_model_sweep.py`.

## 14. Release decision (P7)

* **Decision A — release `v3.9.3-local-model-sweep-alpha` PRERELEASE** if and only if all the following are true:
  - ≥ 2 models in `selected_models` with `claim_label="MEASURED-FOR-THIS-MODEL-AND-PROMPT-SET"`,
  - `release_gate_pass=true`,
  - `forbidden_claims_absent=true`,
  - `no_model_pull_or_download=true`,
  - `no_cloud_api_calls=true`,
  - `provider_jobs_delta=builder_jobs_delta=0`,
  - all P17D-6 tests pass,
  - Docker `--network none` proof passes,
  - `git rev-parse v3.8.0^{}` still equals `824176eb...`,
  - `gh release list` still shows v3.8.0 as Latest,
  - PR CI green.
* **Decision B — no new release**: any of the above fails. The Phase 17D work still lands as a docs+tooling PR but no tag is created.

## 15. Sign-off

This design is the canonical contract for Phase 17D. Any deviation in the implementation must be reflected back into this document by a follow-up edit in the same PR.
