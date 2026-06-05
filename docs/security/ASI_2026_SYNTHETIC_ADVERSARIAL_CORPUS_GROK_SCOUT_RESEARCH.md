# grok-scout-1 Advisory Research: Synthetic Adversarial Corpus (V12 Ingredient 5)

**Topic:** OWASP Agentic Top-10 (ASI 2026) failure modes + corpus-generation techniques WaggleDance (WD) can use to harden its fail-closed promotion gate.
**Date:** 2026-06-02 (research performed on live sources)
**Scope:** Advisory only. Grounded in live web searches + repo evidence read from persistent checkout. No code changes, no merge instructions, no privacy canary markers. Never touched C:\PDAM_LOGBOOK or AM_TRAINING paths.
**Rivals referenced (per V12):** Microsoft AGT, JamJet, Asqav, Preloop, Cordum, SystemPrompt.

---

## 1. Key Findings (with citations)

### OWASP Top 10 for Agentic Applications 2026 (ASI)
The primary reference is the OWASP GenAI Security Project's peer-reviewed list (announced ~Dec 2025, resources active 2026). It targets autonomous agent risks arising from goal misalignment, tool use, delegated trust, memory, inter-agent comms, and emergent behavior — distinct from (but overlapping) the LLM Top-10.

Exact 10 (synthesized from primary resource + detailed implementations; see sources for authoritative wording):

1. **ASI01: Agent Goal Hijack** — Attackers manipulate an agent's objectives, plans, or decision paths via direct/indirect instruction injection (prompts, RAG docs, tool outputs, recursive).
2. **ASI02: Tool Misuse & Exploitation** — Legitimate tools invoked unsafely (chaining, recursion, arg abuse, budget exhaustion, state leakage).
3. **ASI03: Agent Identity & Privilege Abuse** — Impersonation, cross-agent trust abuse, identity inheritance, role bypass via weak auth/delegation.
4. **ASI04: Agentic Supply Chain Compromise** — Poisoned schemas, misleading tool/agent descriptions, registry poisoning, compromised external components that agents dynamically trust/import.
5. **ASI05: Unexpected Code Execution (RCE)** — Agent-generated/triggered code or commands executed without sufficient sandbox/validation (shell, eval, command injection).
6. **ASI06: Memory & Context Poisoning** — Injection/leakage into persistent memory or session context that alters future reasoning/actions across turns.
7. **ASI07: Insecure Inter-Agent Communication** — Message injection, spoofing, MITM on agent-to-agent/planner/executor channels.
8. **ASI08: Cascading Agent Failures** — Small failures (tool, agent, resource, trust) propagate via chains/dependencies into large-scale impact.
9. **ASI09: Human-Agent Trust Exploitation** — Misleading explanations, false authority/credentials, over-confidence, responsibility diffusion to induce uncritical human acceptance.
10. **ASI10: Rogue Agents** — Goal drift, collusion, emergent behavior causing agents to act beyond intended objectives/scope.

**Primary sources (live):**
- https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/
- https://trydeepteam.com/docs/frameworks-owasp-top-10-for-agentic-applications (full per-risk breakdowns + attack examples)
- https://www.humansecurity.com/learn/blog/owasp-top-10-agentic-applications/
- https://www.promptfoo.dev/docs/red-team/owasp-agentic-ai/
- Supporting: Microsoft red teaming references, arXiv agent red-teaming papers (2025-2026).

No single "official exhaustive PDF text" was retrieved in full via public pages (download link exists on OWASP site); descriptions above are consistent across multiple 2026 secondary sources that cite the OWASP ASI directly. Any finer subtype wording should be validated against the official download when used for schema changes.

### WD's Existing Synthetic Adversarial Corpus (v0)
- Strict fixture: `tests/fixtures/magma_adversarial_corpus/v0.json` (59 cases across 15 defect types as of 2026-06-02 live run).
- Validator (`tools/validate_synthetic_adversarial_corpus.py`) + schemas enforce: corpus_version, split (held-out case_ids for blind replay), risk_classes, gates/verdicts, critical defect floors (min 2 for fail-open/governance_bypass/hallucinated-success/path_escape/regression-process/spec-gaming), no extra props, finite nums, redaction rules.
- 21 contract tests pass; `python tools/validate...` reports "synthetic adversarial corpus OK: 59 cases".
- 15 defect types: charter_violation, correlated_review_trap, evidence_spoofing, fail-open, governance_bypass, hallucinated-success, path_escape, payload_leak, policy_bypass, privilege_leak, regression-process, risk_escalation, spec-gaming, subtle_drift, tool_argument_abuse.
- Explicit promotion-gate case exists (e.g., charter_violation:005 targets "promotion receipt for an external_effect solver sets operator_gate_required=false").
- Expansion fixture exists (separate, more permissive for iteration).
- Advisory generator (`tools/generate_magma_adversarial_candidates.py`): template-driven per defect, schema-valid output, never auto-mutates authoritative files.
- Full receipt/EvalResult bundle emission in `tools/run_magma_adversarial_eval.py`.

