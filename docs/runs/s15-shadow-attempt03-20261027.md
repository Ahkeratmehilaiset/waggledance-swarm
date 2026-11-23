# S15 bounded shadow cycle: attempt 03

This receipt records one completed, bounded shadow capture cycle. It contains
aggregate evidence and hashes only; captured content remains on the local disk.
It does not authorize activation, a standing run, or Stage-2.

- Installed source commit: `ddf0617c14cc1d3e921f556465220ae85442da05`.
- Installed manifest SHA-256: `b4ab778651e89b3695088b6267c519e446d2f6ccf540eb6caad10f33c04e6b14`.
- Qualified attempt plan SHA-256: `4ba22c6d645f56adea8550b4c2b5dab4005329a5c8253e40e9bdc147fa01aad4`.
- Requested window: 100; cycles: 1; deadline: 600 seconds.
- Commit time: `2026-10-27T21:38:03.837646Z`; events: 100; new rows: 100.
- `latest.json` SHA-256: `5b0c443f39ed05a6233245a2fa87101ff69c926696d8b96947692c7d97bba9be`.
- Controller outcome SHA-256: `5390a50ea727f64b0996df6dce7f4d9c04eef83e20b5fda3263611eaed0f8ee9`.
- Parent result SHA-256: `93445d8112546c90704db3ee170a1c74860c20ceb855d09733407b3ca26cfd7b`.
- Outer watcher result SHA-256: `e202f738fe754d06934e056ac3df64fa2f00e9dc2d26911711e218cde4cc83a8`.

The controller reported `completed` and root exit code 0. The parent reported
clean completion; the watcher reported that the known process tree was clean
and the producer lock was reacquired and released. RCO1 independently checked
these receipts and the committed cycle. Complete native-job membership and
unobserved descendants remain UNKNOWN; cutoff initiation is not an OS-hard
process-extinction deadline. The producer exited normally: this is not a
continuously running service.

The Lead separately recomputed the canonical publication snapshot hash and
matched `9e6916a94cefbd57c274400b0a4479b702602ace2a4cd7ac6c58a9c910ae5307`;
the complete `read_snapshot` validation was not run, and RCO1 did not verify
that digest. This is a capture/containment result, not a measurement of Mind's
benefit. M0-lite was NOT_RUN at the time of this receipt.
