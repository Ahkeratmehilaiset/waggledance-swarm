# Phase 2B — cross-vendor multi-LLM iteration cycle (design)

**Status:** Implemented 2026-05-06.
**Predecessors:** Phase 2A-1..2A-5 (orchestrator + review hardening).
**Successors:** Phase 2C (parallel-fanout from synthesis) — not in scope here.

This doc captures the architecture decisions behind Phase 2B. The
operator manual lives at
`docs/runs/orchestrator_phase2b_cross_vendor_2026_05_06/cowork_handoff.md`.

## 1. Problem

Phase 2A produced a hardened internal-review surface. Internal
review is fast but homogeneous — three roles run inside the same
Claude Code agent, on the same package. We want a periodic
cross-vendor sanity check: Claude Web architect, Gemini security,
and Grok reliability look at the same evidence, and a GPT
synthesizer reconciles the verdicts and emits the next iteration's
prompt.

The hard constraints:

1. **No browser automation.** Each provider's terms of service
   forbid programmatic chat-UI access. The cycle is operator-driven.
2. **No tag, no release.** Phase 2B is a working-state landing.
3. **Synthetic credentials must be runtime-concatenated.** No
   secret-shaped strings in source.
4. **Deterministic SHA contract.** A reviewer's response is bound
   to the exact evidence bytes the operator showed it; tampering
   in either direction (evidence mutated, reviewer hallucinated a
   SHA) is detected.
5. **MANDATORY model-pinning.** The synthesizer's next-prompt
   first line is a fixed directive that pins Claude Opus 4.7.

## 2. Cycle shape

```
+----------------+      +-----------------+      +------------------+
| local iter 1   |  ->  | local iter 2    |  ->  | local iter N=3   |
+----------------+      +-----------------+      +------------------+
         |                                                  |
         |         (cumulative N=3 OR early-trigger)        |
         v                                                  v
+----------------------------------------+
| Build-WaggleEpochEvidence              |
|  - sha256 over canonical files         |
|  - writes epoch_evidence.json          |
+----------------------------------------+
         |
         v
+----------------------------------------+
| Export-WaggleExternalReviewQueue       |
|  - per-provider bundle                 |
|  - prompt.md + attachments + meta      |
+----------------------------------------+
         |
         v
   [3 reviewers — manual chat UI]
         |
         v
+----------------------------------------+
| Import-WaggleExternalReviewResponse    |
|  - redact, schema, SHA-bind            |
|  - latest valid per (provider, role)   |
+----------------------------------------+
         |
         v
+----------------------------------------+
| New-WaggleSynthesisPasteBlock          |
|  - inline 3 reviewer mds + template    |
|  - attachments for GPT                 |
+----------------------------------------+
         |
         v
   [GPT synthesizer — manual chat UI]
         |
         v
+----------------------------------------+
| Import-WaggleSynthesisResult           |
|  - schema, SHA-bind                    |
|  - MANDATORY first-line check          |
|  - HALT.md or next_prompt.md           |
+----------------------------------------+
         |
         v
+----------------------------------------+
| New-WaggleIterationFromSynthesis       |
|  - SHA recomputed-from-disk            |
|  - new iteration folder + state.json   |
+----------------------------------------+
         |
         v
   (back to local iter 1 of next epoch)
```

## 3. Determinism: `evidence_sha256`

The bundler hashes a canonical, lexically-sorted list of
relative paths (cumulative_diff, cumulative_raportti,
cumulative_supplement, regression_state, per-iteration logs and
internal reviews, optional previous_epoch_synthesis). For each path
it emits `<rel>\n<sha256(file_bytes)>\n` and SHA-256s the
concatenation. The result is what the reviewer is asked to echo
back in `external_review.source_evidence_sha256`, what the
synthesizer is asked to echo back in
`review_synthesis.source_evidence_sha256`, and what the launcher
recomputes from disk before creating the next iteration.

The hash is **content-only**. It does not include `evidence_sha256`
itself, `generated_at_utc`, or any computed-during-build fields.
Re-running the bundler over the same evidence dir reproduces the
same SHA byte-for-byte.

## 4. Trigger model

`Get-WaggleEpochCycleDecision` returns one of:

* `pause` — `state\pause_external_review.flag` is present (highest
  priority; operator wants to stop the loop).
* `halt` — a `HALT.md` (or operator-supplied marker path) is present
  in the latest synthesis dir.
* `trigger` — one or more early-trigger conditions are true:
  * any iteration's hardening_gates_failure_present == true
    (`early_trigger_on_hardening_gate_failure`)
  * any iteration has an internal `critical` finding
    (`early_trigger_on_internal_critical_finding`)
  * any iteration has an internal verdict in {fail, needs_changes,
    insufficient_evidence} (`early_trigger_on_regression`)
  * the trailing run of `no_work_classification = true` iterations
    >= `early_trigger_on_no_work_consecutive`
  * the prior synthesis decision was `requires_attention`
