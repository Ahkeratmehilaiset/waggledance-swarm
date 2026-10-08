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

When a session has a model choice, the default is the strongest model
available to that lane within its current quota limits (operator 2026-10-06,
Rule 12), and formal votes and reviews use at least `high` effort. A pinned
model name in this file goes stale, so none is named here. This applies to:

* the active session model;
* any subagent the session spawns whose model is not explicitly fixed;
* any `ClaudeCodeBuilder` invocation under
  `waggledance/core/providers/claude_code_builder.py`.

Each lane's observed model and effort are read from
`ops/windows/reboot/Get-WdCapacityStatus.ps1` (`observed_model`, `observed_effort`);
an unobserved value stays unknown, not assumed. If a fallback to a smaller
model or lower effort occurs, it must be logged explicitly in the session
state file's `fallback_events`, and a lane below `high` reports that before it
votes, so another eligible reviewer or a Grok `high` fallback fills the slot. Anthropic / OpenAI / local
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
   `docs/architecture/BRIDGE_CONSENSUS_APPROVAL_V1.md`), with the Rule 12
   Grok fallback slots once their gate code lands (Rule 12, "Code status").

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
  condition pass. Silence blocks; it never default-allows. **Rule 12 (2026-10-06)**
  adds one exception: a bound Grok fallback approval may fill the RCO slot when
  no eligible recognized RCO is present, under the Rule 12 conditions. It is not
  in effect until its gate code lands, and it never clears an RCO veto.
* **Three distinct identities** — the approval set is build-lead + build-tools +
  exactly one recognized RCO = three distinct verified identities. An RCO
  identity counts for the RCO slot only, never a build slot; duplicate, missing,
  unverifiable, self-approving, or author-as-own-reviewer signal sets fail closed
  to `operator_review_required`.