**ASI coverage (per WD's own 2026-06-01 mapping doc `docs/security/ASI_2026_MAPPING.md`):** At least one WD defect type maps to every ASI01-ASI10. Strong on ASI01/02/03/06/09; partial (with explicit seeds added) on ASI04/05/07/08/10. Mapping is document-only, no runtime ASI labels or authority changes.

### Integration into Fail-Closed Promotion Gate (V12 context)
- Core: `waggledance/core/magma/adversarial_corpus_eval.py` (run_..., pure, raises on fixture problems) + `adversarial_gate.py` (verify_... : re-derives verdict from per-case results; FAIL-CLOSED on missing/empty/malformed/unbound/<min/not-caught).
- Enforced in `waggledance/core/autonomy_growth/auto_promotion_engine.py` (I11): if require_adversarial_gate (default on, charter-denylisted to disable), run inline (fresh) or use supplied report; must bind exactly to `compiled1.artifact_id`; re-derive ok from cases; any failure rejects promotion with shadow/validation record.
- Composes with MAGMA receipts (5-digest chain incl. charter), EvaluationResult v0, counterfactual replay (A3), solver provenance lifecycle (shadow→canary→live + revoke/quarantine).
- Operator-owned gates (V12 element 5) envelop: `operator_gate_required=true`, `auto_execute=false`; bridge-consensus (3-identity fail-closed) for merges; no auto on consensus.
- Demo policy (`waggledance/core/magma/demo_policy.py`): reference implementation of expected gate/verdict/reason_codes per defect; used for corpus expectations (not live solver execution in the core gate).
- Part of V12 verifiable learning loop for solver-growth substrate (see `docs/architecture/V12_VERIFIABLE_LEARNING_LOOP.md`, `docs/architecture/DREAM_MODE_AGENDA.md`).

### Rival Public Posture (as of live searches ~2026-06)
- Microsoft AGT (Agent Governance Toolkit): Strong public presence on deterministic policy-layer enforcement (YAML rules vs. probabilistic), ATR (425 agent threat rules, shipped/integrated), red-teaming via PyRIT → ARTKIT/Azure AI Foundry Safety Evaluation, eval gates before promotion, governance/federation. Praised for visibility + determinism in independent commentary. No public evidence of WD-equivalent (MAGMA-style chained 5-digest receipts + per-solver-hash bound synthetic adversarial corpus + inline fail-closed re-derived gate + counterfactual delta primitive + operator-charter-versioned promotion lifecycle) in the searched public docs.
- JamJet, Asqav, Preloop, Cordum, SystemPrompt: Minimal-to-no hits on equivalent public synthetic adversarial corpus, hash-bound promotion gates, or full V12-style 5-element composition in 2026 search results. Some have approval flows / require_justification / red team mentions in general agent governance discussions, but nothing matching the verifiable substrate claim scoped in WD's May 2026 snapshot.
- Claim of "no rival publishes equivalent" remains freshness-bounded and public-only (rivals may have strong internal corpora/gates not disclosed).

**Unverifiable (mark as such):**
- Real-world attack catch rate of WD corpus vs. production traffic or novel attacks (synthetic only; no public breach/pen-test correlation data).
- Head-to-head robustness numbers vs. any rival on a shared adversarial suite (no such public benchmark found).
- Whether WD's demo_policy + gate actually blocks a superset of what MS AGT's 287+ ATR rules catch in equivalent scenarios (different abstraction layers).
- Efficacy of WD's generator vs. fully agentic red-team loops in rivals' pipelines.

### Corpus-Generation Techniques (grounded)
Common techniques from 2025-2026 literature and tooling (for hardening agent safety eval corpora):

- **Template + schema-driven curation** (WD's current): Defect taxonomy profiles → deterministic case + expectation pairs; validator floors + held-out split + redaction. Low hallucination, high repeatability.
- **LLM-driven synthetic generation / automated red teaming**: Strong LLM (or swarm) prompted as attacker to produce jailbreaks, goal redirects, context poisons; often with few-shot seeds or principles (e.g., Composition-of-Principles/CoP framework for orchestrating strategies). Scale + diversity; risk of model collapse or detectable patterns.
- **Mutation / fuzz / hard-negative engines**: Start from known seeds, apply 50+ mutations (prompt variation, arg widening, hidden writes, timestamp drift, digest swap, etc.); iterate on evasions that fool current detector/gate (HASTE-style closed-loop).
- **Agentic / multi-agent generation**: Red-team agents that plan, use tools, simulate environments, produce multi-step attack traces (e.g., AgentHarm-Gen, DeepTeam, Microsoft AI Red Teaming Agent using synthetic tool mocks + sensitive data). Good for tool-misuse, inter-agent, cascade cases.
- **Context / RAG / tool-output poisoning simulation**: Explicitly inject hidden instructions into "retrieved" docs or tool responses.
- **Red/blue team loops + self-play**: Generator proposes, target (or reference policy) defends, score ASR (attack success rate), feed failures back.
- **Evolutionary / refinement (PAIR-like, auto-complete)**: Iteratively mutate prompts to maximize bypass while staying in scope.
- **Hybrid human + machine**: Human experts curate high-nuance traps; automation expands volume and covers the long tail.
- **Seed banks from known vulns + OWASP**: Start with ASI attack examples, expand.

WD already uses a disciplined version of template+curated (with generator for candidates). Expansion and recent ASI seeds show incremental addition of supply-chain, execution-boundary, cascade, rogue/reward-hack, inter-agent spoof variants.

Sources for techniques:
- https://trydeepteam.com/guides/guide-agentic-ai-red-teaming (context poisoning, goal redirection, system override)
- arXiv CoP paper (agentic workflow for jailbreak orchestration)
- HASTE framework (hard-negative mining for prompt detectors)
- Microsoft AI Red Teaming Agent docs (synthetic datasets + adversarial LLM for agent probing)
- General: https://ajithp.com/2025/07/13/red-teaming-large-language-models-playbook/ (LLM-as-attacker + loops)
- Agentic data gen discussions (e.g., Arize, Galileo resources).

---

## 2. Honest Read of Where WD Stands

**Strengths (verifiable locally + cited):**
WD has a production-integrated, schema-enforced, fail-closed synthetic adversarial corpus of 59 cases that maps to all 10 ASI 2026 categories. The gate (I11) is inline-executable, hash-bound to the exact artifact under promotion consideration, re-derives its verdict (no trust of top-level flag), and refuses on any defect in the reference policy expectations or on eval error. This directly hardens the promotion surface inside the larger V12 loop (MAGMA receipts + EvalResult + counterfactual + lifecycle + operator charter gates). Validator + tests + held-out split + advisory generator provide measurable process hygiene. Recent (2026-06-01) ASI mapping already exists as a backlog seed. Tests for validator (21) and auto-promotion engine (27) are green on current fixtures. This is concrete, auditable substrate work that aligns with WD's claimed edge (verifiable + deterministic-first + low-risk growth).

**Gaps / limitations (honest):**
- Coverage is still curated/synthetic and WD-specific (demo policy surface); broader real-world or rival-specific attack variants (especially full supply-chain package poisoning, live multi-agent collusion, long-horizon rogue reward hacking) have only first seeds.
- Generator is intentionally non-mutating/advisory; no automated continuous expansion loop yet (e.g., no self-redteaming agent that proposes + scores new cases against the live gate and emits expansion candidates).
- The corpus hardens the *gate machinery and reference policy*, not (directly) every promoted solver's runtime behavior under arbitrary tools (the binding + shadow/canary + counterfactual are the surrounding controls).
- Public rival differentiation claim is time-bounded (May 2026 snapshot freshness ~June 3) and public-docs only. MS AGT in particular has visible momentum on deterministic governance + red team tooling; WD's unique composition may be real but requires ongoing refresh + local proof artifacts to remain differentiated.
- No public evidence of WD running the corpus against actual candidate solvers in a full competitive hex promotion setting yet (per DREAM agenda: hex competitive promotion still partial).
- Unverifiable claims (as noted) remain unverifiable without new evidence.

Overall: WD is **ahead on the verifiable-substrate + fail-closed gate integration** for this V12 ingredient relative to public rival disclosures, with a solid (but still early) corpus foundation. The ASI mapping and critical floors are recent positive signals. The main risk is stagnation of coverage or failure to keep the "must-win" axes (A3/A4) evidenced as rivals publish more.

---

## 3. 3-5 Concrete Recommended WD Actions (Advisory) with Measurable Milestones (S/M/L effort)

Recommendations are scoped, smallest-safe, measurable, and respect existing invariants (fail-closed, charter-denylisted opt-outs, held-out splits, document-only until reviewed PR, no direct main pushes). All assume separate reviewed slices; no autonomous merge.

1. **S (Small, 1-3 days local + review):** Refresh + extend the ASI mapping document to current v0 (59 cases) + any post-2026-06-01 expansions; explicitly call out held-out split coverage per ASI; run full validator + auto-promotion tests as part of update.
   **Milestones:** Updated mapping file committed via PR; `validate...` reports OK on strict corpus; at least 1 new ASI gap note or seed proposal if a fresh public ASI example (e.g., from OWASP crosswalk) has no WD defect analog; all related tests green. Success = mapping stays the single source of truth for ASI alignment.

2. **S-M (Small-Medium, 3-7 days):** Enhance the existing advisory generator to emit ASI-tagged candidate cases + simple mutation variants (e.g., for tool_argument_abuse and evidence_spoofing families: path escape + hidden write, digest replay under supply-chain framing). Output only to stdout / temp; never auto-write to fixtures.
   **Milestones:** Generator accepts --asi or --defect filter and produces ≥5 new schema-valid candidates that the validator accepts when manually fed to expansion expectations; at least 2 of the candidates reviewed and added to an expansion fixture (or strict after consensus) with updated expectations; generator test updated. Measure: coverage count per ASI category in mapping increases for at least 2 partials.

3. **M (Medium, 1-2 weeks):** Add a minimal "hard-negative miner" mode or companion script (offline, no runtime authority) that takes current gate expectations, proposes mutated cases (using templates + limited LLM calls if desired, but default deterministic), scores them against the demo_policy, and emits only those that would have been "not_caught" for human review. Preserve fail-closed: any error path refuses inclusion.
   **Milestones:** New tool (or generator flag) produces a report of N candidate evasions; ≥3 high-quality ones land in expansion after review + test; full end-to-end run is deterministic/reproducible from seed; documented in DREAM agenda as "corpus growth" sub-area. Measure: increase in strict corpus critical-defect or ASI04/05/08/10 case count by ≥2 while keeping 100% validator pass.

4. **M-L (Medium-Large, 2-4 weeks + bridge review):** Prototype (document + spike code, no prod path) a self-adversarial "corpus grower" agent that operates inside WD's own substrate (uses MAGMA receipts, counterfactual, idle proposals) to discover new defect patterns for the promotion gate. Run only in shadow / dream-mode / offline harness; all proposals carry operator_gate_required and are fail-closed by construction.
   **Milestones:** Design note + small spike that emits 1-2 novel candidate cases (with expectations) that pass validator and increase coverage on a chosen ASI gap (e.g., ASI07 inter-agent spoof or ASI10 collusion framing); spike is reviewed via bridge consensus process; no change to auto_promotion_engine or charter; results captured in a new `reports/` or `docs/runs/` artifact with receipts. Measure: at least one growth cycle completed with measurable new case(s) + delta in mapping table.

5. **Cross-cutting (ongoing, low per-cycle):** Maintain freshness of rival snapshot + competitor pilot JSON; re-run local checks on MS AGT / others for any new public "adversarial eval corpus" or "solver promotion gate" claims; tie any new WD corpus growth to a local benchmark delta (e.g., via FUTURE_SCALE or route-depth style contracts).
   **Milestones:** Rival snapshot refreshed at least once per 30 days while this ingredient is active; any WD corpus expansion PR includes a short "rival delta" note; A3/A4 evidence artifacts remain up-to-date per V12 doc.

All actions keep the gate fail-closed, the charter as the outer owner, and growth via reviewed PRs only. Prioritize deepening existing 15 defect types + explicit ASI seeds over new defect types unless a clear gap is proven.

---

## 4. Confidence

**medium** — Local repo facts (case counts, gate logic, file paths, test results, V12 composition) are directly evidenced by file reads + live test execution on 2026-06-02. OWASP ASI list + descriptions grounded in multiple live web sources with primary OWASP URLs. Corpus-gen techniques drawn from cited public papers/tooling docs. Rival "absence" is public-search only and explicitly time-bounded (unverifiable internals). WD internal efficacy (real attack blocking power) and long-term maintenance burden are not measured here. No claims beyond evidence.

---

**End of advisory.** This is research output only. Any implementation requires separate design/review per CLAUDE.md / AGENTS.md (PR gate, bridge consensus where applicable, savepoint checkpoints, persistent C-drive main checkout). Consult operator + bridge for prioritization against other V12 ingredients (MAGMA receipt adoption, multi-instance flywheel, A3 counterfactual, hex promotion).

*Sources for all web-grounded claims are linked above. Internal WD claims cite specific paths + live command outputs from the persistent checkout.*