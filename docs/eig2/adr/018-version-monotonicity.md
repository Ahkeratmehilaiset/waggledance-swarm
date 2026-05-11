# ADR-018 - Version monotonicity inside the sprint chain

Status: Accepted for EIG2-M0 (Claude peer-review signed 2026-05-11)
Author: Codex
Peer reviewer: Claude (signed 2026-05-11)
Date: 2026-05-11
R-rule: R18

## Context

The EIG2 prompt names an alpha version that can collide with the already
stabilized release line. A version collision makes release notes, GitHub tags,
and downstream pinning ambiguous. It also makes acceptance reports harder to
audit because two different feature sets appear under the same semantic version.

## Decision

EIG2 versions must be monotonic relative to the latest stable release and the
current release-candidate chain.

Rules:

1. Do not reuse a version number that already identifies a stable release,
   release candidate, or merged release-prep branch.
2. EIG2 alpha identifiers must advance the minor version if the named base
   version is already occupied.
3. Release reports must state both the base version and the EIG2 alpha suffix.
4. If the correct next version is unclear, choose the next higher minor alpha
   and document why in the release notes; do not ask for an implementation-time
   decision.
5. Git tags remain the source of truth for published versions.

## Alternatives considered

1. Reuse the prompt version literally. Rejected: the prompt predates the current
   stable/release-prep state.
2. Use date-only versions. Rejected: inconsistent with the repo's current
   semver-like release language.
3. Leave versioning to finalization. Rejected: version affects config,
   acceptance reports, and release labels throughout the sprint.

## Consequences

- EIG2 alpha line cannot obscure stable releases.
- Acceptance reports and GitHub releases remain easy to compare.
- Later release PRs must check tags/PR history before naming a version.

## Safety impact

Positive for release governance and rollback clarity.

## Performance impact

Zero.

## MAGMA invariant impact

None.

## Audit / regression class

`bridge_classify.py` maps version collision, reused release version, or non-
monotonic version evidence to `INVARIANT_BREAK`.

## Reviewed by other agent

Claude reviewed and endorses via PR #269 RCO peer-review. The rule is accepted
as the release/version naming invariant for the EIG2 sprint chain.

## Related tests

- `tests/orchestrator/test_bridge_classify.py::test_version_collision_detected_as_invariant_break`

## Provenance

Derived from the EIG2 cold rehearsal risk R2: the prompt's alpha version line
collides with the current stable/release-prep chain.

## Sign-off

- Author (Codex): signed.
- Peer reviewer (Claude): signed 2026-05-11.
