# Phase 2B operator handoff — cross-vendor multi-LLM iteration cycle

> **Superseded by Phase 2B-Revision (ARCH-010 / ARCH-011).** The
> default external-review lane no longer includes `claude_web` —
> the Claude perspective comes from the local Phase 2A-2 internal
> self-review runner. The operator-facing manual for the revised
> flow lives at
> `docs/runs/orchestrator_phase2br_cockpit_codex_regression_2026_05_07/cowork_handoff.md`
> (Phase 2B-Revision P8 also adds an HTML Operator Cockpit that
> drives the manual web-UI step). This document is preserved for
> historical reference of what Phase 2B originally shipped.

**This is the operator-facing manual for the Phase 2B review cycle.**
The orchestrator drives N=1..3 local Claude Code iterations per epoch
between manual external reviews. After each epoch completes (or an
early-trigger condition fires), the operator copies a queue bundle
into three external chat UIs (Claude Web, Gemini, Grok), saves the
responses, then forwards a synthesis paste-block to GPT and saves
that response. Every step is SHA-bound; nothing automates the chat
UI side of the loop.

## 0. Prerequisites

* You are on Windows / PowerShell 5.1.
* `orchestrator.config.json` (or `.example.json`) lives at the repo
  root and contains the Phase 2B `external_review` /
  `iteration_cycle` / `models` blocks.
* The repo's hardening gate driver is green
  (`orchestrator\Run-WaggleHardeningGates.ps1` reports 24/24 PASS).
* You have authenticated paid sessions for: Claude Web (Max plan),
  Gemini Pro Advanced, Grok Expert mode, GPT Pro 5.5 Extended
  Thinking.

## 1. Decision: do we trigger an external review epoch?

After the orchestrator finishes a local iteration, it asks the
trigger library whether the next step is `continue`, `trigger`,
`halt`, or `pause`. You can also ask manually:

```
.\orchestrator\Test-WaggleEpochCycleTrigger.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -IterationIds @('2026-05-07_07-00-00','2026-05-07_07-30-00','...')
```

* `decision = continue` — let the orchestrator launch another local
  iteration. No external review yet.
* `decision = trigger` — the cumulative N-iteration window closed,
  a regression / hardening-gate failure / internal critical
  finding fired, the no-work consecutive threshold met, or the
  prior synthesis flagged `requires_attention`. Proceed to step 2.
* `decision = halt` — the prior synthesis emitted `WAGGLE_HALT`.
  Do not run another iteration; the cycle is complete.
* `decision = pause` — `state\pause_external_review.flag` is set.
  Remove the flag when you want the cycle to resume.

## 2. Build the epoch evidence bundle

```
.\orchestrator\Build-WaggleEpochEvidence.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -IterationIds @('iter-A','iter-B','iter-C')
```

Outputs:

* `iterations\<last_id>\external_reviews\epoch_<epoch_id>\evidence\epoch_evidence.json` — the manifest, including `evidence_sha256`. The reviewer + synthesizer responses MUST echo this SHA back; if they do not, the importer rejects them.
* sibling files: `cumulative_diff.patch`, `cumulative_raportti.md`, `cumulative_supplement.md`, `regression_state.json`, plus `iter*_logs_combined.md` and `iter*_internal_review.md` per iteration.

## 3. Export the per-provider review queue

```
.\orchestrator\Export-WaggleExternalReviewQueue.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -EvidenceJsonPath .\iterations\<last_id>\external_reviews\epoch_<epoch_id>\evidence\epoch_evidence.json
```

Produces three bundles under `external_reviews\queue\<epoch_id>\`:

* `claude_web_architect\` — Claude Web reviewer bundle (architect)
* `gemini_security\` — Gemini reviewer bundle (security)
* `grok_reliability\` — Grok reviewer bundle (reliability)

Each bundle contains: `prompt.md`, `metadata.json`, `attachments\`,
`expected_response_path.txt`. The top-level dir also has
`queue_manifest.json` and `cowork_handoff.md` (a short brief).

## 4. Run the three reviews (manually, in chat UIs)

For each bundle:

1. Open the corresponding chat UI session. Start a fresh thread.
2. Paste the contents of `prompt.md`.
3. Use the chat UI's file-attach to attach every file in
   `attachments\`. Order does not matter; the prompt names them.
4. Wait for the reviewer to emit:
   * a fenced ```` ```reviewer-self-id ```` block,
   * a fenced ```` ```external-review-json ```` block, and
   * the literal `EXTERNAL-REVIEW-COMPLETE` marker on its own line.
5. Save the entire reviewer response (everything from start of
   message to the marker) to the path written in
   `expected_response_path.txt`.

**ToS reminder.** Each provider's terms forbid programmatic /
automated browser use. This step is manual on purpose.

## 5. Import each saved response

```
.\orchestrator\Import-WaggleExternalReviewResponse.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -EpochId <epoch_id> `
    -Provider claude_web `
    -Role architect `
    -ResponseFile <expected_response_path> `
    -IterationId <last_id>
