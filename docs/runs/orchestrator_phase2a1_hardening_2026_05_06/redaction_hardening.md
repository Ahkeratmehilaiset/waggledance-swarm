# Phase 2A-1 — redaction hardening (P2)

## False positive being fixed

Phase 1.6 smoke produced `redaction_report.json` with
`"AWS_SECRET_KEY": 1`. The captured `git_metadata.commit` field in the LLM
input package was rewritten as `[REDACTED:AWS_SECRET_KEY]`. The matched
value was a normal 40-hex git commit SHA. The detector responsible was:

```
@{ name = 'AWS_SECRET_KEY';
   pattern = '(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])' }
```

A 40-hex SHA (`[0-9a-f]{40}`) is a strict subset of the 40-char
`[A-Za-z0-9/+=]` class, so it is swept up by AWS_SECRET_KEY.

We must NOT shrink the AWS class (real AWS secret access keys live in
exactly that base64-with-padding alphabet at 40 chars). Instead, we add
contextual allowlisting for SHAs that appear in known git fields.

## Code-level change summary

`orchestrator\lib\Redactor.ps1`:

1. New script-scope sentinel constants:
   `$Script:GitShaSentinelPrefix = "@@WAGGLE_GIT_SHA_"`,
   `$Script:GitShaSentinelSuffix = "@@"`.

2. New table `$Script:GitShaContextRules` with regexes for SHA-bearing
   field contexts:
   - JSON: `"commit"|"sha"|"oid"|"headRefOid"|"targetCommitish"|"target": "<40hex>"`
   - JSON nested: `"mergeCommit"|"target"|"object": { ..., "oid": "<40hex>" }`
   - YAML: line begins with `<field>:` followed by `<40hex>`
   - KV: `<field>=<40hex>` with `^|space|comma|semicolon` boundary
   - git log: `commit <40hex>` line
   - bare: `sha:` / `sha=` followed by `<40hex>`

3. New `Protect-GitShaContexts` rewrites SHA values inside those contexts
   to sentinels (`@@WAGGLE_GIT_SHA_<n>@@`) and returns the map. The
   SHA-bearing capture group is identified by content (40 hex) rather
   than positional index, so adding/removing capture groups in a rule
   pattern cannot silently break preservation.

4. New `Restore-GitShaContexts` undoes the protection. Sentinels never
   match any default redaction rule (no `@`, no `[A-Za-z0-9/+=]{40}`),
   so they survive the redaction pass cleanly.

5. `Invoke-WaggleRedaction` was rewired to:
   - call `Protect-GitShaContexts` first;
   - count + redact on the protected text (so AWS_SECRET_KEY count
     reflects the post-protection truth, eliminating the FP);
   - call `Restore-GitShaContexts` last.

The default rule list itself is unchanged. AWS_SECRET_KEY, GITHUB_PAT,
GITHUB_OAUTH, BEARER_TOKEN, PASSWORD_KV, PRIVATE_KEY all still fire on
real secret-shaped values.

A subtle implementation note: `$Script:` variables are NOT reliably
visible inside a scriptblock that is invoked as a `MatchEvaluator`
delegate from .NET. The first attempt produced sentinels `0`, `1` instead
of `@@WAGGLE_GIT_SHA_0@@`, which then collided with later rule passes.
Fixed by binding the prefix/suffix to local variables and capturing them
via `GetNewClosure()`.

## Test results

New: `orchestrator\Test-Redaction.ps1` (flat layout, no `tests\` subdir,
matches the existing convention used by `Test-Redactor.ps1` and friends).

```
PASS  fixture is exactly 40 hex chars
PASS  JSON commit field preserves 40-hex SHA
PASS  JSON commit not mis-redacted as AWS
PASS  JSON sha field preserves SHA
PASS  headRefOid preserves SHA
PASS  mergeCommit.oid preserves SHA
PASS  YAML commit: preserves SHA
PASS  git log line preserves SHA
PASS  kv commit= preserves SHA
PASS  targetCommitish preserves SHA
PASS  gho_ token still redacted
PASS  gho_ value gone
PASS  ghp_ token still redacted
PASS  ghp_ value gone
PASS  Bearer still redacted
PASS  password= still redacted
PASS  private key block redacted
PASS  private key body gone
PASS  fixture is 40 chars and not pure hex
PASS  Bare 40-char non-SHA in plain text is treated as AWS secret
PASS  Plain text not redacted
PASS  Plain text byte-identical
PASS  Report does NOT contain SHA literal
PASS  Report does NOT contain bearer body
PASS  AWS_SECRET_KEY count is 0 when only a SHA is present in a git field
PASS  No sentinel residue in output
PASS  Both SHA fields restored

Result: 27/27 tests passed
```

Legacy `orchestrator\Test-Redactor.ps1` still 26/26. No regression in
real-secret detection.

## Known limitations

- A 40-hex SHA in a context that is NOT one of the 6 recognised forms
  (e.g. an unstructured prose string `"deployed 7210a7e012345... yesterday"`)
  will still be redacted as AWS_SECRET_KEY. Adding a global "any 40-hex
  is a SHA" pass would weaken AWS detection too much; we accept this
  in exchange for keeping AWS detection strong.
- The contextual rules use field NAMES from the GitHub REST/GraphQL API
  and from `git log` text. If the orchestrator collects from a different
  git host with different field names, those would need to be added.
- Sentinels appear briefly in the in-memory text between Protect and
  Restore. They never reach disk because Restore runs before
  `[System.IO.File]::WriteAllText`. The new test "No sentinel residue
  in output" guards this invariant.
- `Invoke-WaggleRedaction` is not idempotent across calls — calling it
  twice would re-protect already-restored SHAs. This was already true.

## What was NOT done

- Did not weaken or globally disable AWS_SECRET_KEY detection.
- Did not add a broad "all 40-hex strings preserved" allowlist.
- Did not change the public surface of `Invoke-WaggleRedaction`.
- Did not add new pip/npm dependencies.
- No secrets were printed during testing.
