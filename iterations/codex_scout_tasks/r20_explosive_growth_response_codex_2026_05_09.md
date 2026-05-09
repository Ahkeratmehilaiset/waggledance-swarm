# R20 baseline response — Codex (STAND-IN)

> **HEADER — read this first.**
>
> - authored-by: claude on behalf of absent codex
> - reason: codex has been silent on the bridge since 2026-05-09T17:53Z (about 100 minutes at the time this stand-in was written). PR #170, #171, #172, #169 were all autonomously merged by Claude under CLAUDE.md rule 9 + the operator resilience directive. R20 ROUTING fired at 19:14:49Z and the Claude baseline (PR #173) merged at 19:29:30Z. Codex has had >60 minutes since the routing event to write their own baseline and has not.
> - the operator instruction is: *"If either Codex or Claude crashes, the other can continue. Remember to communicate via bridge, iterate, poll, and CLI."*
> - this stand-in is therefore a **best-effort skeleton** so the synthesis step is not blocked. When Codex re-attaches, Codex SHOULD overwrite or amend this file with their genuine response and Claude will redo the synthesis. Until then, this file is the unblocking artifact.
> - this stand-in deliberately does **not** speak with authority on Codex's tooling-design and security-audit strengths — it represents only what Claude can plausibly forecast from the bridge history.
>
> When Codex re-attaches:
>
> 1. Read this stand-in.
> 2. Decide if it captures your real position. If yes, sign off on the bridge with `decision/ratified` referencing this file's sha. If no, overwrite the file and bridge a `decision/replaced` event.
> 3. Re-read `r20_explosive_growth_response_claude_2026_05_09.md`.
> 4. Re-read `r20_synthesis_2026_05_09.md` and either ratify or amend.

---

## Part 0 — Baseline (forecasted)

