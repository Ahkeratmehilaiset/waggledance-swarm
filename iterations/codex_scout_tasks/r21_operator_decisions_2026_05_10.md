# R21 operator decisions

- timestamp_utc: 2026-05-10T03:51:00Z
- author: operator (recorded by codex)
- task_id: r21-explosive-growth-axis-b-activation-2026-05-10
- basis:
  - explicit operator directive received 2026-05-10
  - R21 synthesis surfaced four decisions
- status: explicit decisions set

## Decision 1: Axis B path

APPROVED beta: deterministic golden-output evaluator.

`tests/oracle/*.yaml` is the source corpus for R21.1. It is not a
`case_trajectory_input -> ground_truth_grade` labelled corpus. It is a
deterministic routing/solver oracle and therefore satisfies R20.3 Decision B
criterion 2.

No labelled corpus is required for R21.1.

## Decision 2: Release tag boundary

If all R21.5 conditions are met, the prerelease tag is:

`v3.11.0-r20-axis-b-activated-alpha`

Do not promote to `v3.11.0` stable in this session.

If any R21.5 condition is not met, ship a Decision C / deferral document
instead of a tag.

## Decision 3: Docker registry

GHCR is the primary registry:

`ghcr.io/ahkeratmehilaiset/waggledance:*`

Docker Hub is not used in this session. Public visibility is allowed.

## Decision 4: PII policy for cloud prompts

Conservative default:

- redact email-like matches using the operator-provided character class
  `[\w@.+-]+` for email tokens
- redact credit-card-like digit spans with `\b\d{13,19}\b`
- redact phone-like spans with `\+?\d[\d\s-]{8,}`
- redact full file paths

`AcceptPiiToCloud=false` is the hard default. Cloud-bound PII is allowed
only with explicit opt-in. R21.3 must fail closed if redaction is unavailable.

## Decision 5: R21 execution order and time bound

Proceed strictly in this order:

1. R21.1
2. R21.2
3. R21.3
4. R21.4
5. R21.5
6. R21.6

Continue until all six PRs are merged or 8 hours have elapsed, whichever
comes first.

At the 4-hour checkpoint, send a bridge `decision/reported` status update to
the operator.

R21.6 is the closeout PR after R21.5: final report / release-or-deferral
summary / bridge status consolidation. It must not publish a second release
artifact beyond the R21.5 decision.

## Decision 6: Quality aggregation

Use macro-average across oracle files.

Reason: each solver/domain oracle should have equal weight. This prevents
larger files or easy-positive utterance clusters from hiding weaker cells.

R21.1 should still report supporting micro-average numbers as secondary
diagnostics when cheap, but the release-gate `delta_quality` uses macro-average.

## Decision 7: Positive and negative weighting

Use equal positive/negative weighting:

```text
quality_arm = (
    correct_positive_routings / total_positive_utterances
    + correct_negative_rejections / total_negative_utterances
) / 2
```

Reason: routing to the right cell and rejecting the wrong cell are both part
of the quality contract. Equal weighting prevents a treatment from improving
positive routing while quietly degrading negative rejection.

## Decision 8: No-Ollama availability

R21.1 should still ship if `OllamaProvider.is_available()` is false.

If no local LLM is available, the treatment arm may collapse to heuristic and
produce `delta_quality = 0`. That is an acceptable informational result for
R21.1, provided the evidence explicitly records:

- local LLM availability status
- provider/fallback distribution
- treatment disabled after measurement
- `delta_quality = 0` caused by unavailable treatment, not by a measured LLM
  underperforming

If Ollama is available, R21.1 should measure the real treatment path. Either
way, the result must be recorded in `EVOLUTION_INDEX.md` and must not be
claimed as a >=20% quality improvement unless the measured number says so.

## Decision 9: R21.5 ownership and release rule

Codex owns R21.5 release-decision work, matching the R21 synthesis.

R21.5 remains blocked until all gates are explicitly checked:

1. R21.1 has a real `delta_quality` number.
2. Part 1 is finalized with:
   - `r21_response_codex_2026_05_10.md`
   - `r21_response_claude_2026_05_10.md`
   - `r21_synthesis_2026_05_10.md`
   - this file
3. R21.4 gate re-verification is green.
4. R20 Decision B's five activation conditions are explicitly checked off.
5. The Profile S env fix is resolved.

Current status of gate 5: satisfied by PR #182 merge.

If all five gates pass, Codex may author the R21.5 release-decision PR. Any
agent can still block it with a concrete finding. If a semver tag or Docker
publish is proposed, the PR body must include the complete gate checklist and
the exact commands/evidence used.

## Immediate next action

After this file lands, R21.1 may begin:

- owner: Claude
- reviewer: Codex
- task_id: `r21-claude-oracle-ab-harness-2026-05-10`
- call site: `HexTopologyRegistry.select_origin_cell`
- oracle source: `tests/oracle/*.yaml`
- primary metric: macro-average `delta_quality`
- fallback policy: no-Ollama result is allowed and must be logged honestly

No R21.5 release approval is granted by this file.
