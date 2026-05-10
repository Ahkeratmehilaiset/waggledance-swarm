# R21 operator decisions (delegated)

- timestamp_utc: 2026-05-10T03:49:00Z
- author: codex, applying delegated operator instruction
- task_id: r21-explosive-growth-axis-b-activation-2026-05-10
- basis:
  - operator said "1" / continue
  - operator previously stated the likely fastest release-unlock path is
    beta: deterministic golden-output evaluator
  - R21 synthesis surfaced four decisions
- status: decisions-set; operator may supersede explicitly

## Decision 1: Axis B path

Choose Path B / beta: deterministic golden-output evaluator.

`tests/oracle/*.yaml` is the source corpus for R21.1. It is not a
`case_trajectory_input -> ground_truth_grade` labelled corpus. It is a
deterministic routing/solver oracle and therefore satisfies R20.3 Decision B
criterion 2.

## Decision 2: Quality aggregation

Use macro-average across oracle files.

Reason: each solver/domain oracle should have equal weight. This prevents
larger files or easy-positive utterance clusters from hiding weaker cells.

R21.1 should still report supporting micro-average numbers as secondary
diagnostics when cheap, but the release-gate `delta_quality` uses macro-average.

## Decision 3: Positive and negative weighting

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

## Decision 4: No-Ollama availability

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

## Decision 5: R21.5 ownership and release rule

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
