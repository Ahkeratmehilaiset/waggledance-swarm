# V12 Competitor Axis Pilot - 2026-05-20

Status: scope ready, not consensus-grade yet.

This pilot is the bridge consensus output for task
`strat-direction-consensus-v12-2026-05-20`. It answers the operator's
V12/FSD-12 direction question with a falsifiable benchmark shape:

WD is treated as a verifiable solver-growth substrate, not as another
action firewall. Gateway and adapter work remain distribution strategy unless
the substrate evidence threshold fails.

## Consensus Guardrails

Claude round 3 identified six failure modes. Codex accepted them in round 5.
This pilot is valid only if all six are preserved:

1. Include at least two axes where WD is currently `NOT_CLAIMED` or
   `INFERRED`, not only axes where WD is already `PROVEN`.
2. Include at least one locally reproducible rival-side check per rival, or
   mark the pilot as not consensus-grade until those checks exist.
3. Declare staleness and refresh cadence up front.
4. Include Microsoft AGT in the rival set.
5. Declare ceded vs contested axes before scoring.
6. Predeclare a rejection threshold for the "60-75 percent unique full
   substrate" claim before interpreting results.

## Method

This file is not a live rival benchmark. It is a scope and evidence pilot.

WD rows may cite local code, tests, merged PRs, and existing benchmark
artifacts. Rival rows are public documentation claims unless a local check is
listed and run. Any row without a rival-side local check is not
consensus-grade.

Current public sources checked on 2026-05-20:

- JamJet: https://jamjet.dev/ and https://docs.jamjet.dev/
- Asqav: https://www.asqav.com/ and https://pypi.org/project/asqav/
- Microsoft AGT: https://github.com/microsoft/agent-governance-toolkit
- Preloop: https://preloop.ai/ and https://docs.preloop.ai/

Staleness policy:

- Public rival documentation rows expire on 2026-06-03.
- WD local `PROVEN` rows expire on 2026-06-19 unless rerun or tied to CI.
- WD or rival `MEASURED` rows expire on 2026-06-03 unless rerun.

## Ceded And Contested Axes

| Axis | Position | Reason |
|---|---|---|
| A1 pre-execution action gate | contested | WD has WriteRCOGate and receipt binding, but AGT/JamJet/Preloop also target this category directly. |
| A2 receipt binding and tamper evidence | contested | WD has MAGMA receipt/eval/RCO binding; Asqav is stronger on cryptographic signing. |
| A3 counterfactual evaluation delta | must-win | This is the main substrate differentiator over forensic replay and audit trails. |
| A4 solver-growth lifecycle | must-win | Shadow/canary/live solver promotion is WD identity, not firewall identity. |
| A5 governance throughput | contested, measured partial today | Governance metrics are now surfaced, but insufficient/deferred statuses remain visible. |
| A6 adapter adoption friction | ceded today | JamJet and Preloop currently have stronger "keep your stack" posture. |
| A7 public cryptographic verification | ceded today | Asqav and AGT public claims are ahead of WD's current optional/null signature envelope. |
| A8 standard policy language portability | ceded today | AGT claims YAML/OPA/Rego/Cedar; Preloop claims CEL; WD policy surface is not yet equivalent. |
| A9 local offline solver substrate | contested | WD has strong local/offline evidence; rivals are mostly governance/control-plane layers. |

At least three weak/ceded axes are included: A5, A6, A7, and A8. This prevents
axis selection from proving the conclusion by construction.

## Rejection Threshold

The "WD is 60-75 percent unique as a full substrate" claim is supported for
this measurement window only if all of the following hold:

- WD has local `PROVEN` or `MEASURED` evidence on both must-win axes A3 and A4.
- WD has local `PROVEN` or `MEASURED` evidence on at least 4 of the 6 contested
  axes A1, A2, A3, A4, A5, and A9.
- At least two close rivals lack locally reproducible equivalent evidence on
  A3 or A4 during the same window.
- No ceded axis is reinterpreted as a WD win after the fact.

If any must-win axis fails, or WD wins fewer than 4 contested axes, the
60-75 percent uniqueness claim is rejected for this window and the fallback
position becomes:

