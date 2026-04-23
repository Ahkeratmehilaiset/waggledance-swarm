# GPT-5 Review Round 3 — Prompt to send

**Purpose:** Final accountability check. Did v3 plan correctly fold in all 12 amendments from your round-2 review? Any remaining issue worth fixing before Phase A.1 starts.

**Attachment to send:**
- `docs/plans/hybrid_retrieval_activation_v3_2026-04-23.md`

**Optional context to send (if GPT has lost prior thread):**
- `docs/plans/GPT_response2.txt` (your round-2 response — only if needed)

---

## The prompt

```text
You did the round-2 review of this plan. I (Claude Sonnet 4.6) have
written v3 with all your amendments folded in (attached).

Final accountability pass. Three short questions:

QUESTION 1 — Did v3 correctly land all 12 round-2 amendments?

Verify each:
  1. enabled → loaded rename or clear documentation                    (§1.1)
  2. Source-side axiom cell honesty (declared cell_id, audit step)      (§1.2)
  3. GOLD/SILVER trajectory router_version + verifier_version filter    (§1.6)
  4. Multi-view top-k dedup test                                        (§1.7)
  5. Solver output schema                                               (§1.8 + §1.18)
  6. Per-query coherent snapshot                                        (§1.14)
  7. Append-only delta ledger for Dream Mode + backfill                 (§1.13)
  8. MAGMA trace +3 fields (cache_hit, embedding_hash, snapshot_version) (§3)
  9. Defer Prometheus/dashboard expansion                                (§7)
 10. Embedding determinism Phase A.6 + source-based manifest checksum   (§1.12)
 11. Embedding cache with model-keyed invalidation                       (§1.15)
 12. Disaster recovery extended (axioms + ledger + model + config)      (§1.16)
 13. CSI split into strict + progress                                    (§1.17)
 14. Phase C scope split (workstation full + edge smoke)                 (§1.19)

For each: PASS / WEAK / MISSING. If WEAK or MISSING, quote the v3 text
that is wrong and the correction needed. Be precise — this is your
last chance to catch a synthesis error before code is written.

QUESTION 2 — Any NEW issue you see in v3 that did not exist in v2?

v3 added these protocol details. Did any introduce a new problem?

  - Append-only delta ledger conflict resolution rules
  - Per-query snapshot reference counting
  - Embedding cache normalization (lower(strip(query)))
  - CSI weighted geometric formula coefficients
  - Disaster recovery test invariants

Look especially for:
  - Hidden coupling between two amendments
  - Edge cases the protocol does not cover
  - Concrete code that won't compile or won't pass tests as specified

QUESTION 3 — Single-sentence final verdict

  - "Begin Phase A.1 now"  (v3 is solid, no further plan revision needed)
  - "Begin Phase A.1 with these N tweaks during implementation"  (list
    tweaks small enough to not need a v4)
  - "v4 needed before Phase A.1"  (list reason; only if showstopper)

Be sharp. If there is any doubt, say so. The previous two rounds you
correctly identified critical issues that would have caused production
pain. Do not soften now.
```

---

## What we expect

GPT round-2 found 12 amendments. v3 incorporates all 12 explicitly with section pointers. Round-3 should:

- Confirm 11-12 of 14 verification points as PASS
- Possibly flag 1-3 as WEAK (synthesis nuance lost)
- Almost certainly verdict: "Begin Phase A.1 now" or "with N tweaks"
- Showstopper extremely unlikely (would require new architectural insight)

If round-3 is clean, **Phase A.1 begins immediately after**. If any WEAK or new issue, we do a small v3.1 patch (not full v4) before Phase A.

---

## Total review effort so far

- Round 1 (v1 → v2): 30 min GPT + Grok + Gemini
- Round 2 (v2 → v3): 30 min GPT
- Round 3 (v3 → Phase A): ~15 min GPT (just verification, no new analysis)

Total: ~75 min of multi-AI review for ~16 h of implementation work. Ratio ≈ 1:13. The original "1 hour planning saves 10 hours doing" Jani principle held.
