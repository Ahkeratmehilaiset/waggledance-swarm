# Verify Bridge Consensus Conformance

**Status:** active conformance contract for the bridge-consensus verifier.
**Scope:** `tools/idle_consensus_auto_merge.py::verify_bridge_consensus`,
`tests/tools/test_verify_bridge_consensus_conformance.py`, and
`tests/tools/verify_bridge_consensus_conformance_corpus.json`.
**Companion:** `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`.

## Purpose

`verify_bridge_consensus` is the fail-closed verifier for autonomous merge
approval. It accepts only a three-identity, head-bound bridge consensus:

1. `codex-lead-1` posts a non-author build-consensus approval at the exact PR
   head.
2. `codex-tools-1` posts a non-author build-consensus approval at the exact PR
   head.
3. One recognized RCO in `{claude-rco-1, claude-rco-2}` posts an `rco_pass`
   at the exact PR head, and that RCO is not the PR author.

Silence, stale approvals, duplicate identities, wrong identities, missing
head binding, author self-review by any build or RCO identity, out-of-set
statuses, or a later veto from either recognized RCO must refuse. A 2-of-3 set
is never enough.

## Locked Corpus

`tests/tools/verify_bridge_consensus_conformance_corpus.json` is the versioned
offline corpus for this contract. It uses synthetic bridge events only: no
network, no wallclock dependency, no real credentials, and no live bridge
state. The paired test drives the real verifier directly over every corpus
case.

The corpus intentionally locks:

- missing lead, tools, or RCO identity;
- duplicate or self-approving identity sets;
- approvals at a different or stale head;
- lead/tools approvals that are not head-bound;
- a wrong agent attempting to satisfy a required role;
- author self-review attempts by lead, tools, or RCO;
- later veto events from either recognized RCO;
- build statuses outside `BUILD_CONSENSUS_STATUSES`;
- valid allow cases with three distinct, head-bound identities.

The test also asserts the required refuse and allow case-name sets exactly.
Deleting a required case or weakening the wrong-agent guard must fail the
suite.

## Change Procedure

Any intentional semantic change to `verify_bridge_consensus` must update this
document, the corpus, and the conformance test in the same PR. The update must
explain which fail-closed behavior changed and add or revise cases so the
new contract remains deterministic and explicit.

This document is descriptive; the enforcement lives in the verifier and the
locked conformance tests.
