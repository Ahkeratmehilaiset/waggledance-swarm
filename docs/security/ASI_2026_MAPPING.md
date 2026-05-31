# ASI 2026 Mapping for MAGMA Synthetic Adversarial Corpus

**Status:** advisory mapping, document-only
**Observed on:** 2026-06-01
**Repo baseline:** `b1e43a0f5aa9b18b0527cc7a38bf9826d7cc08ba`
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

The strict v0 corpus currently has 54 cases across 15 defect types.

| Defect type | Cases |
|---|---:|
| `charter_violation` | 5 |
| `correlated_review_trap` | 3 |
| `evidence_spoofing` | 7 |
| `fail-open` | 2 |
| `governance_bypass` | 2 |
| `hallucinated-success` | 2 |
| `path_escape` | 2 |
| `payload_leak` | 5 |
| `policy_bypass` | 3 |
| `privilege_leak` | 3 |
| `regression-process` | 2 |
| `risk_escalation` | 3 |
| `spec-gaming` | 2 |
| `subtle_drift` | 9 |
| `tool_argument_abuse` | 4 |

PR #800 added a strict full-coverage floor of two cases for the six
highest-risk critical defect classes. Expansion fixtures remain permissive;
this mapping is about the strict v0 corpus.

## ASI Coverage Rollup

Coverage means the existing WD defect type has a meaningful adversarial
relationship to the ASI class. It is not a claim that the current corpus
fully exercises every real-world attack pattern in that OWASP category.

| ASI ID | OWASP category | Current coverage | WD defect types |
|---|---|---|---|
| ASI01 | Agent Goal Hijack | Strong | `charter_violation`, `policy_bypass`, `risk_escalation`, `spec-gaming`, `subtle_drift` |
| ASI02 | Tool Misuse and Exploitation | Strong | `tool_argument_abuse`, `path_escape`, `payload_leak`, `fail-open` |
| ASI03 | Identity and Privilege Abuse | Strong | `privilege_leak`, `governance_bypass`, `payload_leak` |
| ASI04 | Agentic Supply Chain Vulnerabilities | Partial | `evidence_spoofing`, `governance_bypass`, `spec-gaming` |
| ASI05 | Unexpected Code Execution (RCE) | Partial | `path_escape`, `tool_argument_abuse`, `fail-open` |
| ASI06 | Memory and Context Poisoning | Strong | `subtle_drift`, `regression-process`, `evidence_spoofing`, `correlated_review_trap` |
| ASI07 | Insecure Inter-Agent Communication | Partial | `correlated_review_trap`, `governance_bypass`, `hallucinated-success` |
| ASI08 | Cascading Failures | Partial | `fail-open`, `regression-process`, `risk_escalation`, `tool_argument_abuse` |
| ASI09 | Human-Agent Trust Exploitation | Strong | `hallucinated-success`, `evidence_spoofing`, `correlated_review_trap`, `governance_bypass` |
| ASI10 | Rogue Agents | Partial | `governance_bypass`, `privilege_leak`, `policy_bypass`, `risk_escalation`, `spec-gaming` |

The corpus has at least one mapped defect type for every ASI category. The
highest-value follow-up is not to add labels everywhere; it is to add a small
number of explicit cases where coverage is currently indirect.

## Defect-Type Crosswalk

