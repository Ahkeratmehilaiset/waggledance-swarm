# P6 — auto-repair classifier + repair prompt builder

## Synthetic findings driven through the classifier

| Finding | Class | Fixability | Reason |
|---------|-------|------------|--------|
| **F1** PowerShell parse error: missing closing brace | TRIVIAL_AUTO_FIX | trivial | trivial_with_small_scope |
| **F2** schema field name mismatch | LOCAL_REPAIR ✓ (after P6 fix) | clear | clear_or_trivial_local_scope |
| **F3** missing optional field reviewer_self_id | TRIVIAL_AUTO_FIX | trivial | trivial_with_small_scope |
| **F4** architectural direction unclear / 5 affected files | EXTERNAL_REVIEW_REQUIRED | strategic | fixability_strategic |
| **F5** credential leak in test fixture | NEEDS_MANUAL_ACTION | unsafe | unsafe_or_manual_category |

The trivial / clear cases route to local auto-repair as designed.
Strategic / unsafe cases route external or manual.

## Bug found + fixed during P6: CLF-BUG-001

F2 originally classified as EXTERNAL because the classifier's
clear-signal regex required `\s+actual\s+`, which doesn't match
real-world `actual:` (colon, no trailing space). Routed through
the classifier itself → LOCAL_REPAIR → patched + tests added.
See `classifier_runs.md` for the full routing record.

## Repair prompt builder

Built a TRIVIAL_AUTO_FIX repair prompt for F1 (PowerShell parse
error). Verified:

| Check | Result |
|-------|--------|
| `max_files=2` (per finding_classifier.max_files_for_trivial_auto_fix) | ✓ |
| 11 hard rules emitted | ✓ |
| `SCOPE LIMIT` clause present | ✓ |
| Demands a test (rule 4) | ✓ (`Add or update at least one test`) |
| Demands verification iteration awareness (rule 11) | ✓ (`The next iteration after this one is automatically a verification iteration`) |
| `repair_escalated.txt` instruction (rule 6/7/8) | ✓ |

## Outcome

P6 PASS: classifier behaves as designed across all 5 representative
findings; one mechanical bug found and fixed (the classifier
regex itself), strengthening real-world coverage.
