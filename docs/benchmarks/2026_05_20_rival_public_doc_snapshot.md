# Rival Public-Doc Snapshot - 2026-05-20

This file is a freshness companion to
`docs/benchmarks/2026_05_20_competitor_axis_pilot.md`. The pilot's
`rival_side_local_checks_required` table still lists all four rivals as
`not_run` — that part of the pilot is unchanged.

What this snapshot adds is **verbatim retrieval evidence** that the public
claims used to classify each rival on the A1–A9 axis read are real public
claims as of 2026-05-20, not paraphrases or stale recall. The pilot rule was
that the public-doc rows expire on 2026-06-03 unless refreshed; this is the
refresh.

Each rival cell below cites the URL and the retrieval date returned by the
WebFetch/WebSearch tooling. Where a fetch returned an error (Asqav direct
home page), the snapshot falls back to publicly indexed coverage rather
than removing the rival.

## Method

* `WebFetch` retrieved JamJet (jamjet.dev), Microsoft AGT
  (github.com/microsoft/agent-governance-toolkit), and Preloop
  (preloop.ai) directly on 2026-05-20.
* Asqav (www.asqav.com) returned HTTP 403 to the WebFetch tool and the
  PyPI project page returned a JavaScript error; Asqav rows below cite
  third-party coverage indexed by WebSearch on 2026-05-20 (Help Net
  Security, DEV Community, and the Asqav-published SDK README on
  GitHub).
* No rival SDK was installed or run. This snapshot does not satisfy the
  `rival_side_local_checks_required` preconditions in the pilot.

## Rivals

### JamJet — jamjet.dev

* Distribution: `pip install jamjet`, `npm i`, Maven Central (Spring Boot
  Starters), MCP shim, hosted `app.jamjet.dev`, OSS GitHub repo.
* A1 pre-execution gate: explicit — "a 4-level policy hierarchy blocks the
  call before execution."
* A2 receipt binding: explicit — `action_receipt.json` with `receipt_hash`
  and `arguments_hash` fields, "signed & verified", "signed, verifiable by
  anyone, independent of your agent framework."
* A3 counterfactual delta: not present — only "replays the event log and
  resumes at the failed node" for crash recovery, not counterfactual /
  scenario delta.
