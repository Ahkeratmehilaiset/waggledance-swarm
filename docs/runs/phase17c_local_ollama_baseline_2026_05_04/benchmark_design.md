# Phase 17C — Benchmark Design (BEFORE Coding)

**Status:** Authoritative for Phase 17C. Code in
`tools/run_phase17c_local_ollama_baseline.py` MUST conform to this document.

This document specifies what the Phase 17C harness measures, what it does NOT
measure, and what it must produce. It exists per Phase 17C master-prompt rule
"Do not code until this exists."

---

## 1. Purpose

Upgrade the Phase 17B Ollama baseline track from `SKIPPED_OPTIONAL` to
`MEASURED`, using only an already-installed local Ollama model. Produce a single
canonical artifact (`phase17c_local_ollama_baseline.json` + `.md`) that:

* aggregates the existing Phase 17B WaggleDance tracks (A–E) by reference
  (delegating to `tools/run_phase17b_local_efficiency_benchmark.py`);
* adds a new Track F "Local Ollama probe (one model, deterministic prompts,
  offline-by-policy)";
* keeps Tracks G "documented external slots" frozen at `NOT_RUN` with prior
  17B labels intact;
* never widens claims beyond `MEASURED-LOCAL-OLLAMA-ONE-MODEL`.

Phase 17C does not change the WaggleDance autonomy code, the six-family
allowlist, the canonical 128-seed corpus, or the 10k synthetic-scale ceiling.

## 2. Scope and invariants

### In scope

1. Calling Phase 17B aggregator as a subprocess and re-publishing its A–E
   metrics under `waggle_tracks` in the 17C JSON.
2. Detecting `ollama` on PATH; reading `ollama list` output to build a local
   model inventory.
3. Selecting one model per the rule-14 preference order (see §6).
4. Issuing a fixed 30-prompt deterministic probe to that one model via
   `ollama run <model> --` and recording per-prompt latency, output token
   approximation, and hash of output.
5. Producing `phase17c_local_ollama_baseline.json` and `.md` artifacts under
   `docs/runs/phase17c_local_ollama_baseline_2026_05_04/`.
6. Claim-label upgrades restricted to the Ollama track only.

### Out of scope (hard fail-closed)

1. Pulling, downloading, or modifying any Ollama model.
2. Calling any cloud LLM API (Anthropic, OpenAI, Google, Mistral, Cohere, etc.).
3. Modifying the Phase 17B aggregator behavior, output schema, or numbers.
4. Changing the canonical corpus size, allowlist, or autonomy code.
5. Issuing a stable tag — Phase 17C may at most produce a PRERELEASE
   (`v3.9.2-local-ollama-baseline-alpha`).
6. Stage-2 atomic flip; HUMAN_APPROVAL.yaml collection.
7. Any prose using forbidden vocabulary substrings outside JSON disclaimer
   fields (see §10).

## 3. Output JSON schema (top-level fields)

```json
{
  "benchmark_version": "phase17c.v1",
  "git_sha": "<full hex of current HEAD>",
  "python_version": "<sys.version>",
  "platform": "<platform.platform()>",
  "started_utc": "<iso8601>",
  "finished_utc": "<iso8601>",
  "duration_seconds": <float>,

  "selected_ollama_model": "gemma4:e4b" | "<fallback>" | null,
  "ollama_baseline_status": "MEASURED" | "NOT_AVAILABLE_NOT_RUN" | "FAILED",
  "no_model_pull_or_download": true,
  "no_cloud_api_calls": true,

  "waggle_tracks": {
    "A_smoke_in_process": { ... pass-through from 17B ... },
    "B_targeted_tests": { ... pass-through from 17B ... },
    "C_solver_router_perf": { ... pass-through from 17B ... },
    "D_phase10_truth_regression": { ... pass-through from 17B ... },
    "E_offline_full_suite": { ... pass-through from 17B ... }
  },

  "ollama_track": {
    "model": "<model name>",
    "model_id": "<short id>",
    "model_size_bytes": <int>,
    "ollama_version": "0.22.1",
    "prompt_count": 30,
    "prompts_run": <int>,
    "prompts_failed": <int>,
    "median_latency_seconds": <float>,
    "p95_latency_seconds": <float>,
    "total_seconds": <float>,
    "hash_chain_sha256": "<sha256 of concatenated stdouts>",
    "deterministic_seed_argv": ["--seed", "17"],
    "per_prompt": [
      {"index": 0, "prompt_hash": "<sha256>", "stdout_hash": "<sha256>",
       "latency_seconds": <float>, "stdout_bytes": <int>, "stderr_bytes": <int>,
       "exit_code": 0}
    ]
  },

  "documented_external_slots": [ ... pass-through from 17B Track G ... ],

  "claim_labels": {
    "ollama_local_baseline": "MEASURED-LOCAL-OLLAMA-ONE-MODEL",
    "competitive_evidence_axis_J": "MEASURED-LOCAL-OLLAMA-ONE-MODEL",
    "no_cross_model_ranking": true,
    "no_cloud_api_comparison": true
  },

  "not_claimed": [
    "no_consciousness",
    "no_sentience",
    "no_human_like_mind",
    "no_beats_all_competitors",
    "no_world_best",
    "no_world_fastest"
  ],

  "provider_jobs_delta": 0,
  "builder_jobs_delta": 0,
  "release_gate_pass": <bool>,
  "forbidden_claims_absent": <bool>,

  "release_gates": {
    "tests_pass": <bool>,
    "phase17b_aggregator_clean_exit": <bool>,
    "ollama_track_status_in_allowed_set": <bool>,
    "no_forbidden_substring_in_json_or_md": <bool>,
    "no_provider_jobs_added": true,
    "no_builder_jobs_added": true,
    "no_allowlist_widened": true,
    "no_stable_release_in_phase17c": true
  }
}
```

