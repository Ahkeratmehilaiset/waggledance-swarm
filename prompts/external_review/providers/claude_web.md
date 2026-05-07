# Provider hints: Claude Web (claude.ai)

> **Phase 2B-Revision (ARCH-010): claude_web is no longer a default
> external-review provider.** The Claude perspective is provided by
> the local Phase 2A-2 self-review runner (architect/security/
> reliability roles run inside Claude Code). This file is retained
> for legacy and explicit-opt-in use only — the operator can still
> invoke `Export-WaggleExternalReviewQueue.ps1 -Providers @('claude_web', ...)`
> if they want a Claude Web second opinion alongside the internal
> reviews. The default queue export now writes only Gemini and
> Grok bundles.

When sending a review prompt to Claude Web, the operator (or the
future Phase 2C Selenium adapter) should:

- Open a NEW chat to keep the context window clean.
- Verify the model dropdown shows the value in
  `expected_model_in_ui` (e.g. "Claude Opus 4.7 (Max plan)").
  If wrong, switch and re-verify before sending.
- Attach all listed attachment files via the paperclip / attach
  button. Claude Web supports up to 5 attachments per message
  with a per-file size limit; if the attachment plan exceeds
  that, the queue exporter will have already consolidated to fit.
- Paste the prompt content into the message body.
- Submit and wait. Claude Web typically responds within
  `timeout_sec` (default 600 = 10 min).
- The full response is captured by selecting all and copy-pasting
  into a markdown file at the path specified in
  `expected_response_path.txt`. Be sure to capture:
  - the optional human-readable preface (if any)
  - the `reviewer-self-id` fenced block
  - the `external-review-json` fenced block
  - the `EXTERNAL-REVIEW-COMPLETE` marker line
- If Claude Web shows "I'd like to think about this longer", DO
  NOT interrupt; wait for the final response.
- If Claude Web's UI breaks the response into a "thinking" panel
  plus a final answer, capture both. The orchestrator's importer
  will redact and validate the JSON block regardless of where
  it appears in the file.
