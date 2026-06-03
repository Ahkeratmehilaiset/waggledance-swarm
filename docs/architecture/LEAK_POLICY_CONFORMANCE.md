# LEAK_POLICY_CONFORMANCE (versioned offline corpus + locked test)

Status: producer slice (grok-scout-1). Disjoint paths only. NEW FILES ONLY. Does not modify `waggledance/core/leak_policy.py` or `tests/core/test_leak_policy.py`.

## Purpose

This conformance asset locks the shared `looks_like_leak_simple` (and by extension the full policy surface) against regression. Model/provider leak coverage has regressed/recurred across slices #827/#829/#830/#832/#833. The corpus enumerates the exact must-reject and must-accept sets so that any future change that weakens the detector (missing a glued form, loosening a pattern, altering false-positive guards) will cause `tests/core/test_leak_policy_conformance.py` to fail deterministically.

All operations are offline, deterministic, no network, no model pulls, no wallclock or random values in the committed artifacts (beyond the static "provenance" label).

## Files (exact allowed set for this slice)

- `tests/core/leak_policy_conformance_corpus.json` — the versioned fixture (two arrays + claim gate declarations)
- `tests/core/test_leak_policy_conformance.py` — the loader/assert test (imports only the public `looks_like_leak_simple` + `CLAIM_GATES`)
- `docs/architecture/LEAK_POLICY_CONFORMANCE.md` — this document

No other files may be created or edited in this round (no manifests, no aggregation scripts, no capability files, no changes to existing tests/docs).

## Corpus shape

```json
{
  "corpus_version": "wd.leak_policy.conformance_corpus.v1",
  "title": "...",
  "description": "...",
  "provenance": "hand-authored stable shapes per LEAK_POLICY spec; no wallclock...",
  "must_reject": [ /* every provider bare + glued (gpt4o_hit, mpt7b_case, cohere_internal_model, claude_secret_x, command-r, falcon, yi, mpt, ...), Bearer/sk-/AKIA forms, drive/unix/../hf://, org/model:tag shapes */ ],
  "must_accept": [ "language_detection", "hot_cache", "hybrid_retrieval_8_cell", "deterministic_solver", "feature/normal-branch", "main", "yield_route_case", "v3.latency_fixtures.local.v1", "command_center" ],
  "claim_gates": {
    "claim_gate_satisfied": false,
    "claim_safe": false,
    "literal_future_claim_safe": false,
    "controls_present": false,
    "runtime_authority_granted": false,
    "external_writes_applied": false,
    "required_runtime_evidence_present": false
  }
}
```

The test loads the corpus at collection/runtime and parametrizes assertions:

- for every item in must_reject: `looks_like_leak_simple(v) is True`
- for every item in must_accept: `looks_like_leak_simple(v) is False`

It also asserts that the corpus itself carries every gate as the literal boolean `false`.

## Claim gates (strict)

This is a pure test asset. All claim gates are N/A and are emitted as literal `false` in the JSON. The test hard-asserts their presence and falsity. There are no carve-outs, no "future" relaxation, and no consumer may read this corpus and set any gate to true.

See `waggledance/core/leak_policy.py:CLAIM_GATES` and the sibling `docs/architecture/LEAK_POLICY.md`.

## Invariants locked by this corpus + test

- Every bare provider token from the internal `_PROVIDER_TOKENS` allowlist must trigger (anthropic, claude, ..., yi, command-r, etc.).
- Glued / suffixed / separated forms required by prior regressions must trigger: gpt4o_hit, mpt7b_case, cohere_internal_model, claude_secret_x, hf/..., org/model:tag, etc.
- Secret shapes (Bearer, sk-16+, AKIA) must trigger.
- Path shapes (drive letters, unix roots/home/tmp, .. traversal, hf://, org:tag) must trigger.
- Legit strings listed in must_accept (including command_center, yield_route_case, main, v3.* fixtures, branch names with /, hot_cache etc.) must NOT trigger (the false-positive guards in MODEL_PROVIDER_TOKEN_PATTERN and overall LEAK_PATTERNS must hold).
- `looks_like_leak_simple` (the path-unaware scalar walk helper) is the surface under test here; the path-aware `looks_like_leak` is already locked by the original test.
- Deterministic + offline only. No non-finite numbers, no random, no external I/O beyond loading the sibling JSON.

## Usage

The conformance test is intended to be run as part of the core test suite:

```
python -m pytest tests/core/test_leak_policy_conformance.py -q
```

It can also be used by future contract authors as a machine-readable list of the exact strings that the shared policy must continue to classify correctly. Import the corpus JSON directly if a contract needs the authoritative list of shapes (still call the shared `looks_like_leak_simple` rather than reimplementing patterns).

## Relation to LEAK_POLICY.md and the original locked test

- `docs/architecture/LEAK_POLICY.md` + `tests/core/test_leak_policy.py` remain the single source of truth for the implementation and the full enumerated cases (including the path-aware variant and source-metadata allowlisting rules).
- This conformance corpus + test is an additional regression brake focused on the `looks_like_leak_simple` entry point and the minimal set of must/must-not cases that repeatedly regressed. It is deliberately a disjoint producer slice.

## Anti-drift contract

Changing the provider allowlist, loosening any LEAK_PATTERN, altering the FP guards in `_MODEL_PROVIDER_FALSE_POSITIVE_PATTERN`, or changing `looks_like_leak_simple` logic such that a must_reject becomes safe or a must_accept becomes a leak will break this test. That is the intended outcome.

When the policy legitimately expands (new provider shape added to `_PROVIDER_TOKENS`), the corpus must be updated in the same change that updates the implementation (still only touching the three allowed paths for a future slice of this type). Never weaken coverage.

All claim gates remain false in every artifact produced by or consuming this conformance material.

## Related

- `waggledance/core/leak_policy.py`
- `tests/core/test_leak_policy.py`
- `docs/architecture/LEAK_POLICY.md`
- MAGMA / bridge consensus / RCO docs (this slice is build-consensus + RCO review gated; producer does not merge)
