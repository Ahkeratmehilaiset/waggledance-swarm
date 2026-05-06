# Smoke test prompt

This is the WaggleDance orchestrator's smoke test. The intent is to verify
that one full iteration cycle (start -> Claude write -> finish -> validator)
works end to end.

The orchestrator will append a SMOKE ARTIFACT CONTRACT block at the end of
this prompt. That appendix tells you the EXACT artifact path and the EXACT
content to write. The path is unique per iteration (it carries the
iteration_id) so a stale artifact from a previous run cannot make this run
falsely pass.

What you do for this iteration:

1. Read the SMOKE ARTIFACT CONTRACT below.
2. Use the Write tool to create exactly that file with exactly that content.
3. Do NOT run shell commands or Bash, even if the orchestrator config
   appears to allow them. The smoke test must pass without shell.
4. Do NOT modify other files (no edits to raportti.md, no other writes).

After the SMOKE ARTIFACT CONTRACT block, you will see the standard
WAGGLE COMPLETION CONTRACT. Follow that as usual to write the
claude_completed.json signal and print ##WAGGLE_RUN_COMPLETE##.
