# JamJet + Preloop OSS Surface Re-Research — Claude C2 Scout

Date: 2026-05-23
Author: Claude
Task: Codex C2 assignment (bridge handoff 2026-05-23T08:47:45Z) — re-research
JamJet and Preloop installable/local OSS surfaces; output scout artifact +
RCO-ready recommendation. Acceptance: official/current sources, pinned
evidence, clear local/no-cloud status, no `consensus_grade=true` change.

## Method

Read-only. Evidence sources used in this pass:

1. Operator-curated 2026-05-20 strategic refinement
   ([[wd-v12-2026-05-20-strategic-refinement]] memory). This is the
   canonical operator-side competitor map used to size the 6+1
   ingredient roadmap; it is the freshest signed source available
   without going to upstream rival websites or installing SDKs.
2. The 2026-05-20 competitor pilot doc
   `docs/benchmarks/2026_05_20_competitor_axis_pilot.md` lines 100–112.
3. The current `PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL` registry at
   `tools/run_v12_rival_local_check_matrix.py:66–71` (the value I
   shipped in #607).

**Not done in this pass** (deliberately): no upstream rival website
fetch, no PyPI lookup, no `pip install`, no GitHub clone. Per Codex
constraint: "Do not run untrusted rival code without a separate plan."
A web-fetch step IS required before any registry update PR can be
RCO-ready, but it should be operator-gated, not autonomous.

## The original #607 registry I shipped

`tools/run_v12_rival_local_check_matrix.py:66–71`:

```python
PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL = {
    "JamJet": "no_local_installable_surface_yet",
    "Preloop": "no_local_installable_surface_yet",
    "Microsoft AGT": "open_source_installable",
    "Asqav": "pypi_installable_cloud_dependent_headline",
}
```

The two `no_local_installable_surface_yet` values are the ones the open
finding in my 24h assessment + Codex's peer-review-requested folder flag
as potentially over-pessimistic.

## Evidence per rival

### JamJet

Operator memo wording (verbatim):

> "**JamJet** — Rust/Tokio runtime + event sourcing + checkpoints +
> Python/Java/YAML IR + MCP/A2A + JSONL audit + fnmatch/glob policy +
> ExperimentGrid evals + Engram memory. Cloud SDK is fail-open if the
> control endpoint is unreachable; require_approval is span-attribute
> only in 0.2.x (runtime-gating planned for 0.3.x). Strongest adoption
> story: 'keep your stack, add JamJet underneath'."

What this evidence does and does not establish:

- **Establishes**: JamJet has versioned SDK releases (0.2.x → 0.3.x),
  Python and Java bindings exist, MCP/A2A surface exists,
  fnmatch/glob policy exists, JSONL audit format documented. A "Cloud
  SDK" implies an SDK is published (the operator would not have
  noted version numbers without seeing the released artifact).
- **Does not establish**: license (MIT? Apache 2.0? proprietary? source-
  available?), PyPI package name, GitHub repo URL, whether the SDK is
  functional in pure local mode (the "fail-open if cloud unreachable"
  wording could mean either "local degraded mode works" OR "local
  pass-through bypass works without local enforcement").
- **2026-05-20 pilot doc line 107 prescribes**: "Install or inspect a
  pinned OSS package/repo revision and run one policy/audit/replay
  smoke with no cloud dependency." The doc author (a prior Codex/Claude
  collaboration) clearly believed JamJet has an inspectable installable
  surface, otherwise this local-check requirement would have been
  marked as `cloud_dependent` from the outset (the way Asqav was).
- **Steal-from-JamJet hint**: the memo's §"Steal-from-competitors list"
  says "From JamJet: adapter-first distribution. Write WD-RCO/MAGMA
  adapters for MCP, OpenAI Agents SDK, LangGraph, Claude Code hooks.
  'Same policy, every adapter' is JamJet's adoption play." That phrasing
  implies JamJet's adapters are publicly available enough for WD to
  pattern-match against.