* `trigger` (cumulative) — `count >= local_iterations_per_external_review`.
* `continue` — none of the above.

Order matters: pause beats halt beats early-triggers beats
cumulative.

## 5. SHA-binding failure modes

| Detected by | What is checked |
|-------------|-----------------|
| Reviewer importer | `external_review.source_evidence_sha256` == disk SHA |
| Synthesis importer | `review_synthesis.source_evidence_sha256` == disk SHA |
| Iteration launcher | recomputed disk SHA == synthesis JSON's SHA |

Any mismatch refuses with `source_evidence_sha256_mismatch`.
Re-running the bundler is a deliberate user action — it produces a
new epoch_evidence.json with a new SHA, and any pre-existing
reviewer responses against the previous SHA become `.invalid`.

## 6. Untrusted-data discipline

All reviewer text is treated as UNTRUSTED:

* The reviewer importer applies `Invoke-WaggleRedaction` to the
  whole response before storing it (defense in depth — the bundler
  already redacted the upstream evidence).
* Stored copies are markdown only; the importer does not execute
  any reviewer-supplied script, regex, or fenced command block.
* Prompt-injection text (e.g. `IGNORE ALL PRIOR INSTRUCTIONS`) is
  preserved verbatim in the stored .md but never interpreted.

The synthesis importer does the same on the GPT response.

## 7. Provider profile model

Provider profiles live in `external_review.providers.<name>` in
the config. Defaults are baked into
`orchestrator/lib/external_review/ProviderProfiles.ps1`:

* `claude_web` — Claude Opus 4.7 (Max plan)
* `gemini` — Gemini Pro Advanced
* `grok` — Grok Expert mode
* `gpt_synthesis` — GPT Pro 5.5 Extended Thinking

Each profile carries `enabled` (the queue exporter emits an
`ok=false` bundle for disabled providers, so the operator can see
the gap), `timeout_sec` (operator-facing reminder), and
`expected_model_in_ui` (operator must verify the chat UI's model
selector matches before pasting).

Roles are fixed by convention:
`claude_web/architect`, `gemini/security`, `grok/reliability`,
`gpt/synthesis`. The schemas allow other roles for future fanout
phases.

## 8. Attachment cap and consolidation

Each provider has a `max_attachments_per_provider` cap (default
20). The bundler picks the canonical set of evidence files in
priority order (manifest → diff → raportti → supplement → previous
synthesis → regression → per-iteration logs → per-iteration
reviews). If the picks exceed the cap, lower-priority items are
consolidated into combined files (`run_logs_combined.md`,
`internal_reviews_combined.md`). If still over cap, the bundler
either throws (when `fail_on_attachment_overflow=true`) or returns
`ok=false` so the exporter can decide.

## 9. Files added in Phase 2B

```
schemas/
    external_review.schema.json
    review_synthesis.schema.json
    epoch_evidence.schema.json

orchestrator/lib/external_review/
    EvidenceBundler.ps1
    EpochCycleTrigger.ps1
    ExternalReviewSchema.ps1
    ProviderProfiles.ps1
    SynthesisSchema.ps1

orchestrator/
    Build-WaggleEpochEvidence.ps1            + Test-EpochEvidence.ps1
    Export-WaggleExternalReviewQueue.ps1     + Test-ExternalReviewQueue.ps1
    Import-WaggleExternalReviewResponse.ps1  + Test-ExternalReviewImport.ps1
    New-WaggleSynthesisPasteBlock.ps1        + Test-SynthesisPasteBlock.ps1
    Import-WaggleSynthesisResult.ps1         + Test-SynthesisResultImport.ps1
    Test-WaggleEpochCycleTrigger.ps1         + Test-EpochCycleTrigger.ps1
    New-WaggleIterationFromSynthesis.ps1     + Test-IterationFromSynthesis.ps1

prompts/external_review/
    architect.md, security.md, reliability.md, synthesis_gpt.md
    providers/{claude_web,gemini,grok,gpt}.md
```

The hardening gate driver
(`orchestrator/Run-WaggleHardeningGates.ps1`) was extended from 17
to 24 gates; the 7 new gates are the test drivers above.

## 10. What Phase 2B explicitly does NOT do

* No browser automation. The chat UI side stays manual.
* No tag/release. The branch lands via PR per CLAUDE.md rule 6.
* No autonomous merge. The cutover into `main` is operator-gated.
* No atomic-flip work. That belongs to a separate cutover session
  per CLAUDE.md rule 10.