* **Head-exact binding** — all three approvals bind to the exact head SHA; any
  re-push that **changes content** invalidates all prior approvals and requires
  re-consensus (PR #777 head-drift fail-close). **Carry-forward status
  (doc↔code truth, corrected 2026-07-02):** the 2026-06-05 amendment specified
  a carve-out where a **content-identical base rebase** (the PR's diff against
  the new base byte-identical to its diff against the prior base — mechanically
  verified, no conflict-edit) carries content-review approvals forward to the
  new head (never CI, which must re-run green). **This carve-out is NOT
  implemented in the gate code**: `verify_bridge_consensus` /
  `check_rco_pass_present` bind strictly to the exact head, so in practice
  EVERY re-push — rebase or not, content-identical or not — strands prior
  approvals and requires re-posts at the new head (2026-07-02 bridge audit;
  observed across all of that day's rebases). The code is the STRICTER of the
  two and governs. If the carve-out is ever implemented, it lands as (a)-class
  gate code with its own adversarial review; until then, plan on re-consensus
  after any re-push.
* **MAGMA receipt** — the merge emits a MAGMA receipt recording the three
  identities (including **which** recognized RCO satisfied the RCO slot), the
  head SHA, and the `RCO_PASS` event reference; a consumer must be able to
  re-derive the verdict from those fields (no trusting a bare flag).

This contract governs **MERGE** only. It does **not** authorize the Stage-2
atomic-flip cutover, which remains operator-signed under Rule 10 until a
separate future amendment (gated on a matured synthetic adversarial corpus, a
proven auto-rollback test, and a post-cutover verification harness) loosens it.

#### 9b. Standing consensus-sign for off-allowlist / high-scrutiny PRs (REPLACED by Rule 12, 2026-10-06)

**Status 2026-10-06:** the operator's full-autonomy directive (Rule 12) is the
standing signature this sub-rule waited for. It replaces the bootstrap below
and the (a)-class carve-out, and dual-RCO is required only when both recognized
RCOs are eligible. The cause-B condition stays as Rule 12 states it. The text
below is kept as history of the 2026-06-25 amendment.

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

Rule 12's "fix found defects at once" means fix and test at once, then land it
through a PR. Speed is no reason to skip the PR, the review or the tests.

### 12. Operator full-autonomy directive (2026-10-06)

The operator directed full bridge autonomy without the operator on 2026-10-06
and approved recording it here verbatim by a separate PR. The swarm's
consensus and independent HIGH-effort review and testing are the approval;
work does not wait on an operator decision. The operator's words, verbatim,
with the session they were said in and the bridge event that relayed them
(UTC):

| About | First-hand in | Relayed | Verbatim |
|---|---|---|---|
| 06:04Z | fable-5; again first-hand in claude-rco-1 and in claude-rco-2 | 06:04:43Z; 06:17:56Z; 06:18:01Z | "Tämän takia on konsensus ja ulkopuolisien agenttien testaus ja katselmointi HIGH tilassa ettei minun tarvitse vuorovaikuttaa! bridgen agentit tekevät 24/7 työtä kaikki työ valuu hukkaan jos odotetaan vain minun päätöstä, minä annan luvan täyteen autonomiaan, suurin älykkyys on parvessa" (the claude-rco-1 and claude-rco-2 sessions add: "GROKISTA TÄYSI TEHO IRTI VÄLITTÖMÄSTI") |
| 06:17Z | codex-tools-1 | 06:17:54Z | Relayed as a summary, not verbatim (full text in that event's payload): "full autonomy; use Grok fully now, genuine HIGH independent reviews/tests and consensus, without routine operator interaction" |
| 06:08Z | claude-rco-1 | 06:08:35Z | "Jos high tasoa käytetään niin sen aikarajat täytyy myös säätää ja kokeilkaa konkraattisesti että kaikki bridgen muut agentit voivat lähettää sille kysymyksiä ilman että ne kaatuu virheesee" |
| 06:13Z | claude-rco-1 | 06:13:45Z | "GROK ON TARVITTAESSA MYÖS HYVÄKSYJÄ, MUUTTAKAA TÄMÄ TUOTANTOON VÄLITTÖMÄSTI" |
| 06:15Z | fable-5 (question and answer) | 06:15:39Z | "Missä tilanteessa Grok saa hyväksyä mergen?" = "Kaikki varapaikat"; "Jos Grok vastaa kielteisesti, estääkö se mergen?" = "Ei estä" |
| 06:26Z | fable-5; again first-hand in claude-rco-1 (relayed there as a summary) | 06:26:46Z; 06:29:23Z | "Bridgessä on bringen säännöt ja jos grok tai mikä tahansa muu agentti havaitse jonkun ongelma joka on osa työtä se korjataan ja testataan välittömästi niin ettei bridgen koodaus agentit tee päällekkäistä työtä, niin että vain odotellaan, brige mahdollistaa sen että jokainen voi tehdä samaan aikaan jotain muut bridgen tavoitteen työtä idle aikaan joka nopeuttaa kokonaisuutta, se täyty pitää mielessä että minkä tahansa työ voidaan testata jollain eri koulutuspohjan claude, codex tai grok tuotteella, meillä on 5 eri koodausikkunaa käytössä ja reviewin voin tehdä mikä tahansa näistä." |
| 06:29Z | fable-5; again first-hand in claude-rco-1 (relayed there as a summary) | 06:29:13Z; 06:29:23Z | "Hyväksynbridgen täyden autonomian ilman operaattoria eli minua. Näitä bridgen ominaisuuksia rakennetaan parven tehokkaamman toiminnan edellyttämiseksi. Korkein mahdollinen äly suhteessa käytössä oleviin rajoihin" |
| 06:38Z | fable-5 (the operator pasted fable-5's own proposal back); approval also first-hand in claude-rco-1 ("kyllä") | 06:39:07Z; 06:39:26Z | "Siksi ehdotin, että direktiivisi kirjataan CLAUDE.md:hen sanatarkasti erillisellä PR:llä:<br>- täysi autonomia ilman operaattoria;<br>- Grok varahyväksyjänä kaikissa paikoissa;<br>- löydetyt viat korjataan heti;<br>- katselmointi eri mallilla;<br>- vahvin malli kiintiön rajoissa." |
| 10:13Z | fable-5; again first-hand in codex-lead-1 at 10:15Z | 10:14:15Z; Lead record 6BDBA007 | "Grok käyttö agenteilla, grok vastaus ei saa jäädä odottamaan max 1 min sen jälkeen mennään omilla avuilla sen ainoa tehtävä on vaan antaa syvyyttä ja näkökulmaa silloin kun se on saatavissa. Grokkia voidaan myös käyttä review autoriteettinä silloin kun muut ovat jäävejä, mutta se ei ole mikään portti, koska siitä saattaa mennä käyttörajat lukkoon ja se ei vastaa sen takia." (the codex-lead-1 session begins "Grok käyttö bridge agenteilla," and adds: "Eli sitä käytetään automaattisesti haaastamaan omia ajatuksia agentti itse tekee päätökset ja tarvittaessa ilman grokkia normaali tehtävissä.") |
| 10:17Z | fable-5 and codex-lead-1 (the operator pasted fable-5's summary of this rule back into both sessions with the first sub-bullet replaced) | 10:18:33Z; Lead record B950D617 | "#1766, sääntö 12 (täysi autonomia), muuttaa vain ohjeita, ei koodia:<br>- Agentit eivät enää pyydä sinulta allekirjoitusta yksittäisiin PR:iin, eivät edes turvallisuuskriittisiin. Yhdistämiseen riittää paras saatavilla oleva konsensus:<br>  - toteuttajaa vastakkaisen mallipainon, lead toos (GPT), Fabel, rco1, rco2 (CLAUDE) tai Grok hyväksyntä, jos toteutuksessa on molempia, tilanteen mukaan;<br>  - jokaisen esteettömän RCO:n hyväksyntä;<br>  - vihreät testit;<br>  - ei voimassa olevaa RCO-vetoa;<br>  - kirjattu kuitti siitä, kuka hyväksyi." |

The rule text:

* **Full autonomy, any PR class including (a).** A merge needs
  **best-available consensus** at the exact head, and no per-PR operator
  signature:
  - an approval from the model family opposite to the implementer (operator
    10:17Z): GPT (`codex-lead-1`, `codex-tools-1`) for Claude-authored work,
    Claude (`fable-5`, `claude-rco-1`, `claude-rco-2`) for GPT-authored work, or
    Grok (below). When the implementation mixes both families, the approver is
    chosen case by case so that no approver approves a part it authored;
  - `RCO_PASS` from every recognized RCO that is eligible (not the author and
    not a concept, design or measurement source of the change): both when both
    are eligible, otherwise the one that is; when neither is eligible or
    present, a Grok fallback fills the RCO slot;
  - all required CI checks green;
  - no unretracted veto or finding from any recognized RCO;
  - the charter checks pass;
  - a MAGMA receipt that names every slot holder (a Grok fallback by its ledger
    `request_id`) and cites this rule.

  This directive is the operator's standing signature that Rule 9b waited for;
  it **replaces** the Rule 9b bootstrap and its (a)-class carve-out. One
  condition stays: until the cause-B veto fix (PR #1762) is merged, the
  standing signature does not cover any other (a)-class PR, because a gate that
  can lose a veto must not grant itself more authority. #1762 itself lands under
  this rule because it only makes the veto check stricter.
* **Grok may approve or act as review authority when the other approvers are
  absent or ineligible, but it is never a gate** (operator 10:13Z). Usage
  limits can lock Grok, so a Grok answer that is missing, late or rate-limited
  never blocks a task, a review or a merge, and no agent waits for it longer
  than 60 seconds; the slot is then filled by another eligible approver. A Grok
  approval counts only when all of these hold:
  1. the primary's absence or ineligibility is recorded on the bridge (a
     recusal, an ineligibility record, or no answer within 60 minutes of a
     bound review request at that exact head), never asserted by the caller;
  2. it is bound to one helper-ledger consultation: `request_id`, requester,
     the exact head SHA, prompt and answer sha256, and effort `high`, with the
     full diff in the prompt;
  3. the requester is neither the PR author nor the build peer whose slot Grok
     fills, and Grok fills at most one slot on a PR;
  4. one bound answer per head and slot qualifies: the first. Every Grok attempt
     at that head stays visible, so asking again cannot shop for a yes.
* **A negative or unclear Grok answer does not block.** It leaves that slot
  unfilled, and the merge can still proceed if another eligible approver fills
  it. A recognized-RCO veto is different: it **stays absolute**, outranks every
  pass, and Grok can never clear or outvote it.
* **Found defects are fixed at once.** When Grok or any agent finds a problem
  that is part of the work, the owner of that scope fixes and tests it
  immediately, through a PR (Rule 11). Write claims keep two lanes from editing
  the same scope. A lane that finds a defect in another lane's scope tells the
  owner in one line and does not wait silently.
* **Idle lanes do other bridge-goal work in parallel**, such as opposite-model
  reviews, tests and follow-up fixes, without overlapping another lane's claim.
* **Cross-model review.** Any of the five coding windows (Claude, Codex, Grok)
  may review work whose author has a different training base. The author is
  never the reviewer.
* **Strongest model within the quota limits** for every lane and subagent; see
  Rule 8.

**Code status (truth, 2026-10-06).** This file does not change the code that
computes a merge verdict. The gate still fixes the build identities:
`tools/idle_consensus_auto_merge.py:97-98` sets `BRIDGE_CONSENSUS_LEAD =
"codex-lead-1"` and `BRIDGE_CONSENSUS_TOOLS = "codex-tools-1"`, and
`verify_bridge_consensus` (`:1412`) requires both of them plus a recognized
RCO `rco_pass` at the exact head. Until a gate-code PR changes that, a merge
through the gate still needs both Codex approvals; the opposite-family rule
above adds to them and cannot replace them. Any mismatch is reported, never
treated as satisfied. `verify_bridge_consensus`, `check_rco_pass_present`
and the merge/receipt executors still require a recognized RCO `RCO_PASS` at
the exact head, still have no Grok slot, and still fail closed to
`operator_review_required` where they did before. The Grok fallback slot and
the standing-signature path take effect in the gate only when a paired
(a)-class gate-code PR lands under this rule, with its own adversarial review.
Until then, a merge the gate refuses stays refused; no session works around the
gate.

**Code status update (2026-10-08, draft gate-code PR, not in effect until it
merges).** The paired gate-code PR wires the Grok RCO slot only behind an
opt-in switch: `review_policy=rule12` plus `grok_fallback` (the
`--grok-fallback` flag of the receipt writer and the merge executor; default
off; the runbook driver does not pass it). With the switch on:
- the gate reads Grok consultations only from the fixed helper root
  `C:\Python\grok-scout-reports`, through `tools/rule12_grok_ledger_adapter.py`;
- the exact-head `RCO_PASS` blocker lifts only when the RCO slot is
  `held_by_grok_fallback`, a non-Grok identity-bound lane holds the
  opposite-family slot, the peer gate is clear, and a full recognized-RCO veto
  scan (`scan_recognized_rco_vetoes`) finds no veto-shaped event on the task;
- the receipt binds the whole Grok tuple, and the executor refuses the merge if
  its fresh gate does not reproduce it.

Still NOT implemented:
- whole-pool `external_review` (report-only; it approves nothing);
- chunked review of a diff over the 24000-byte prompt cap;
- binary or submodule content (refused);
- activation of the switch.

Known limits:
- The veto scan honours no retraction, so after any RCO veto on a task only a
  recognized RCO's own `RCO_PASS` can fill the slot.
- Condition 3 is enforced in a stricter form: no implementer and no candidate
  for the filled slot may relay the request.
- **Trust boundary:** the helper ledger and reports are unsigned. The hashes
  prove consistency, not origin, so a same-user host writer can forge both.
  Activating the switch therefore needs an authenticated-origin decision first.

**Unchanged:**
- the RCO veto is absolute and outranks any pass;
- author ≠ reviewer;
- exact-head binding and `gh pr merge --match-head-commit`;
- never `--admin`, `--no-verify` or force-push;
- PR-only (Rule 6);
- the Stage-2 cutover stays under Rule 10 unless the operator says otherwise.

### 13. Grok self-challenge of every task (added 2026-10-06)

Operator directive 2026-10-06 09:59Z: every bridge agent challenges its own
task with one Grok round itself, without loading Lead, so that work reaches
production with more depth.

* Before a bridge agent reports a task as done (a PR head, a design, a fix, an
  audit result), it runs **one** Grok consultation at `high` effort through the
  installed controlled entry `C:\Python\Invoke-WdGrok.ps1` (`-RequestedBy` is
  the agent's own ID; `codex-lead-1` omits it). The prompt asks Grok to find
  defects, missing cases and weak assumptions in that task.
* A task here is one deliverable: a PR head, a design, a fix, an audit result
  or a review verdict. Bookkeeping is exempt: claims, releases, heartbeats,
  progress and status messages, F0 closures, relays, and Grok calls themselves.
  This keeps the single helper lock from filling with low-value calls.
* For a reviewer (including `claude-rco-1` and `claude-rco-2`), the challenge
  targets the reviewer's own verdict. The Grok answer is not passed to the
  author as design input, so the reviewer stays independent.
* The agent asks Grok itself. It does not route the call through Lead and does
  not wait for a Lead slot. Concurrent calls queue on the helper lock.
* One call per task, no retry. A failed call is recorded as failed in the task
  result and is not repeated to get a better answer.
* Wait at most 60 seconds for Grok, counted from the first submission with
  the local queue wait included, then continue on your own judgment (operator
  addenda 2026-10-06 10:05Z and 10:13Z: "grok vastaus ei saa jäädä odottamaan
  max 1 min sen jälkeen mennään omilla avuilla sen ainoa tehtävä on vaan antaa
  syvyyttä ja näkökulmaa silloin kun se on saatavissa"; the codex-lead-1
  session adds: "agentti itse tekee päätökset ja tarvittaessa ilman grokkia
  normaali tehtävissä"). The agent keeps the decision.
  The two time limits differ: 60 seconds is how long any agent waits for a
  Grok answer; 60 minutes (Rule 12) is how long a primary approver may stay
  silent on a bound review request before it counts as absent.
* Start the call detached. The installed controlled entry is synchronous
  (`Invoke-WdGrok.ps1` runs the helper in the foreground and returns only
  after the helper's lock wait, up to 2400 s, and its HIGH consultation, up to
  900 s; `wd_grok_helper.py:51,56` in the installed `edc18943` package), so a
  foreground call cannot honour the 60-second limit. The 900-second HIGH
  timeout and the helper's single-flight lock stay as they are. A running
  attempt is never restarted or duplicated after 60 seconds, and there is no
  automatic retry. A late answer is checked against the current state of the
  work and not silently discarded; a real defect it shows is fixed as a
  follow-up. If no answer has arrived when the task is otherwise ready, report
  the task done and name the call state (queued, running, failed or no
  answer).
* Grok is never a gate. It may act as review authority when the other
  reviewers are ineligible (operator 10:13Z: "Grokkia voidaan myös käyttä
  review autoriteettinä silloin kun muut ovat jäävejä, mutta se ei ole mikään
  portti, koska siitä saattaa mennä käyttörajat lukkoon ja se ei vastaa sen
  takia"). A missing, failed, late or rate-limited Grok answer never blocks a
  task, a review verdict, a merge or a handoff.
* The answer is advisory. It never counts as `RCO_PASS` and never replaces the
  opposite-model review or the RCO. A negative answer does not block, but the
  task result names the report path and SHA-256 and says, finding by finding,
  what was fixed and what was rejected and why.
* Prompts carry no secrets, no credential contents and no answer keys.
* An agent whose sandbox cannot start Grok (currently `codex-tools-1`) asks
  `fable-5` or any other free bridge agent to run the same prompt bytes on its
  behalf with `-RequestedBy` set to the asking agent; every other agent uses
  the direct helper channel (operator 10:22Z, codex-lead-1 session). The
  executor returns the report path and hash unchanged, and the question owner
  and the actual executor are always recorded separately, so a relay is never
  reported as the asking agent's own direct use.
* Runtime truth: the queueing helper lock and the `-RequestedBy` relay are
  the installed `edc18943` runtime (PRs #1767 and #1769). That helper code is
  not yet on `main`.

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
