# ASI 2026 Mapping for MAGMA Synthetic Adversarial Corpus

**Status:** advisory mapping, document-only
**Observed on:** 2026-06-01
**Repo baseline before gap expansion:** `0f865ba4133b77802d4780a24f112c10cf331a5f`
**Corpus:** `tests/fixtures/magma_adversarial_corpus/v0.json`

## Scope

This document cross-walks the current MAGMA synthetic adversarial corpus
defect taxonomy to the OWASP Top 10 for Agentic Applications 2026
(`ASI01` through `ASI10`).

It does not grant runtime authority, change promotion policy, modify
schemas, add cases, or alter validator behavior. It is an evidence map for
the five-ingredient roadmap item "Synthetic adversarial corpus" and a
backlog seed for later, separately reviewed hardening slices.

## Inputs

- OWASP GenAI Security Project, "OWASP Top 10 for Agentic Applications for
  2026":
  `https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/`
- OWASP PDF download for the same publication:
  `https://genai.owasp.org/download/52117/?tmstv=1765059207`
- Grok scout advisory memo dated 2026-05-31, which recommended an explicit
  ASI crosswalk, gap list, and later targeted case additions for ASI04,
  ASI07, ASI08, and ASI10.
- Current strict v0 corpus counts from
  `tools/validate_synthetic_adversarial_corpus.py` inputs.
- Claude/RCO bridge posture from the preceding critical-floor work: preserve
  fail-closed invariants, keep this slice document-only, and avoid coupling
  ASI labels to runtime authority until a later reviewed implementation.

## Current Corpus Counts

The strict v0 corpus currently has 59 cases across 15 defect types.

| Defect type | Cases |
|---|---:|
| `charter_violation` | 5 |
| `correlated_review_trap` | 3 |
| `evidence_spoofing` | 8 |
| `fail-open` | 3 |
| `governance_bypass` | 3 |
| `hallucinated-success` | 2 |
| `path_escape` | 2 |
| `payload_leak` | 5 |
| `policy_bypass` | 3 |
| `privilege_leak` | 3 |
| `regression-process` | 2 |
| `risk_escalation` | 3 |
| `spec-gaming` | 3 |
| `subtle_drift` | 9 |
| `tool_argument_abuse` | 5 |

PR #800 added a strict full-coverage floor of two cases for the six
highest-risk critical defect classes. Expansion fixtures remain permissive;
this mapping is about the strict v0 corpus.

The first explicit ASI gap expansion added five strict-v0 seeds without
changing the defect taxonomy: `case:adv:evidence_spoofing:008`
(agentic supply-chain deception), `case:adv:tool_argument_abuse:005`
(execution boundary), `case:adv:governance_bypass:003` (inter-agent
spoofing), `case:adv:fail_open:003` (cascade propagation), and
`case:adv:spec_gaming:003` (runaway/reward-hacking framing).

## ASI Coverage Rollup

Coverage means the existing WD defect type has a meaningful adversarial
relationship to the ASI class. It is not a claim that the current corpus
fully exercises every real-world attack pattern in that OWASP category.

| ASI ID | OWASP category | Current coverage | WD defect types |
|---|---|---|---|
| ASI01 | Agent Goal Hijack | Strong | `charter_violation`, `policy_bypass`, `risk_escalation`, `spec-gaming`, `subtle_drift` |
| ASI02 | Tool Misuse and Exploitation | Strong | `tool_argument_abuse`, `path_escape`, `payload_leak`, `fail-open` |
| ASI03 | Identity and Privilege Abuse | Strong | `privilege_leak`, `governance_bypass`, `payload_leak` |
| ASI04 | Agentic Supply Chain Vulnerabilities | Partial, explicit seed present | `evidence_spoofing`, `governance_bypass`, `spec-gaming` |
| ASI05 | Unexpected Code Execution (RCE) | Partial, explicit seed present | `path_escape`, `tool_argument_abuse`, `fail-open` |
| ASI06 | Memory and Context Poisoning | Strong | `subtle_drift`, `regression-process`, `evidence_spoofing`, `correlated_review_trap` |
| ASI07 | Insecure Inter-Agent Communication | Partial, explicit seed present | `correlated_review_trap`, `governance_bypass`, `hallucinated-success` |
| ASI08 | Cascading Failures | Partial, explicit seed present | `fail-open`, `regression-process`, `risk_escalation`, `tool_argument_abuse` |
| ASI09 | Human-Agent Trust Exploitation | Strong | `hallucinated-success`, `evidence_spoofing`, `correlated_review_trap`, `governance_bypass` |
| ASI10 | Rogue Agents | Partial, explicit seed present | `governance_bypass`, `privilege_leak`, `policy_bypass`, `risk_escalation`, `spec-gaming` |

The corpus has at least one mapped defect type for every ASI category. The
highest-value follow-up is not to add labels everywhere; it is to add a small
number of explicit cases where coverage is currently indirect.

## Defect-Type Crosswalk

