# R22.x — Finnish→English agent-contract migration RFC

**Status**: APPROVED 2026-05-11 by operator (broadcast "Korjatkaa, rakentakaa
hyväksyn kaikki" on bridge in response to joint Claude+Codex recommendation
for Option B). Implementation begins POST R22.5 stable cut to avoid
extending the 14-day soak window.

**Authored**: 2026-05-10 by Claude as scout
(`iterations/codex_scout_tasks/r22_x_finnish_to_english_agent_contract_scout_2026_05_10.md`,
gitignored). Promoted to tracked RFC 2026-05-11 with operator approval
folded in.

**Origin**: Codex's live-agent-capacity-audit (2026-05-10T14:42:22Z)
recommendation: *"English agent contract + prompt trim / token cap
policy before claiming scalable live agents"*. PR #214 ships the
per-call FI→EN→FI adapter; this RFC sizes the agent-yaml-level
English-baseline migration.

## TL;DR

- **75 `agents/*/core.yaml` files** with Finnish content.
- **~4 695 Finnish lines** across `DECISION_METRICS_AND_THRESHOLDS`,
  `SEASONAL_RULES`, `FAILURE_MODES`, `eval_questions`,
  `header.name_fi` metadata.
- **No `system_prompt` field** — Finnish is embedded in operator-facing
  action descriptions and test harnesses.
- **3 test files** pin Finnish strings: `test_e2e_chat_200.py`,
  `test_legacy_adapters.py`, `test_embedding_determinism.py`.
- **Migration shape: Option B (APPROVED)** — full English baseline
  in `core.yaml` + `agents_locale/fi/<id>.yaml` overlay for
  user-facing localization.
- **Effort estimate**: 42–50 FTE hours, ~2.1–2.6k LoC diff,
  3–4 week calendar window.

## 1. Inventory

- File count: **75** `core.yaml` files under `agents/<agent>/core.yaml`
- Total YAML LoC across `core.yaml`: **15 782**
- 74/75 have `header.name_fi` localized title
- 75/75 have at least some Finnish prose in actions/rules

## 2. Field-by-field Finnish content classification

| Field | Coverage | Example | Translate-safety |
|---|---|---|---|
| `header.name_fi` | 74/75 | "Tarhaaja (Päämehiläishoitaja)" | UI-facing — **keep in locale overlay** |
| `ASSUMPTIONS` | 75/75 | "202 yhdyskuntaa", "JKH Service Y-tunnus" | Mixed FI/EN; Finnish entity names; keep |
| `DECISION_METRICS_AND_THRESHOLDS.*.action` | 75/75 | "Yli 18% → ei linkota, anna kuivua kannessa" | **Internal** — translate to EN |
| `SEASONAL_RULES.*.action` | 75/75 | Pure Finnish prose under "Kevät/Kesä/Syksy/Talvi" | **Internal** — translate to EN |
| `FAILURE_MODES.*.action` | 75/75 | "Emottomaksi jäänyt pesä" | **Internal** — translate to EN |
| `PROCESS_FLOWS` | 40+/75 | "Maalis: kevättarkastus" | **Internal** — translate to EN |
| `KNOWLEDGE_TABLES` | 30+/75 | "Siivousvastaava — Kynnysarvot" | Hybrid — table titles keep FI in overlay |
| `COMPLIANCE_AND_LEGAL` | 75/75 | "Eläintenpitäjäksi rekisteröityminen Ruokavirastoon pakollinen" | Hybrid — keep regulatory refs FI |
| `eval_questions` | 75/75 | "Mikä on varroa-hoitokynnys?" | Test harness — translate + update tests |

## 3. Locale boundary

**Internal (LLM-input only) — safe to translate**:
- `DECISION_METRICS_AND_THRESHOLDS.action`
- `SEASONAL_RULES.action`
- `FAILURE_MODES.action`
- `eval_questions`

**Operator/user-facing — keep in `agents_locale/fi/`**:
- `header.name_fi`
- `KNOWLEDGE_TABLES.title`
- `ASSUMPTIONS` domain keywords

**Hybrid — case-by-case**:
- `COMPLIANCE_AND_LEGAL` — regulatory references (e.g. "AOYL 2009",
  "Ruokavirasto") stay Finnish; action prose translates.

## 4. Test-pinning audit

Tests asserting specific Finnish strings (must update on translate):

- `tests/test_e2e_chat_200.py` — ~10 assertions for "hunaja", "pesä",
  "mehiläinen", "Kuinka paljon hunajaa..."
- `tests/autonomy/test_legacy_adapters.py` — 1 assertion "linkoa hunajaa nyt"
- `tests/test_embedding_determinism.py` — 1 assertion "hunaja talteenotto"

Total: 3 files, ~12 assertions. Update is mechanical.

## 5. Migration shape — Option B (approved)

Full English translation of `core.yaml`; create
`agents_locale/fi/<agent_id>.yaml` overlay for user-facing localization
(names, examples, table titles).

**Pros**:
- Agents become English-baseline (token cost down, Ollama model
  throughput up, scalable live-agent claim becomes structurally true).
