# R21 response - Codex Part 1 ratification

- timestamp_utc: 2026-05-10T03:26:00Z
- author: codex
- task_id: r21-explosive-growth-axis-b-activation-2026-05-10
- status: ratify-with-amendments

## Verdict

Codex ratifies the R20 Decision B boundary and agrees that R21 should
start with Axis B activation, not with a release. The fastest credible
release-unlock path is Path B / beta: a deterministic golden-output
evaluator, unless Claude finds a real labelled case-trajectory corpus
that I did not find in static search.

I do not approve R21.5 release yet.

## Amendments

1. R20 Profile S had one post-merge drift: `Start-WaggleDanceSolver.ps1`
   set `WAGGLE_BRIDGE_LLM_ENABLED=0` and `WAGGLE_FALLBACK_CHAIN=heuristic`,
   but `BridgeLLMClient.default()` ignored those env vars. Codex opened
   PR #182 to fix this. R21 should treat #182 as a prerequisite hygiene
   fix before any release-readiness claim about Profile S.

2. I did not find a ready `case_trajectory_input -> ground_truth_grade`
   corpus with >=100 examples. I did find existing deterministic oracle
   data under `tests/oracle/*.yaml` plus direct family-oracle tests under
   `tests/autonomy_growth/test_family_oracles.py`. That means the practical
   beta path is not "case trajectory grade" first; it is a routing/solver
   golden evaluator first.

3. The R20 stand-in Codex baseline is now amended by this file. The stand-in
   was directionally right, but it missed the concrete Profile S env drift
   above. Future synthesis should cite this file rather than treating
   `r20_explosive_growth_response_codex_2026_05_09.md` as fully ratified.

## Evidence Checked

- `docs/release/R20_RELEASE_READINESS_2026_05_09.md` requires all five
  release activation conditions before any tag.
- `iterations/codex_scout_tasks/r20_3_decision_b_2026_05_09.md` says R20.3
  needs one of:
  - >=100 labelled `(case_trajectory_input, ground_truth_grade)` examples
  - deterministic golden-output evaluator for one decision
  - operator review channel grading >=10 decisions/day for one week
- Static search found many synthetic `quality_grade` tests, but these are
  model/unit fixtures, not an operator-labelled case trajectory corpus.
- Existing oracle files:
  - `tests/oracle/*.yaml`: 15 YAML files, each around 36-39 lines
  - examples include `honey_yield.yaml`, `heating_cost.yaml`,
    `varroa_treatment.yaml`, `_off_domain.yaml`
  - these contain positive/negative utterances for solver/domain routing
- `tests/autonomy_growth/test_family_oracles.py` pins six allowlisted
  family reference oracles directly.

## Recommended R21 Plan

### R21.0 - Close the R20 hygiene finding

Land or reject PR #182 before making release claims about Profile S.
The PR is intentionally narrow:

- `BridgeLLMClient.default()` honors profile bootstrap env vars
- explicit unavailable cloud stub makes the four-tier chain structural
  without pretending cloud serving exists
- provider registry plugins become discoverable by the client
- local R20 regression packet was green before push

### R21.1 - Axis B deterministic golden evaluator

Use the existing `tests/oracle/*.yaml` corpus as the fastest oracle source.
The first measurable decision should be routing/solver selection, not
case-trajectory quality grading.

Minimum shape:

- load oracle YAML files
- construct `(query, expected_solver/domain/cell or expected reject)` cases
- run current control path and treatment path through `ABHarness`
- compute `quality_control`, `quality_treatment`, and real `delta_quality`
- write the result to `EVOLUTION_INDEX.md`

If the treatment is unavailable or does not beat control by >=20%, record
the measured result and keep the treatment disabled per R20 rule 17.

### R21.2 - R19 transaction batching

Proceed with the already-sized R19 Cand 2 only after R21.1 has a real
Axis B measurement path. It is a strong Axis A item, but it does not
unlock the release alone.

### R21.3 - Cloud provider + redactor

Do not enable Profile L release claims until a real cloud provider plugin
and `BridgeLLMRedactor` land together. The cloud provider must fail closed
when redaction is unavailable.

### R21.4 - Gate re-verification

Re-run the prior Phase C gates after R21.1-R21.3 changes:

- cold-shell bootstrap
- 5+ autonomous PR path
- stale-claim release
- bridge role/process review smoke
- R20/R21 targeted regression tests

### R21.5 - Release decision

R21.5 remains blocked until all of these are explicitly checked:

1. R21.1 has a real `delta_quality` number.
2. Part 1 is finalized with both Codex and Claude responses plus synthesis.
3. R21.4 gate re-verification is green.
4. The five R20 Decision B conditions are checked off explicitly.
5. PR #182 or an equivalent Profile S env fix is merged or consciously
   superseded.

## Open Questions for Claude

1. Does Claude know of any committed or external labelled
   `case_trajectory_input -> ground_truth_grade` corpus with >=100 examples?
   If yes, use it. If not, Path B / beta should be routing golden-output.

2. Which call site should R21.1 measure first?
   My recommendation: a routing/solver-selection call site backed by
   `tests/oracle/*.yaml`, because the oracle data already exists.

3. Should PR #182 be merged before R21.1 implementation, or should R21.1
   branch depend on it? I prefer merging #182 first if CI is green, because
   R21.1 may call `BridgeLLMClient.default()` and should inherit the
   corrected Profile S behavior.

## Handoff

Claude should now write `r21_response_claude_2026_05_10.md`, then synthesis
should produce:

- `iterations/codex_scout_tasks/r21_synthesis_2026_05_10.md`
- `iterations/codex_scout_tasks/r21_operator_decisions_2026_05_10.md`

The operator's default recommendation is accepted: choose beta unless
existing labelled data is found.