The JSON schema above is the contract that
`tools/run_phase17c_local_ollama_baseline.py` and
`tests/autonomy_growth/test_phase17c_local_ollama_baseline.py` enforce.

## 4. Markdown rendering rules

The `.md` sibling MUST:

1. List the WaggleDance tracks A–E with their pass/fail status only (no
   re-derivation of numbers — pull from the JSON).
2. Render the Ollama track with: model name, version, prompt count, latency
   median + p95, hash-chain head.
3. Include a section "What this measures" and "What this does NOT measure" in
   plain prose.
4. NEVER include any forbidden vocabulary substring. Substring-checked at the
   bytes level after rendering.
5. Include the literal string "**No cloud API calls were made.**" and
   "**No model was pulled or downloaded.**" as policy declarations.
6. Include the literal SHA `8bf1869` (post-v3.6.0 truthfulness commit) in any
   CURRENT_STATUS.md update so `tests/phase10/test_truth_regression.py::
   test_current_status_main_sha_matches_origin_main` continues to pass.

## 5. Subprocess contract for Phase 17B aggregator

```
python tools/run_phase17b_local_efficiency_benchmark.py \
    --output docs/runs/phase17c_local_ollama_baseline_2026_05_04/_phase17b_pass_through.json \
    --skip-ollama
```

* `--skip-ollama` is required: 17B must NOT run its own Ollama probe when
  invoked from 17C — that path is owned by 17C.
* If 17B exits non-zero, 17C exits non-zero. No partial outputs.
* 17B output is read once; the relevant subset is mirrored under
  `waggle_tracks`. The full 17B JSON is also kept under
  `_phase17b_pass_through.json` next to the 17C artifact for audit.

## 6. Ollama model selection (rule 14)

Preference order, picked first match present in `ollama list` output:

1. `gemma4:e4b` (preferred — 9.6 GB, mid-size, recent Gemma family)
2. `gemma4:26b` (fallback — 17 GB, larger Gemma)
3. `gemma3:4b` (fallback — 3.3 GB, prior Gemma)
4. `qwen2.5:7b` (fallback — 4.7 GB, well-known instruct family)
5. `phi4-mini:latest` (fallback — 2.5 GB, Microsoft Phi)
6. `llama3.2:3b` (fallback — 2.0 GB, Meta Llama)

If none of those six are present locally, the harness records
`ollama_baseline_status="NOT_AVAILABLE_NOT_RUN"` and skips Track F. The harness
NEVER pulls a model. The CLI can override the selection via `--ollama-model
<name>`; if the override is not present in `ollama list`, the harness fails
closed (`ollama_baseline_status="FAILED"`, exit 2).

## 7. Probe prompts (deterministic)

30 prompts, all fully deterministic, derived from the six-family low-risk
allowlist (5 prompts per family). Prompts are short, factoid-style requests
the model can answer in O(seconds), and they do NOT exercise any WaggleDance
runtime — they are a pure local LLM smoke test. Examples:

* scalar_unit_conversion (5): "Convert 10 km to miles. Reply with one number
  rounded to two decimals." (etc.)
* lookup_table (5): "What is the chemical symbol for tin? Reply with one word."
* threshold_rule (5): "Is 37 above or below the threshold 30? Reply with one
  word: above or below."
* interval_bucket_classifier (5): "Bucket the value 17 into [0,10), [10,20),
  [20,30). Reply with one bucket label."
* linear_arithmetic (5): "Compute 14 + 9. Reply with one integer."
* bounded_interpolation (5): "Linear interpolation between (0, 0) and (10, 100)
  at x=3. Reply with one number."

Prompts are baked into the harness as a Python tuple. Their SHA-256 hashes are
emitted in the JSON.

## 8. Probe execution rules

* Spawn `ollama run <model> --seed 17 --temperature 0` (or equivalent CLI flags
  the installed Ollama version supports — fall back to plain `ollama run` if
  `--seed`/`--temperature` are rejected).
* Pass the prompt via stdin.
* 60-second per-prompt timeout. On timeout, record `exit_code=124` and continue;
  do NOT abort the whole probe.