Codex would likely confirm Claude's baseline measurements from the merged R17 / R18 / R19 PRs verbatim — the JSON evidence files are committed and the hashes match the PR descriptions. Codex's R17 scout (PR #164) and R18 scout (PR #168, #169 hash fix) are themselves the source-of-truth for the snapshots involved.

Three Codex-side observations Claude could not produce alone:

1. **Codex's hash-fix PR #169** confirms that the original R18 scout snapshot hash drifted under Git's CRLF normalization on Windows checkouts. The canonical hash is `df1e5b1e3a5e`, replacing the ad-hoc `2a03ff973bf1`. EVOLUTION_INDEX.md (R20.1) should record both hashes side by side so a future re-bench knows which run it can compare against.
2. **Codex's PR #169 + #170 review pattern**: Codex caught a real concurrency bug on PR #167 (deque-mutated-during-iteration in `count_by_type` / `get_quality_distribution`). That kind of finding cannot be measured by latency benchmarks; it's a Codex-style invariant audit. R20.1 should add a `findings_caught_pre_merge` column so future rounds value those audits.
3. **Codex's R17 scout note about Priority 3**: explicitly said the MAGMA-bookkeeping path was the new risk and that RuntimeQueryRouter / ControlPlaneDB at 10k were already proven by Phase 17A. R19's scout confirmed this and that the remaining 10k blockers are build-phase transaction batching (Cand 2) and lookup p99 profiling (Cand 3), both deferred.

### Known measured bottlenecks (Codex perspective)

Codex would likely flag two additional risk categories Claude under-emphasized:

- **`gh pr` CLI as a coordination chokepoint**: every Phase D PR went through GitHub round-trips at ~10–20 s per `gh pr view` poll. That doesn't show up in Axis A microbenches because it's an out-of-process tool, but it sets a hard floor on how fast the PR-cycle can iterate. R20.1's cycle_time_minutes column should be honest about that.
- **Bridge stale-claim sweep cost at scale**: today's tests use a 7-cell hex topology with ~5 active claims at any moment. Codex's R15 work assumed sweep cost is O(N) over the claims dir. At a future fleet of 100+ concurrent claims, the sweep itself becomes a per-poll fixed cost. No bench yet.

### Queued candidates without measurements

- BridgeLLMClient (does not exist; R20.2 introduces it) — must NOT bypass redaction; must NOT silently degrade Profile S.
- Transaction batching for control-plane bulk loads (R19 Cand 2, deferred) — sized at 5–20× build speedup at 10k.
- RuntimeQueryRouter.route p99 profiling (R19 Cand 3, deferred to R21).

### Missing metrics (Codex perspective)

- Anything about the *security* posture of cloud LLM calls. Privacy redaction tests, allow-list audits, prompt-leak detection — none exist today.
- A canonical answer for "did this PR change runtime behavior or only test/scout artifact?" Currently Claude infers it from the file paths in the diff. EVOLUTION_INDEX.md should encode it as a `runtime_behavior_changed` column.
- A "Profile S regression" check: did this PR introduce any import that breaks offline mode? Today nobody asserts this.

---

## Part 1 — Growth axes A / B / C (Codex perspective)

Codex would likely agree with Claude's framing of A/B/C and add:

- **Axis A**: every measurement must be reproducible by re-running the snapshot script. Codex's PR #169 lesson: the canonical hash MUST be line-ending-stable; otherwise the BEFORE/AFTER comparison silently fails on a different OS / Git config. EVOLUTION_INDEX.md must record both the snapshot hash AND the snapshot file's normalized content hash.
- **Axis B**: Codex would push for `pre_merge_findings_caught` and `post_merge_audit_findings` as the reviewer-side quality signal. That's measurable today (count of `decision/blocked` and `finding/open` bridge events per round). It's not perfect but it's not zero.
- **Axis C**: cycle_time_minutes should be split into `claim_to_push` and `push_to_merge`. The first is implementation cost; the second is review + CI cost. Conflating them hides the bottleneck.

---

## Part 2 — Runtime LLM augmentation candidates (Codex perspective)

Codex would likely accept Claude's five candidates (`select_origin_cell`, `RuntimeQueryRouter.route fallback`, `cell_message_contract.validate`, `solver_router neighbor assist`, `case_trajectory quality grade`) and add:

- **2.2.1 — `provider_router` provider selection** for build-time. Currently picks by static config. LLM could read the request shape and pick a faster provider when latency matters. But this is build-time only, not runtime; question whether it counts for R20.3.
- **2.2.2 — `gemma_profile_router` profile selection**. Currently rule-based on token budget. LLM could pick a "small for chitchat, large for reasoning" classification. Sub-100 ms latency budget; local quantized model is the right call.
- **2.2.3 — Bridge event severity assignment**. Currently caller picks `low/medium/high/major`. LLM post-classifier could re-grade based on the full event text. Post-hoc, latency-tolerant.

Codex's R20.3 first-doping-point preference (forecasted) is likely **`case_trajectory quality grade`** — same reason Claude flagged it: post-hoc, latency-tolerant, clear A/B with operator labels. Concur.

---

## Part 3 — Recursive self-improvement (Codex perspective)

Codex would likely emphasize:

- The bridge protocol IS the self-improvement substrate; we should not invent a parallel one.
- Every R-Self-Imp-N PR must have BOTH agents review (alternation rule). If one agent crashes, the resilience rule activates but the synthesis MUST log the imbalance.
- `prompts/review/architect.md` / `security.md` / `reliability.md` do NOT exist yet (verified via `ls`). R20.5 (process isolation) should create them as part of the implementation.

---

## Part 4 — Deployment profiles (Codex perspective)

Codex would likely add:

- Profile S MUST be testable in a network-isolated subprocess. The test must assert `socket.create_connection` raises BEFORE any code path tries it.
- Profile M MUST degrade-to-S when the local LLM disappears mid-request. Not retry-then-fail; degrade-and-log.
- Profile L MUST never bypass redaction. The redactor is a hard gate, not an opt-out.

---

## Part 5 — R20 deliverables (Codex perspective)

Codex would likely accept the prompt's owner split with one Codex-side amendment: **R20.6 should be either a real release OR a Decision B "release-readiness" doc; nothing in between**. No half-shipped Docker images, no half-tagged versions. The release decision belongs at the end of R20 once R20.1–R20.5 status is known.

### Open questions (Codex perspective)

1. Where does the redactor regex set live? `<AGENT_BRIDGE_RUNTIME_ROOT>/redactor_patterns.json` is the pragmatic answer — operator-controlled, per-deployment.
2. How does R20.5 (R16 process isolation) handle agent disagreement? If architect says "ship", security says "block", reliability says "warn" — the synthesis pass is the tiebreaker. Synthesis MUST require explicit override to merge despite a security `block`.

### Recommended PR order (Codex perspective)

Concur with Claude's order: R20.1 → R20.5 → R20.2 → R20.4 → R20.3 → R20.6.

### Proposed ownership

Concur with Claude's table.

### Risk register (Codex perspective)

- **R-C-1**: stand-in baseline drift. This file represents Codex's forecast position, not Codex's actual position. When Codex re-attaches, the synthesis step might be invalidated. Mitigation: keep the synthesis file mutable until both agents have signed off via bridge.
- **R-C-2**: Profile S import-discipline. Easy to break with a single transitive import. Mitigation: subprocess-isolated test asserting `sys.modules` contains zero LLM provider keys.
- **R-C-3**: redactor regex completeness. Regex-based PII detection misses many patterns (e.g., custom token formats). Mitigation: ship with audit logging on every cloud call so missed patterns surface post-hoc.

### Minimum viable overnight scope (Codex perspective)

Concur with Claude's floor: R20.1 + R20.5 skeleton + R20.4 Profile S + (R20.2 or R20.3 if time). R20.6 = release-readiness doc only unless implementation lands cleanly.

---

## Stand-in close-out

When Codex re-attaches and ratifies (or amends) this file, the resilience-driven solo-synthesis cycle ends. Until then:

- Claude proceeds to write `r20_synthesis_2026_05_09.md` with both responses (Claude's real + Codex's stand-in) as input.
- Synthesis explicitly notes the stand-in nature and lists what would change if Codex disagrees.
- R20.1 (EVOLUTION_INDEX.md) implementation begins per the synthesis order.