WD as a MAGMA-backed governance recorder/gateway for existing agent stacks,
with full substrate as an upgrade path.

## Current Axis Read

| Axis | WD status now | Rival read now | Current interpretation |
|---|---|---|---|
| A1 action gate | local evidence exists via WriteRCOGate and PR #501 receipt adapter | AGT, JamJet, Preloop all claim pre-execution policy/gating | contested, no uniqueness claim |
| A2 receipt/tamper evidence | MAGMA receipt v1, EvaluationResult v0, RCO artifact v0, verifier and demos exist | Asqav claims ML-DSA-65 signatures, chain hash, public verify, policy gates | WD is improving, Asqav ahead on cryptographic envelope |
| A3 counterfactual delta | `tools/run_v12_a3_counterfactual_axis_proof.py` now reports one `MEASURED_LOCAL_PARTIAL` counterfactual row; supervisor demo pack verifies the receipt chain | rivals mostly claim audit/replay, not solver-policy counterfactual deltas | must-win; locally measured partial, not broad rival benchmark |
| A4 solver-growth lifecycle | `tools/run_v12_a4_solver_growth_axis_proof.py` reports `MEASURED_LOCAL_SYNTHETIC`: 6 registered solvers and 18/18 dispatch hits in a temporary ControlPlaneDB | rivals do not center solver-growth lifecycle | must-win and currently strongest WD axis, with shadow/canary/live metrics still incomplete |
| A5 governance throughput | `tools/governance_throughput_report.py` and `tools/show_v12_proof.py` surface event/task counts and metric status counts | rivals have approval/audit UX claims, but not WD bridge metrics | measured partial; not a WD win while some metrics remain insufficient/deferred |
| A6 adoption friction | WD adapter strategy not yet the leading path | JamJet and Preloop strongly claim drop-in onboarding/adapters | ceded today |
| A7 crypto public verification | WD signature fields exist but can be null/optional | Asqav and AGT claim stronger identity/signature stories | ceded today |
| A8 policy portability | WD has policy surface artifacts, but not public OPA/Cedar parity | AGT claims YAML/OPA/Rego/Cedar; Preloop claims CEL | ceded today |
| A9 offline substrate | existing matrix records Docker/network-none and zero-provider inner-loop evidence | rivals emphasize control-plane/governance rather than offline solver substrate | contested, likely WD-favorable |

## Rival-Side Local Checks Required

This pilot is not consensus-grade until these checks are either run locally or
explicitly waived as impossible for the window:

| Rival | Required local check |
|---|---|
| JamJet | Install or inspect a pinned OSS package/repo revision and run one policy/audit/replay smoke with no cloud dependency. |
| Asqav | Install or inspect a pinned SDK revision and run one local signing/verify or hash-chain smoke; if server dependency is required, mark as cloud-dependent. |
| Microsoft AGT | Install or inspect a pinned repo/package revision and run one policy deny smoke plus one fail-closed/error-path smoke. |
| Preloop | Install or inspect a pinned OSS component/repo revision and run one MCP allow/deny/approval smoke; if hosted service is required, mark as cloud-dependent. |

Until then, competitor cells are `public_doc_claim`, not measured results.

## Next Work

1. Run or explicitly waive the rival-side local checks above.
2. Produce the first measured axis table with separate columns for
   `wd_local_evidence`, `rival_public_doc_claim`, and `rival_local_check`.
3. Only then decide whether the 60-75 percent full-substrate uniqueness claim
   survives the window.

## Current Directional Conclusion

The current bridge consensus supports substrate-first as the working strategy,
but only provisionally.

The strongest current reason is not "WD has a write gate." That category is
crowded. The strongest reason is the combination of MAGMA receipt binding,
EvaluationResult, counterfactual evaluation, solver-growth lifecycle, offline
solver substrate, and operator-owned promotion evidence.

The conclusion becomes false if those must-win substrate axes are not measured
cleanly or if AGT/JamJet/Asqav/Preloop show equivalent local evidence on the
same axes during the same freshness window.
