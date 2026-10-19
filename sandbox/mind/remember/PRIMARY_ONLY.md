# Situation-only runs (`--arms situation`), sandbox, advisory

Lead's round 4/10 calls this mode "primary". It runs REMEMBER's situation-only arm without the rewrite input.

- **Design basis:** Lead's round 4/10 proposal `R04_PROPOSAL.v1.md` (sha256 E08A3A3A...), from Grok consultation
  f544285167ee48d8b45fc6a6ddf603a9 (answer sha256 4940e926...).
- **Implementation:** fable-5's choice, on a NEW sandbox revision.
- **The frozen Q4 candidate is untouched:** 61044a85 and its locked Q4 plan are never edited.

## Why

- **The plan:** the locked Q4 plan scores the situation-only arm, and it never switches to the rewrite arm.
- **The command:** the frozen `shortlists` command still required `--rewrites` before it ran any arm. So the run
  needed a rewrites producer that the measured arm never reads.
  - That producer's role binding is still open in Lead's output custody overlay.
  - Feeding an empty rewrite file or a new writer model is NOT a fix to the locked protocol.
- **This revision:** the measured arm can now run without that input.

## Interface

| Command | `--arms` | `--rewrites` | Files written |
|---|---|---|---|
| v2 `shortlists` | `all` (default) | required | the frozen 7: v2c outputs and diagnostics, v2a and v2ca shortlists and judge input, `shortlists-manifest.json` |
| v2.1 `shortlists` | `all` (default) | required | the frozen 5: v21a and v21ca shortlists and judge input, `shortlists-v21-manifest.json` |
| v2 `shortlists` | `situation` | refused | 3: `v2a/shortlists.jsonl`, `v2a/judge-input.md`, `shortlists-situation-manifest.json` |
| v2.1 `shortlists` | `situation` | refused | 3: `v21a/shortlists.jsonl`, `v21a/judge-input.md`, `shortlists-v21-situation-manifest.json` |
| v2 `compose` | `situation` | none | 3: `v2a/outputs.jsonl`, `v2a/diagnostics.jsonl`, `compose-situation-manifest.json` |
| v2.1 `compose` | `situation` | none | 3: `v21a/outputs.jsonl`, `v21a/diagnostics.jsonl`, `compose-v21-situation-manifest.json` |

**Refusals before anything is read or loaded:**
- `--arms all` without `--rewrites`;
- `--arms situation` with `--rewrites`, which is refused rather than silently ignored;
- an unknown arm set.

**What a situation run does not do:** it never calls the gate (`v2c`) or the rewrite arm, and it never claims they were
evaluated. It loads one engine.

**Situation manifests:**
- Each carries its own `contract`: `wd.remember-v2-situation-run.v1` or `wd.remember-v21-situation-run.v1`.
- Each also carries:
  - `selected_arms`, the real arm id;
  - `blind_sha256`, a pin of the blind case bytes;
  - `memory_sha256`;
  - `rewrites_sha256: null`.
- They use their own file names, so no consumer of the frozen manifests can read one by mistake.

**Compose in a situation run:**
- It takes only the situation arm's picks; side-arm picks are refused.
- It composes only the shortlists its manifest binds. The contract, blind, memory and shortlist bytes must all match.
- Otherwise it refuses with a fixed message and writes nothing.
- The compose manifest pins the shortlists manifest by sha256.

**The default `--arms all`:**
- It keeps the frozen algorithm, file membership and manifest keys. Tests pin the key sets.
- Only the code blob identities change, as with any new revision.

## Adaptations of the proposal (fable-5)

1. **The flag value is `situation`, not `primary`.**
   - In `remember_v2.py`, `PRIMARY` is the historical pre-registered `v2ca`, the rewrite arm. A `--arms primary` that
     ran `v2a` would invite exactly the mix-up that Grok raised.
   - The mode is therefore named for its arm. The file name of this note follows the proposal.
2. **Compose is bound by hashes, not only by mode.** A missing, edited or full-run shortlist cannot be composed into a
   situation answer.

## Finding while testing

- **Shortlist rows carried wall-clock telemetry:** each row held `telemetry.ms`, and so did every answer.
- **So bytes were not reproducible:** two otherwise identical runs differed in bytes.
- **What the equality tests did:** they dropped exactly that field, and they compared the judge input byte for byte.
- **Fixed in D12** (`sandbox/mind-remember-no-clock-fable5-20261019`): no shortlist or answer holds a clock reading
  any more, and the equality tests compare the shortlist files byte for byte. `test_no_clock.py` holds every engine
  (v2, v3, v2.1, v2.2, v2.3) to the same bytes whatever the clocks read.
- **Why it still matters:** revisions before D12, among them the frozen run's and the selected Q4 revision, still
  write `telemetry.ms`, so a run-reproducibility check of their files must still allow for this field.

## What this does NOT do

- **No encoder, model, case or Q4 run** happened. The tests use the step-2 fixtures and the fake encoder.
- **No blind projection checker:** Lead's `verify_projection` touches case projection.
  - fable-5 authored the Q4 candidate, so a non-author writes it.
- **No performance claim.**
- **No new freeze.** Using this revision in a real measurement needs all of the following, by Lead and non-authors:
  - a new Source freeze (head and tree);
  - new command and manifest pins;
  - consumer compatibility;
  - an explicit pre-registration that drops the side arms;
  - an unchanged primary-arm definition and the blind role map.
- **No change to the old frozen run:** an accepted sandbox patch alone never changes that run.

## Tests

- **Command:**
  `C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/remember -q -p no:cacheprovider`
- **Coverage:**
  - v2 and v2.1 situation runs;
  - the refusal order (mode before any read or load);
  - one engine load;
  - no gate and no rewrite query;
  - byte equality with the default run's situation arm (before D12: equality without `telemetry.ms`);
  - compose binding traps: side picks, no picks, no manifest, edited shortlists, edited blind, other memory and
    other contract;
  - the default run's file set and manifest keys.
- **Mutant check:** fable-5's audit folder holds the 15-mutant check with a passing unmutated control.
