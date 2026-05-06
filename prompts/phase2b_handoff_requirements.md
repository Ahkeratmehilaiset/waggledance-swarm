# Phase 2B handoff requirements (committed by Phase 2A-5)

This document is the **persistent contract** that any future Phase
2B prompt MUST satisfy. It is written here so that whoever drafts
the Phase 2B prompt cannot accidentally drop the ledger / gate-
report disciplines that Phase 2A-5 just instituted.

If a Phase 2B prompt does not honor these requirements, treat it as
incomplete and stop.

## Required: ledger maintenance

Every Phase 2B session that:

1. lands a fix referenced in code with `# Phase 2B TAG-NNN`,
2. carries a Phase 2A finding forward as backlog,
3. discovers a false-positive-due-to-truncation,
4. or marks an existing entry as already_fixed,

MUST update **both** `docs/design/phase_fix_ledger.json` (source of
truth) **and** `docs/design/phase_fix_ledger.md` (rendered view).

`Test-PhaseFixLedger.ps1` runs in the hardening-gate driver and
fails the gate if:

- a `Phase 2B (ARCH|REL|SEC)-N` reference exists in source/tests
  but no `(phase, tag)` ledger row matches,
- a row is missing canonical anchors / tests / future-phase notes
  in the appropriate column,
- a canonical anchor file does not exist or its `:: text` cannot be
  found in the file.

### Tag-ID disambiguation

Tag-IDs (`ARCH-NNN`, `REL-NNN`, `SEC-NNN`) are NOT globally unique
across phases -- each review run numbers its findings from 0. The
ledger's unique key is `(phase_introduced, tag)`. Phase 2B may
issue an `ARCH-001` that is a different finding from Phase 2A-3's
`ARCH-001` and Phase 2A-4's `ARCH-001`. Each gets its own row,
disambiguated by `phase_introduced`.

## Required: hardening-gate report path

Phase 2B MUST use the Phase 2A-5 phase-agnostic default ReportPath:

```
docs/runs/hardening_gates/<utc_timestamp>.json
```

with the local `latest.json` shortcut. Phase 2B MUST NOT introduce
a new phase-specific default ReportPath in
`Run-WaggleHardeningGates.ps1`. If a particular Phase 2B step needs
to commit a definitive run, it should pass an explicit `-ReportPath`
under that phase's `docs/runs/orchestrator_phase2b_*` folder; the
default stays phase-agnostic.

`docs/runs/hardening_gates/.gitignore` keeps generated JSON reports
out of git. Phase 2B MUST NOT loosen this ignore (no committed
generated reports). Final reports may QUOTE summaries from a gate
JSON.

## Required: final-report contents

The Phase 2B final report MUST include, at minimum:

- the hardening-gate timestamped report path used for the
  definitive run (one explicit `-ReportPath` invocation),
- the ledger rows added or updated in this phase,
- any new backlog tags created (with future-phase + acceptance
  criteria, since `Test-PhaseFixLedger` enforces both),
- a finding-by-finding resolution table for any reviewer findings
  raised during the phase,
- explicit `PASS`/`HOLD` decision.

## Required: scope discipline

Phase 2A-5 is the LAST pre-2B cleanup. Phase 2B is the multi-LLM
manual paste lane (Anthropic + Gemini + GPT + Grok, manual). Phase
2B must NOT:

- start any browser automation,
- automate any third-party LLM web UI,
- bypass login / captcha / rate limits,
- introduce paid external APIs without explicit operator approval,
- create a tag or release.

Phase 2B is interactive paste-driven. The orchestrator's role is
to PREPARE the prompt, REDACT it, EMBED it in a quarantined
`<<<UNTRUSTED>>>` block (same shape as Phase 2A-2), and then save
the operator's pasted external response back into the iteration
folder for review. There is no automated browser session.

## Acceptance criteria for the Phase 2B prompt itself

Before any Phase 2B work begins, the operator-supplied Phase 2B
prompt must:

- [ ] reference `prompts/phase2b_handoff_requirements.md` (this
      file) as a binding requirement,
- [ ] not introduce a new phase-specific default `-ReportPath`,
- [ ] update `docs/design/phase_fix_ledger.json` for every new
      ARCH/REL/SEC tag it introduces or carries forward,
- [ ] keep `docs/runs/hardening_gates/.gitignore` strict,
- [ ] keep all synthetic credential fixtures runtime-concatenated
      (no contiguous token-shaped literals in source),
- [ ] not call `gh auth token`,
- [ ] not bypass GitHub push protection,
- [ ] not create a tag or release.

If the Phase 2B prompt fails any of the above, the session must
stop and ask the operator for a corrected prompt.
