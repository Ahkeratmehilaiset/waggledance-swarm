# P8 — real self-epoch evidence bundle

Built an epoch evidence bundle from the live repo state targeting
the validation iteration left over from Phase 2B-R.

## Result

```
epoch_id: phase2br1-self
evidence_sha256: e4628ce7062769a2b7d7ed1db8a483892c5fc2cb22f454f8ceb96e6dd63c8e5f
format_version: 2b.1
iteration_count: 1
review_readiness_status: REVIEW_READY
reviewable_files_count: 1
reviewable_lines_count: 13
```

## Bundle file sizes

| File | Bytes |
|------|-------|
| `cumulative_diff.patch` | 348 |
| `cumulative_raportti.md` | 449 |
| `cumulative_supplement.md` | 85 163 |
| `iter1_logs_combined.md` | 3 210 |
| `iter1_internal_review.md` | 379 |
| **total bundle** | **89 549 bytes** (~88 KB) |

`cumulative_supplement.md` (the Phase 2A-3 review-surface
supplement) dominates — that is by design when the diff is small.

## Attachment cap respected

`Get-WaggleAttachmentPlanForProvider -MaxAttachments 20` returned
7 attachments — well under the 20-file cap. No consolidation
needed.

The 7 canonical attachments:

* `epoch_evidence.json` (manifest with evidence_sha256)
* `cumulative_diff.patch`
* `cumulative_raportti.md`
* `cumulative_supplement.md`
* `regression_state.json`
* `iter1_internal_review.md`
* `iter1_logs_combined.md`

## Compactness

88 KB total fits well under any reasonable chat-UI attachment
cap. The supplement carries the reviewable surface; everything
else is cheap.

## Outcome

P8 PASS: real self-epoch evidence bundle is compact (~88 KB),
relevant (REVIEW_READY), respects the 20-file cap, and the
deterministic `evidence_sha256` is exposed for the SHA-binding
contract used by reviewer + synthesis importers.

No external review was generated from this bundle (per the rule
"do not call Gemini/Grok/GPT" and "do not require manual paste").
The bundle's existence + shape is the test.
