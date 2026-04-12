# Mama Event Gate — Final Verdict

## Baseline verdict: `STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED`

One or more events reached a strong band and the caregiver binding crossed a session boundary. This is the top verdict the framework will emit. It is still NOT a claim about inner experience.

## Closed verdict set

* `NO_CANDIDATE_EVENTS`
* `WEAK_SPONTANEOUS_EVENTS_ONLY`
* `GROUNDED_CAREGIVER_TOKEN_CANDIDATE_DETECTED`
* **`STRONG_PROTO_SOCIAL_EMERGENCE_CANDIDATE_DETECTED`**

## Honesty notes

* This verdict is produced by deterministic scoring + a closed gate function.
* It does not claim inner experience. It describes the evidence available in the event log and nothing more.
* Every subsystem referenced in the scoring function was ablation-tested. See `MAMA_EVENT_ABLATIONS.md` for the matrix.
