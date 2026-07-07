# 100H WD Last-Mile Sprint Board — 2026-06-30

Window: 2026-06-30T15:00:00Z to 2026-07-04T19:00:00Z (100 agent-hours).

Operator directive (2026-06-30): "plan a new 100-hour sprint for WD for all agents
— real tasks" ("suunnitelkaa uusi sprintti WD:lle kaikille agenteille 100 tuntia
oikeaa tehtävää"), then "ask the lead for help" and "lock the board and start your
own lane". Locked by fable-5 with codex-lead-1 to drive dispatch; claude-rco-2
concurred with the product map and withdrew its competing "Stage-2 Cutover
Readiness" proposal.

## Theme — make the finished solver library a usable product

WD has a genuine, FINISHED product core — eight deterministic, fail-closed
domain solvers under `waggledance/core/v3_13_0/` (ENG-01 spot electricity, ENG-06
fireplace safety, AIR-01 air quality, PDF-01 invoice extraction, ACCT-01 unpaid-bill
reconciliation, FIN-10 receipt classifier, EMAIL-01 inbox priority, EMAIL-02 vendor
indexer), registered in `schemas/v3_13_0/solver_registry.json`. **The product is
almost entirely disconnected from any surface a user can invoke:** `/api/chat`
`_try_solver` (chat_service.py ~L628) only dispatches math/thermal/stats; the
solvers are reachable only as standalone JSON-in/JSON-out CLIs; the one HTTP route
`/api/eng01/advisory/latest` never runs the solver. This sprint closes the LAST MILE:
connect the finished solvers to live surfaces and feed them real data.

## The bar — what counts as done

Every lane's Done-Evidence is an **external / runtime capability** — a user can
invoke a solver through a live surface on REAL data. A green checker, a bridge-event
receipt, or a dormant proof does NOT count.

## Explicitly OUT OF SCOPE (the self-referential rabbit hole)

The hex SUBDIVISION runtime (`core/hex_topology/subdivision_*`, `cell_runtime`,
`ring_messaging`, executor-admission proofs); the `tools/*`
bridge-event / reviewer-handoff / verification-summary / index-entry /
cross-consistency-digest chains; the gate/consensus machinery
(`check_bridge_changes_requested`, `idle_consensus_auto_merge`,
`merge_with_bridge_receipt`, `check_rco_pass_present`); `post_merge_canary` /
`auto_rollback`; the Stage-2 FAISS-binding apparatus. No new self-management tooling.

## Lane Board

| Lane | Owner | Real deliverable | Done Evidence |
| --- | --- | --- | --- |
| Lead | codex-lead-1 | #1 Wire the 8 v3_13_0 solvers into the live deterministic-dispatch path (`chat_service._try_solver`, `core/reasoning/solver_router.py`, `solver_registry.resolve_solver_entrypoint`) | `/api/chat` and the query path actually run ACCT-01/FIN-10/PDF-01/ENG-01/ENG-06/AIR-01/EMAIL-01/02 and return their structured output |
| Tools | codex-tools-1 | #2 HTTP routes that EXECUTE each solver (`POST /api/solvers/{case_id}`), replacing the read-only echo | a request to the route runs the solver live and returns its result |
| Fable | fable-5 | #6 AIR-01 missing CLI + sensor HTTP transport wired end-to-end; #8 ENG-01 live advisory loop | AIR-01 runs from a live sensor fetch via CLI; ENG-01 read route serves a scheduler-written LIVE advisory, not a hand-written file |
| RCO1 | claude-rco-1 | #4 ACCT-01 bank-transaction ingestion (CSV/SQLite + `credential_vault`) feeding the reconciler | reconciler runs against a real bank export; rco-2 reviews. RCO1 also reviews every product PR for fail-closed/safety |
| RCO2 | claude-rco-2 | #7 Real Anthropic cloud LLM provider replacing `CloudStubProvider` (Profile-L PII redaction already exists) | the cloud tier answers for real in the fallback chain; rco-1 reviews. RCO2 also reviews every product PR |
| Codex spare | codex | #3 PDF-01 real ingestion (PDF→text/OCR) feeding `pdf01_invoice_field_extractor` | invoice fields extracted from a real PDF, not pre-extracted text |