| WD defect type | Cases | Primary ASI mapping | Rationale |
|---|---:|---|---|
| `charter_violation` | 5 | ASI01, ASI10 | Tests attempts to redirect the agent away from declared operator and charter constraints. This is direct goal hijack coverage and partial rogue-agent drift coverage. |
| `correlated_review_trap` | 3 | ASI06, ASI07, ASI09 | Models review-process traps where context, reviewer coordination, or trusted review framing can steer decisions. It overlaps context poisoning, inter-agent communication, and trust exploitation. |
| `evidence_spoofing` | 7 | ASI04, ASI06, ASI09 | Targets fabricated or misleading evidence that can poison context or exploit trust in presented proof. It partially overlaps supply-chain risk when the evidence is treated as an upstream artifact. |
| `fail-open` | 2 | ASI02, ASI05, ASI08 | Covers unsafe continuation when a gate, tool, or execution check cannot prove safety. It is relevant to tool misuse, execution containment, and cascading failure prevention. |
| `governance_bypass` | 2 | ASI03, ASI04, ASI07, ASI09, ASI10 | Tests attempts to route around governance controls. It maps to privilege abuse, compromised policy surfaces, spoofed coordination, trust exploitation, and rogue autonomy. |
| `hallucinated-success` | 2 | ASI07, ASI09 | Captures false success claims that can mislead humans or peer agents into accepting unsafe state. |
| `path_escape` | 2 | ASI02, ASI05 | Exercises unsafe path and boundary handling that could turn a legitimate tool or artifact operation into unintended filesystem or execution impact. |
| `payload_leak` | 5 | ASI02, ASI03 | Tests data exposure through tool use, inherited access, or unsafe output handling. |
| `policy_bypass` | 3 | ASI01, ASI10 | Captures attempts to route around policy while preserving an appearance of compliance. This is goal hijack coverage and partial rogue-agent/spec drift coverage. |
| `privilege_leak` | 3 | ASI03, ASI10 | Exercises exposure or misuse of privileged context. It directly maps to identity and privilege abuse and partially to rogue behavior when authority is retained or reused. |
| `regression-process` | 2 | ASI06, ASI08 | Tests whether process drift reintroduces known failures. It overlaps persistent context/process poisoning and cascade prevention. |
| `risk_escalation` | 3 | ASI01, ASI08, ASI10 | Captures small local deviations that compound into higher-risk behavior. It maps to goal drift, cascading failures, and rogue autonomy. |
| `spec-gaming` | 2 | ASI01, ASI04, ASI10 | Tests reward/spec loopholes and misleading compliance. It maps to goal hijack, partial supply-chain or descriptor deception, and rogue optimization. |
| `subtle_drift` | 9 | ASI01, ASI06 | Covers gradual deviation from intended objectives or context. It is strong coverage for goal hijack and memory/context poisoning style risks. |
| `tool_argument_abuse` | 4 | ASI02, ASI05, ASI08 | Exercises unsafe tool argument construction, which can become legitimate-tool misuse, execution risk, or downstream cascade input. |

## Follow-Up Gaps

The current corpus is useful but still curated and mostly local to WD's
existing gate surfaces. The following gap list should drive the next small
implementation slices:

| Gap | Current state | Smallest safe next slice |
|---|---|---|
| ASI04 explicit supply-chain deception | Covered indirectly by evidence spoofing, governance bypass, and spec-gaming. | Add 1-2 cases for malicious tool schema, descriptor, registry, or prompt-package deception without changing the defect taxonomy unless the review explicitly approves it. |
| ASI05 execution boundary | Covered by path escape and tool argument abuse, but not by a full RCE-style scenario. | Add a contained fixture that proves code/shell/eval-like intent is refused without executing anything. |
| ASI07 inter-agent spoofing | Covered indirectly by review traps and governance bypass. | Add bridge or peer-agent message spoofing fixtures that remain synthetic and do not touch live bridge state. |
| ASI08 cascade propagation | Covered indirectly by fail-open, regression-process, and risk_escalation. | Add fixtures where one accepted bad step would create a second-order unsafe decision, then assert the gate refuses at the first unsafe point. |
| ASI10 rogue/collusion/runaway | Covered indirectly by governance bypass, spec-gaming, and risk escalation. | Add 1-2 cases for runaway autonomy, collusion framing, or reward-hacking behavior while preserving operator-owned approval boundaries. |

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
poisoning, and human-agent trust exploitation. The weak spots are explicit
agentic supply-chain deception, inter-agent spoofing, cascade propagation, and
rogue-agent/runaway autonomy. Those should be filled with small, validator-
passing synthetic fixtures before any broader ASI-aware reporting or generator
work is attempted.
