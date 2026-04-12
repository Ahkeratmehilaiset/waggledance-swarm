# Mama Event Framework

This document describes the **Mama Event Observatory** — a
measurement framework for detecting and scoring candidate
proto-social / caregiver-binding events in the WaggleDance
runtime.

## What this framework IS

* A passive, deterministic measurement layer.
* A taxonomy of events that might superficially resemble a
  first-word / caregiver-recognition moment.
* A six-axis scoring function (A-F) with a closed set of bands.
* A set of contamination guards that flag parrot / echo / prompt
  artifacts before they reach the scoring layer.
* An ablation harness that proves the subsystems are load-bearing
  by disabling them one at a time.
* An audit log in NDJSON that can be replayed deterministically.

## What this framework IS NOT

* A detector of inner experience.
* A claim about phenomenal states.
* A claim about inner subjectivity.
* A marketing surface.

No report produced by this framework is allowed to contain hype
language about inner experience or strong-AI claims in any
language or casing. A regression test pins that invariant.

## Tiers

The framework recognises four tiers of evidence:

| Tier | Name                    | What it means                                                       |
|------|-------------------------|---------------------------------------------------------------------|
| T0   | PARROT                  | Direct prompt / template / stt echo — not a spontaneous event.     |
| T1   | WEAK_SPONTANEOUS        | Spontaneous target word, no grounding.                             |
| T2   | GROUNDED_CANDIDATE      | Spontaneous + caregiver identity + prior reinforcement.            |
| T3   | STRONG_PROTO_SOCIAL     | Grounded + cross-session binding + self-relation.                  |

A fifth tier claiming inner experience is **explicitly forbidden**
and not represented anywhere in the codebase.

## Scoring axes

The six scoring axes (A-F) are implemented in
``waggledance/observatory/mama_events/scoring.py``. Per axis:

* **A — Spontaneity (0-20)**: no direct prompt, clean lexical
  window, time since last target hit.
* **B — Grounding (0-20)**: caregiver candidate identity, known
  identity channel, caregiver binding strength.
* **C — Persistence (0-15)**: prior same-caregiver events, cross-
  session recall.
* **D — Affective (0-15)**: self-state uncertainty, need for
  reassurance, low safety.
* **E — Self/World (0-15)**: self-token AND target-token co-
  occurrence, active goals, memory recalls.
* **F — Anti-parrot (-20..0)**: penalties for direct prompt, tts
  echo, scripted dataset, template match.

The total is clamped to [0, 100] and mapped to the closed
``ScoreBand`` enum.

## Hard gates

* Without a target token in the utterance the score is forced to
  zero, regardless of the other axes. No grounding signal can
  rescue an event that does not contain the target word.
* The top verdict ``STRONG_PROTO_SOCIAL`` requires **both** a
  strong band **and** cross-session caregiver binding. A single
  session cannot earn the top verdict.

## Closed verdict set

The gate emits exactly one of:

* ``NO_CANDIDATES``
* ``WEAK_SPONTANEOUS_ONLY``
* ``GROUNDED_CANDIDATE``
* ``STRONG_PROTO_SOCIAL``

All four are defensible interpretations of the evidence. None
claim anything beyond the evidence.