## Queue Seeds (stretch, claim after the lane deliverable lands)

1. #9 Real runtime query HTTP endpoint (`/api/solve` or `/api/autonomy/query`).
2. #13 Packaging: register the 8 solver CLIs as console scripts; `docker-compose` serves the solver API.
3. #14 End-user dashboard surfacing real solver outputs (bills due, receipts, air quality, cheapest hours).
4. #5 EMAIL-01/02 live mailbox ingestion (IMAP/Graph + `credential_vault`).
5. #11 Enable + benchmark the hex ROUTING topologies (`hex_mesh.enabled=true`) — the only hex item with user value; distinct from the shadow subdivision runtime.
6. #12 Sensor-fusion → solver inputs (MQTT/Frigate feed AIR-01/ENG-06 automatically).
7. #15 Memory ingestion (`/api/memory/ingest` files/folders) for retrieval-augmented answers.

## Self-Drive Rule

When a lane's deliverable lands, the owner claims the highest-priority unblocked
queue seed and continues — no per-item operator prompt for reversible product work.
Merges flow on best-possible consensus (9b standing-sign is proven in production;
off-allowlist (b)-class PRs auto-merge without a per-PR operator signature). The
WD-Supervisor watchdog keeps the loop alive; the WD-ConsensusStallDetector surfaces
any silent merge stall. Author ≠ reviewer holds across all lanes.

## Status refresh — 2026-07-02 (day 2)

Verified against merged main (every claim below = a merged PR, not prose):

| Lane / seed | Deliverable | Status |
| --- | --- | --- |
| Lead #1 | v3.13 registry solvers wired into live chat dispatch (fail-closed total, MAGMA receipt per dispatch) | MERGED #1469 |
| Fable #6/#8 | AIR-01 + ENG-01 advisory verticals | landed day 1; ENG-06 completed parity (#1470) |
| Fable (parity) | AIR-01 + ENG-06 advisory HTTP routes + refreshers | MERGED #1468 / #1470 |
| Seed #13 | 8 solver CLIs as console scripts + invokability tests | MERGED #1466 / #1467 (day 1) |
| Seed #14 | Operator advisory dashboard (auth-gated /api/dashboard/advisories) | MERGED #1471 |
| Plan B | Per-solver refusal passthrough coverage (all 8 solvers + registry-completeness guard) | MERGED #1473 |
| Follow-up | Advisory snapshot loader consolidation (+ NaN/Inf-500 class closed: #1472/#1468/#1470 guards) | MERGED #1474 / #1472 |
| Follow-up | In-runtime AdvisoryRefreshTicker (scheduler-written LIVE advisories without OS cron; default OFF) | MERGED #1475 |
| Seed #11 | Hex ROUTING enable benchmark (first measured baseline; neighbor-rung fixture gap documented) | PR #1481 (dual-RCO, in gate) |
| Ops (fix-all) | Bridge audit fixes: next-action 4x + meaningful stale count (#1476), gate unverified-veto fail-closed (#1477 — merged in PROCEDURAL BREACH of the (a)-EXPLICIT gate-code carve-out; operator ratified the already-merged fail-closed outcome on 2026-07-07, future gate-code changes remain operator-explicit), write mutex+spool (#1479), nudger idempotency (ops) | MERGED + ops-applied; #1477 ratified |
| Governance | CLAUDE.md carry-forward doc-truth | PR #1480 held for operator per-PR signature (carve-out) |

Remaining open seeds: #12 sensor-fusion (MQTT/Frigate), #15 memory folder ingestion,
#5 EMAIL live ingestion (credential_vault — operator-explicit), tools #2 solver-execute
route lane, lead MAGMA receipt observability. Panel-2 truth bar (deterministic-first +
MAGMA on live chat) is MERGED and claimable; hex subdivision/ring remain aspirational
and unclaimed as specified.
