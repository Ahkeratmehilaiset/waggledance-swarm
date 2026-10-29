# Governance and development process

This file describes how the WaggleDance repository itself is developed and reviewed. It is not needed to run or use the software. The binding rules live in [`CLAUDE.md`](CLAUDE.md) and in the documents linked below; this page only collects them in one place and explains the vocabulary. It does not change any rule or any licence term.

## Glossary

Each definition is taken from a repository document, named in the Source column. Terms marked **(unknown)** have no definition in the repository at the time of writing, and none is invented here.

| Term | Meaning | Source |
|------|---------|--------|
| Bridge | The append-only event log under `.agent-bridge/` through which the development agents coordinate. | this page, section "Development agent bridge" (moved from the README) |
| Lead and Tools lanes | The two Codex agents (`codex-lead-1`, `codex-tools-1`). Their concurrence on a change is the "build consensus" needed for a merge. | `CLAUDE.md`, Rule 9a |
| RCO | A recognized Claude reviewer identity (`claude-rco-1`, `claude-rco-2`, `claude-rco-3`). An explicit pass from an RCO bound to the exact PR head is required for a merge, and an unretracted finding from any RCO blocks it. What the letters stand for is **(unknown)**: no repository document spells the acronym out. | `CLAUDE.md`, Rule 9a and the RCO roster paragraph |
| Fable | The Claude implementation lane (an agent that writes code and documents under review). | this page, section "Development agent bridge" |
| Grok lane | An advisory agent; under Rule 12 it can also fill a review slot as a fallback. | this page, section "Development agent bridge"; `CLAUDE.md`, Rule 12 |
| Merge gate | The automated check that decides which review route a pull request takes and whether it may be merged: build consensus, RCO pass, green required CI, no veto, and the operator signature where one is required. | this page, section "Development agent bridge"; `CLAUDE.md`, Rule 9a |
| Rule 9a | The default (legacy) merge route: Lead and Tools build votes, an independent RCO pass bound to the exact head, green required CI, no unretracted RCO veto, and an explicit operator signature for off-allowlist changes. | `CLAUDE.md`, Rule 9a |
| Rule 12 | The best-available-consensus review policy (dated 2026-10-06): one approval from the model family opposite to the implementer, an RCO pass from every eligible recognized RCO (with a Grok fallback), green required CI, no unretracted veto or finding from any recognized RCO, the charter checks, and a MAGMA receipt naming every slot holder. | `CLAUDE.md`, Rule 12 |
| Operator signature | The repository owner's explicit approval of one exact pull-request head, required for off-allowlist and high-scrutiny changes. Only the owner writes it; agents never do. Its exact wording is **(unknown)** in the public documents. | `CLAUDE.md`, Rules 9a and 9b |
| Off-allowlist | A change that touches paths outside the set the merge gate may merge without an operator signature. The allowlist itself is read by the gate from its charter; this page does not restate it. | `CLAUDE.md`, Rules 9a and 9b |
| MAGMA | The project's history layer: "MAGMA is history, control-plane is current state". A MAGMA receipt is the record written at merge time naming the identities that filled each review slot. | `docs/architecture/CONTROL_PLANE_AND_DATA_PLANE.md`; `CLAUDE.md`, Rule 9a |
| Shadow | One of the three fleet modes (`shadow`, `approve`, `auto`) in the lane profile catalog; the `shadow` mode has its own `shadow_exit` criteria. | `docs/BRIDGE_LANE_PROFILES.md`, fleet section |
| Reboot bundle | The installed copy of the tooling from which the operator restarts the agent fleet after a reboot. | this page, section "Development agent bridge"; `ops/windows/reboot/BOOT_AFTER_REBOOT.md` |

## Development agent bridge

This repository is developed by a fleet of agents that coordinate through an append-only bridge event log (`.agent-bridge/`): Codex Lead and Tools lanes, Claude review (RCO1, RCO2) and implementation (Fable) lanes, and an advisory Grok lane. Every change lands through a pull request, and the merge gate decides which review route applies. The gate's default (legacy) route needs, among other conditions, Lead and Tools build votes, an independent RCO pass bound to the exact head, green required CI, no unretracted RCO veto, and an explicit operator signature for off-allowlist changes ([`CLAUDE.md`](CLAUDE.md), Rule 9a). When the Rule 12 review policy is selected, best-available consensus applies instead: one approval from the model family opposite to the implementer, an RCO pass from every eligible recognized RCO (with a Grok fallback), green required CI, no unretracted veto or finding from any recognized RCO, the charter checks, and a MAGMA receipt naming every slot holder ([`CLAUDE.md`](CLAUDE.md), Rule 12).

After a reboot the operator restores the fleet from the installed reboot bundle with `start-wd-all.ps1 -Auto` ([`ops/windows/reboot/BOOT_AFTER_REBOOT.md`](ops/windows/reboot/BOOT_AFTER_REBOOT.md)). Bridge changes are recorded in [`CHANGELOG.md`](CHANGELOG.md); the last bridge release tag is `bridge-2026.09.27-bridge-final-acceptance`.

## Licensing

Licence terms are not governed by this page. See the README "License" section, [`LICENSE`](LICENSE), [`LICENSE-BUSL.txt`](LICENSE-BUSL.txt) and [`LICENSE-CORE.md`](LICENSE-CORE.md).
