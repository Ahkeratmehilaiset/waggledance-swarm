# GPT-5 Review Round 2 — Prompt to send

**Purpose:** Final architecture review of `hybrid_retrieval_activation_refined_2026-04-23.md` before Phase A implementation begins.

**Attachment to send with this prompt:**
- `docs/plans/hybrid_retrieval_activation_refined_2026-04-23.md` (the refined plan)

**How to use:**
1. Open ChatGPT (GPT-5 or strongest available model).
2. Attach the refined plan markdown file.
3. Paste the prompt below verbatim.
4. Bring response back to Claude for final synthesis or for begin Phase A.

---

## The prompt

```text
You reviewed an earlier version of this plan and provided extensive
architectural recommendations. I (Claude Sonnet 4.6) have synthesized
your review with Grok 4's and Gemini 2.5 Pro's reviews into a refined
plan, attached below.

Two-part task for you:

PART A — Coverage check on your earlier review

Read the attached refined plan and verify:
  - Did I correctly capture your activation-mode design (shadow /
    candidate / authoritative)?
  - Did I correctly capture your cell-assignment-honesty fix
    (keyword + centroid parallel + ring1 of both, log disagreement)?
  - Did I correctly capture your three independent ground truth
    sources (axiom oracle / verified GOLD-SILVER trajectories / blind
    adjudication)?
  - Did I correctly capture your multi-view embedding pattern
    (canonical_en / canonical_fi / mixed / units / examples + dedup
    by canonical_solver_id)?
  - Did I correctly capture your negation/question-type parser?
  - Did I correctly capture your FAISS RCU + atomic swap pattern?
  - Did I correctly capture your MAGMA retrieval trace schema?
  - Did I correctly merge your metrics doc (hex_topology_performance_
    metrics_2026-04-23.md) requirements into the plan?

If any of your original recommendations are missing, weaker, or
distorted in this synthesis, list them precisely. Quote the original
phrasing if needed.

PART B — Blind spots that NONE of the three reviews (yours, Grok's,
Gemini's) addressed, and that my synthesis only partially handled

Please specifically evaluate these 7 topics that I identified as gaps
after reading all three reviews. For each, give a 2-4 sentence
recommendation: either "ignore for this phase" or "must add before
Phase A" or "add but defer to a follow-up plan".

  1. EMBEDDING DETERMINISM. Is `nomic-embed-text` deterministic across
     calls? If two runs produce slightly different vectors for the same
     input, the per-cell version manifest checksums become meaningless
     and Phase C metrics are noisy. What's the test, and what's the
     contingency if non-deterministic?

  2. DREAM MODE + FAISS STAGING INTERACTION. Dream Mode writes new
     case trajectories to cells. The plan says "Dream Mode writes to
     staging only", but doesn't specify the merge protocol when staging
     is concurrently being built by axiom backfill. Concrete protocol
     needed.

  3. IN-FLIGHT QUERY DURING MANIFEST SWAP. RCU/atomic swap protects
     individual reads, but a single multi-cell query may search
     cell_thermal at version v3 and cell_energy at version v4 if the
     swap happens mid-query. Should we snapshot manifest_version per
     query (entry-time read), or accept inconsistency, or block reads
     during swap?

  3.5 [BONUS] CONCURRENT-WRITE-TO-CELL ORDERING. If both axiom backfill
     and Dream Mode try to write to the same cell's staging, what's
     the conflict resolution? First-writer-wins, sequence number,
     append-only ledger?

  4. PHASE C COMPUTE COST ON EDGE HARDWARE. Three architectures × 1000
     queries × full embedding ≈ 5000 embedding calls. On Raspberry Pi
     (Grok's number: 80-180 ms/embed), that's 7-15 minutes minimum,
     and the lazy hot-path means many queries embed multiple cells.
     Is Phase C feasible on edge, or must it run on the workstation?

  5. DISASTER RECOVERY FROM AXIOMS ALONE. If `data/faiss_live/`
     corrupts, can we rebuild from `configs/axioms/*.yaml` + the
     embedding model alone? The plan implies yes (B.0 backfill), but
     it's not stated as a tested invariant. Add as explicit gate?

  6. EMBEDDING CACHE FOR REPEATED QUERIES. The 400h gauntlet repeats
     similar queries thousands of times. Caching embeddings by
     query_hash would save 90%+ of embedding cost. Should this be in
     this plan or deferred?

  7. CAPABILITY SURFACE INDEX FORMULA. The metrics doc defines:
       CSI = cell_occupancy_ratio
           * oracle_recall_at_5
           * verifier_pass_rate
           * (1 - false_solver_activation_rate)
           * (1 - llm_fallback_rate)
           * log1p(useful_composite_paths_total)
           * magma_trace_completeness
     Multiplicative form means any term going to 0 zeros the whole
     index. Is this intended (catastrophic-failure visibility) or
     accidental (single bad metric hides progress elsewhere)? Should
     it be additive with weights, or geometric mean, or as written?

PART C — Implementation order opinion

After Parts A and B, give a single explicit verdict in one sentence:

  - "Approve as-is, begin Phase A" — refined plan is solid
  - "Approve with minor amendments" — list 1-3 changes, then proceed
  - "Major revisions needed" — name the showstopper, do NOT proceed
  - "Wait" — list the precondition we should establish first

Please do not soft-pedal. The previous round you found 6 critical
issues; if the refined plan still has issues, name them sharply.
```

---

## What we're testing for

This second-round review specifically targets:

1. **Synthesis fidelity** — did I capture GPT's original recommendations correctly, or did I dilute them in synthesis?

2. **Beyond-3-reviewer blind spots** — 7 issues NONE of the original three reviews touched. These are the ones most likely to cause production pain because no expert has thought about them yet.

3. **Final binary decision** — "begin Phase A" or "don't yet". After this, we either implement or revise once more.

## Expected response shape

GPT will likely:
- Confirm 6-7 of the 8 PART A coverage points, flag 1-2 nuances I missed
- Address each of the 7 PART B blind spots with specific recommendations
- Give a Part C verdict (most likely "approve with 2-3 minor amendments")

Bring response back to Claude. Synthesis will produce final plan revision OR begin Phase A directly if approval is clean.
