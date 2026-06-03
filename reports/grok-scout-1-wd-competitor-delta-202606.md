# WD Competitor-Landscape Delta (grok-scout-1 advisory)

**Date**: ~2026-06 early (post May-20/22 last sweep)  
**Scope**: Agent-governance / verifiable-autonomy entrants and moves vs. WD verifiable solver-growth substrate (audit-truth + MAGMA provenance + deterministic-first routing + autonomous low-risk growth).  
**Rivals tracked**: Microsoft AGT, JamJet, Asqav, Preloop, + Cordum, SystemPrompt (per query).  
**Method**: Live web search + page fetches + X keyword (since:2026-05-20); all claims URL-grounded. No local rival installs executed. Unverifiable claims marked. No fabrication. Advisory only.

## 1. Key Findings (with citations)

- **Microsoft AGT** (github.com/microsoft/agent-governance-toolkit): Launched ~Apr 2 2026 with MIT OSS runtime security/policy/identity/sandbox/SRE for agents, multi-lang (Python/TS/.NET/Rust/Go), OWASP 10/10 claim, MCP integrations, Ed25519+ML-DSA, Merkle/tamper-evident logs. [web:0](https://opensource.microsoft.com/blog/2026/04/02/introducing-the-agent-governance-toolkit-open-source-runtime-security-for-ai-agents/) [web:1](https://github.com/microsoft/agent-governance-toolkit)  
  **Post-May22 delta (major)**: v4.0.0 released 2026-06-01 — breaking Python consolidation (45 pkgs → 5 distros: core/runtime/sre/cli/full), monorepo v4, TEE keystore abstraction, Entra-signed JWT + verified tiers + session counters, wire-protocol facets + credential injection across SDKs, LangGraph v1.0 adapter, AGT test replay engine, Cedarling integration, expanded PII/secret redaction + many auth/POP/JWKS/sandbox hardenings (no silent fallbacks), docs overhaul, 60+ tutorials. 1,848+ commits, ~3.8k stars, 19+ framework integrations, 10 formal specs/992 conformance tests, governance dashboard, PromptDefense, MCP security gateway. .NET MCP blog Apr29 (borderline), webinars through June. Strong on A1 (pre-exec deterministic sub-ms), A2 (Merkle+quantum-safe), A5 (35k ops/sec claims), A6/A7/A8 (multi-lang + OPA/Rego/Cedar + ML-DSA). [CHANGELOG fetch + web:3,5,9,10,13]

- **JamJet** (github.com/jamjet-labs/jamjet, jamjet.dev): Open-source (Apache2) safety layer — portable policy.yaml (block/require_approval/budget/audit/replay) across Claude Code hooks, MCP shims, OpenAI Agents guardrails, Python/TS/Java runtimes; Engram durable memory layer (temporal KG, MCP/REST/Python/Java/Spring). Cloud option. [web:25](https://github.com/jamjet-labs/jamjet)  
  **Post-May22 delta**: Rapid releases — python-sdk-v0.8.7 (Jun 2 2026, record_outcome etc.), v0.8.6 (May19), v0.8.5 (May14), earlier May v0.8.x series (demo CLI launch for unsafe-tool/approval/budget/MCP-policy, evidence/ dir, audit schema, openai_guardrail integration). Focus shift to "safety layer for AI agents" + durability + cross-adapter policy. Low public star count in snapshot but active commits (recent 6d/24h). [releases fetch + web:25,29,33]

- **Asqav** (asqav.com, github.com/jagmarques/asqav-sdk + asqav-mcp): "Evidence layer" — ML-DSA-65 (FIPS204 quantum-safe) signatures + hash chain + RFC3161 timestamps per action, self-hosted signer option (keys/payloads local), public verification (no account), policy gates/approvals, compliance receipts/export (EU AI Act/DORA/SOC2 mapping, 10yr+ retention framing), <1ms signing, guardrails (prompt inj/PII/secrets). MCP + SDK (Py/TS), integrations (Haystack, Dify, pytest plugin). [web:15](https://www.asqav.com/) [web:16](https://www.helpnetsecurity.com/2026/04/09/asqav-ai-agent-audit-trail/)  
  **Post-May22 delta**: GitHub activity (3d ago), marketplace/plugin listings (Dify/Haystack), pytest-asqav, ongoing multi-agent audit roadmap per Apr coverage. Live site with agent/incident/audit metrics. Strong A2 (crypto chain + legal timestamps) + compliance angle. [web:17-24 + site fetch]

- **Preloop** (preloop.ai, github.com/preloop/preloop): Open-source (Apache2 core) AI agent control plane — MCP firewall (allow/deny/require-approval/require-justification + per-param CEL YAML policies), AI model gateway (budgets/attribution), human approvals (mobile/Slack/Mattermost/email), runtime sessions + audit trails. Self-host + CLI `agents discover` for zero-SDK onboard of Claude Code/Cursor/etc (rewrites endpoints). Positioned as OSS alt to AWS Bedrock AgentCore; AI Act readiness artifacts. [web:35](https://github.com/preloop/preloop) [web:36](https://preloop.ai/)  
  **Post-May22 delta**: Active docs/product presence (ProductHunt/LinkedIn/IG/YT/Reddit), emphasis on MCP-native + unified gateway+firewall+approvals+observability vs stitched products. Recent crawls/docs. [web:37-44 + site fetch]

- **Cordum** (github.com/cordum-io/cordum): "Open agent control plane" / deterministic governance layer for probabilistic agents. Pre-exec policy enforcement, approval gates, audit trails. Full Go stack (API gateway, scheduler, safety-kernel, workflow-engine, context-engine, NATS/Redis, dashboard); CAP protocol (governance vs MCP tool-calling); strict provenance (resolved approval audit event + action_hash required, not just requested); Cordum Edge (local compliance firewall for Claude Code: hook + agentd + redacted evidence export); quickstart (docker/k8s/helm), cosign-signed images, multi-arch. HN Show HN earlier (~4mo), 481★ ref, active 2d ago. [web:45](https://github.com/cordum-io/cordum) [web:46](https://news.ycombinator.com/item?id=46667812)  
  **Post-May22 delta**: Ongoing commits/activity (2d), Edge + provenance strictness + full plane emphasis. Fits "new verifiable-autonomy entrant" profile (deterministic layer + Edge local + receipt-like evidence). Deploy artifacts ~May6 but post-sweep visibility/momentum. [web:47-49 + fetch]

- **SystemPrompt** (systemprompt.io, github.com/systempromptio/...): Self-hosted governance layer (Rust binary template + 30-crate core) for Claude Code/MCP agents — authn/authz, rate limiting, audit (16 event hooks / 5 trace points, full decision chain), cost controls, policy enforcement. Curated awesome-ai-agent-governance list. Comparisons to AGT (Apr 2026). Focus on "audit every policy evaluation path" for regulated. [web:50](https://github.com/systempromptio/awesome-ai-agent-governance) [web:51](https://systemprompt.io/guides/systemprompt-vs-microsoft-agent-governance)  
  **Post-May22 delta**: Listings/activity (1d ago), core/template packaging. [web:52-54]

- **Broader / new signals**: Academic counterfactual eval advancing (CAIR: Counterfactual-based Agent Influence Ranker for agentic workflows, EMNLP 2025; ICLR 2026 MALGAI workshop: EvoCF multi-agent evolutionary counterfactual planning, LUMINA long-horizon oracle counterfactual, Project Ariadne structural causal faithfulness audit via counterfactuals, etc.). VoltAgent 2026 awesome papers list. Enterprise: AvePoint AgentPulse (multi-cloud discovery/policy/lifecycle), Zenity (positioned "company to beat" in AI Agent Governance, Mar 2026 checklist). MCP governance theme dominant. No public hits for "MAGMA receipt", WD-specific "solver-growth substrate", "hex competitive promotion", or full "synthetic adversarial corpus" in governance products. [web:11,12,55-59]

- **Unverifiable as direct WD parity**: No evidence (public or indexed) of any rival shipping autonomous low-risk solver/mined-solver growth lifecycle + deterministic-first inner loop + multi-instance MAGMA-style flywheel + A3 counterfactual eval substrate + hex topology + synth adv corpus in one verifiable package. Rivals center on governance overlays (A1/A2/A5/A6/A7/A8 strengths).

## 2. Honest Read of Where WD Stands

WD substrate (deterministic solver-first routing + MAGMA provenance/replay + autonomous low-risk growth within allowlist + offline zero-provider proofs + restart continuity + hex + V12 A3 counterfactual + synth corpus + multi-instance flywheel) remains a distinct position vs. the crowded "agent governance / control plane / MCP firewall" layer (policy pre-exec gates, signed/chain audit receipts, approvals, observability, framework adapters, some crypto). 

Last sweep (May-20 pilot + May-22 local-check matrix) already noted: A3/A4 as must-win (rivals audit/replay/lifecycle but not solver-growth or counterfactual delta); A1/A2 contested; A6/A7/A8 ceded (adapters, public crypto, policy portability). Post-sweep moves (AGT v4 consolidation+replay+TEE+Entra, JamJet rapid safety SDKs+Engram, Cordum full plane+Edge+strict provenance, Preloop unified OSS MCP+approvals+gateway, Asqav/SystemPrompt crypto/audit depth) show the governance layer commoditizing and maturing fast, with Microsoft legitimacy + OSS self-host/Edge options proliferating. WD's internal PROVEN matrix (offline substrate, zero-provider, autonomous growth within 6 families, restart, etc.) is not directly contested on those exact axes in public rival claims.

**Risks**: Adapter friction and policy-lang gaps persist (rivals multi-lang + OPA/Cedar/CEL/MCP-native onboarding claims); basic verifiable audit/gating now table-stakes (rivals ship signed chains + public verify + approvals). If V12 (MAGMA receipt, A3, flywheel, synth corpus, hex promo) slips or lacks public pinned evidence, "unique full substrate" claim weakens. Opportunity: rivals are mostly *on top of* frameworks; WD owns the verifiable growth engine underneath.

No overclaim: rivals have real shipped artifacts and momentum on A1/A2/A5/A6 dimensions; WD differentiation is engineering-substrate + low-risk autonomy invariants, not yet fully productized in the governance conversation.

## 3. 3-5 Concrete Recommended WD Actions (measurable milestones, S/M/L effort; advisory only — no merge instructions)

1. **S (small, 1-2 weeks)**: Refresh public-doc snapshot + rival local-check matrix (or new 2026-06 dated companion) incorporating post-May22 evidence from GitHub changelogs/releases/pages for AGT v4, JamJet 0.8.7, Cordum Edge/provenance, Preloop onboarding, Asqav/SystemPrompt integrations. Explicitly call out staleness reset and any new Cordum/SystemPrompt rows.  
   **Milestone**: PR with updated/added .md + .json under docs/benchmarks/ or rival_benchmarks/; delta table vs May-20 pilot; no local smoke claims without artifacts.

2. **S/M (small-medium, 2-4 weeks)**: Produce and publish fresh measurable evidence for A3 (counterfactual eval delta) + MAGMA receipt (per V12 roadmap) as pinned artifacts (JSON + verifier). Update pilot/matrix axes.  
   **Milestone**: `tools/run_v12_a3...` (or equivalent) + receipt bundle artifact committed + offline-verifiable; pilot read refreshed with PROVEN/MEASURED labels where earned; cross-ref in COMPETITIVE_EVIDENCE_MATRIX.

3. **M (medium, 4-8 weeks)**: Deliver at least one (ideally 2) pinned rival-side local evidence manifest + smoke artifact for a key rival (e.g., AGT policy-deny + fail-closed replay; JamJet or Preloop MCP allow/deny/approval), satisfying the "cloud_dependency=false + smoke_result=passed" bar from the May-22 matrix — or explicit waiver in the pilot doc.  
   **Milestone**: docs/benchmarks/rival_local_checks/<rival>.json + artifact updated/passing; matrix row flips from not_configured/cloud_dependent; reproduction command documented.

4. **M/L (medium-large, 6-12+ weeks)**: Advance V12 multi-instance flywheel + synthetic adversarial corpus with public receipt + coverage metrics (tie to A3/A4). One cross-instance share manifest verified end-to-end; corpus expanded with new cases exercising counterfactual/growth deltas.  
   **Milestone**: tools/export/import_magma... exercised + verified artifact; corpus vN + eval report published under docs/benchmarks/ or runs/; matrix/roadmap note linking to measured flywheel + corpus growth.

5. **L (large/ongoing)**: Establish lightweight quarterly "substrate delta" advisory note (this format) tracking governance-layer commoditization (Cordum CAP, AGT replay/TEE, SystemPrompt trace depth, Preloop unified OSS, academic counterfactual productization signals) vs. WD unique claims. Keep ≤14-day freshness target for competitor matrix per DREAM_MODE_AGENDA.  
   **Milestone**: First follow-up note by ~2026-09; tracked in idle-loop or dream seeds; no new overclaims.

All actions respect: ground in public artifacts/URLs; prefer smallest safe patch; no direct merge instructions; bridge consensus where applicable.

## 4. Confidence

**medium** — Grounded in live web results (2026-dated Microsoft blog, GitHub READMEs/CHANGELOGs/releases, company sites, HN, academic arXiv/ICLR refs) + internal last-sweep docs (May-20 pilot/snapshot + May-22 matrix). X search low-signal (term ambiguity). No rival local execution performed (per pilot spirit + scope). Some rivals (Cordum/SystemPrompt) have less "sweep history" so delta partly first-look. WD internals (e.g., exact A3 proof status) taken from repo docs without re-running. Academic vs. shipped product gap noted. Unverifiable: exact "no one matches full WD substrate" is absence-of-evidence (search-limited); real-world deployment traction of rivals not measured.

Sources cited inline; full tool trace in session. This is advisory research only.

---

*Report written to reports/grok-scout-1-wd-competitor-delta-202606.md per task. All claims URL- or artifact-grounded; no privacy-canary or forbidden paths touched.*