# Phase 18B — P0 Baseline Verification

**Date (UTC):** 2026-05-05
**Branch:** `phase18b/gap-miner-feedback`
**Worktree:** `C:/Python/project2-phase18b-gap-miner-feedback`
**Base:** `origin/main @ 2d32b9b2267d271508d689f94f4631e2965f3be2` (Phase 18A post-release docs PR #80 merge)

Phase 18B ports/implements a fail-closed runtime gap-mining feedback loop on top of v3.10.0-benchmark-schema-alpha. It is a capability-extension sprint: runtime signals → mined gap candidates → six-family allowlisted solver specs OR explicit rejection / quarantined builder handoff. Phase 18A bundle validation is preserved as a carry-forward gate.

## 1. Tag invariants (must remain unchanged through Phase 18B)

| Tag | Target SHA | isPrerelease | Latest? |
| --- | --- | --- | --- |
| `v3.8.0` | `824176ebf2a6b8debed41982090a125cbe2ddad1` | false | **Yes — GitHub Latest** |
| `v3.9.0-producer-fabric-alpha` | `c726995c816ee4c09e031c2190c3de6592e82879` | true | No |
| `v3.9.1-local-efficiency-benchmark-alpha` | `f4d0a4a4152ca74e98a8d7f7161c233075bf4111` | true | No |
| `v3.9.2-local-ollama-baseline-alpha` | `db5d7db1ecb9ae6f17293f0bf7261f4c9d40e91c` | true | No |
| `v3.9.3-local-model-sweep-alpha` | `d0704efe46be18d480ed425ff83b087cd36ef9bd` | true | No |
| `v3.10.0-benchmark-schema-alpha` | `4554b24a47045ab10c1c0fbcb010f695d47d867c` | true | No |

Phase 18B must NOT modify any of these six tags.

## 2. Phase 18A carry-forward — defect found and fixed in P0

`tools/validate_phase18a_benchmark_bundle.py --bundle-dir docs/runs/phase18a_benchmark_externalization_2026_05_05/export_bundle` failed on this fresh worktree with checksum mismatches on three schema files:

```
- checksums.sha256: mismatch for schemas/local_model_sweep.schema.json: expected 024cfee64c3c... got c8771bf7922d...
- checksums.sha256: mismatch for schemas/local_ollama_baseline.schema.json: expected 009a840e1868... got 50c933431264...
- checksums.sha256: mismatch for schemas/release_lineage.schema.json: expected e1ed4648ae68... got 7710476636e5...
```

Root cause: Windows `core.autocrlf=true` (the platform default for git-bash) translates the index's LF endings to CRLF on checkout. The Phase 18A exporter used `Path.write_text(..., encoding="utf-8")`, which on Windows performs the same LF→CRLF newline translation in text mode. Both behaviors masked each other on the original Phase 18A run (write CRLF, hash CRLF, validator reads CRLF — all consistent in one session) but broke on a fresh checkout where the hashes had been computed against the original local CRLF state, and the working tree byte content differed.

**Fix applied in P0:**

1. `.gitattributes` added at repo root with explicit `text eol=lf` for `schemas/benchmarks/v1/*.schema.json` and the entire `docs/runs/phase18a_.../export_bundle/` subtree. This ensures the index, working tree, and checksums all agree on LF.
2. `tools/run_phase18a_benchmark_externalization.py` rewritten to use a new `_write_text_lf` helper that writes via `Path.write_bytes(text.encode("utf-8"))`, bypassing platform newline translation. A companion `_copy_text_lf` is used for copying source schemas into the bundle, normalizing CRLF→LF if the source was checked out CRLF.
3. The bundle was re-exported. `Validator: PASS`.
4. Phase 18A test suite re-run: `tests/benchmarks/test_phase18a_benchmark_externalization.py`: 15/15 PASS in 0.50 s. Determinism test still holds (two runs to two paths produce byte-equal bundles).

This fix is in-scope for Phase 18B because it is the Phase 18B carry-forward gate's first line of defense and was discovered by it.

## 3. What Phase 18B CHANGES (in scope)

1. **`waggledance/core/autonomy_growth/gap_mining.py`** (and any helper modules) — port/implement runtime gap mining: structured candidates, family allowlist check, evidence threshold, risk rejection, duplicate suppression, builder-handoff quarantine.
2. **`tools/run_phase18b_gap_miner_feedback_proof.py`** — proof harness with 30+ deterministic synthetic runtime signals across 6 families + edge cases.
3. **`tests/autonomy_growth/test_phase18b_gap_miner_feedback.py`** — minimum 17 tests covering all candidate verdicts + invariants + Phase 18A carry-forward.
4. **`docs/benchmarks/GAP_MINER_FEEDBACK_LOOP_2026.md`** — new public-facing doc.
5. **`docs/benchmarks/COMPETITIVE_EVIDENCE_MATRIX_2026.md`** — axis M upgrade if release gates pass.
6. Candidate-mode entries in CURRENT_STATUS.md, CHANGELOG.md, RELEASE_READINESS.md, README.md.
7. **`.dockerignore`** carve-outs for the new tool, modules, and tests.
8. **`.gitattributes`** + Phase 18A exporter EOL fix (fix-up commit).

## 4. What Phase 18B does NOT change (out of scope)

* Does NOT modify any of the 6 prior tags. v3.8.0 remains GitHub Latest.
* Does NOT widen the six-family low-risk allowlist (`scalar_unit_conversion`, `lookup_table`, `threshold_rule`, `interval_bucket_classifier`, `linear_arithmetic`, `bounded_interpolation`).
* Does NOT execute Stage-2 atomic flip; does NOT collect HUMAN_APPROVAL.
* Does NOT touch `phase8.5/*` branches (read-only inventory only).
* Does NOT pull or download any Ollama model; does NOT call any cloud LLM API.
* Does NOT introduce a new high-risk autonomy mechanism (no Reflective Conductor, no Episodic Replay Engine, no new curiosity scheduler, no autonomous high-risk promotion, no actuator autonomy, no provider HTTP adapter, no `/api/autonomy/query`).
* Does NOT make any cross-vendor ranking claim or raw-intelligence superiority claim.
* Does NOT issue a stable tag — at most a PRERELEASE.
* Does NOT require live Claude Code / builder execution for release gates. Builder handoff is represented only as a quarantined request object with `no_auto_promotion = true`.
* Does NOT edit `CURRENT_STATE.md` manually.

## 5. Push / merge discipline

* All landings via PR — no direct push to `main`.
* Autonomous squash-merge only with `gh pr merge --match-head-commit`.
* Fresh-clone retest before merge per master prompt P10.

## 6. Result of P0

Baseline verification PASS (after the in-P0 Phase 18A bundle EOL fix). Proceeding to P1 (`gap_miner_feedback_design.md`) before any gap-miner code is written.
