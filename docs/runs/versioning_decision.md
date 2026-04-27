# Versioning Decision (Phase 7)

## Decision: 3.5.7 → 3.6.0 (MINOR bump)

## Rationale

Per WD_release_to_main_master_prompt.md §PHASE 7 default rule:

> prefer MINOR bump if this release lands major additive architecture and release polish without a live runtime flip

This release matches that exactly:

- **Major additive architecture:** 16 new phases (F–Q), ~25 000 insertions across 201 files, 18 new core packages
- **Release polish:** README repositioned, license cleanup, SPDX convention unified
- **Without a live runtime flip:** the atomic runtime flip is explicitly deferred to a separate Prompt 2 session

A PATCH bump would understate the scale of the architecture additions. A MAJOR bump would imply a breaking default runtime behavior change, which has **not** happened — the Phase 9 fabric is additive scaffolding, and the existing 3.5.7 runtime path remains the authoritative live read path until Prompt 2 executes the flip.

## Considered alternatives

- **3.5.8 (PATCH)** — Rejected. Insertions, scope, and conceptual surface make this clearly more than a patch.
- **4.0.0 (MAJOR)** — Rejected. No backwards-incompatible runtime change was made in this release; the fabric is gated behind the human-approved promotion ladder. A MAJOR bump should accompany the eventual atomic flip session.

## What 3.6.0 represents

- The autonomy fabric scaffold is on main
- The contract surfaces (IR adapters, schemas) are stable
- The promotion ladder is enforced
- All Phase 8.5 producer subsystems can now be merged separately on top of this base

## When 4.0.0 will land

When Prompt 2 (separate session) executes the atomic runtime flip and the live runtime read path is repointed to the new fabric. This is intentionally a separate risk domain and a separate version bump.
