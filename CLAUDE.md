# CLAUDE.md — operator rules for Claude Code in this repo

Claude Code agents MUST follow these rules. They exist because on
2026-04-11 the `U:\project2` RAM-disk working tree disappeared together
with a full day of Phase 7 hologram/news/wiring work, commit `3babb93`
(`fix(hologram,feeds): HOLO-001 + NEWS-001/002/003 + WIRE-001`). The
rules below are written so that failure mode can never recur.

## Golden rules

1. **The only source of truth is the persistent C-drive repo.**
   Work exclusively in `C:\Python\project2`. Never develop in `U:\`,
   `R:\`, any RAM-disk, `%TEMP%`, or a zip-extraction folder.

2. **GitHub is the primary history. Backups are secondary.**
   Zip backups are for disaster recovery only. They must not be used
   as the canonical repo. If you are ever asked to work from a zip,
   clone GitHub into a persistent C-drive folder first, then overlay
   runtime data (DBs, chroma, models, logs) on top of the clone.

3. **Never `git init` on a restored backup snapshot.**
   A fresh `git init` erases every commit that ever pointed at that
   file tree — that is what caused the 2026-04-11 loss. The correct
   recovery is always:
   ```
   git clone https://github.com/Ahkeratmehilaiset/waggledance-swarm.git C:\Python\project2_new
   # overlay current working data onto the clone (robocopy, excluding .git and any junctions)
   # commit + push the delta from the real HEAD
   ```
   See `docs/RECOVERY_POLICY.md` for the exact recipe.

4. **Every green checkpoint MUST be committed AND pushed.**
   "Green" = tests pass + smoke pass. Do not leave green work sitting
   in a local branch. Use `tools/savepoint.ps1` to enforce this:
   ```
   .\tools\savepoint.ps1 -Message "fix(...): ..." -TestPath "tests/test_foo.py"
   ```
   The script refuses to run off the C: drive, refuses to run from a
   RAM-disk, runs the tests you pass, commits, and pushes in one step.

5. **If you reconstruct work from reports, say so.**
   When the original source is lost and you reconstruct it from
   release-final reports (e.g. `C:\WaggleDance_ReleaseFinalRun\...\reports\`),
   put that explicitly in the commit message, include the report paths
   and the known-good RC commit SHA you are targeting.

## Operational discipline (added 2026-04-28, post-Phase-10)

These rules are the result of Phase 9 / Phase 10 release lessons. They are not
optional; any future Claude Code session that lands work in this repo must
follow them.

### 6. PR-only — no direct push to main

**All commits land via PR.** No direct push to `main`, even for docs-only or
"trivial" changes. There is no carve-out for typo fixes, README polish, or
state-file updates. The PR gate is the only landing surface.

* If a session's work is docs-only, it still goes through a PR.
* If a session believes a change is so trivial it doesn't warrant review,
  the session is wrong about that. Open the PR.
* Branch protection on `main` may not enforce this for every actor; the
  rule is operator-side regardless of what the server enforces.

### 7. Push verification — never classify push as failed before 180s

`git push` from the Claude Code shell can complete asynchronously. The
v3.6.0 and Phase 10 release sessions both initially classified pushes as
"silently_backgrounded" within 10 seconds; both pushes had in fact reached
the remote and the classification was a false positive that produced
unnecessary stop-and-handoffs.

**The contract:**

* After any `git push`, do NOT classify failure earlier than 180 seconds.
* Verify with `git ls-remote origin <branch>` every 15 seconds for up to
  180 seconds before deciding the push is blocked.
* Do not stack parallel push retries while a prior push is still in flight.
* Once the remote tip matches the local tip, the push has succeeded —
  treat any earlier "no output" as harmless.

### 8. Strongest-model default

When a session has a model choice, the default is the strongest available
Claude Opus model in this repo's coding/teaching lane (currently
`claude-opus-4-7`). This applies to:

* the active session model;
* any subagent the session spawns whose model is not explicitly fixed;
* any `ClaudeCodeBuilder` invocation under
  `waggledance/core/providers/claude_code_builder.py`.

If a fallback to a smaller model occurs, it must be logged explicitly in
the session state file's `fallback_events`. Anthropic / OpenAI / local
provider lanes remain supported peers; no lane should be over-claimed as
"already implemented" if it is not.

### 9. Autonomous-merge guardrails

A Claude Code session MAY autonomously create PRs, wait for CI, and
squash-merge them WITHOUT a fresh per-action operator prompt **only if all of
the following hold**:

a) PR head SHA matches the local `EXPECTED_HEAD`,
b) all required CI checks are green,
c) GitHub mergeable state is `clean` / `mergeable`,
d) no rule in this file (or in any tracked per-session prompt returned by
   `git ls-files '*master_prompt*.md'`) is violated,
e) **bridge consensus is verified** per the bridge-consensus approval contract
   below (this replaces the per-action operator query; see
   `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`).

Use `gh pr merge --match-head-commit="$EXPECTED_HEAD"` to refuse stale-SHA
merges. Never `--admin`, never `--no-verify`, never force-push.

#### 9a. Bridge-consensus approval contract (replaces the per-action operator query)

Per operator directive 2026-05-29 ("build the storyboard system; approvals via
bridge consensus, not per-action operator queries"), the approval authority for
an autonomous **MERGE** is **three distinct, verified bridge agent identities**,
evaluated fail-closed:

* **Build consensus** — the lead (`codex-lead-1`) and the tools/impl peer
  (`codex-tools-1`) both concur on the change.
* **Independent RCO** — a **recognized RCO identity** posts an explicit
  `RCO_PASS` (`type=decision` with a status in the approval set) on the PR's
  **canonical task_id** (= branch name) at the **exact head SHA**. The
  recognized RCO set is `{claude-rco-1, claude-rco-2}` (backup-RCO co-authority,
  added 2026-06-05 to relieve the single-RCO availability SPOF). A valid
  `RCO_PASS` from **either** recognized identity satisfies the RCO slot, so a
  merge can proceed when one RCO is offline. The passing RCO **must not be the
  PR author** (author ≠ reviewer); if a recognized RCO authored the PR, only the
  *other* recognized RCO can satisfy the RCO slot.
* **RCO veto is absolute and per-identity** — any `finding`/`changes_requested`
  from **any** recognized RCO identity on that task blocks the merge
  (`tools/check_bridge_changes_requested.py`), and a veto **outranks a pass**: if
  one recognized RCO passes while the other has an unretracted veto at the same
  head, the gate is blocked. The backup RCO can never be used to out-vote a veto.
* **RCO absence = NO merge** — if no recognized RCO `RCO_PASS` at the exact head
  is present, the gate refuses even when build-consensus and every charter
  condition pass. Silence blocks; it never default-allows.
* **Three distinct identities** — the approval set is build-lead + build-tools +
  exactly one recognized RCO = three distinct verified identities. An RCO
  identity counts for the RCO slot only, never a build slot; duplicate, missing,
  unverifiable, self-approving, or author-as-own-reviewer signal sets fail closed
  to `operator_review_required`.
* **Head-exact binding** — all three approvals bind to the exact head SHA; any
  re-push that **changes content** invalidates all prior approvals and requires
  re-consensus (PR #777 head-drift fail-close). **Exception — content-identical
  base rebase (added 2026-06-05):** a pure rebase onto current `origin/main`
  with **no content change** (the PR's diff against the new base is byte-identical
  to its diff against the prior base — mechanically verified, no conflict-edit)
  **carries the consensus approvals forward** to the new head, because the
  reviewed content is unchanged. CI **must still be re-run green** against the
  new head before merge (to catch semantic skew from the advanced base). The
  carry-forward applies to content-review approvals only, never to CI; any
  content difference (conflict resolution, edit) forfeits it and forces full
  re-consensus.
* **MAGMA receipt** — the merge emits a MAGMA receipt recording the three
  identities (including **which** recognized RCO satisfied the RCO slot), the
  head SHA, and the `RCO_PASS` event reference; a consumer must be able to
  re-derive the verdict from those fields (no trusting a bare flag).

This contract governs **MERGE** only. It does **not** authorize the Stage-2
atomic-flip cutover, which remains operator-signed under Rule 10 until a
separate future amendment (gated on a matured synthetic adversarial corpus, a
proven auto-rollback test, and a post-cutover verification harness) loosens it.

#### 9b. Standing consensus-sign for off-allowlist / high-scrutiny PRs (DORMANT until bootstrap-signed)

Per operator directive 2026-06-25 ("allekirjoitan parhaan mahdollisen
konsensus-hyväksynnän JÄLKEEN kaikki, nyt + tulevaisuudessa, jatkakaa"), the
operator's per-PR signature on an **off-allowlist / high-scrutiny** PR may be
satisfied by a **STANDING** signature whenever a defined **best-possible
consensus** state holds — removing the per-PR-sign bottleneck while keeping the
gate at its *fullest* form. The full specification (definition, carve-outs,
bootstrap, fail-closed semantics, and the #1387 safety case) is the **standing
consensus-sign amendment** in `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`
(v1.1, 2026-06-25). In summary:

* **Best-possible consensus** = lead+tools `build_consensus`@head + **DUAL-RCO**
  `RCO_PASS`@head (BOTH `claude-rco-1` AND `claude-rco-2`, mandatory — stronger
  than the Rule-9a single-RCO bar for allowlist-clean merges) + CI all-required
  green@head + **no** unretracted veto/finding from any recognized RCO + charter
  checks pass + correct head-exact, author≠reviewer consensus computation + a
  MAGMA receipt recording the basis. Any missing/ambiguous element fails closed to
  `operator_review_required` (an explicit signature is still required).
* **Scope — the (a)/(b) split** (operator scope decision 2026-06-25; the precise
  membership line + mechanical rule are in the contract). **(a) stays
  operator-EXPLICIT** (never rides standing consensus): Rule-10 / Stage-2 cutover,
  any irreversible/outward-facing action, and the **verdict-computing + core
  governance class** — `CLAUDE.md`, the charter + its loader, this contract, and
  the gate code the merge gate reads/executes at runtime to compute a verdict
  (`verify_bridge_consensus`, `check_bridge_changes_requested`,
  `check_rco_pass_present`, `idle_consensus_auto_merge`, the merge/receipt
  executors, `check_proven_safe_autosign_class`), plus any PR that **wires** a
  dormant tool onto that runtime path — *the gate must not weaken itself via the
  mechanism it grants*. **(b) RIDES the standing sign**: gate-ADJACENT artifacts
  NOT on the runtime verdict path — the P1/P2/P3/P4 **spec docs**, **dormant
  unwired tools** (`bridge_event_taxonomy`, `auto_rollback_eligibility`,
  `post_merge_canary`), and the **P4c corpus/validator** (CI tests). A dormant tool
  migrates (b)→(a) the moment a PR wires it into the gate. When in doubt, **(a)**.
* **DORMANT until bootstrap-signed AND cause-B fixed**: the rule has NO effect
  until the operator places an explicit per-PR signature on **both** PR #1393
  (charter gate-policy denylist) **and** the PR carrying this amendment, **AND**
  the **activation precondition** in the contract is met — the cause-B free-text
  latch fail-open in `tools/check_bridge_changes_requested.py` (which computes the
  "no unretracted RCO veto" element 4) is fixed/wired so a recognized-RCO veto
  latches by event **type**, with a CI-green conformance harness proving a
  mistokened/free-text veto cannot clear it (rco-2 fence #1396). Consensus-as-sign
  amplifies any gate fail-open into an operator-signature bypass, so element 4 must
  be proven sound first. Until all of this holds, off-allowlist / high-scrutiny PRs
  require an explicit per-PR operator signature as before.

This sub-rule governs **MERGE** only and changes nothing about Rule 10.

### 10. Atomic-flip discipline

The atomic runtime flip ("Stage-2 cutover") is a separate risk domain.

* Do NOT execute the cutover in design / build / docs sessions.
* Do NOT collect `HUMAN_APPROVAL.yaml` during design / build / docs sessions.
  Approval is one-shot and belongs only to the actual cutover execution
  session — the operator signs once at execution time.
* Do NOT prompt the operator for "approval keys" or signatures during
  ideation, RFC authoring, or refactoring.
* Bringing forward a previously-collected-then-SUPERSEDED approval as an
  audit artifact (header explicitly says `*** SUPERSEDED — DO NOT EXECUTE ***`)
  is preservation, not collection, and is allowed.
* The cutover mechanism is specified in
  `docs/architecture/STAGE2_CUTOVER_RFC.md`. A real cutover session
  reads that RFC, the `00_README.md` SUPERSEDED block, and the
  `HUMAN_APPROVAL_V2.yaml.draft` (when authored) before doing anything.

### 11. Trivial-rationalization warning

"It's just docs" / "it's just a typo" / "it's just one line" is not a
license to bypass the PR gate, the truth review, or any rule above.
Docs-only PRs still:

* go through PR review;
* run targeted tests if they touch any tested doc invariant
  (e.g., `tests/phase10/test_truth_regression.py`);
* respect MAGMA / FAISS / control-plane truth (no doc edits that imply a
  runtime read path that the code does not actually take).

If a session catches itself reasoning "this is trivial, I'll commit
direct," that is the moment to stop and open a PR.

## What this file does NOT override

- `AGENTS.md` task rules still apply.
- `.gitignore` still applies.
- The project's existing build, test, and release processes still apply.
- Tracked per-session prompts returned by `git ls-files '*master_prompt*.md'`
  still apply on top of this file. Per-session rules can be more strict; they
  cannot loosen the rules above.

## When in doubt

- Stop.
- Verify you are on the C: drive.
- Verify `git remote -v` points at the real GitHub repo.
- Verify `git status` is clean or that your in-progress work is staged.
- For pushes: run `git ls-remote origin <branch>` and wait up to 180s
  before classifying anything as blocked.
- For merges: verify `EXPECTED_HEAD == origin head` before
  `gh pr merge --match-head-commit`.
- Then proceed.
