# Idle Consensus Artifact v1

`tools/idle_consensus_artifact.py` is the manual operator-review handoff for a
completed idle-protocol soft or hard convergence.

It reads an idle transcript from bridge events and writes a local JSON +
Markdown evidence artifact. The artifact records:

- the full idle transcript
- the convergence report
- `operator_gate_required=true`
- `auto_execute=false`
- prohibited actions: no task creation, no branch creation, no pull request
  creation, and no external effect

Example:

```powershell
.\.venv\Scripts\python.exe tools\idle_consensus_artifact.py `
  --events .agent-bridge\shared\events.jsonl `
  --out-dir docs\architecture\consensus_artifacts `
  --json
```

The tool refuses incomplete transcripts, charter-violation transcripts, invalid
payloads, privacy markers, and existing output files. It does not append bridge
events and does not create implementation work.
