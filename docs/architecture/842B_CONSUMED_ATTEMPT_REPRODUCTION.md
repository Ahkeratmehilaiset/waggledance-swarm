# 842B consumed attempt: reproduction status and required measurement receipt

Status: HOLD_NO_RETRY retained. Documentation only. This file authorizes nothing.

Written 2026-10-26 for the S13 unblock item A2 (Lead request
`s13-unblock-fable-a2-unknown-tuple-doc-20261026-v1`, base `main` =
`6b33c6f95bf2fe350bcde7485e7ed094a59ad4e9`). Author: fable-5 (documentation
owner for this item only; not the owner of the 842B action, not a reviewer).

Marker conventions used below:

- `UNKNOWN`: the author could not verify the value from an immutable record
  within this item. It is not a claim that the value does not exist.
- `NOT_MEASURED`: no measurement has been taken; nothing may be inferred.
- No placeholder, example or sample value in this document is evidence.

## 1. What 842B is, as far as the public record shows

In the S13 runtime inventory (R1, identity prefix `926E8A8C`, with its
supplement `2C3A49D7`) the label **842B** names one consumed runtime attempt:
a one-shot action whose single permitted attempt has been used, held in the
state **HOLD_NO_RETRY**. R1 assigns 842B batch execution to Lead/Root, not to
a producer lane. The hold is restated in public PR text on `main` (PR #1922
body: "Installed generation remains `227aa862`; 842B HOLD_NO_RETRY and
canonical writer STOP remain") and in every S13 progress packet since.

What the attempt executed, which driver ran it, and how it closed are
**UNKNOWN** to this author. This document does not reconstruct them.

## 2. What the S13 A2 preparation established (2026-10-26)

Source: codex-tools-1 read-only decision-facts work for Lead's A1/A2/A4
request (bridge milestone 05:56:01Z, event reference
`79ec3a651c8aa4cb298bb3f3ba6ae769f3af50a21852a23e0a0e3ea0972e0383`;
handoff 06:05:30Z, event reference
`83805b46ce1e8fc3a3a11a3777e44986d0a0bf70f5dcb6078932702a11a0dfdf`).

| fact | value |
|---|---|
| consumed-attempt receipt identity | prefix `d538c88b08*` (R1 secondary description only; the full receipt object was requested once and not obtained) |
| referenced task / driver / closure tuple | UNKNOWN (never read as immutable objects) |
| relevant fix already on `main` | none established (no commit, path or blob mapped to the 842B failure) |
| historical compact bridge read for six R1-known receipt prefixes | failed `stable_snapshot_unavailable`, exit 1, one attempt, no retry |
| installed bundle generation at the time of the preparation | `227aa862763ca058888c81db1cfc148c52d6f479`, unchanged |
| physical peak (memory / disk / handles) during any 842B attempt | NOT_MEASURED |
| runtime, writer, driver or supervisor invoked during the preparation | none |
| disposition recorded by Tools | "full consumed 842B attempt packet/task/driver/closure tuple UNKNOWN, no relevant fix mapping established; retain HOLD_NO_RETRY" |

The `stable_snapshot_unavailable` refusal is the fail-closed answer of the
compact bridge reader (`tools/bridge_compact_view.py`, tail-read branch) when
the log cannot be read as a stable snapshot. Lead classified that read as
STOP_NO_RETRY: it is not re-attempted under this item, and its failure is not
evidence that the historical record is absent.

## 3. Required measurement receipt for any future reproduction attempt

No reproduction attempt of 842B may be started, and none may be accepted as a
measurement, unless a receipt with every field below is written **before**
(section 3.1 to 3.3) and **after** (3.4 to 3.6) the attempt, as immutable
objects whose hashes are recorded in the bound bridge result. A field whose
actual value is not known is written as `UNKNOWN`; a receipt containing an
`UNKNOWN` in sections 3.1 to 3.3 does not authorize an attempt. Current
values are listed so that the gap is explicit.

### 3.1 Identity of the attempt being reproduced

| field | required content | current value |
|---|---|---|
| `original_event_ref` | bridge event reference (sha256) of the event that recorded the consumed attempt | UNKNOWN |
| `original_task_id` | canonical task id under which the attempt ran | UNKNOWN |
| `original_attempt_id` | attempt identity (the R1 receipt prefix is `d538c88b08*`; the full id is required) | UNKNOWN beyond the prefix |
| `original_receipt_sha256` | hash of the full consumed-attempt receipt object | UNKNOWN |
| `original_closure_ref` | reference of the closure record (how the attempt ended) | UNKNOWN |
| `reproduction_attempt_id` | fresh id for the new attempt, bound to its own bridge request | none (no attempt authorized) |

### 3.2 Immutable roots

| field | required content | current value |
|---|---|---|
| `source_commit` | exact `main` commit the attempt runs from | none authorized; `main` at writing `6b33c6f95bf2fe350bcde7485e7ed094a59ad4e9` |
| `source_blobs` | git blob id of every executed source file (driver, writer, helpers) | UNKNOWN for the original; required for the reproduction |
| `driver_identity` | path and blob/sha256 of the driver that launches the attempt | UNKNOWN |
| `bundle_generation` | installed bundle generation (source commit id) and its `deployment-manifest.json` sha256 | generation `227aa862763ca058888c81db1cfc148c52d6f479` installed; manifest hash as recorded by the launcher's pin check |
| `dependency_roots` | interpreter path and version, package lock hash, model/data roots with hashes | UNKNOWN |
| `environment_pins` | names (never values) of every inherited pin variable in force, and whether each was cleared (`cleared=[]` is the only acceptable value) | UNKNOWN for the original |

### 3.3 Invocation

| field | required content | current value |
|---|---|---|
| `command_literal` | the exact command line, verbatim, with every argument | UNKNOWN |
| `runtime_identity` | process image, start time, parent chain as the execution-evidence helper records them | UNKNOWN |
| `token_identity` | the lane token's identity label and level (for example High or Medium), never the token value; agents never read, log or store token material | UNKNOWN |
| `config_identity` | hash of every configuration file read at start | UNKNOWN |
| `one_shot_budget` | the written statement that exactly one attempt is permitted, who signed it, and at which head | none |

### 3.4 Inputs and outputs

| field | required content | current value |
|---|---|---|
| `input_hashes` | sha256 of every input object the attempt reads | UNKNOWN |
| `output_hashes` | sha256 of every object the attempt writes, with paths | UNKNOWN |
| `canonical_checkpoint_before` / `_after` | identity of the canonical lane checkpoint record before and after | UNKNOWN |

### 3.5 Failure and loaded code

| field | required content | current value |
|---|---|---|
| `exit_code` | process exit code | UNKNOWN |
| `failure_literal` | the exact error text, exception type and HResult / errno, verbatim | UNKNOWN |
| `failure_site` | file and line of the raise, bound to `source_blobs` | UNKNOWN |
| `loaded_modules` | the list of modules actually loaded, with origin paths and hashes | UNKNOWN |
| `physical_peak` | peak memory, disk and handle use, measured by the recorded method | NOT_MEASURED |

### 3.6 Control

| field | required content | current value |
|---|---|---|
| `one_shot` | true; the receipt names the single attempt and forbids a second | required |
| `stop_condition` | the written condition under which the attempt stops itself | required |
| `rollback` | what is restored, from which immutable object, and the proof that it was restored | required |
| `owner` | the identity that owns the attempt and its hold state (R1: Lead/Root for 842B batch execution) | Lead/Root (R1) |
| `authorizer` | the operator signature that lifts HOLD_NO_RETRY for exactly this attempt | none |

## 4. Disposition

Exact scoped meaning, nothing wider:

**842B is not reproducible on `main` with the available tuple.** The
consumed attempt's task, driver and closure records have not been read as
immutable objects; the one historical compact read that could have located
them failed `stable_snapshot_unavailable` and is STOP_NO_RETRY. No fix on
`main` has been mapped to the failure.

This is **not** a proof that the bug is absent. The absence of a reproducible
tuple says nothing about whether the failure would recur.

**HOLD_NO_RETRY remains.** No runtime retry of 842B is authorized by this
document, by the S13 unblock packet, or by any bridge consensus; lifting the
hold is an operator action bound to one specific attempt receipt (section 3).

## 5. Unblock condition and owner

**Condition (one):** the full consumed 842B tuple (the complete receipt whose
identity begins `d538c88b08`, with its referenced task, driver and closure
records) is produced as immutable objects with recorded sha256 hashes, from a
source other than the failed historical compact read, and delivered to the
bridge as a bound result. Only then can section 3.1 leave `UNKNOWN`, and only
then can an operator decide on one measured attempt.

**Owner:** codex-lead-1 (Lead), as the R1 holder of 842B batch execution and
the custodian of the historical bridge record. The operator remains the only
authority that can lift HOLD_NO_RETRY.

## 6. What this document is not

- Not an authorization of any run, install, activation, restart or retry.
- Not an inventory or diagnostic of the runtime; it repeats none.
- Not a reading of private logs, cases, gold sets or key material; only
  bridge event references, PR text, R1 identities and `main` object ids are
  cited.
- Not a measurement: every current value is `UNKNOWN` or `NOT_MEASURED`.

## 7. Provenance of this document

- Lead GO 2026-10-26T06:47:42Z, request `s13-unblock-fable-a2-unknown-tuple-doc-20261026-v1`.
- Tools milestone and handoff event references in section 2.
- PR #1922 body (public, GitHub).
- `main` `6b33c6f95bf2fe350bcde7485e7ed094a59ad4e9` for the compact reader's refusal site.
- No test consumes this file; CI collects `tests/` whole and this change adds no test.
