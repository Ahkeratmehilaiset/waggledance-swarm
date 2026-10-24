# Measured new-test classification (prepared interface, not activated)

`classify_ab(paths)` and all existing admission callers keep their existing
behavior. New unrecognized test paths remain (a) on that legacy path. The new
optional `git_context` keyword is preparation only: no snapshot, merge gate,
receipt writer, workflow, default switch, roster, veto, or charter is rewired.
Neither merging this change nor a returned (b) class enables standing-sign.

A caller may supply exactly `base`, `head`, `base_tree`, `head_tree` (full
lowercase SHA-1 object IDs), and `cwd` (its explicit Git repository directory).
The classifier re-reads those immutable commit/tree objects and the entire
diff. It requires agreement between NUL-delimited raw status, name/status,
exact text patch, absent base paths, regular `100644` head blobs, and locally
recomputed Git blob IDs. Rename and copy detection are explicit, including
copies from unchanged files. Caller-written `added_only`, status rows, or
snapshot approval booleans cannot substitute for these reads.

Exact lowercase path absence does not prove absence on Windows. The reader
also enumerates the entire immutable base namespace with NUL-delimited
`ls-tree -r --name-only -z`, rejects missing/truncated/duplicate or malformed
namespace evidence, and compares each added path AND every directory ancestor.
Casefold aliases (including Unicode casefold and trailing-dot/space aliases)
involving an added path or ancestor refuse the exception. Its evidence receipt
binds the full base namespace output SHA-256 as well as the base tree ID.

The exception is deliberately narrow: all entries must be added new
`test_*.py` files under canonical lowercase ASCII `tests/**` paths. Existing
(a) and charter-deny predicates win. Mixed changes, modifications, deletions,
copies, renames, mode changes, executable/symlink/gitlink entries, binary or
non-UTF-8 bodies, ambiguous/quoted/truncated evidence, duplicate paths, aliases,
and mismatched objects fall back to exactly the old path-only result.
That fallback can still be (b) for a category already recognized before this
change; failed measurement never creates a new (b) exception.

Additional conservative exclusions cover `tests/windows/**`, Windows device
components, and paths containing fixture/corpus/corpora/conformance/hook/config/
import/conftest/sitecustomize/usercustomize markers. These intentionally decline
some ordinary test names too; they do not broaden any existing category.
At most 32 paths and 4 MiB per returned Git evidence object are accepted.
The latter is a validation limit, not a physical memory ceiling. Each read has
a 10-second timeout; this is not a hard total-runtime guarantee.

A successful measured result records exact base/head/tree IDs, paths, blob IDs,
base namespace SHA-256, and patch SHA-256. It grants no approval, signature,
merge, or runtime authority.
Future wiring requires its own review: carry and independently re-derive this
evidence at the fresh gate/receipt boundary, not trust a stored success row.
Normal Rule 9a consensus and operator requirements remain unchanged.

At base `1712d369`, the existing admission call in
`tools/idle_consensus_auto_merge.py:672` supplies only `changed_paths` to
`evaluate_standing_consensus_sign`; that evaluator still calls the legacy
classifier without context. The smallest operational follow-up would carry
fresh object context through that evaluator boundary and preserve/re-derive
its proof through the existing snapshot, fresh-gate, and receipt checks
(`pr_status_snapshot.py`, `idle_consensus_auto_merge.py`,
`write_bridge_consensus_merge_receipt.py`, `merge_with_bridge_receipt.py`).
None of that plumbing is implemented or activated by this prepared interface.

The focused conformance tests replace Git, charter I/O, and enrollment with
strict synthetic fixtures. Their result is author fixture evidence, not CI,
native acceptance, or production observation. Run existing gate-conformance
anchors in a separate explicit file-selected process excluding newly added
test modules. The integration custodian runs the affected-test selector and
required tests on actual integrated Source before its savepoint and PR.
