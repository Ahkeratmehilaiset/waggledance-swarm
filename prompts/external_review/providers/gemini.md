# Provider hints: Gemini (gemini.google.com)

When sending a review prompt to Gemini:

- Open a NEW chat. Verify the model selector shows the value in
  `expected_model_in_ui` (e.g. "Gemini Pro Advanced"); switch
  and re-verify if not.
- Attach all listed attachment files via the file-attach button.
  Gemini may show a warning for very large attachments; the
  queue exporter caps at 20 files per provider, but if Gemini
  rejects an individual file size, capture the error in the
  saved response so the importer flags it.
- Paste the prompt content into the message body and submit.
- Gemini may show a "Show drafts" panel with multiple drafts.
  Pick the FIRST draft as the canonical response. The other
  drafts can be discarded (do NOT capture them in the saved
  response file -- the importer expects exactly one
  `external-review-json` block).
- Save the full response to the path specified in
  `expected_response_path.txt`. Include the
  `reviewer-self-id` block, the `external-review-json` block,
  and the `EXTERNAL-REVIEW-COMPLETE` marker.
- If Gemini hits its per-conversation context limit, abort and
  re-attempt with fewer / consolidated attachments.
