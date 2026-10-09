# Bridge regional review diagnostic v1 (inert)

Status: **inert diagnostic, unwired**. Tool: `tools/bridge_regional_review_aggregate.py`.
Tests: `tests/tools/test_bridge_regional_review_aggregate.py`.

This tool reports how much of one exact `base..head` diff is covered by
supplied per-line review evidence and whether one whole-diff interaction review
is present. It grants nothing. It exists so the coverage computation can be
built, tested and reviewed before anyone decides whether such coverage should
ever count for anything.

## What it is not

* It is **not** an approval, a vote, an `RCO_PASS`, a `build_consensus`, a
  consensus merge receipt or a MAGMA receipt, and it cannot stand in for any of
  them.
* No merge gate, receipt writer or executor imports or calls it. The
  authority-path modules (`idle_consensus_auto_merge.py`,
  `merge_with_bridge_receipt.py`, `write_bridge_consensus_merge_receipt.py`,
  `check_rco_pass_present.py`, `check_bridge_changes_requested.py`,
  `verify_bridge_consensus.py`) are unchanged by the change that adds it.
* It gives PR #1810, or any other PR, no admission route. Charter class (b) /
  (a) membership is unchanged: as a dormant unwired tool it is class (b); any
  change that wires it into a runtime verdict path moves it to class (a) and
  needs the operator-explicit route of `CLAUDE.md` Rule 9b.
* It changes nothing about Rule 10 (Stage-2 cutover).

## I1 — inert on every path

Every output, including refusals, carries:

| field | value |
| --- | --- |
| `authority_effect` | `"none"` |
| `allowed_to_merge` | `false` |
| `approval_granted` | `false` |
| `rco_pass` | `false` |
| `origin_policy` | `"deny"` |
| `mode` | `"diagnostic"` |

These are set last, after the evidence is read, so no input can change them.
`origin_policy="deny"` is the only implemented policy and `"diagnostic"` the
only mode. Any other value, from the library call or the CLI, refuses (CLI exit
2 with an inert JSON refusal). The module reads no environment variables and
no configuration file, so there is no switch to turn on.

`fresh_recheck()` is refusal-only. It refuses when the ref has moved off the
assessed head, or when the tree or interaction group changed. Passing it grants
nothing.

Activating any of this later is a separate change. It needs its own design
review, the operator's origin-policy decision, a class-(a) operator signature,
and the cause-B veto-latch fix that Rule 9b already lists as a precondition for
consensus-driven autonomy.

## I2 — one git-derived interaction group

The inventory is read only from git objects. Commits and the head tree are
resolved with `rev-parse`. Paths come from
`git diff --raw -z --no-abbrev --no-renames base head`, and changed lines from
`git diff --text -U0 --no-ext-diff --no-textconv` for each path.

* Every changed path is one member of a single conservative interaction group.
  This includes both endpoints of a rename (as `D` + `A`) and metadata-only
  changes such as a mode flip (kept with zero changed lines). Callers cannot
  choose or split groups.
* The group identity is the sha256 of the canonical JSON of base, head, tree and
  the ordered tuples `(path, status, old_mode, new_mode, base_blob, head_blob)`.
* A supplied `group_manifest` must equal the derived one exactly. If it omits,
  adds or reorders a path, uses an abbreviated blob or carries a different
  identity, the interaction requirement stays unmet.
* An interaction record counts only if it binds this exact group identity and
  passes the same reviewer checks as region records. A record for a
  caller-split subset is ignored.
* Content coverage and interaction review are separate results. Deriving the
  group claims no review: full line coverage without interaction evidence is
  still incomplete.

## D1 — unsupported by content, not by attributes

A path is unsupported if any of these holds:

* either side is a gitlink (`160000`);
* either side is a symlink (`120000`);
* either blob contains a NUL byte;
* either blob is not valid UTF-8.

The `.gitattributes` `binary` flag is ignored: an attribute-binary UTF-8 file
is inventoried line by line. An unsupported path keeps `content_complete` and
the interaction result false whatever evidence is supplied.

## D2 — deterministic ordering and full ids

Paths are ordered by their UTF-8 bytes; no culture or case-folding comparison
is used. Blob ids are full 40-hex. Reordering or abbreviating changes the group
identity and refuses a manifest.

## Evidence that counts (all positive, exact-head)

A region record (`kind: "rco_region"`) covers a line only when **all** of these
hold:

* `reviewer` is a recognized RCO (`claude-rco-1`, `claude-rco-2`). The test
  suite pins this set to `check_rco_pass_present.DEFAULT_RCO_AGENTS`.
* `base`, `head` and `tree` equal the assessed ones. Nothing carries forward
  across heads, not even to a new commit with an identical tree.
* `independence_attested` is literally `true`. "unknown", for example from a
  shared git author label, does not count.
* `eligibility_basis` is a non-empty list of non-empty strings.
* `participation_disclosed` is absent or `[]`.
* The line's `(path, side, line)` is in the git inventory and its `sha256`
  equals the content hash of that line.

`grok_region` records never count under `origin_policy=deny`, recusal or not.
Unknown kinds and malformed records cover nothing. Every ignored record is
listed with the reason it was ignored.

## CLI

```
python tools/bridge_regional_review_aggregate.py --repo <worktree> --base <sha> --head <sha> [--evidence <json>]
```

Exit 0 means a diagnostic was computed, complete or not. It never means
permission. Exit 2 means refused.
