# 2026-05-22 AGT + Asqav Rival Local Evidence Slice

Task: `codex-agt-asqav-deep-benchmark-2026-05-22`

This run advances the V12 competitor-axis pilot from public-doc claims toward
pin-locked local evidence. It is intentionally narrow and non-consensus-grade:
Claude owns the JamJet and Preloop rows; Codex owns Microsoft AGT and Asqav.

## Outcome

- Microsoft AGT: `passed` for one pinned local Python policy-engine smoke.
- Asqav: still `cloud_dependent`; the local `asqav==0.4.5` smoke queues an
  action offline but does not locally produce the headline signed receipt /
  ML-DSA-65 proof path.
- Matrix result: `1/4` rival local checks passed.
- Consensus grade: `false` until JamJet, Asqav, Microsoft AGT, and Preloop all
  have passing local evidence or honest blockers are accepted by the sprint
  decision record.

## AGT Pin

- Official repository: `https://github.com/microsoft/agent-governance-toolkit`
- Pinned revision:
  `e122e47180d618b546b825c0c3103132e00ada3c`
- Local source path used for the smoke:
  `C:/tmp/wd-rival-sources/agent-governance-toolkit`
- Evidence manifest:
  `docs/benchmarks/rival_local_checks/microsoft-agt.json`
- Evidence artifact:
  `docs/benchmarks/rival_local_checks/artifacts/microsoft-agt-local-smoke.json`

The AGT smoke imports the local `agent_os.policies` engine from the pinned clone,
verifies that a `delete_file` tool call is denied by a rule, verifies that a
`web_search` control call is allowed, and forces an evaluator iteration error to
confirm deny/fail-closed behavior.

## Guardrails

- This does not validate AGT identity, sandboxing, SRE, MCP, OPA/Rego, Cedar,
  cloud behavior, or non-Python SDKs.
- This does not upgrade WD's full competitor claim to consensus grade.
- The matrix now requires rival-specific `observations` in every passing local
  artifact, so a row cannot pass on a generic `ok=true` stub.

## Commands

```powershell
git ls-remote https://github.com/microsoft/agent-governance-toolkit HEAD
git clone --depth 1 https://github.com/microsoft/agent-governance-toolkit C:\tmp\wd-rival-sources\agent-governance-toolkit
C:\Python\emailknow-venv\Scripts\python.exe tools\run_v12_rival_local_check_matrix.py --evidence-dir docs\benchmarks\rival_local_checks --json
C:\Python\emailknow-venv\Scripts\python.exe -m pytest tests\tools\test_v12_rival_local_check_matrix.py -q --basetemp .pytest-tmp
```

Targeted local verification:

- `tests/tools/test_v12_rival_local_check_matrix.py`: `19 passed`
- Matrix: `1/4 rival local checks passed`
