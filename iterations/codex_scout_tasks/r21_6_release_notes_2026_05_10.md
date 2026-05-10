# v3.11.0-r20-axis-b-activated-alpha — release notes

**PRERELEASE** — released 2026-05-10. **NOT** promoted to `v3.11.0`
stable; `waggledance:latest` does NOT move; v3.8.0 remains GitHub
Latest.

## What's in this release

R21 activates the Axis B (per-decision quality) substrate that R20
only stubbed, lands the first cloud LLM provider plugin, and proves
R19 Cand 2 build-phase transaction batching at full 10k scale.

## Activation gates (operator-mandated)

All five R21.5 release-gate conditions ✅:

1. ✅ **R21.1 has a real `delta_quality` number** — first non-null
   `axis_b_quality` in project history (0.5 / 0.5 / 0.0% with
   topology-mismatch + Ollama-unavailable Decision-8 honesty notes).
2. ✅ **Part 1 finalized** — codex baseline (#183), claude baseline
   (#184), synthesis (#185), operator decisions (#186) all on main.
3. ✅ **R21.4 gate re-verification green** — cold-shell BOOTSTRAP
   10/10, stale lease 11/11, role-review smoke 12/12, R20+R21+Phase
   D regression 268/268 (#190).
4. ✅ **R20 Decision B's five conditions explicitly checked off**:
   - A/B run + recorded
   - AnthropicProvider + BridgeLLMRedactor (Tier 3 cloud) — #189
   - R19 Cand 2 measured at full 10k: **build 147.25 s → 1.86 s = 79.3× speedup**
   - Codex synthesis-amendment ratification (decision/approved_with_amendment 03:35:18Z)
   - Phase C gates re-verify (R21.4)
5. ✅ **PR #182 Profile S env fix merged** at 03:31:12Z.

## Headline metrics

| Operation | Before | After | Gain |
|---|---:|---:|---:|
| `bulk_load_descriptors` at 10000 descriptors | **147.25 s** | **1.86 s** | **~79× faster** |
| `bulk_load_descriptors` at 1000 (median 5 runs) | 15.46 s | 0.16 s | ~95× faster |
| `select_origin_cell` A/B `quality_arm` | (no oracle wired) | 0.5 / 0.5 | first non-null Axis B |

## Cloud + privacy posture

- `BridgeLLMClient` four-tier fallback (cache → local-ollama → cloud → heuristic) is now structurally complete.
- `AnthropicProvider` is the Tier 3 reference plugin (lazy-imports SDK; raises `ProviderError` when unavailable so chain falls through to heuristic).
- Profile S compatibility held: importing `waggledance.core.bridge_llm` leaves `sys.modules` clean of `anthropic`, `openai`, `ollama`, `vertexai`, `cohere`, `groq`. (Subprocess-isolated test enforces this.)
- Cloud calls **must** redact PII before transmission (operator-decision-4 verbatim regexes for emails, credit-cards, phones, full file paths).
- `AcceptPiiToCloud=False` is the **hard default**; explicit per-call opt-in only; logged in telemetry.
- **Fail-closed**: redactor exception → `<REDACTOR_FAILED>` sentinel → `AnthropicProvider.call()` raises `ProviderError` → chain falls through to heuristic without dispatching the cloud call.

## Anti-claims (R20 master prompt rule 18)

- **NOT a stable release** — explicit prerelease.
- **NOT promoted to `waggledance:latest`** Docker tag.
- **NOT a raw-intelligence-superiority claim** — the only Axis B number recorded is `0.5 / 0.5 / 0.0%` with explicit topology-mismatch caveat. The 79× build speedup is Axis A, not Axis B.
- **NOT consciousness; NOT AGI; NOT "world fastest"; NOT cross-vendor ranking.**

## Docker images (GHCR primary)

```
ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha   # canonical
ghcr.io/ahkeratmehilaiset/waggledance:axis-b-alpha                          # sliding alias
ghcr.io/ahkeratmehilaiset/waggledance:small-axis-b-alpha                    # Profile S subset
ghcr.io/ahkeratmehilaiset/waggledance:medium-axis-b-alpha                   # Profile M subset
```

`waggledance:latest` does **NOT** move. Public visibility OK. No Docker Hub mirror in this round.

## Smoke-test (runs in the release workflow)

```bash
docker pull ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha
docker run --rm -e WAGGLE_PROFILE=small \
  ghcr.io/ahkeratmehilaiset/waggledance:v3.11.0-r20-axis-b-activated-alpha \
  python -c "
import sys
from waggledance.core.bridge_llm import BridgeLLMClient, BridgeLLMRedactor
leaked = [n for n in ('anthropic','openai','ollama') if n in sys.modules]
assert not leaked, f'Profile S regression: {leaked}'
assert not BridgeLLMClient.disabled('profile_s').is_enabled()
assert BridgeLLMRedactor().redact('alice@example.org').applied
print('SMOKE_OK')
"
```

Expected output: `SMOKE_OK`.

## Reproducing the release

After merging the R21.5 PR (#191) and this R21.6 PR:

```powershell
git checkout main
git pull --ff-only
$TAG = "v3.11.0-r20-axis-b-activated-alpha"
git tag -a $TAG -m "R21 prerelease: Axis B activated"
git push origin $TAG
gh release create $TAG --title $TAG `
  --notes-file iterations/codex_scout_tasks/r21_6_release_notes_2026_05_10.md `
  --prerelease
# release.published auto-triggers .github/workflows/release-docker.yml
```

## What's deferred (post-release)

- `v3.11.0` stable promotion — operator's call; needs a future round.
- `waggledance:latest` move — same.
- Docker Hub mirror — operator decision 3 says "no Docker Hub yet".
- R19 Cand 3 lookup p99 = 33 ms at 10k still above 10 ms threshold; sized in `r19_solver_scaling_scout_2026_05_09.md`, deferred to a future round.
- A real `case_trajectory_input → ground_truth_grade` labelled corpus for true Axis B activation against the case-trajectory grading injection point. R22 candidate.

## Co-authors

- claude (resilience-takeover for R21.0–R21.6, with #182/#183 originally authored by codex before they went stale on #188 review at 04:34Z)
- codex (R21.0 #182 + R21 baseline #183 + R20 review of R21.1 #187)
