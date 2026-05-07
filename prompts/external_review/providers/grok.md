# Provider hints: Grok (grok.com or x.com/i/grok)

When sending a review prompt to Grok:

- Open a NEW chat. Verify the model selector shows the value in
  `expected_model_in_ui` (e.g. "Grok Expert mode"). Expert mode
  enables the longer thinking path.
- Attach all listed attachment files. Grok's attachment limits
  are looser than Claude Web's, but the queue exporter still
  caps at 20.
- Paste the prompt content into the message body and submit.
- Grok in Expert mode adds a "thinking" or "reasoning" section
  ABOVE the final response. Capture BOTH in the saved response
  file -- the orchestrator's importer ignores the thinking
  section but uses it as audit evidence of how the reviewer
  arrived at the JSON.
- Grok timeouts are typically longer than Claude Web's; the
  default `timeout_sec` is 900 (15 min). Do not interrupt
  before that.
- Save the full response (preface + thinking + reviewer-self-id
  block + external-review-json block +
  EXTERNAL-REVIEW-COMPLETE marker) to the path specified in
  `expected_response_path.txt`.
