# README Positioning Summary (Phase 5)

## Diff scope

`README.md`: 270 → 274 lines. 161 insertions, 157 deletions.

The rewrite repositions the project from bee/swarm/honeycomb-first marketing to cognitive-OS-first technical positioning.

## What changed (high level)

| Section | Before | After |
|---|---|---|
| Tag line | "Local-first AI runtime with solver-first routing, self-training specialists, overnight dream learning, and full MAGMA audit trail" (bee-leaning) | "A local-first cognitive operating system. Deterministic solver-first routing, builder/mentor lanes for capability growth, vector provenance with identity anchors, multimodal ingestion, and a Reality View as the operator surface — with safe review and human-gated promotion separating proposal from runtime change" |
| Opening (3 paragraphs of bee/honeycomb metaphor) | First-class storytelling | Replaced by "What this is" (7 bullet points) |
| "Why solver first" implicit | Argument made via bees | Argument explicit in "What this is" + "What this is not" |
| Phase 9 visibility | Absent | First-class section listing all 16 phases with module paths |
| Builder / Mentor lanes | Not mentioned | Dedicated section explaining advisory-only nature |
| Memory and Identity | Scattered across MAGMA + introspection mentions | Dedicated section grouping vector identity, memory tiers, invariant extractor, self-model |
| Capsules | Not mentioned by name | Dedicated section listing 6 active capsules (factory_v1, cottage_v1, home_v1, gadget_v1, personal_v1, research_v1) |
| Reality View | Generic "/hologram page renders" | Dedicated section emphasising never-fabricate invariant + linking real evidence render |
| Promotion ladder | Not mentioned | Dedicated section describing the 14-stage ladder and human-gate at runtime stages |
| Atomic flip status | Not addressed | Dedicated "Final atomic runtime flip — separate session" section |
| Bee/swarm origin | First-class storytelling | Demoted to "Why the name WaggleDance?" near the bottom |
| BUSL Change Date | 2030-03-18 | 2030-03-19 (corrected) |
| Test badge | "5580 passing" | "657 Phase 9 targeted + full suite" |
| Tag line at bottom | "Local. Auditable. Autonomous." | "Local. Auditable. Human-gated." (more truthful — autonomy is bounded by promotion ladder) |

## What did NOT change

- Architecture diagram (still shows the same Layer 1/2/3 + verifier + audit trail flow)
- Layer table (3 — Authoritative / 2 — Learned / 1 — Fallback / 1b — Optional)
- Quick start (Docker + native + presets — same commands)
- Source layout listing (same file tree, just truthful for actual release branch)
- MAGMA layer table (L1–L5)
- API endpoint table
- Security section (HttpOnly, no eval, Safe Action Bus)
- Testing section
- Phase 8 description (kept; just expanded to mention Phase 9 succeeds it)
- License section (just date corrected and SPDX coverage added)
- Credits

## Honesty preservation

The new README explicitly states what is NOT shipped or claimed:

- "Not a chatbot wrapper"
- "Not auto-merging or auto-deploying"
- "Not pretending the producer-side is on main"
- "Not finished"

These guard against future drift where someone might over-claim shipped functionality.

## Strategy A compliance

- Did NOT introduce features
- Did NOT remove honest disclaimers (just reorganized)
- Did NOT fabricate test counts or timelines
- Did NOT claim v4.0.0 or atomic flip success
- Preserved full bee/swarm origin story under "Why the name WaggleDance?"

The README is the public face of the release; it had to be technically honest about what v3.6.0 contains and doesn't contain.