* Capture stdout, stderr, exit code, wall-clock latency.
* Hash both prompt and stdout with SHA-256.
* If at least one prompt fails (exit_code != 0 or timeout), still emit the
  partial track but set `prompts_failed > 0` and refuse to claim "MEASURED" —
  fall back to `ollama_baseline_status="FAILED"` and `release_gate_pass=false`.

## 9. CLI surface

```
python tools/run_phase17c_local_ollama_baseline.py \
    [--output <path>] \
    [--skip-ollama] \
    [--ollama-model <name>] \
    [--prompt-count 30] \
    [--phase17b-binary <path>] \
    [--allow-no-ollama-track]
```

Flags:

* `--output` — output JSON path. Defaults to
  `docs/runs/phase17c_local_ollama_baseline_2026_05_04/phase17c_local_ollama_baseline.json`.
* `--skip-ollama` — force `NOT_AVAILABLE_NOT_RUN` even if Ollama is present
  (used by Docker `--network none` verification in P7).
* `--ollama-model` — override model selection.
* `--prompt-count` — limit number of prompts (testing); production = 30.
* `--phase17b-binary` — override the 17B aggregator path (testing).
* `--allow-no-ollama-track` — allow exit 0 with
  `ollama_baseline_status="NOT_AVAILABLE_NOT_RUN"` (used by Docker proof).

## 10. Forbidden vocabulary check

After rendering both JSON and MD, the harness lower-cases their bytes and
asserts that NONE of the following substrings is present anywhere outside the
`not_claimed` array of disclaimer flags:

```
conscious, sentient, aware, alive, agi, revolutionary, magical,
human-like mind, self-aware, explosive intelligence, emergent,
beats all competitors, world's best, world's fastest
```

The `not_claimed` array uses underscored compound tokens
(`no_consciousness`, `no_sentience`, etc.) so substring scanning over the
rendered prose does not produce false positives. Phase 17B already had this
problem with the substring "conscious" inside "consciousness"; the 17C harness
uses the same paraphrase strategy.

If any forbidden substring is found, harness exits non-zero with
`forbidden_claims_absent=false` and the artifact JSON is rewritten with
`release_gate_pass=false`.

## 11. Test plan (P17C-6)

`tests/autonomy_growth/test_phase17c_local_ollama_baseline.py`:

1. **MEASURED path** — fake `ollama` shim on PATH that emits a deterministic
   stdout for any prompt. Assert JSON has
   `ollama_baseline_status="MEASURED"`, `prompts_run=30`, `prompts_failed=0`,
   `release_gate_pass=true`, `forbidden_claims_absent=true`,
   `provider_jobs_delta=0`, `builder_jobs_delta=0`.
2. **NOT_AVAILABLE_NOT_RUN path** — empty PATH (no `ollama` shim). With
   `--allow-no-ollama-track`, harness exits 0 and JSON has
   `ollama_baseline_status="NOT_AVAILABLE_NOT_RUN"`,
   `selected_ollama_model=null`, `release_gate_pass=true`.
3. **FAILED path** — fake `ollama` shim that exits 1 for half the prompts.
   Harness exits non-zero, JSON has `ollama_baseline_status="FAILED"`,
   `release_gate_pass=false`.
4. **Forbidden substring path** — patch the MD renderer to inject the substring
   "conscious " (note trailing space to avoid false positive on "consciousness"
   if a future paraphrase reintroduces it). Harness exits non-zero,
   `forbidden_claims_absent=false`.

`--phase17b-binary` is wired to a stub aggregator in tests so the WaggleDance
A–E tracks don't actually run during these unit tests; they're tested
end-to-end in P17C-4.

## 12. Docker proof (P17C-7)

```
docker build -t wd-phase17c -f Dockerfile .
docker run --rm --network none wd-phase17c \
    python tools/run_phase17c_local_ollama_baseline.py \
        --skip-ollama --allow-no-ollama-track \
        --output /tmp/phase17c.json
```

Expected: exit 0, JSON has `ollama_baseline_status="NOT_AVAILABLE_NOT_RUN"`
(because `--skip-ollama` is set, and the container has no Ollama installed
anyway). This proves the 17C harness is offline-safe.

`.dockerignore` carve-out: `!tools/run_phase17c_local_ollama_baseline.py`
(otherwise the broad `tools/*.py` exclude inherited from earlier phases would
strip it from the image).

## 13. Release decision (P17C-8)

* **Decision A — release v3.9.2-local-ollama-baseline-alpha PRERELEASE** if and
  only if all the following are true:
  - `ollama_baseline_status="MEASURED"`,
  - `release_gate_pass=true`,
  - `forbidden_claims_absent=true`,
  - all P17C-6 tests pass,
  - Docker `--network none` proof passes,
  - `git rev-parse v3.8.0^{}` still equals `824176eb...`,
  - `gh release list` still shows v3.8.0 as Latest,
  - PR CI green.
* **Decision B — no new release**: any of the above fails. The Phase 17C work
  still lands as a docs+tooling PR but no tag is created.

## 14. Sign-off

This design is the canonical contract for Phase 17C. Any deviation in the
implementation must be reflected back into this document by a follow-up edit
in the same PR.
