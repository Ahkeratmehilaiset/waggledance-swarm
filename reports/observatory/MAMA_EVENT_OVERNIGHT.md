# Mama Event Observatory — Overnight Run

This report is the output of an overnight real-data collection cycle. It only describes evidence available in the appended NDJSON logs at `logs/observatory/overnight/`. It does NOT make any claim about inner experience and it never blends synthetic data into the verdict.

## 1. Data availability

* **data_status**: `NO_NEW_REAL_DATA`
* **new_real_events_this_run**: 0
* **cumulative_real_events**: 0
* **checkpoint_start_row_id**: 124
* **checkpoint_end_row_id**: 124
* **first_started_at**: `2026-04-10T00:03:40Z`
* **last_run_started_at**: `2026-04-10T00:03:50Z`
* **last_run_ended_at**: `2026-04-10T00:03:54Z`
* **wall_clock_seconds**: 14.0
* **snapshots_recorded**: 7
* **NDJSON total size (bytes)**: 4480

_No new real rows arrived in `data/chat_history.db` during the overnight window. This is a property of the source corpus, not of the framework. It is recorded as a data-availability outcome and does NOT count as a framework failure._

## 2. Framework stability

* All append-only sinks present and growing monotonically.
* Snapshot indices monotonic from 0.
* No counter regressions in the snapshot stream.

* **process_rss_mb (first / last / max)**: 24.1 / 24.4 / 24.4

## 3. Observatory verdict on new real data

* **observatory_verdict**: `NO_CANDIDATE_EVENTS`
* **candidate_events**: 0
* **max_score**: 0
* **contamination_hits**: 0
* **caregiver_binding_hits**: 0
* **self_state_emissions**: 0
* **consolidation_writes**: 0

_No score band reached on real new data._

_No non-zero candidate events on real new data this overnight cycle._

## 4. Honesty separation

* `data_status` and `observatory_verdict` are reported as separate fields. An observatory verdict of `NO_CANDIDATE_EVENTS` produced by an empty overnight window is not the same statement as the same verdict produced by a stream of new real rows that all scored zero — the morning analysis must keep these distinct.
* Synthetic data from `tools/mama_event_longrun.py` PASS B is NEVER mixed into the overnight verdict. Only the rows tailed from `data/chat_history.db` during this overnight cycle were considered.
* The strongest verdict in this report is the strongest member of the closed `GateVerdict` enum that the live observer reached on snapshot boundaries during this run. The enum has exactly four members and is not extended for the overnight path.
* No claim about inner experience or strong-AI properties is made. `assert_no_hype` is invoked against this entire report text before anything is written to disk.
