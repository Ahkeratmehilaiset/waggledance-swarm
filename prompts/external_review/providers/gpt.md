# Provider hints: GPT (chatgpt.com) -- synthesis

When sending the synthesis paste-block to GPT:

- Open a NEW chat. Verify the model selector shows the value in
  `expected_model_in_ui` (e.g. "GPT Pro 5.5 Extended Thinking").
  Extended Thinking is required for synthesis quality.
- Attach all listed attachment files via the paperclip button.
  GPT supports up to 20 attachments per message in current
  plans; the synthesis bundler caps at 20 with consolidation.
- Paste the synthesis paste-block content (which already
  contains the synthesis_gpt.md template + the three reviewer
  responses inline) into the message body.
- Submit and wait. GPT Extended Thinking can take 30+ minutes
  for synthesis of this depth. The default `timeout_sec` is
  4800 (80 min). DO NOT INTERRUPT before that. Closing the tab
  loses the response.
- When GPT finishes, save the full response (extended thinking
  section if shown, then the synthesizer-self-id block, the
  synthesis-json block, the next-claude-code-prompt block (if
  decision=continue), and the SYNTHESIS-COMPLETE marker) to
  the path specified in `expected_response_path.txt`.
- If GPT's response is broken across multiple turns (rate-limit
  pause or "would you like me to continue"), concatenate them
  in order in the saved file. The orchestrator's importer
  expects exactly ONE synthesis-json block and (if continuing)
  exactly ONE next-claude-code-prompt block.
- If GPT decides HALT, the saved file will have synthesis-json
  with `decision: halt` and `halt_marker: WAGGLE_HALT` and NO
  next-claude-code-prompt block. The importer detects this and
  writes `HALT.md` to the synthesis dir.