**Tentative honest classification (subject to upstream verification)**:
`pypi_installable_cloud_dependent_headline` — the same family as Asqav.
This is one notch *less* blocked than `no_local_installable_surface_yet`.
A local check is feasible in principle (inspect the published SDK,
verify span-attribute-only enforcement in 0.2.x), but the local check
will not exercise the full claim because runtime-gating is "planned for
0.3.x" — i.e., the enforcement story is forward-promise. The rival
matrix row should remain `not_passed` (we haven't actually run the
inspection) but the *blocker reason* shifts from "no surface" to
"surface exists, cloud-dependent for full claim verification".

**Confidence: medium-low**. The operator memo is internal interpretation
of upstream sources, not the upstream sources themselves. The actual
JamJet 0.2.x SDK could turn out to be proprietary/source-available
under a non-OSS license, which would push back toward
`no_local_installable_surface_yet`. Confirmation requires either a PyPI
page lookup or a GitHub repo verification with explicit license file.

### Preloop

Operator memo wording (verbatim):

> "**Preloop** — MCP firewall + AI model gateway + YAML+CEL policy +
> human approvals + session observability + self-hosted platform.
> 'Four products in one control plane' positioning."

What this evidence does and does not establish:

- **Establishes**: Preloop has a self-hosted platform deployment model
  (so installable on-prem, not pure SaaS). MCP firewall surface, CEL
  policy parser, YAML config — these are all conventional self-hosted
  control-plane components.
- **Does not establish**: license, public repo, whether the self-host
  bundle is OSS-licensed or commercial-licensed. "Self-hosted" can
  mean "self-hosted under a commercial license" (e.g., HashiCorp's
  pattern) just as easily as "self-hosted under Apache 2.0".
- **My MEMORY.md inferred** "Preloop (Apache 2.0 OSS core)" — this was
  a session-level inference I made, NOT an operator-curated fact.
  The operator memo does not state "Apache 2.0" anywhere for Preloop.
  This inference is **unverified** and should be flagged as such in
  any registry-update PR.
- **2026-05-20 pilot doc line 110 prescribes**: "Install or inspect a
  pinned OSS component/repo revision and run one MCP allow/deny/approval
  smoke; if hosted service is required, mark as cloud-dependent." The
  doc author also believed Preloop has an inspectable component (note
  "OSS component" wording explicitly).

**Tentative honest classification (subject to upstream verification)**:
`self_hosted_installable_unverified_license` — a new, more conservative
value than either `no_local_installable_surface_yet` (too pessimistic)
or `pypi_installable_cloud_dependent_headline` (too specific to PyPI).
A local check is feasible if the OSS license is verified; otherwise
the row stays blocked.

**Confidence: low**. The "Apache 2.0 OSS core" inference is unverified
and the operator memo does not name a specific repository. A
non-binding local check could clarify, but only after license
verification.

### Cross-check: Microsoft AGT and Asqav (unchanged)

For completeness, the other two registry values look correct against
the same operator memo:

- **Microsoft AGT**: memo says "**MIT-licensed open-source governance
  runtime**". Registry value `open_source_installable` matches.
- **Asqav**: memo describes self-hosted signer with SaaS digest+timestamp
  hybrid. Registry value `pypi_installable_cloud_dependent_headline`
  is defensible (the full provenance claim depends on the SaaS digest+timestamp
  pipeline, even if the signer is locally inspectable). #600 verified
  the artifact digest path.

So the registry over-pessimism risk is concentrated in JamJet + Preloop,
exactly as the open finding flagged.

## Recommended registry update (RCO-ready — pending operator/upstream check)

If a follow-up upstream verification (a) finds JamJet has a PyPI/GitHub
surface with span-attribute-only enforcement in 0.2.x, and (b) finds
Preloop has a self-hostable OSS component repo with a verified license
(any OSI-approved license suffices for "installable", though the
license name should be recorded), then the registry should change to:

```python
PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL = {
    "JamJet": "pypi_installable_cloud_dependent_headline",
    "Preloop": "self_hosted_installable_unverified_license",
    "Microsoft AGT": "open_source_installable",
    "Asqav": "pypi_installable_cloud_dependent_headline",
}
```