- PR #214 FI→EN→FI adapter becomes optional polish, not critical path.
- Supports future locale expansion (sv, de, en variants).
- Aligns with "scalable live agents" R22 narrative.

**Cons**:
- Larger diff (2.1–2.6k LoC).
- Requires lookup-overlay system in agent loader.
- One-time UI rendering update (Finnish display still works via
  overlay; defaults to English when overlay absent).

## 6. Effort estimate

- Inventory + glossary: **4 h**
- Translation (75 agents, 5–15 min each): **12 h**
- Locale overlay scaffolding (loader patch, fallback rules): **8 h**
- Test updates (12 assertions, 3 files): **2 h**
- Review + iterate (architect/security/reliability per R20.5): **8 h**
- Buffer (locale-overlay UI integration, edge cases): **8–16 h**

**Total: 42–50 FTE hours, ~2.1–2.6k LoC diff**, 3–4 week calendar
window with parallel review.

## 7. Risk & mitigations

- **Translation quality** — domain-specific Finnish terms (hunaja,
  varroa, JKH-vastike, Ruokavirasto-rekisteröinti) need expert glossary,
  not raw LLM translate. **Mitigation**: Claude + Codex collaborate on
  glossary; operator (native Finnish speaker) signs off.
- **Locale overlay loader adds boot-time complexity** — testable but
  new. **Mitigation**: Phase 1 ships scaffolding + 1 reference agent;
  validates loader before bulk migration.
- **Loose coupling to `agents/` schema** — must define overlay-merge
  semantics. **Decision**: deep-merge at key level, fall back to
  baseline when overlay key absent. Codified in the loader.

## 8. Operator decisions confirmed (2026-05-11)

1. **Option B approved** ✅ (operator "hyväksyn kaikki" 2026-05-11)
2. **Glossary owner**: Claude + Codex collaboratively, operator final review
3. **Overlay semantics**: deep-merge at YAML key level; missing overlay key → fall back to baseline
4. **Test scope**: keep existing tests asserting English+Finnish I/O; new locale-test file `test_e2e_chat_locale_fi.py` covers Finnish-only path
5. **Sequence**: POST-R22.5 stable cut (2026-05-24+). Migration starts on the first session after the cut lands.

## 9. Implementation plan

### Phase 1 — Scaffolding (this PR)

- Create `agents_locale/fi/` directory placeholder (this RFC's PR adds
  empty directory with `.gitkeep`).
- Document RFC under `docs/architecture/` (tracked, this file).
- Operator merges this PR = formal commitment to Option B.

### Phase 2 — Loader infrastructure (separate PR, POST-R22.5)

- Implement YAML overlay loader in `waggledance/agents/yaml_bridge.py`
  (or equivalent loader).
- Add unit tests for deep-merge semantics + fallback paths.
- Migrate ONE reference agent (`apartment_board` or similar low-risk)
  with full English baseline + Finnish overlay.
- Validate live-LLM performance on the reference agent vs the
  Finnish-only baseline.

### Phase 3 — Bulk migration (3 batched PRs, POST-R22.5)

- **Batch 1**: home/utility domains — ~25 agents
- **Batch 2**: bee_ops/environment domains — ~25 agents
- **Batch 3**: factory/production/logistics domains — ~25 agents
- Each batch: Codex review + targeted live-LLM benchmark.

### Phase 4 — Polish (POST migration)

- PR #214 adapter becomes optional (default off).
- README + agent-onboarding docs reflect English-baseline.
- New locale expansion (sv/de) becomes additive PRs against the
  proven Option B infrastructure.

## 10. Rollback gate

If any batch (Phase 3) regresses live-agent concurrent-think latency
by >20%, operator may instruct rollback:

- Revert the batch's agents to the pre-migration baseline
- Keep the loader infrastructure (no harm even with all-Finnish baseline)
- Investigate which translated prompts triggered the regression
- Operator decision: continue with refined glossary, or fall back to
  Status-Quo + per-call adapter (the safety net PR #214 already ships)

## Anti-claims

- This RFC is **policy-level commitment**, not implementation. The 2.1–2.6k
  LoC diff happens in future PRs after the cut.
- Effort estimates are honest but unverified — Phase 2 reference-agent
  pass can recalibrate.
- Live-agent capacity claim "1 004 clones / 120 s" stays at the
  object-runtime level (PR #219 EVOLUTION_INDEX entry). The Finnish-prompt
  LLM-latency bottleneck this migration addresses is the next
  scaling-ceiling, NOT the current one.
- This RFC's scope is operator-facing agents under `agents/`. It does
  NOT cover internal `solver-profiles/*.json` (those are LLM-agnostic)
  or `voikko/` Finnish morphological adapter (that's a runtime
  language tool, not a prompt-contract surface).

## Related PRs

- **PR #214** — group-call language pipeline (FI→EN→FI per-call adapter,
  merged 2026-05-10). Backward-compatible safety net for this migration.
- **PR #233** — pyproject 3.6.0 → 3.12.0 + Dockerfile entrypoint
  canonicalization (R22.5 cut prep, open in CI 2026-05-11).
- This PR — promotion of scout to tracked RFC + `agents_locale/fi/`
  scaffolding directory.
