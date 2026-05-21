# Asqav Rival Local Check — 2026-05-22

**First real rival-side local check** for the 2026-05-20 competitor-axis
pilot. Until now the pilot's `rival_side_local_checks_required` table was
0/4 run; every rival cell was `public_doc_claim`. This is the first
`local_install_and_run` evidence row.

It is recorded under the evidence-manifest contract
(`wd.v12.rival_local_evidence_manifest.v1`, introduced in PR #544) at
`docs/benchmarks/rival_local_checks/asqav/asqav.json`, with the
reproducible smoke artifact at
`docs/benchmarks/rival_local_checks/asqav/artifacts/asqav-local-smoke.json`
(sha256 `4dce02020d877a9f43de06f5de86a6c13abb18c9fce0e68755b7daf1f7818bd6`).

## What was run

```
pip download asqav --no-deps          # inspect wheel before install
pip install asqav==0.4.5 cryptography  # core has NO required deps; cryptography is needed for signing
python -c "
  from asqav.keys import generate_local_keypair, SUPPORTED_ALGORITHMS
  from asqav.local import local_sign
  generate_local_keypair('ed25519')
  local_sign(agent_id='wd-rival-check', action_type='tool_call',
             context={'tool':'read_file','offline':True}, queue_dir=<tmp>)
"
```

Isolated venv, Python 3.13, no network calls after install, MIT license,
`Requires-Python >=3.10`. All framework integrations (LangChain, CrewAI,
LiteLLM, Haystack, OpenAI Agents) are optional extras; the core install
is clean.

## Measured findings

| Observation | Value |
|---|---|
| `ALGORITHM_ML_DSA_65` constant exported | `ml-dsa-65` (yes) |
| `SUPPORTED_ALGORITHMS` for local keypairs | `{ed25519, es256}` |
| **ML-DSA-65 locally supported** | **false** |
| Local keypair generation (ed25519) | works offline |
| `local_sign(...)` output | a QUEUE entry, not a signed receipt |
| Queued entry keys | `action_type, agent_id, context, queued_at, status` |
| Queued entry `status` | `pending` |
| Queued entry has a signature field | **false** |

## Interpretation

Asqav's public headline (per the 2026-05-20 snapshot) is **"every agent
action signed with ML-DSA-65, quantum-safe, FIPS 204, hash-chained, RFC
3161 timestamped."** The local check refines that:

* The **offline path is real** — it installs without cloud, generates
  ed25519/es256 keypairs, and queues actions locally.
* But the **ML-DSA-65 signature, the signed receipt, and public
  verification are cloud-dependent**: `local_sign` only enqueues an
  action with `status=pending` and no signature; the quantum-safe
  signing happens server-side when the queue is flushed. ML-DSA-65 is
  not even in the local `SUPPORTED_ALGORITHMS` set.

This is recorded with `cloud_dependency: true`, so per the rival matrix
logic (PR #544) the row is `cloud_dependent` and does **NOT** contribute
to `consensus_grade`. The pilot remains `consensus_grade=false`.

## Effect on the competitor pilot axes

* **A7 public cryptographic verification** (ceded today): the cede stands
  — Asqav's ML-DSA-65 story is genuinely ahead of WD's optional/null
  signature envelope. But the local check adds nuance: Asqav's
  quantum-safe signing is **cloud-dependent**, whereas WD's MAGMA
  receipt sha256 hash-chain is **local-complete** (no cloud needed to
  produce or verify a receipt offline via `tools/verify_magma_receipt.py`).
* **A2 receipt binding / tamper evidence** (contested): refined — WD's
  receipt is locally emitted and locally verifiable; Asqav's signed
  receipt requires its cloud. For the **offline / air-gapped** operator
  posture (A9 local offline solver substrate), WD's receipt path is more
  self-contained.

## Honesty boundary

* This is ONE rival, ONE version (`asqav==0.4.5`), ONE offline smoke. It
  does not benchmark throughput, does not test the cloud path, and does
  not claim Asqav is worse overall — Asqav's cloud ML-DSA-65 + RFC 3161
  story is a real strength WD does not match.
* The finding is specifically: **Asqav's quantum-safe crypto is
  cloud-dependent; its offline mode queues unsigned actions.**
* `consensus_grade` stays `false`. Three rivals (JamJet, Microsoft AGT,
  Preloop) still have 0 local checks run.

## Reproduce

```
python tools/run_v12_rival_local_check_matrix.py --json
# then place this manifest + artifact under an evidence-dir and re-run
# with --evidence-dir to see the cloud_dependent classification.
```