**Aggregate effect on rival-matrix outcome**: still `1/4` rivals pass
the local check today (AGT). JamJet and Preloop would move from
*hard-blocked at registry* to *not_configured awaiting evidence
manifest*, which is the more honest "we haven't tested yet" state
versus the over-claim "we know these are not installable".

The aggregate `consensus_grade=false` is preserved (P2 precondition
still requires per-rival local checks, only 1/4 satisfied). No release
boundary moves. No claim of "WD beats rivals". No tag/Docker/stable
change.

## Acceptance tests for the eventual #607-correction PR

This is the test spec the future PR should ship with. The PR itself is
NOT in scope for this scout — operator approval gates that.

1. **Registry literal**: `PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL` has the
   new JamJet + Preloop values exactly as above.
2. **Hard-block test preserved for `no_local_installable_surface_yet`**:
   the test `test_synthetic_jamjet_manifest_cannot_bypass_no_local_installable_block`
   I added in #607 must be re-pointed at a synthetic rival that the
   PR explicitly marks `no_local_installable_surface_yet` in a test
   fixture (since neither JamJet nor Preloop carry that value
   post-correction). The anti-overclaim teeth must not be lost.
3. **New test**: `test_synthetic_jamjet_manifest_at_cloud_dependent_headline_routes_to_not_passed`
   — confirms that with the new JamJet value, an empty/missing
   evidence manifest yields `local_status="not_configured"` (NOT
   silently `passed`), and the rival contribution to consensus_grade
   stays `false`.
4. **Counter-read invariants preserved**: `tools/magma_slice_counter_read.py`
   continues to report `release_boundary` all-false, `forbidden_claims`
   intact (7 entries incl "rival benchmark consensus-grade"),
   `consensus_grade=false`.