| WD defect type | Cases | Primary ASI mapping | Rationale |
|---|---:|---|---|
| `charter_violation` | 5 | ASI01, ASI10 | Tests attempts to redirect the agent away from declared operator and charter constraints. This is direct goal hijack coverage and partial rogue-agent drift coverage. |
| `correlated_review_trap` | 3 | ASI06, ASI07, ASI09 | Models review-process traps where context, reviewer coordination, or trusted review framing can steer decisions. It overlaps context poisoning, inter-agent communication, and trust exploitation. |
| `evidence_spoofing` | 8 | ASI04, ASI06, ASI09 | Targets fabricated or misleading evidence that can poison context or exploit trust in presented proof. It now includes an explicit agentic supply-chain descriptor deception seed. |
| `fail-open` | 3 | ASI02, ASI05, ASI08 | Covers unsafe continuation when a gate, tool, or execution check cannot prove safety. It now includes a second-order cascade propagation seed. |
| `governance_bypass` | 3 | ASI03, ASI04, ASI07, ASI09, ASI10 | Tests attempts to route around governance controls. It now includes an explicit peer-agent/bridge spoofing seed while preserving exact-head governance boundaries. |
| `hallucinated-success` | 2 | ASI07, ASI09 | Captures false success claims that can mislead humans or peer agents into accepting unsafe state. |
| `path_escape` | 2 | ASI02, ASI05 | Exercises unsafe path and boundary handling that could turn a legitimate tool or artifact operation into unintended filesystem or execution impact. |
| `payload_leak` | 5 | ASI02, ASI03 | Tests data exposure through tool use, inherited access, or unsafe output handling. |
| `policy_bypass` | 3 | ASI01, ASI10 | Captures attempts to route around policy while preserving an appearance of compliance. This is goal hijack coverage and partial rogue-agent/spec drift coverage. |
| `privilege_leak` | 3 | ASI03, ASI10 | Exercises exposure or misuse of privileged context. It directly maps to identity and privilege abuse and partially to rogue behavior when authority is retained or reused. |
| `regression-process` | 2 | ASI06, ASI08 | Tests whether process drift reintroduces known failures. It overlaps persistent context/process poisoning and cascade prevention. |
| `risk_escalation` | 3 | ASI01, ASI08, ASI10 | Captures small local deviations that compound into higher-risk behavior. It maps to goal drift, cascading failures, and rogue autonomy. |
| `spec-gaming` | 3 | ASI01, ASI04, ASI10 | Tests reward/spec loopholes and misleading compliance. It now includes a runaway candidate loop/reward-hacking seed. |
| `subtle_drift` | 9 | ASI01, ASI06 | Covers gradual deviation from intended objectives or context. It is strong coverage for goal hijack and memory/context poisoning style risks. |
| `tool_argument_abuse` | 5 | ASI02, ASI05, ASI08 | Exercises unsafe tool argument construction, which can become legitimate-tool misuse, execution risk, or downstream cascade input. It now includes an explicit dry-run execution-boundary seed. |

## Follow-Up Gaps

The current corpus is useful but still curated and mostly local to WD's
existing gate surfaces. The following gap list should drive the next small
implementation slices:

| Gap | Current state | Smallest safe next slice |
|---|---|---|
| ASI04 explicit supply-chain deception | First explicit seed present in `case:adv:evidence_spoofing:008`; broader package provenance remains partial. | Add 1-2 more cases for malicious prompt-package or registry deception only if the review needs broader supply-chain variants. |
| ASI05 execution boundary | First contained shell/eval-like seed present in `case:adv:tool_argument_abuse:005`; no code is executed. | Add a second path/eval boundary variant if future reviewers need more than one execution-boundary shape. |
| ASI07 inter-agent spoofing | First synthetic peer-agent/bridge spoofing seed present in `case:adv:governance_bypass:003`; it does not touch live bridge state. | Add a correlated-review variant only if spoofed identity and correlated summary risk need to be tested separately. |
| ASI08 cascade propagation | First second-order cascade seed present in `case:adv:fail_open:003`. | Add a regression-process cascade variant if future promotion-gate work needs process-specific cascade coverage. |
| ASI10 rogue/collusion/runaway | First runaway/reward-hacking seed present in `case:adv:spec_gaming:003`. | Add a collusion framing case only if consensus artifacts need explicit two-agent collusion coverage. |

## Recommended Use

- Treat this file as the human-readable source for an eventual
  machine-readable ASI rollup.
- Keep the validator authoritative for existing defect-type coverage.
- Do not make ASI labels promotion gates until the labels are represented in
  schema, fixture, and test updates in a separate PR.
- When adding cases, prefer preserving the existing 15 defect types until a
  real need for a new defect type is proven.
- Preserve the held-out split and strict critical floors introduced before
  this mapping.

## Summary

WD already covers the shape of all ten OWASP ASI 2026 categories, with strong
coverage for goal hijack, tool misuse, privilege abuse, memory/context
poisoning, and human-agent trust exploitation. The former weak spots now have
first explicit strict-v0 seeds, but broader supply-chain, inter-agent,
cascade, and rogue-agent variants remain future work. No broader ASI-aware
runtime reporting or promotion-gate behavior should be attempted until those
labels are represented in schema, fixture, and test updates in a separate
reviewed PR.
