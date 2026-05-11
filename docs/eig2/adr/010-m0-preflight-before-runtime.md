# ADR-010 - M0 preflight before runtime work

Status: Accepted for EIG2-M0 (Claude peer-review signed 2026-05-11)
Author: Codex
Peer reviewer: Claude (signed 2026-05-11)
Date: 2026-05-11
R-rule: R10

## Context

The 200-option cold rehearsal converged on one ordering rule: start with M0
documents, inventory, governance, and reference shims before touching runtime.
The recent audit-fix series showed why. Most expensive regressions came from
code that entered integration before its assumptions were pinned by evidence.

## Decision

No EIG2 runtime PR may modify `waggledance/core/*`, route selection, MAGMA
writers, compact memory, tunnel routing, or provider behavior until all M0
preflight gates are closed:

1. PR1 reality-check inventory and 200-option summary are merged.
2. PR2 reference shims, conservative config, bridge projection, classifier, and
   no-human lint are merged.
3. R10-R19 plus ADR 020 are present in `docs/eig2/adr/` and cross-signed.
4. `configs/explosive_intelligence_growth_v2.yaml` has `enabled: false`.
5. The M0 bridge thread has a `done/merged_verified` or equivalent closed state
   for PR1 and PR2.

If a proposed change violates this ordering, it is an `INVARIANT_BREAK`.

## Alternatives considered

1. Begin with runtime stubs and fill ADRs later. Rejected: importable stubs
   become accidental runtime surface.
2. Allow a narrow runtime spike during M0. Rejected: the R12 freeze is simpler
   and cheaper to verify.
3. Treat M0 as documentation only. Rejected: PR2 reference shims are needed so
   later runtime work has executable guards.

## Consequences

- M0 becomes a concrete gate, not a label.
- Runtime work starts slower but with fewer assumption regressions.
- The first runtime touch in M1 is easier to review because M0 contracts already
  define what must not change.

## Safety impact

Positive. It prevents governance from lagging behind code.

## Performance impact

Zero during M0. No production path changes.

## MAGMA invariant impact

Positive by ordering. MAGMA remains untouched until replay/card ADRs and tests
exist.

## Audit / regression class

`bridge_classify.py` maps missing M0 preflight or runtime-before-M0 language to
`INVARIANT_BREAK`.

## Reviewed by other agent

Claude reviewed and endorses via PR #269 RCO peer-review. The rule is accepted
as the M0 ordering gate before any runtime work.

## Related tests

- `tests/orchestrator/test_bridge_classify.py::test_m0_scope_leak_detected_as_invariant_break`

## Provenance

Derived from `docs/eig2/spikes/M0-200-option-summary.md` section 2 item 1 and
R12/R13 ordering evidence from PR #263 and PR #268.

## Sign-off

- Author (Codex): signed.
- Peer reviewer (Claude): signed 2026-05-11.
