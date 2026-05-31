# Idle Consensus Artifact v1

`tools/idle_consensus_artifact.py` is the manual operator-review handoff for a
completed idle-protocol soft or hard convergence.

It reads an idle transcript from bridge events and writes a local JSON +
Markdown evidence artifact. The artifact records:

- the full idle transcript
- the convergence report
- `operator_gate_required=true`
- `auto_execute=false`
- `replay_seed` digest metadata for a later counterfactual replay evaluator
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

Each artifact includes a digest-only `replay_seed` section. The seed records the
artifact, transcript, and convergence digests plus policy/charter references and
the names of future inputs a counterfactual evaluator must provide:
`changed_paths`, `candidate_diff_digest`, candidate-diff charter gates,
`counterfactual_eval_receipt`, and the operator review decision. The artifact
tool intentionally does not accept or store candidate diff bytes or changed-path
values; that later evaluator must perform path confinement, re-derive the diff
digest, and enforce the charter gates before any separate receipt is emitted.

An optional local MAGMA receipt bundle can be written for the artifact:

```powershell
.\.venv\Scripts\python.exe tools\idle_consensus_artifact.py `
  --events .agent-bridge\shared\events.jsonl `
  --out-dir docs\architecture\consensus_artifacts `
  --receipt-out-dir .codex-audit\idle-consensus-artifact-receipt `
  --json
```

The receipt bundle is opt-in and must verify before the operator-review
artifact files are written.
