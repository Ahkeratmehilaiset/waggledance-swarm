# Third recognized RCO: Source recognition is not runtime enrollment

Operator decision: 2026-10-21. This change is one (a)-class PR; its merge needs
an explicit exact-head operator signature. Installation is a separate normal
main-bundle decision. No installed bundle, launcher, live environment, key,
session, worktree, process or measurement is changed merely by this Source PR.

## Positive enrollment and verified absence

Recognition reserves a veto identity; it does not enable positive votes.
`tools/bridge_rco_enrollment.py` reads only the inherited deployment root and
external manifest SHA256. A verified manifest without an RCO3 entry means
`not_provisioned`: a silent future lane does not enlarge the current quorum.
Missing, unreadable, malformed or mismatched pins mean `unknown`, not absence.
Unknown cannot approve or relax the quorum. A public role UUID, handshake file,
`enrollment_verified: true` report flag, or JSON-copied context is insufficient.

Positive RCO3 PASS, clearing and quorum-relaxing self-recusal must match its
operator-attested native conversation and the installed generation/manifest.
All recognized vetoes are considered even without enrollment. Two eligible
positive holders suffice; no three-vote requirement is introduced.

For the separately approved **normal bundle** preparation, the installer accepts
optional `-RcoEnrollmentRecordPath` naming one bounded, strict 17-field
`wd.rco-enrollment.v1` record on persistent C:. It reuses the no-reparse,
opened-handle-bound approval reader. The Source converter, manifest emission
and existing-manifest binding are tested by extracted functions on synthetic
records; those tests never execute the installer. Omission adds no enrollment
key and preserves the original manifest bytes. No real record is supplied by
this PR, and this parameter is not installation permission.

The record holds role identity, target generation, a genuinely observed fresh
native conversation, prior registration session/run, observed PID/launcher and
UTC lifetime, Medium/non-elevated token observation, and explicit operator
attestations of fresh history, no Mind authorship and no case access. It must
exist before the final manifest/hash is frozen. Prior PIDs/session/run are an
observation receipt, not impossible pins on future cold-launched processes.
Current event evidence must match conversation, target generation and manifest
and contain its own current positive PIDs, session/run and observed lifetime.

The currently pinned execution-evidence collector derives a Claude conversation
ID from `--resume <observed-conversation-id>` on its native command line. A fresh
first session without that argument reports no conversation ID and cannot grant
positive authority. The separately approved cold adoption must resume the same
genuinely observed fresh conversation; this is not permission to restart it now.

This is consistency under the externally approved manifest and existing
same-user bridge trust, **not** cryptographic proof of event origin, OS
isolation, automatic independence certification, or cold-ready acceptance.
Creating/observing the fresh session, assigning its real fleet path and native
identity, obtaining the operator attestation and validating medium-token cold
adoption remain explicit prerequisites of the later runtime package. Do not
substitute synthetic fixture UUIDs or old generation pins.

## Identity and independence

The three current recognized identities are `claude-rco-1`, `claude-rco-2` and
`claude-rco-3`. Dual review requires two distinct eligible non-author identities,
not three votes. All three retain an absolute per-identity veto. The ordinary
single-slot checks and their other prerequisites remain separate.

The proposed bridge-role UUID for `claude-rco-3` is
`3c7f49d1-5ac4-4ca3-8ff5-6c97c07bcd50`, newly assigned for this role. It is not an
observed native conversation/session UUID, a signing key, or evidence that a
session already exists. The operator reviews this registry binding with the PR.

Before its first review vote, the actual new Claude session must:

1. Start without inherited conversation/history. It must not be a Lead child or
   an alias of an existing reviewer, author or measurement session.
2. Disclose that it wrote none of Mind's code, plans or measurement
   cases. Never load measurement cases, gold, sealed sets or private captures.
3. Observe and record a medium/non-elevated token and its real native session
   identity, distinct from the assigned bridge role UUID. High/unknown is HOLD.
4. Register through the normal deployed, integrity-pinned bridge session
   bootstrap with agent `claude-rco-3`, that role UUID, and the real fresh
   session/run metadata. Reconcile the actual registration receipt and roster
   before publishing a vote. Never manufacture native IDs, bypass a bootstrap
   refusal, copy another lane's owner token or signing key, or self-grant access.
5. Review and run only authorized tests, disclose evidence class, and publish a
   head-exact bound decision with its own identity. Authorship and veto rules
   remain enforced; Source membership alone is never an approval.

The generic helper `Start-AgentBridgeSession.ps1` accepts Agent, AgentUuid,
RunId, Role and Capabilities. Use it only via the approved deployed bootstrap;
do not run a worktree-relative copy or hot-repin an existing session. This PR
does not invent a fleet worktree path, resume ID or an active `wd-fleet.json`
lane. Actual provisioning and cold adoption must name those real values in the
normal bundle/start decision before the new session can vote.

Lead also chooses the same controlled, advisory-only Grok beneficiary label for
RCO3 as for RCO1/RCO2. This enables neither the broker nor F0 and grants no vote.
Only separately authorized public, synthetic or privacy-cleared review evidence
may be supplied; Mind authorship work, measurement cases and gold remain forbidden.
No actual consultation or native provisioning is performed by this Source change.

## Scope and historical truth

Current-policy veto, PASS, standing-sign, event publisher/schema, signature audit,
registry, workflow roster and admission mirrors move together. The parity test
requires the explicit three-member set, rather than merely equal old lists.
The standing receipt names actual counted reviewers, not the whole roster.

Historical signed DriverProfiles retain the recognized set of their pinned
generation. In particular, `check_signing_package_routes.py` must not claim an
older signed driver recognizes RCO3. Existing sessions inherit old pins until
separately approved adoption. No auto-start, install, restart or Stage-2 is
authorized by this document.
