# LEAK_POLICY (shared, offline, deterministic)

Status: producer slice (grok-scout-1). Canonical shared implementation for benchmark contracts and manifests.

Single source of truth: `waggledance/core/leak_policy.py` + its locked test `tests/core/test_leak_policy.py`.

Consumers (future contracts) import from here instead of re-implementing per-file `LEAK_PATTERNS` / `_looks_like_leak` (the drift surface that recurred across prior slices #827/#829/#830/#832).

## What it provides

1. **Model/provider token pattern** (case-insensitive):
   - Built from an explicit internal allowlist of token *shapes* (`_PROVIDER_TOKENS`).
   - Covers at minimum: `anthropic|claude|cohere|command|command-r|deepseek|falcon|gemini|gemma|google|gpt|grok|hf|huggingface|llama|mistral|mixtral|mpt|ollama|openai|phi|poro|qwen|xai|yi`
   - Matches glued forms required by spec: `gpt4o_hit`, `mpt7b`, `claude3`, `cohere_internal_model`, `command-r`, `hf/...`, `org/model:tag` (via companion pattern), digit/underscore/hyphen/dot/slash/colon continuations.
   - Tuned lookarounds + separator rules + narrow explicit false-positive guards so that common non-model words (`yield`, `yield_route_case`, `command_center`, `command_center_v2`, `mycommandhelper`, `xai_helper` etc.) do **not** over-trigger.

2. **Secret patterns**:
   - `Bearer ...`, `bearer ...`
   - `sk-...` (16+ alphanum)
   - `AKIA...` (AWS-like)

3. **Path patterns**:
   - Drive paths (`C:\...`, `D:/...`, `C:tmp`, `X:\Users|Python|Program Files|tmp` shapes)
   - Windows shares (`\\wsl\...`, `\\share\...`)
   - Unix roots: `/home`, `/root`, `/etc`, `/var`, `/opt`, `/mnt`, `/tmp` (and `Users`)
   - Traversal: `..` / `../` / `foo/../`
   - `hf://...`
   - `org/model:tag` (and `name/rev:sha` shapes)

4. **Path-aware `looks_like_leak(field_path, value, allowed_metadata_paths)`**:
   - `allowed_metadata_paths` is a caller-supplied `frozenset[str]` of the *exact* repo-relative paths that are blessed for this artifact (e.g. the `SOURCE_PATHS` tuple used when building a given benchmark).
   - A value that matches `REPO_RELATIVE_PATH_PATTERN` is **only** accepted (returns False) when **both**:
     - the value is present in the supplied `allowed_metadata_paths`, **and**
     - `field_path` names a permitted metadata source field (`$.axis_definition_source` or `$.source_paths[...]`).
   - Everything else (repo path in wrong field, unknown path even in right field, any secret/model/path pattern match) returns True (leak) — **no fail-open**.
   - `looks_like_leak_simple(value)` is the path-unaware variant (repo paths always leak).

5. `CLAIM_GATES`:
   - Exact tuple of the seven gates that any consuming benchmark/manifest/contract artifact **must** emit as the literal boolean `False`.
   - The module itself is pure utility; it never emits claim-bearing artifacts.

Also exposes the compiled `LEAK_PATTERNS`, `REPO_RELATIVE_PATH_PATTERN`, and `MODEL_PROVIDER_TOKEN_PATTERN` (string) for direct use or tests.

## Invariants (enforced by the locked test)

- Deterministic, offline, no network, no model pulls, no wallclock, no random, no non-finite handling here (callers do).
- Model list is the allowlist of shapes; regex is derived from it (prefer schema allowlists).
- `command_center`, `yield`, and similar must not be falsely flagged by the model token matcher.
- `gpt4o_hit`, `mpt7b`, `cohere_internal_model`, `command-r`, bare `openai` etc. **must** be flagged.
- Repo-relative paths only ever survive when both allowlist value + named field conditions hold.
- All other appearances of the shapes are rejected.
- Claim gates are irrelevant to this module; the test never sets any gate True.

## Usage in a contract (example shape, do not copy into other files this round)

```python
from waggledance.core.leak_policy import (
    looks_like_leak,
    looks_like_leak_simple,
    CLAIM_GATES,
)

ALLOWED = frozenset([
    "waggledance/adapters/http/routes/chat.py",
    "docs/architecture/HONEYCOMB_SOLVER_SCALING.md",
])

def validate(artifact: dict) -> list[str]:
    errors = []
    for gate in CLAIM_GATES:
        if artifact.get(gate) is not False:
            errors.append(f"{gate} must be exact false bool")
    # ... other scope checks ...

    for p, v in _walk_scalars(artifact):
        if isinstance(v, str) and looks_like_leak(p, v, ALLOWED):
            errors.append(f"{p} contains a forbidden secret/path-like/model string")
    return errors
```

The `allowed` set is artifact-specific and must be explicitly enumerated (no inheritance, no globs).

## Why a shared module now

Prior slices duplicated the LEAK_PATTERNS + ad-hoc `_looks_like_leak` / `_is_allowed_metadata_path` logic (with slight variations in drive patterns, model regex, field checks). Future benchmark contracts, insight-score manifests, route-depth etc. must import this to guarantee the same detector and the same false-positive guards.

This slice touches **only** the three disjoint paths listed in the producer charter for the task:
- `waggledance/core/leak_policy.py`
- `tests/core/test_leak_policy.py`
- `docs/architecture/LEAK_POLICY.md`

No existing benchmark, contract, manifest, or tool file was modified.

## Anti-drift / lock

The test `tests/core/test_leak_policy.py` is intentionally "locked": it enumerates every required token/secret/path shape and the FP guards. Changing the provider list or loosening the model regex or the allow-only logic will cause the test to fail (or new forms will be untested). Add to the parametrized lists when the allowlist legitimately grows; never weaken.

## Related

- Consuming contracts (future): will import here (not in scope of this producer slice).
- `docs/architecture/SANITIZATION_CONTRACT_V0.md` — separate redaction surface for PII/credentials leaving via the bridge.
- `waggledance/core/bridge_llm/redactor.py` — the runtime redactor (different concerns).
- MAGMA / claim-gate discipline in `docs/architecture/MAGMA_*.md` and bridge consensus docs (all gates remain false for local offline utility artifacts).

All claim gates N/A for the policy module itself.
