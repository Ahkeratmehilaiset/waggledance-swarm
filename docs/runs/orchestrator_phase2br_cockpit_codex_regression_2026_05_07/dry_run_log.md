# Phase 2B-Revision end-to-end synthetic dry-run

Generated at: 2026-05-07T13:33:28.9937386Z
Epoch: `p11-e2e-2br`
evidence_sha256: `70a0944395f3b4c29fb3a93e4b48366237a5b7a5e3ef0df9d53d8384e6c8a8a4`
Iterations: 2026-05-07_2bra, 2026-05-07_2brb, 2026-05-07_2brc, 2026-05-07_2brd

| Step | OK | Seconds |
|------|----|---------|
| P11-1: build synthetic 4-iteration project tree | PASS | 0.09 |
| P11-2: Build-WaggleEpochEvidence (4 iterations) | PASS | 0.43 |
| P11-3: Import synthetic Codex findings | PASS | 0.08 |
| P11-4: Build-WaggleProposalMatrix (internal + Codex) | PASS | 0.12 |
| P11-5: Build-WaggleCockpitData (round 1) | PASS | 0.04 |
| P11-6: Open-WaggleCockpit smoke | PASS | 0 |
| P11-7: Export-WaggleExternalReviewQueue (gemini + grok) | PASS | 0.26 |
| P11-8: Import 2 synthetic external responses | PASS | 0.07 |
| P11-9: Build-WaggleProposalMatrix (with external) | PASS | 0.04 |
| P11-10: New-WaggleSynthesisPasteBlock | PASS | 0.09 |
| P11-11: Import synthetic synthesis response (continue) | PASS | 0.04 |
| P11-12: New-WaggleIterationFromSynthesis (correct SHA, dry-run) | PASS | 0.04 |
| P11-13: New-WaggleIterationFromSynthesis (mutated evidence, refused) | PASS | 0.03 |
| P11-14: Build-WaggleCockpitData (round 2 â€” post-import) | PASS | 0.17 |

All 14 steps PASS. The Phase 2B-Revision pipeline
composes correctly: 4-iter epoch + internal Claude reviews (SEC-009 shape)
+ Codex Scout import + proposal matrix (internal + Codex + external) +
cockpit data + 2-bundle queue (gemini + grok) + 2 external imports +
synthesis paste-block + synthesis import (continue) + SHA-bound launcher
(correct -> dry-run success; mutated -> refused).