* A4 solver-growth lifecycle: not present.
* A5 governance throughput / peer review metrics: not present.
* A6 adapter distribution friction: very strong — multi-language SDKs +
  MCP + framework-agnostic ("Keep LangGraph, CrewAI, Claude Code, MCP,
  OpenAI Agents SDK, or Spring AI — add JamJet").
* A7 cryptographic verification: signed receipts described, no algorithm
  named on the public page snapshot.
* A8 policy language portability: YAML only ("One `policy.yaml` in
  `~/.jamjet/`"); no OPA/Cedar/CEL grammar visible.
* A9 offline / local-only: claimed — local Python/Java runtimes,
  "cloud-neutral" / on-prem.

### Microsoft Agent Governance Toolkit — github.com/microsoft/agent-governance-toolkit

* Distribution: `pip install agent-governance-toolkit[full]`,
  `@microsoft/agent-governance-sdk` on npm, NuGet (.NET), Rust cargo, Go
  module, GitHub Copilot CLI integration, Docker Compose.
* A1 pre-execution gate: explicit — "Every tool call, resource access,
  and inter-agent message is evaluated against policy *before* execution
  — deterministic, sub-millisecond, and auditable", "Fail-closed by
  default — if the engine errors, the action is denied."
* A2 receipt / tamper evidence: explicit — "Tamper-evident Merkle-chained
  audit logs. Reconstructible Decision BOMs from observability signals."
* A3 counterfactual delta: partial — "replay debugging" is mentioned under
  Agent SRE, but no counterfactual delta language.
* A4 solver-growth lifecycle: agent lifecycle is described (provisioning,
  rotation, orphan detection, decommissioning), but no shadow/canary or
  promotion described.
* A5 governance throughput: "35K ops/sec concurrent" and "0.012ms p50 for
  single rule" cited.
* A6 adapter distribution friction: very strong — multi-language SDKs +
  framework support for LangChain, CrewAI, AutoGen, OpenAI Agents,
  Google ADK, Semantic Kernel, AWS Bedrock, "and 20+ more."
* A7 cryptographic verification: explicit — "Ed25519 + quantum-safe
  ML-DSA-65 agent credentials."
* A8 policy language portability: explicit — "YAML, OPA/Rego, and Cedar
  policy languages."
* A9 offline / local-only: implied (Python middleware, sub-millisecond
  local), not explicitly claimed.

### Preloop — preloop.ai

* Distribution: self-hostable on macOS/Linux via `curl` installer,
  Apache 2.0 OSS core, hosted cloud trial. On-prem capable.
* A1 pre-execution gate: explicit — "MCP Firewall for tool access
  control", "allow, deny, require-approval, and require-justification
  rules for any MCP tool", "per-parameter conditions."
* A2 receipt / audit: audit-only — "Every action is logged with
  attempted tool, inputs, matched policy, decision, approver, model
  spend, and outcome." No cryptographic signing claim.
* A3 counterfactual delta: not present.
* A4 solver-growth lifecycle: not present.
* A5 governance throughput: not explicitly metric'd; "team-based
  approvals with quorum" listed under Enterprise Edition only.
* A6 adapter distribution friction: strong — Apache 2.0, self-host,
  on-prem, MCP-native.
* A7 cryptographic verification: not claimed.
* A8 policy language portability: explicit — "Policy-as-code in YAML
  with CEL expressions, ordered rules with priority."
* A9 offline / local-only: self-host is supported; explicit offline-only
  claim not made.

### Asqav — www.asqav.com (direct fetch 403; this row uses public coverage)

* Distribution: `asqav-sdk` (Python) and `asqav-mcp` (MCP server), both
  open-source on GitHub under user `jagmarques`. EU AI Act Article 12
  positioning (tamper-evident logging required by August 2026).
* A1 pre-execution gate: claimed (policy enforcement on each action) per
  SDK README and Help Net Security coverage; no per-call gate language
  matched verbatim in the snapshot window.
* A2 receipt / tamper evidence: very strong, central claim — every agent
  action signed with ML-DSA-65, entries linked into a hash chain, each
  signature carries an RFC 3161 timestamp.
* A3 counterfactual delta: not claimed in the snapshot window.
* A4 solver-growth lifecycle: not claimed in the snapshot window.
* A5 governance throughput: not claimed in the snapshot window.
* A6 adapter distribution friction: strong on the integration side —
  framework support listed for LangChain, CrewAI, LiteLLM, Haystack, and
  OpenAI Agents SDK; MCP server distributed separately.
* A7 cryptographic verification: explicit and ahead — "ML-DSA-65,
  standardized under FIPS 204, designed to remain secure against quantum
  computing attacks", "10+ year retention" framing tied to EU AI Act.
* A8 policy language portability: not the centerpiece of the public
  claim; CEL/Rego/Cedar parity not documented in the snapshot window.
* A9 offline / local-only: SDK + MCP server form-factor implies local
  execution; no offline-only claim located in the snapshot window.

## Axis Read Refresh (2026-05-20)

This refresh does not change the bridge-consensus-sealed `declared_position`
column in the pilot. It restates the *evidence* basis for each axis.

| Axis | WD posture | Strongest rival evidence (2026-05-20 snapshot) |
|---|---|---|
| A1 pre-execution gate | WriteRCOGate route + receipt v1 + RCO artifact v0 | All four rivals make a strong public claim. Commoditized. |
| A2 receipt + tamper evidence | MAGMA receipt v1 with sha256 chain; optional/null signature | JamJet signed receipts; AGT Merkle-chain + Ed25519 + ML-DSA-65; Asqav ML-DSA-65 + RFC 3161 chain. |
| A3 counterfactual delta | run_pdam_counterfactual_demo + 15-case adversarial corpus + receipt-bound eval report (PR #507) | No rival claims counterfactual delta in the snapshot window. AGT mentions replay debugging only. JamJet replays for crash recovery only. |
| A4 solver-growth lifecycle | activate/sign/revoke/quarantine + auto_promotion_engine (both receipt_bound) | No rival claims solver-growth lifecycle. AGT has agent lifecycle (provisioning/rotation/decommissioning), not solver promotion. |
| A5 governance throughput | governance_throughput_report v0 (8 metrics) | AGT cites 35K ops/sec + 0.012ms p50. Throughput-density advantage to AGT. |
| A6 adapter distribution friction | Python-only currently | All four rivals stronger: JamJet/AGT multi-language; Preloop self-host + curl + MCP; Asqav SDK + MCP separately. |
| A7 public cryptographic verification | signature envelope optional/null in current MAGMA receipt v1 | Asqav and AGT both quantum-safe ML-DSA-65; JamJet signed receipts. WD ceded today, as previously declared. |
| A8 standard policy language portability | code-enforced charter, internal policy surface | AGT YAML/OPA/Rego/Cedar; Preloop YAML+CEL; JamJet YAML. WD ceded today, as previously declared. |
| A9 local offline solver substrate | offline-only operator stance + Docker network-none + zero-provider inner-loop evidence | JamJet claims cloud-neutral / on-prem; rivals broadly self-host capable. Contested. |

## What Changes In The Pilot

* Pilot's `rival_local_checks_status` interpretation: "all
  public_doc_claim, 0/4 rival local checks run yet" stays. This
  snapshot does not flip any rival to `measured`.
* Pilot's freshness invariant: rival public-doc rows were due to expire
  on 2026-06-03. This snapshot resets that clock to **2026-06-03 +
  measured from 2026-05-20** (today). The next refresh should land by
  2026-06-03 or the rows revert to stale.
* Pilot's two must-win axes (A3 counterfactual delta, A4 solver-growth
  lifecycle): no rival in the snapshot window publicly claims either.
  This is the strongest current external read of the substrate-first
  thesis.
* Pilot's three ceded axes (A6 adapter friction, A7 crypto verify, A8
  policy lang): all confirmed ceded by this snapshot. No reinterpretation.

## Sources

* JamJet: https://jamjet.dev/ (WebFetch 2026-05-20)
* Microsoft AGT: https://github.com/microsoft/agent-governance-toolkit
  (WebFetch 2026-05-20)
* Preloop: https://preloop.ai/ (WebFetch 2026-05-20)
* Asqav: https://www.asqav.com/ (WebFetch 2026-05-20 — HTTP 403),
  fallback coverage:
  * https://www.helpnetsecurity.com/2026/04/09/asqav-ai-agent-audit-trail/
  * https://github.com/jagmarques/asqav-sdk
  * https://github.com/jagmarques/asqav-mcp
  * https://www.asqav.com/blog/posts/quantum-safe-signatures-fips-204-explained
  * https://dev.to/jagmarques/asqav-vs-microsoft-agent-governance-toolkit-what-is-the-difference-598d
