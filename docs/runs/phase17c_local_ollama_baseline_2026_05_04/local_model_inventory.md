# Phase 17C — Local Model Inventory + Selection

**Captured (UTC):** 2026-05-04T21:35:00Z
**Ollama version:** 0.22.1
**Ollama path:** `C:/Users/mfi0jjko/AppData/Local/Programs/Ollama/ollama`

## Selection (rule 14 preference order)

| Rank | Model | Size | Present? | Selected? |
| --- | --- | --- | --- | --- |
| 1 | `gemma4:e4b` | 9.6 GB | yes | **YES** |
| 2 | `gemma4:26b` | 17 GB | yes | — |
| 3 | `gemma3:4b` | 3.3 GB | yes | — |
| 4 | `qwen2.5:7b` | 4.7 GB | yes | — |
| 5 | `phi4-mini:latest` | 2.5 GB | yes | — |
| 6 | `llama3.2:3b` | 2.0 GB | yes | — |

The harness picks the first present match. All six preferred models exist
locally, so `gemma4:e4b` wins by rank.

**Selected model:** `gemma4:e4b` (id `c6eb396dbd59`, 9.6 GB).

## Full local inventory (22 models)

| Name | ID | Size |
| --- | --- | --- |
| `gemma4:26b` | `5571076f3d70` | 17 GB |
| `gemma4:e4b` | `c6eb396dbd59` | 9.6 GB |
| `all-minilm:latest` | `1b226e2802db` | 45 MB |
| `deepseek-r1:1.5b` | `e0979632db5a` | 1.1 GB |
| `qwen3:1.7b` | `8f68893c685c` | 1.4 GB |
| `qwen3:0.6b` | `7df6b6e09427` | 522 MB |
| `nomic-embed-text:latest` | `0a109f422b47` | 274 MB |
| `phi4-mini-reasoning:latest` | `3ca8c2865ce9` | 3.2 GB |
| `phi4-mini:latest` | `78fad5d182a7` | 2.5 GB |
| `smollm2:1.7b` | `cef4a1e09247` | 1.8 GB |
| `llama3.2:3b` | `a80c4f17acd5` | 2.0 GB |
| `llama3.2:1b` | `baf6a787fdff` | 1.3 GB |
| `akx/viking-7b:latest` | `dd132bac7ae6` | 4.6 GB |
| `gemma3:4b` | `a2af6cc3eb7f` | 3.3 GB |
| `gemma3:1b` | `8648f39daa8f` | 815 MB |
| `qwen2.5:3b` | `357c53fb659c` | 1.9 GB |
| `qwen2.5:1.5b` | `65ec06548149` | 986 MB |
| `qwen2.5:0.5b` | `a8b0c5157701` | 397 MB |
| `jobautomation/OpenEuroLLM-Finnish:latest` | `a3d3a21cda0d` | 8.1 GB |
| `osoderholm/poro:latest` | `2d5ab8ec7548` | 20 GB |
| `qwen2.5:32b` | `9f13ba1299af` | 19 GB |
| `qwen2.5:7b` | `845dbda0ea48` | 4.7 GB |

## No-pull / no-download invariant

* The Phase 17C harness does NOT call `ollama pull`.
* The Phase 17C harness does NOT download model bytes from any registry.
* The Phase 17C harness does NOT call any cloud LLM API.
* The 22 models above were already present on disk before Phase 17C started.
* If `ollama` is missing from PATH, Track F is recorded as
  `NOT_AVAILABLE_NOT_RUN`. The harness never tries to install it.

## Currently running (`ollama ps`)

```
NAME    ID    SIZE    PROCESSOR    CONTEXT    UNTIL
(empty — no models loaded into memory at session start)
```

The harness will warm `gemma4:e4b` on first prompt; subsequent prompts share the
loaded session. Wall-clock latency for prompt 0 will therefore include load
time.
