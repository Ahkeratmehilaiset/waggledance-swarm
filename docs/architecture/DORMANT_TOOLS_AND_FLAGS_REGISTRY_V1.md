# Dormant tools and flags registry v1

This is Source-only, read-only static conformance, not activation or runtime
observation. The merge gate does not import the registry. Its CI consumer is
`tests/tools/test_dormant_tool_registry.py`; the checker never imports or executes
any registered or protected target. It has no network, subprocess, apply or
installation mode.

Run the offline checker with `python tools/dormant_tool_registry.py --root .`.
It emits one JSON object and exits nonzero for named differences. A successful
report means the declared static facts match the scanned Source, not that any
installed generation, merge route or feature is enabled. The inspected baseline
is Git revision `6b33c6f95bf2fe350bcde7485e7ed094a59ad4e9`; the checker does not
run Git or assert that a caller's checkout is that revision.

## Recorded tools

| Path in `tools/` | Static status | Presence | Importing Source modules |
| --- | --- | --- | --- |
| `bridge_event_taxonomy.py` | wired | exists | `check_bridge_changes_requested.py`, `check_promotion_eligible.py`, `p4_adversarial_corpus.py` |
| `auto_rollback_eligibility.py` | wired | exists | `p4_adversarial_corpus.py` |
| `post_merge_canary.py` | dormant | exists | none found by the defined AST import scan |
| `standing_sign_receipt_replay_canary.py` | dormant | exists | none found by the defined AST import scan |

Here **wired** means a statically resolved non-test Source import exists, including
conditional imports. It does not mean installed, enabled, executed, or entitled
to compute a verdict. In particular, the corpus import of rollback eligibility
does not activate rollback. Reports separately list protected importers.

The historical contract's dormant-tool paragraph still names taxonomy as dormant;
its actual veto-scanner import makes that description stale. This registry records
the Source fact without editing the contract or enabling standing-sign. New tools
named by that contract paragraph must also appear in the registry.

## Exact declared protected set and the named reservation

Rule 9b in `CLAUDE.md` points to the precise membership list in
`BRIDGE_CONSENSUS_APPROVAL_V1.md`. `PROTECTED_PATHS` preserves that list's exact
seven path strings and order: `verify_bridge_consensus`,
`check_bridge_changes_requested`, `check_rco_pass_present`,
`idle_consensus_auto_merge`, `merge_with_bridge_receipt`,
`write_bridge_consensus_merge_receipt`, and `check_proven_safe_autosign_class`
(all `tools/*.py`). CI extracts and compares those path bytes, then cross-checks
the five short names and the merge/receipt executor phrase in Rule 9b.

File presence is separate from protected membership and dormant/wired status.
Only `tools/verify_bridge_consensus.py` may be `reserved_absent`. The charter's
denylist explicitly calls it a "defensive future-proof" reservation and states
that its verifier logic currently lives in the already-denylisted
`tools/idle_consensus_auto_merge.py`. Its absence is not a contract error and is
not evidence that the verifier logic is absent. No placeholder is needed.

The checker requires that exact charter reservation line when the named file is
absent, and reports the quotation, source path and inspected Git revision. Any
other required path missing fails conformance. If the reserved file appears,
it is scanned normally: dormant-tool imports fail and every direct `WD_*` reader
must be registered. Removing or renaming its protected membership still fails.
There is no general `allow_missing` mechanism. The original S12 candidate and its
real-tree failure remain historical evidence; this S13 v2 changes that one
documented-presence rule, not the charter or contract.

This is not a byte comparison of whole prose documents. The class checker's
broader `_A_CODE_SUBSTR` list also names governance, conformance and writer
surfaces; it is not silently equated to this declared seven-path list. No claim
is made that the seven paths exhaust every transitive runtime dependency.

## Environment flags and scan boundary

`WD_FLAGS` records `(reading module, WD_* name)` pairs. At the inspected baseline
it is empty for the seven declared modules. AST discovery and exact comparison
make a new direct reader a conformance failure until the registry is updated.
Reports label each discovered pair `from_code_not_observed`. An empty direct-reader
set does not mean the installed bridge, imported dependencies or other modules
read no environment flags.

Recognized forms include `os.getenv`, `os.environ.get` and subscripts, import
aliases, and literal string concatenation. An unresolved environment key or
environment snapshot in protected code fails closed; no default value is treated
as evidence that a switch was actually enabled.

Imports are parsed across Python Source under `tools/` and `waggledance/`, never
under `tests/` or audit directories. Absolute, relative, from-package and literal
dynamic imports are scanned. Syntax/read failures are named; unresolved dynamic
imports fail closed in the declared protected set. Non-protected dynamic import
resolution, script/path-string execution, alias reassignment/dataflow, and
external packages are not a proof of runtime absence. This tool grants no merge,
sign, veto, installation, measurement or Stage-2 authority.
