# P3 — proposal matrix exercise (real-repo)

## Procedure

1. Built proposal matrix from real validation iteration with empty
   internal reviews + no external imports + no Codex → 0 rows
   (correct empty-state behavior).
2. Imported a synthetic Codex Scout finding (1 finding, 2 proposals)
   via `Import-WaggleCodexFindings.ps1` against
   `iterations/_phase2b_validation/test1_iter_2026-05-07/`.
3. Re-built the matrix → 2 rows, both with `source_kind=codex`.

## Verifications

| Property | Result |
|----------|--------|
| Codex proposals included | 2/2 (`PM-CDEX-001`, `PM-CDEX-002`) |
| `source_provider` populated | `codex` ✓ |
| `source_role` populated | `scout` ✓ |
| `original_proposal_id` preserved | `CDEX-PROP-001`, `CDEX-PROP-002` ✓ |
| `estimated_effort` field | `small` (echoed from source) ✓ |
| `expected_payoff` field | populated from source ✓ |
| `risks` field | populated from source ✓ |
| `category` inferred | `other` / `test_coverage` ✓ |
| `matrix_status` | `candidate` (default) ✓ |

## Synthesis paste-block inclusion

The P9 dry-run earlier in Phase 2B-Revision proved the synthesis
paste-block inlines `proposal_matrix.md` under the
`## PROPOSAL MATRIX` heading (verified in Test-SynthesisPasteBlock
test cases p9: paste-block has PROPOSAL MATRIX section).

## Dedup behavior

The matrix builder does not currently merge similar proposals
across providers (each row carries its own `original_proposal_id`).
The synthesis stage (GPT) is responsible for the cross-source
de-duplication via `merged_from_proposals[]`. This is intentional
per the design — the matrix is a *decision surface*, not a
*merged list*.

## Outcome

P3 PASS: matrix-building from a real iteration tree works,
provenance + effort/payoff/risk fields preserved, paste-block
inclusion already verified by Phase 2B-R tests.