```

Repeat for `gemini`/`security` and `grok`/`reliability`.

The importer:

* applies the Phase 2A-1 redactor to the entire response (defense
  in depth — reviewer text is UNTRUSTED, may contain credentials);
* requires the reviewer-self-id block + a single
  external-review-json block + the EXTERNAL-REVIEW-COMPLETE marker;
* validates the JSON against `schemas/external_review.schema.json`;
* recomputes `evidence_sha256` from disk and verifies the response's
  `source_evidence_sha256` matches.

Failures land as `*.invalid.md` + `*.invalid.metadata.json` next to
the valid imports. Investigate, fix the reviewer response (or
rerun), and re-import. Re-importing the same file is allowed; each
attempt gets a fresh `import_id`. Synthesis uses the LATEST valid
import per `(provider, role)` tuple.

## 6. Build the synthesis paste-block

```
.\orchestrator\New-WaggleSynthesisPasteBlock.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -EpochId <epoch_id> `
    -IterationId <last_id>
```

Writes `iterations\<last_id>\external_reviews\synthesis\<epoch_id>\paste_block.md`
plus `attachments\` with the canonical evidence files.

## 7. Run the synthesis (manually, in GPT)

1. Open GPT Pro 5.5 Extended Thinking. Start a fresh thread.
2. Paste the contents of `paste_block.md`.
3. Attach every file in `attachments\` via the chat UI's file-attach.
4. Wait for GPT to emit:
   * a fenced ```` ```synthesizer-self-id ```` block,
   * a fenced ```` ```synthesis-json ```` block,
   * (when `decision = continue`) a fenced ```` ```next-claude-code-prompt ```` block,
   * the literal `SYNTHESIS-COMPLETE` marker on its own line.
5. Save the entire response to
   `iterations\<last_id>\external_reviews\synthesis\<epoch_id>\gpt_response.md`.

The first non-blank line of the next-prompt block MUST be exactly
the MANDATORY directive that pins Claude Opus 4.7. If GPT drifts,
re-prompt — do not edit the response by hand.

## 8. Import the synthesis result

```
.\orchestrator\Import-WaggleSynthesisResult.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -EpochId <epoch_id> `
    -IterationId <last_id> `
    -ResponseFile <synthesis_dir>\gpt_response.md
```

The importer re-runs the same shape checks the reviewer importer
runs, plus the synthesis-only checks (single
`next-claude-code-prompt` block on `continue`, none on `halt`,
MANDATORY first line match, schema-required `sources[]` /
`merged_from_proposals[]`).

* On `decision = continue`: writes `next_claude_code_prompt.md`.
* On `decision = halt`: writes `HALT.md` with the halt rationale.

## 9. Launch the next iteration (when decision = continue)

```
.\orchestrator\New-WaggleIterationFromSynthesis.ps1 `
    -ConfigPath .\orchestrator.config.json `
    -EpochId <epoch_id> `
    -IterationId <last_id> `
    -SynthesisImportId <synth_id>
```

Optional `-DryRun` previews the new iteration ID + verifies the
SHA without writing anything. The launcher refuses if the evidence
has been mutated since synthesis (recomputed SHA != synthesis SHA),
the new iteration ID collides with an existing folder, or the
synthesis decision is anything other than `continue`.

The next iteration's `iteration_prompt.md` is the verbatim
`next_claude_code_prompt.md`. The orchestrator picks it up.

## 10. When something goes wrong

* **Reviewer drops the JSON / marker.** Re-prompt in the same chat
  thread; the reviewer can re-emit. If it cannot, switch the entire
  reviewer's verdict to `insufficient_evidence` (the importer then
  records it as a valid import with a non-actionable verdict).
* **Reviewer echoes a wrong `source_evidence_sha256`.** Either the
  reviewer hallucinated it, or you pasted into the wrong epoch's
  prompt. Recheck the prompt's `metadata.json` and re-prompt.
* **GPT synthesizer omits the MANDATORY first line.** The importer
  refuses; re-prompt GPT until the line is present. Do NOT hand-edit.
* **Operator wants to pause.** Touch
  `state\pause_external_review.flag` (any contents). The trigger
  library returns `pause` until the flag is removed.

## 11. Files this manual references (quick index)

| Area | Path |
|------|------|
| Config example | `orchestrator.config.example.json` (root) |
| Schemas | `schemas\external_review.schema.json`, `schemas\review_synthesis.schema.json`, `schemas\epoch_evidence.schema.json` |
| Reviewer prompts | `prompts\external_review\{architect,security,reliability}.md` |
| Synthesis prompt | `prompts\external_review\synthesis_gpt.md` |
| Provider hints | `prompts\external_review\providers\{claude_web,gemini,grok,gpt}.md` |
| Build evidence | `orchestrator\Build-WaggleEpochEvidence.ps1` |
| Export queue | `orchestrator\Export-WaggleExternalReviewQueue.ps1` |
| Import reviewer | `orchestrator\Import-WaggleExternalReviewResponse.ps1` |
| Build paste-block | `orchestrator\New-WaggleSynthesisPasteBlock.ps1` |
| Import synthesis | `orchestrator\Import-WaggleSynthesisResult.ps1` |
| Trigger decision | `orchestrator\Test-WaggleEpochCycleTrigger.ps1` |
| Launch from synthesis | `orchestrator\New-WaggleIterationFromSynthesis.ps1` |
| Hardening gates | `orchestrator\Run-WaggleHardeningGates.ps1` (24 gates) |