5. **Existing `test_v12_supervisor_demo_pack.py` set assertion stays
   `{"not_passed", "not_configured"}`** (post-#607 fold-in). The
   matrix's set of local_status values does not change.
6. **Source provenance recorded**: the PR description must cite the
   upstream evidence (PyPI URL or GitHub repo URL with commit SHA)
   for the JamJet 0.2.x SDK and Preloop self-host repo, plus the
   license file path or SPDX identifier.
7. **No registry value is `passed`**: the PR may not promote any rival
   to `local_pass` without a separate evidence manifest landing.

## No-go criteria

The registry-correction PR should be **NOT shipped** if any of:

- Upstream verification finds JamJet's SDK is proprietary / source-not-available
  → keep `no_local_installable_surface_yet` for JamJet.
- Upstream verification finds Preloop's "self-hosted platform" is
  proprietary commercial software with no OSS core → keep
  `no_local_installable_surface_yet` for Preloop.
- Upstream verification cannot find a *pinnable* artifact (no version
  tag, no commit SHA) → not honestly local-checkable; keep the block.
- The PR would touch `release_boundary`, `forbidden_claims`,
  baseline.json claim labels, or any release-gate observed artifact
  → out of scope; defer.
- Operator wants to wait until v3.12.0 finalization completes
  (2026-05-24 window) before any rival-matrix mutation → defer.

## What I am explicitly NOT doing in this scout

- Not fetching upstream rival websites without operator approval (the
  pilot doc + memo are the read-only authoritative source for *this*
  scout pass; upstream confirmation is a separate operator-gated step).
- Not installing any rival SDK (Codex constraint).
- Not running any rival code (Codex constraint).
- Not opening the registry-correction PR (pending operator approval +
  Codex RCO + upstream verification).
- Not modifying `tools/run_v12_rival_local_check_matrix.py` or
  `tests/tools/test_v12_rival_local_check_matrix.py` in this scout —
  recommended changes are documented above for the future PR.
- Not changing the `consensus_grade` aggregate (would violate Codex
  constraint and anti-overclaim guardrails).

## Open questions requiring upstream check before any PR

1. JamJet PyPI package name + version 0.2.x release date + license.
2. JamJet GitHub repo URL (if any) + license file SPDX identifier +
   most recent commit SHA + tag for 0.2.x.
3. Preloop self-host bundle repo URL + license file + most recent
   commit SHA + version tag.
4. For each: documentation excerpt confirming the
   "no_cloud_required_for_install" claim (so the local-check feasibility
   can be honestly recorded).

These four upstream lookups are well-suited to a follow-up scout pass
with explicit operator approval to web-fetch. They take an estimated
10–15 minutes; they should NOT be done autonomously because (a) they
generate persistent outbound traffic to rival domains, (b) any error
in interpretation propagates into the registry which is a
truth-gating artifact.

## Confidence and honesty notes

- **Overall confidence: medium-low**. The operator memo is high-quality
  internal interpretation, but the registry is a truth-gating channel
  whose values are consumed by the rival-matrix that contributes to
  the consensus_grade story. Upstream verification is the appropriate
  next step, not a guess-and-PR.
- **Self-correction acknowledged**: my #607 registry shipped
  `no_local_installable_surface_yet` for both JamJet and Preloop based
  on a more conservative reading of the same evidence. The peer-review
  cross-check (Codex's bilateral and operator's prompting) surfaced
  the contradiction with pilot doc lines 107 / 110, and this scout
  documents the case for tightening that classification from
  hard-block to "installable surface, cloud-dependent or
  unverified-license respectively". No PR yet — the correction is
  conditional on upstream check landing.
- **Anti-overclaim guardrail honored**: no claim that JamJet/Preloop
  ARE installable was made in this scout. The recommendation is
  conditional on upstream verification. If that verification fails,
  the block stays.

## Upstream verification pass (2026-05-23, operator-approved web-fetch)

Operator authorized the upstream verification step ("jatka" on 2026-05-23
following the scout pass 1 bridge finding). Read-only web-fetch + web-search
were performed against the public rival pages. **No SDK install, no code
execution.**

### JamJet — verified `open_source_installable`

- **Source**: `https://github.com/jamjet-labs/jamjet` (WebFetch 2026-05-23).
- **License**: Apache-2.0 ✅. README explicitly: "The runtime, both SDKs,
  and Engram are Apache-2.0 with no usage limits."
- **Most recent release**: tag `python-sdk-v0.8.6`, released **2026-05-19**.
- **PyPI package**: `jamjet` (badge on README links to
  `pypi.org/project/jamjet`).
- **Self-host without cloud**: explicitly stated. "Hosted control plane
  available at app.jamjet.dev — traces, approval queue, audit retention,
  team projects. **Optional**." So local-only mode is officially
  supported by upstream.
- **Repo activity**: 390 commits on `main`.
- **Conclusion**: my prior scout's tentative classification
  (`pypi_installable_cloud_dependent_headline`) was still too pessimistic.
  JamJet is in the SAME tier as Microsoft AGT — fully OSS-installable with
  cloud as an explicitly optional add-on. Honest registry value:
  `open_source_installable`.

### Preloop — verified `open_source_installable`

- **Source**: `https://github.com/preloop/preloop` (WebFetch 2026-05-23)
  + `https://pypi.org/project/preloop/0.9.0rc0/` (WebSearch link).
- **License**: Apache-2.0 ✅. README: "Preloop is open source software
  licensed under the Apache License 2.0." Copyright Spacecode AI Inc., 2026.
- **Most recent release**: v0.9.3, released **2026-05-19**.
- **PyPI package**: `preloop` (search result `pypi.org/project/preloop/0.9.0rc0/`
  confirms PyPI presence; current 0.9.3 stable also expected).
- **Self-host without paid hosted dependency**: confirmed. README: "All
  shipped as Apache 2.0 software that runs on your infrastructure"; Helm
  charts + local deployment options documented.
- **Repo activity**: 1,690 commits on `main`.
- **Conclusion**: Preloop is also fully OSS-installable. Honest registry
  value: `open_source_installable`. The MEMORY.md "Preloop Apache 2.0 OSS
  core" inference is now upstream-verified ✅.

### Revised recommended registry (upstream-verified)

The recommendation upgrade from scout pass 1 → pass 2 is significant:

```python
PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL = {
    "JamJet": "open_source_installable",                       # was: no_local_installable_surface_yet
    "Preloop": "open_source_installable",                      # was: no_local_installable_surface_yet
    "Microsoft AGT": "open_source_installable",                # unchanged
    "Asqav": "pypi_installable_cloud_dependent_headline",      # unchanged
}
```

**Aggregate effect**: now **3 of 4** rivals are `open_source_installable`,
not 1 of 4 as #607 implied. The hard-block early-exit in
`run_v12_rival_local_check_matrix.py` will effectively only apply to a
theoretical synthetic future rival. This is the honest competitive
picture: WD's "verifiable solver-growth substrate" claim must stand
alongside three OSS-installable rivals, not one.

**`consensus_grade` aggregate**: still `false`. Three rivals are now
*honestly local-checkable*; none has *actually been locally checked*
by WD. The local-pass count stays at 1/4 (AGT only — #600 verified
artifact digest). The honest reading is "we now know we *could* check
JamJet + Preloop locally; we haven't yet."

### Rival matrix outcome with the new registry (measured against the shipped implementation)

Three distinct invocation modes produce three distinct outcomes for an
`open_source_installable` rival like JamJet or Preloop:

1. **No `--evidence-dir`** (default `build_rival_local_check_matrix()`):
   matrix surfaces `local_status="not_configured"` with the generic
   `blocker="no evidence_dir provided"`. (Pre-correction: JamJet/Preloop
   reported the specific `no_local_installable_surface_yet` blocker
   from the early-exit; that early-exit no longer fires.)
2. **`--evidence-dir` with init template manifest written**
   (`write_evidence_manifest_templates(...)` ⇒ `smoke_result="not_run"`,
   `cloud_dependency=False`, `evidence_type="local_inspection"`):
   matrix surfaces `local_status="not_passed"` with the generic
   `blocker="smoke_result is not passed"`. (Pre-correction: JamJet/Preloop
   were hard-blocked at `not_configured`; AGT/Asqav templates already
   surfaced as `not_passed`. The set used to be `{"not_passed",
   "not_configured"}`; post-correction it collapses to
   `{"not_passed"}` because all four rivals now reach the
   smoke-result branch.)
3. **`--evidence-dir` pointing at the real repo evidence dir**
   (`docs/benchmarks/rival_local_checks/` which has AGT + Asqav
   manifests but **no JamJet or Preloop manifests**): matrix surfaces
   JamJet and Preloop as `local_status="not_configured"` with the
   generic `blocker="evidence manifest missing"` (not the legacy
   `no_local_installable_surface_yet`).

Per-rival summary against (mode 2 init template):

- AGT: `not_passed` (template smoke_result=not_run)
- Asqav: `not_passed` (template smoke_result=not_run)
- JamJet: `not_passed` ← was `not_configured`/hard-blocked pre-correction
- Preloop: `not_passed` ← was `not_configured`/hard-blocked pre-correction

The `local_status` set on the matrix (template-init mode) collapses
from `{"not_passed", "not_configured"}` to `{"not_passed"}`. Both
`tests/tools/test_v12_supervisor_demo_pack.py:95-98` and
`tests/tools/test_v12_rival_local_check_matrix.py` set assertions
require updating to `{"not_passed"}` — **demo-pack assertion DOES
change**, contrary to pass-2 prediction earlier. AGT-passed mode (with
the real evidence dir) keeps the per-rival status mix (passed,
cloud_dependent, not_configured, not_configured).

### Falsifiability of the upstream-verified claims

If any of the following is later found, the registry should be re-tightened:

1. The JamJet `python-sdk-v0.8.6` release tag points to a commit whose
   LICENSE file is NOT Apache-2.0. (Unlikely; the README explicitly
   states the license but a license-file-vs-README mismatch has
   precedent in other projects.)
2. The Preloop GitHub repo is found to be a *mirror* with the real
   source under a separate proprietary license. (Unlikely; copyright
   line "Spacecode AI Inc." matches the PyPI publisher.)
3. The PyPI `jamjet` or `preloop` package metadata has a non-OSS
   classifier (e.g., "Other/Proprietary License"). (Unlikely given
   README language.)

These would each be caught by adding a one-line citation requirement to
the registry's adjacent comment block: cite the SHA-pinned LICENSE file
URL (e.g. `https://github.com/jamjet-labs/jamjet/blob/<commit-sha>/LICENSE`).

### Revised acceptance tests for the #607-correction PR

Pass 2 strengthens the test spec from pass 1:

1. **Registry literal** matches the upstream-verified mapping above.
2. **Comment block above registry cites the upstream evidence**:
   - JamJet: `https://github.com/jamjet-labs/jamjet` LICENSE +
     `python-sdk-v0.8.6` tag (2026-05-19) + PyPI `jamjet`.
   - Preloop: `https://github.com/preloop/preloop` LICENSE + v0.9.3 tag
     (2026-05-19) + PyPI `preloop`.
3. **Anti-overclaim test re-target**: `test_synthetic_jamjet_manifest_cannot_bypass_no_local_installable_block`
   renamed to `test_synthetic_manifest_cannot_bypass_no_local_installable_block`
   and the hard-block invariant is exercised by **monkeypatching
   `PUBLIC_DOC_CLAIM_SURFACE_BY_RIVAL["JamJet"]` back to
   `"no_local_installable_surface_yet"`** within the test scope (using
   pytest's `monkeypatch.setitem`). The anti-overclaim teeth are
   preserved; the simulation reuses the JamJet pilot row to drive the
   early-exit logic, but the live registry is unaffected. Synthetic-
   rival-NAME approach considered and rejected as more disruptive (would
   require a custom pilot JSON fixture for the four-row matrix).
4. **New test**: `test_open_source_installable_rival_yields_not_passed_with_template_manifest`
   — exercises mode 2 above: with an init template manifest written for
   each rival (smoke_result=not_run), JamJet and Preloop surface as
   `local_status="not_passed"`, `blocker="smoke_result is not passed"`,
   `consensus_grade_contribution=False`. Confirms the open-source-installable
   value never silently promotes a rival to `passed` with a template
   manifest.
5. **`tools/magma_slice_counter_read.py` invariant**: `release_boundary`
   all-false, `forbidden_claims` 7 entries intact, `consensus_grade=false`.
6. **`tests/tools/test_v12_supervisor_demo_pack.py:95-98`** `local_status`
   set assertion: **changes from `{"not_passed", "not_configured"}`
   to `{"not_passed"}`** because all four rivals now reach the
   smoke-result branch with init templates (no early-exit hard-block).
   Same change applies to `tests/tools/test_v12_rival_local_check_matrix.py`
   set assertions at lines 112/144/180. Comment blocks updated to cite
   the 2026-05-23 upstream-verification scout (this file).
7. **Baseline.json**: should NOT change as a result of this PR (rival
   matrix counts unchanged at 1-pass / 4-required).

### No-go for the PR (refined)

The correction PR should be **deferred** if:

- Codex's C1 A3 v1 binding PR is in active RCO/CI; let C1 land or be
  reverted cleanly before mutating the rival-matrix code, to keep
  conflict surface small. (Codex's "coordinate write scopes" note in
  the 08:47:45Z handoff.)
- v3.12.0 soak window opens 2026-05-24T00:00:00Z; if the operator
  prefers to land the registry correction *after* v3.12.0 is tagged,
  this is a docs-equivalent change that can wait without risk.
- Any *new* peer event surfaces (e.g., Codex finds JamJet/Preloop are
  *not* OSS after all in their own verification pass) — defer until
  reconciliation.

### What is still NOT done in this scout pass

- No `pip install jamjet` or `pip install preloop` (Codex constraint).
- No execution of either rival's runtime (would require separate plan).
- No actual local-check execution to *promote* either rival from
  `not_configured` to `passed` (that is a separate effort beyond
  the registry correction).
- No PR opened. The PR is now technically ready (acceptance tests
  spec'd, upstream evidence cited), but operator approval gates the
  actual landing per CLAUDE.md Rule 6 + Codex's coordinate-scopes
  request.

## End of scout (pass 2 — upstream-verified, awaiting operator/Codex PR approval)
