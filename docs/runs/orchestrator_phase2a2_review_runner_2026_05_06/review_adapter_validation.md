# P6 — Review schema + adapter validation

## Files added

| File | Purpose |
|---|---|
| `schemas/review.schema.json` | wire format for `review.json` (JSON Schema draft-7) |
| `orchestrator/lib/review/ReviewSchema.ps1` | PS 5.1 validator (no external deps) |
| `orchestrator/lib/review/ReviewAdapter.ps1` | role resolve, package load+truncate, redact, prompt build, fenced-JSON parse, schema validate, markdown render, safe-profile constant |
| `orchestrator/lib/review/Adapter.Architect.ps1` | tiny role descriptor |
| `orchestrator/lib/review/Adapter.Security.ps1` | tiny role descriptor |
| `orchestrator/lib/review/Adapter.Reliability.ps1` | tiny role descriptor |
| `orchestrator/Test-ReviewSchema.ps1` | unit tests for schema validation |
| `orchestrator/Test-ReviewAdapter.ps1` | unit tests for adapter |

## Tests

```
powershell -NoProfile -ExecutionPolicy Bypass -File ".\orchestrator\Test-ReviewSchema.ps1"
  Result: 16/16 tests passed

powershell -NoProfile -ExecutionPolicy Bypass -File ".\orchestrator\Test-ReviewAdapter.ps1"
  Result: 38/38 tests passed
```

## Coverage

`Test-ReviewSchema.ps1` (16 cases):

- happy path for each of the 3 roles
- happy path with one medium finding
- invalid role rejected
- missing summary rejected
- missing findings rejected
- invalid severity rejected
- missing metrics field rejected
- negative metric rejected
- invalid verdict rejected
- completed=false rejected
- empty summary rejected
- malformed JSON rejected
- empty JSON text rejected
- finding missing required field rejected

`Test-ReviewAdapter.ps1` (38 cases):

- role resolution for architect, security, reliability + invalid role throws
- package path resolution from `-SourceIterationId` and `-PackagePath`,
  including explicit-path precedence, agreement check, disagreement fail,
  and the "neither arg" fail case
- package read happy path, oversize truncation with TRUNCATED marker,
  missing-file throw
- redaction integration: GITHUB_PAT replaced; Phase 2A-1 contextual
  SHA allowlist preserves SHAs in JSON `commit` field
- prompt build: template included, `<<<UNTRUSTED PACKAGE BEGIN>>>`/`END`
  delimiters present, prompt-injection text remains as data inside the
  delimiters, REVIEW-COMPLETE contract included
- fenced ```review-json``` block extraction
- REVIEW-COMPLETE marker detection (positive + negative)
- full parse+validate happy path
- parse+validate negative paths: missing marker, schema-invalid,
  role mismatch, iteration_id mismatch, unparseable JSON
- markdown render: title, verdict, "_None._" for empty findings,
  per-finding heading
- safe profile constant: allowBash=false, requireUniqueArtifact=false,
  dangerouslySkipPermissions=false, disallowedTools includes
  Bash/Write/Edit, allowedTools = Read/Glob/Grep

## Notes

- All non-ASCII separators in the adapter sources were converted to
  ASCII ("--") to keep PS 5.1 default code-page parsing happy. The
  prompt templates (under `prompts/review/`) and docs are markdown
  files and may keep unicode punctuation; PS 5.1 only parses `.ps1`.
- `Find-WaggleReviewJsonBlock` greedily matches the FIRST fenced
  ```review-json``` block, so injection attempts to add a second
  fake block cannot displace the real one (a malicious second block
  would be after the legitimate one).

P6 PASS.
