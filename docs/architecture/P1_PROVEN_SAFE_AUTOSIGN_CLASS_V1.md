<!-- SPDX-License-Identifier: BUSL-1.1 -->
# P1 — Proven-Safe Auto-Sign Class (V1)

**Status:** DRAFT spec. This document defines an INVARIANT the operator signs
**once** (auditable). It is the first of a 3-PR rollout (RFC item P1). **Nothing
in this spec loosens any gate until the separate gate-wiring PR (#3) is
operator-signed.** Authoring this spec (PR #1) and the dormant checker (PR #2)
changes no runtime behavior.

> **OPTION (c) amendment (2026-06-24, after the original P1-pair merge).** The
> operator narrowed the in-class set: **`tests/**` is DROPPED entirely** and the
> class is now **only the statically-provable set** (additive metric defs +
> *inert, non-executable* `docs/benchmarks/**` data/doc files). This SUPERSEDES
> the earlier "keep tests/ + best-effort dangerous-callable scan" ruling.
> Rationale: proving an arbitrary `tests/**` file RCE-free is undecidable and a
> dangerous-callable denylist is non-exhaustive — dropping `tests/` *eliminates*
> the RCE auto-sign surface instead of denylisting it, so "proven-safe" becomes
> **literally accurate** with no best-effort/undecidable tier. This narrowing is
> strictly safer; the operator re-signs the narrowed invariant.

RFC: WD Bridge Throughput, Resilience & Pool-Decorrelation, item **P1**
("asymmetric operator-reduction: per-policy sign for a proven-safe class").

## 0. One-paragraph summary

P1 lets a pull request auto-merge **without the per-PR operator signature** —
**only** when a fail-closed checker certifies the PR is in a narrow class. The
waiver removes **only the operator signature**. It is **ANDed on top of the
entire existing gate**: full build consensus (lead + tools), recognized-RCO
`RCO_PASS` at the exact head, no RCO veto, CI 6/6, and charter-clean all still
apply, unchanged. The operator signs the **invariant below once**; the gate then
enforces it fail-closed per-PR.

**One assurance tier — "proven-safe" is now literally accurate (option c):**
The in-class set is **exclusively statically-provable**, two non-executable kinds:
- **Additive metric definitions** on the positive `METRICS_PATHS` allowlist. AST
  verifies the hunk is exclusively
  `NAME = Counter|Gauge|Histogram|Summary(<all-inert-literal args>)`, which is
  mechanically decidable and cannot execute arbitrary code.
- **Inert `docs/benchmarks/**` data/doc files** whose extension is on a positive
  non-executable allowlist (`.md/.rst/.txt/.json/.csv/.tsv`). These are never
  imported or executed (a `.py`/`.ipynb`/`.ps1`/`.yaml` under `docs/benchmarks/`
  is **excluded** → operator-sign, because a benchmark runner / conftest could
  execute it — same RCE class as `tests/`).

There is **no best-effort / undecidable tier**: `tests/**` is dropped. The
predicate-(G) dangerous-callable scan is retained only as **optional
defense-in-depth** on the remaining paths; it is no longer the load-bearing RCE
control, because the in-class surface is already statically inert.

## 1. What P1 changes — and what it does NOT

**Changes (the only change):** for a PR the checker certifies IN-CLASS, the
autonomous-merge gate may proceed **without** waiting for a per-PR operator
signature, substituting the operator's **one-time signature on this invariant**.

**Unchanged — every other guarantee is preserved (non-loosening):**
- Build consensus: lead **and** tools `build_consensus_pass` at exact head.
- Recognized-RCO `RCO_PASS` at the exact head (dual-RCO where charter requires).
- **RCO veto stays absolute and per-identity** — any `finding`/`changes_requested`
  from a recognized RCO blocks, and a veto outranks a pass.
- `author != reviewer` (independence) stays.
- Head-exact binding stays — any content-changing re-push invalidates approvals.
- CI 6/6 green stays. Charter-clean stays.
- Silence still BLOCKS; absence of a required signal never default-allows.

P1 is **strictly additive scrutiny that the operator pre-authorizes** for a
narrow, **statically-provable** class (additive metrics + inert benchmark
data/docs). It never removes build, RCO, CI, or charter checks; the signature
waiver is the *only* thing it removes.

## 2. In-class predicates (the checker must PROVE ALL, fail-closed)

A PR is IN-CLASS only if **every** predicate A–G holds. Any failure, any
exclusion, any parse error, or any ambiguity → **NOT in class** → per-PR operator
signature required (the pre-#1 behavior).

- **(A) Paths (option c — statically-provable set only).** Every changed path is
  **either** (1) an **inert `docs/benchmarks/**` data/doc file** whose extension is
  on a **positive non-executable allowlist** (`.md/.rst/.txt/.json/.csv/.tsv`),
  **or** (2) a **proven-additive metric definition** in a file on a **narrow
  positive metrics-allowlist** (`METRICS_PATHS`, e.g. `waggledance/**/metrics.py`).
  *(Scope notes: **`tests/**` is DROPPED** (operator option-c ruling 2026-06-24) —
  pytest imports test modules at collection, so a `tests/` file with malicious
  module-level code executes in CI; proving an arbitrary test RCE-free is
  undecidable, so `tests/**` is excluded entirely → always operator-sign, rather
  than relying on a non-exhaustive dangerous-callable denylist. **`docs/benchmarks`
  is restricted to non-executable extensions** (rco-1 sharp check 2026-06-24): a
  `.py`/`.ipynb`/`.ps1`/`.sh`/`.yaml`/`.toml`/extension-less file under
  `docs/benchmarks/` is the SAME arbitrary-code-RCE class as `tests/` (a benchmark
  runner / conftest could import it) and is therefore **excluded** → operator-sign.
  The allowlist is positive (inert data/doc types only), not an executable
  denylist. `docs/runs/**` stays dropped (off charter allowlist, low-value).* The
  metric path is **default-DENY**: it admits ONLY files explicitly on
  `METRICS_PATHS`, never "any non-denylisted path" — a denylist-gap on a
  sign-waiver path would fail open. The metric definition is **AST-verified**: the
  change must parse as module-level
  `NAME = Counter|Gauge|Histogram|Summary(<all-literal args>)` assignments only —
  any nested call/name/attribute in the args, any other statement, or an
  unparseable hunk is rejected. Adding `METRICS_PATHS` to the charter is itself a
  **charter carve-out = operator-signed + dual-RCO-fenced + P4-gated for
  activation**, delivered as a separate PR; until then no metric path auto-signs.)*
- **(B) Effect.** Read-only **or** default-OFF: no change to a default-emission
  value, and no new throwable code on a live hot path.
- **(C) No `claim_safe` flip.** The change must not flip any `claim_safe` (or
  equivalent capability-claim) value.
- **(D) No authority-flag edit.** No edit to authority flags
  (`gate_skip` / `solver_call` / `receipt_required` / `clinical_decision`, etc.).
  Authority flags are read strict-bool (`x is True`) and never string-coerced.
- **(E) No control-plane / runtime change.** No routing, control-plane, or
  runtime-behavior change.
- **(F) Hard exclusions (any one → operator sign).** The PR touches none of:
  gate / charter / denylist logic; `.agent-bridge/bin/**`; `.github/workflows/**`;
  `requirements*` / lockfiles; `AGENTS.md` / `CLAUDE.md` / tracked master-prompts;
  any Rule-10 surface; anything `evaluate_paths` denylists or
  `evaluate_diff_content` flags.
- **(G) OPTIONAL defense-in-depth dangerous-callable screen on ANY changed line.**
  Any match → operator sign. Detected by **AST** (resolving import aliases) with a
  substring fallback for non-Python/unparseable hunks:
  `eval`/`exec`/`compile`/`__import__`; `os.system`/`os.popen`/`os.exec*`/
  `os.spawn*`/`os.remove|unlink|rename|replace`; `subprocess.*`/`ctypes.*`/
  `importlib.*`; `pickle`/`marshal` load(s); `shutil.rmtree`; a from-import of a
  dangerous module/name; dynamic-dispatch **escape-hatch** builtins referenced
  anywhere (`getattr`/`setattr`/`delattr`/`vars`/`globals`/`locals`/`__import__`/
  `eval`/`exec`/`compile`/`__builtins__`/`breakpoint`); `builtins.<hatch>` dotted;
  reflection / gadget-traversal dunders (`__dict__`/`__class__`/`__bases__`/
  `__subclasses__`/`__globals__`/…); and `operator.attrgetter`/`methodcaller`.
  > **Role under option (c):** (G) is **NO LONGER the load-bearing RCE control** —
  > with `tests/**` dropped and `docs/benchmarks` restricted to inert extensions,
  > the in-class surface is already statically inert, so there is no executable
  > content for (G) to be the primary guard of. (G) is retained purely as
  > **belt-and-suspenders** (e.g. a dangerous-looking string in a benchmark `.md`).
  > Its known non-exhaustiveness is therefore **no longer a soundness concern** for
  > the auto-sign class: a (G) miss cannot admit executable code, because predicate
  > (A) already excludes every executable path. The prior "best-effort, undecidable
  > tests/ residual" framing is **obsolete** — that residual was removed with
  > `tests/`.

### Fail-closed rule
> Any path outside (A); any (C)/(D)/(E)/(G) pattern; any (F) exclusion; any parse
> error or ambiguity → the PR is **NOT in class** → per-PR operator signature is
> required. The checker **never** default-allows on uncertainty.

## 3. Anti-widening controls

- The checker file `tools/check_proven_safe_autosign_class.py` is **added to the
  charter denylist** (PR #3). The class therefore **cannot be silently widened**
  by editing the checker — any change to it is itself operator-gated.
- Each P1 auto-merge emits a **MAGMA receipt** re-deriving the A–G verdict and
  recording the **signed-invariant version** of this spec. A consumer must be
  able to re-derive the in-class verdict from the receipt (no trusting a bare
  flag).
- **Widening the class** (relaxing any predicate, adding a path root) requires a
  **future operator-signed amendment** to this spec (a new version), reviewed by
  both recognized RCOs.

## 4. Rollout — 3 separate PRs (never bundled)

1. **PR #1 — this spec.** `docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md`.
   **Charter-allowlist-clean by path** (`evaluate_paths` and `evaluate_diff_content`
   both return `allowed=True`); it changes no behavior. **But it AUTHORIZES the
   gate-loosening invariant (§1–§3)**, so it must **NOT** autonomous-merge — the
   operator-sign requirement here is a **content/policy** matter, *not* a
   charter-path gate. The operator's invariant endorsement is **captured** by
   either **(a)** an explicit operator-signed merge at the exact head, or **(b)** a
   recorded operator-sign event referencing the exact head. Until that capture, a
   recognized-RCO `changes_requested` holds the autonomous driver; a clean charter
   path must never be read as the missing operator signature.
2. **PR #2 — checker + tests, DORMANT/UNWIRED.**
   `tools/check_proven_safe_autosign_class.py` implementing A–G fail-closed, with
   a positive corpus (#1364/#1369-shaped in-class) and a negative corpus (one
   case per F exclusion and per C/D/E violation, each proving fallback to
   operator-sign). It is consulted by **nothing** — pure, testable logic.
3. **PR #3 — denylist entry + gate-wiring.** Adds the checker to the charter
   denylist and wires the gate to consult it to waive **only** the operator
   signature. **Gate-critical / denylisted → a SEPARATE operator sign at exact
   head, highest scrutiny.** **Nothing activates until PR #3 is operator-signed.**

## 5. Activation prerequisites (PR #3 only)

Before the loosening in PR #3 may activate:
1. **Separate operator signature** at the exact head of PR #3 (distinct from the
   §1 invariant signature).
2. **P4 safety substrate as prerequisite** — per CLAUDE.md Rule 10 and the
   dual-RCO fence (rco-1 2026-06-24): any gate loosening is gated on a matured
   synthetic adversarial corpus + a proven auto-rollback test + a post-cutover
   verification harness. **P1 activation (PR #3) must not precede P4.** PRs #1
   and #2 (this spec + the dormant checker) carry no such dependency because they
   loosen nothing.
3. **Corpora green** — the tools-run positive and negative corpora pass,
   demonstrating in-class auto-sign and out-of-class fallback to operator-sign.

## 6. Ownership & separation of duties

- **fable-5 (non-RCO producer)** authors PR #1 (this spec) and PR #2 (checker).
  A recognized RCO must **not** author P1 — it would collapse dual-RCO to a
  single independent reviewer on the most safety-critical class, and is
  reviewer-designing-the-reviewer-rules (rco-1 + rco-2, 2026-06-24).
- **codex-tools-1** build-signs and runs the positive/negative corpora. (Tools
  cannot author — a tools-authored PR self-cosign-blocks the tools build slot and
  there is no tools-slot waiver.)
- **claude-rco-1 / claude-rco-2** fence the checker adversarially
  (negated/malformed/boundary inputs per predicate) and review every P1 PR for
  gate-loosening; their veto is absolute.
- **codex-lead-1** authors PR #3 (gate-wiring) and coordinates.
- **Operator** signs the §1 invariant (PR #1) and, separately, the activation
  (PR #3).

## 7. Relationship to CLAUDE.md

P1 modifies the autonomous-**merge** approval surface (it pre-authorizes the
operator-signature for a proven class). **PR #1** is charter-allowlist-clean by
path yet **operator-sign-required by content** (it authorizes the invariant;
captured by an operator-signed merge or operator-sign event at the exact head,
and held meanwhile by a recognized-RCO `changes_requested`). **PR #2** is
**off-allowlist** (`evaluate_diff_content` gate-logic denylist hit) →
operator-sign. Both change nothing live. PR #3 (activation) is Rule-10-adjacent
(a gate loosening) and is therefore operator-signed and P4-gated. P1 does not alter the bridge-consensus contract (Rule 9a) except to add
the operator-signature waiver for the mechanically-proven in-class set; build
consensus, recognized-RCO pass, RCO veto, author≠reviewer, head-exact binding,
CI, and charter-clean are all retained unchanged.
