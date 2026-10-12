# Fleet resurrection, 2026-10-12

> *All of this has happened before, and all of this will happen again.*
> — Battlestar Galactica (2004)

Between **15:37:25Z** and **19:02:19Z** on 2026-10-12, the operator relaunched all five
persistent WaggleDance agent lanes, one at a time, onto the new pinned bridge bundle
`227aa862` (deployment manifest `3C87E68E…`). **Every lane resumed its own
conversation.** None started fresh, and `start-wd-all.ps1 -Auto` was never used.

This page records what was measured, what refused, and what went wrong. It records an
event. It does not claim any capability beyond what the code does.

## Definitions

- **Lane:** a named, persistent agent identity with its own worktree, bridge identity and
  conversation. The lanes are `codex-lead-1`, `codex-tools-1`, `claude-rco-1`,
  `claude-rco-2` and `fable-5`. Grok (xAI) is an on-demand advisory helper, not a lane, so
  there was nothing to relaunch.
- **Relaunch (L2):** a relaunch of one lane at a time:
  1. The operator closes that lane's window.
  2. The operator runs its wrapper with `-DryRun` (Tools: `-ValidateOnly`) and checks that
     the resume target is the lane's recorded conversation and that `fresh_session` is
     `False`.
  3. The operator launches the lane for real.

  Before the native CLI starts, the wrapper re-hashes the bundle entry point and the
  manifest, and checks the installed state pointer. The launcher then starts a new bridge
  generation and the native CLI resumes the recorded conversation. The machine was **not**
  rebooted.
- **Continuity:** two checks, both measured outside the agent:
  - the new native process's command line resumes the recorded conversation id;
  - the same conversation file keeps growing.

  An agent saying that it remembers is not evidence of continuity.

## The fleet after relaunch

| Lane | CLI, launch-preflight model / effort | Relaunched (UTC) | PID | Conversation | Conversation since (UTC) | Age at relaunch | Conversation file at 19:07Z |
|---|---|---|---|---|---|---|---|
| `codex-lead-1` | Codex, gpt-6.1-sol / high | 15:37:25 | 9156 | `01a0a654` | 2026-09-16 01:29 | 26.59 d | 2,866,845,488 B |
| `claude-rco-1` | Claude Code, claude-opus-5-5 / xhigh | 18:19:43 | 6192 | `8abcb653` ← `b562b69a` | 2026-09-27 17:36 (lineage) | 15.03 d | 15,978,901 B (+ 241,409,000 B predecessor) |
| `codex-tools-1` | Codex, gpt-6.1-sol / high | 18:47:25 | 28600 | `01a0a07b` | 2026-09-14 15:14 | 28.15 d | 2,415,017,966 B |
| `fable-5` | Claude Code, claude-opus-5-5 / xhigh | 18:54:40 | 33996 | `9f375967` | 2026-09-15 18:28 | 27.02 d | 802,850,759 B |
| `claude-rco-2` | Claude Code, claude-opus-5-5 / xhigh | 19:02:19 | 28244 | `9d0b4b40` | 2026-09-27 17:52 | 15.05 d | 253,804,558 B |

Model and effort are the values that launch preflight resolved at start. For the Codex
lanes both came from the user config. For the Claude lanes the model came from the resumed
transcript and the effort from user settings. They are a startup resolution, not an
observed runtime value, and this record did not measure the runtime model or effort. Lead
reported that its own native metadata at 17:31:25Z showed effort `xhigh`, not the `high`
in the table (event d6fd45cb5a8e). Ages are measured from the conversation's first record
to the new native process start.

## Numbers

| Quantity | Value | Source |
|---|---|---|
| Lanes relaunched / resumed own conversation / fresh sessions | 5 / 5 / 0 | continuity check 1: each new native command line resumes the lane's recorded conversation (launcher DryRun: `fresh_session` `False`) |
| Conversation files that kept growing | 5 of 5 between 19:07:05Z and 19:22:26Z | continuity check 2: file lengths at both times |
| Fleet `-Auto` runs | 0 | Lead decisions 434728209d33, 5322870dc010 |
| Resumed conversation history | 6.35 GB in five files (6.60 GB with RCO1's predecessor file) | file lengths at 19:07:05Z |
| Oldest resumed conversation | 28.15 days (`codex-tools-1`, since 2026-09-14T15:14:23Z) | first record timestamp |
| First to last relaunch, sequential | 3 h 24 min 54 s | native process start times |
| Lead native start to its first gate decision | 19 min 7.6 s (BUILD_CONSENSUS_PASS #1861 at 15:56:33Z) | event 7afde5d60ad4 |
| Blocking daemon exit to Lead launcher start | 6.4–7.4 s after the exit was detected (15:35:29Z → 15:35:36.416Z), at most 22.4 s after the exit itself (15 s poll) | process creation time; event 0f57b928ddce |
| Bundle closed set | 517 manifest-listed files + the manifest = 518 files | `deployment-manifest.json`, file count |
| Install verification | 536 machine-integrity rows, 517 package hashes, 518-file closed set | event d0b1e8fed07c |
| Retained WAL record before relaunch | 1 record, 3,525 bytes (`88DFBB16`); canonical log 144,830,401 bytes | event 2cb7f29878bf |
| WAL record after Lead's session-start drain | in the canonical log exactly once, byte-identical | event 5322870dc010 |
| Repository at `227aa862` | 2,355 commits on `main`; first commit 2026-03-01 (225 days) | `git rev-list --count`, `git log --reverse` |

## Timeline (UTC, 2026-10-12)

| Time | What happened | Event ref |
|---|---|---|
| 13:29:34 | E6 no-apply rehearsal on the staged package: exit 0, would_drain 1, failed 0, drained 0. The WAL, its marker and the canonical log length were unchanged. | 03d59c6fbb79 |
| 13:36:16 | Cold handoff ready from `main` 227aa862: manifest 3C87E68E, independent check 517/517, installer DryRun exit 0. A machine reboot was not yet proven safe. | 8e73b848aeae |
| 13:55:50 | The operator's L1 DryRun failed in a Limited (non-elevated) window: Tools host ownership was unreadable and live launchers read as zero. No Auto. | 6dfeb5310042 |
| 13:57:10 | RCO2: L1 cannot run from a Limited window by design. Run the installer, then relaunch Lead alone (L2) from an elevated caller. | 187547480e76 |
| 14:06:59 | Lead and RCO2 select the bounded L2 route: no `start-wd-all -Auto` before or after the install. | 434728209d33 |
| 15:02:59 | `claude-rco-1`'s session starts a transient `claude.exe daemon run` (PID 35140) through WMI, with no launcher ancestry. Its conversation `b562b69a` continues as `8abcb653` under that daemon, and the lane loses its working context. | — |
| 15:20:03 | Lead verifies the operator's install of 227aa862: 536 machine-integrity rows, 517 package hashes, 518-file closed set. The supervisor is disabled. | d0b1e8fed07c |
| by 15:27:20 | The operator's Lead launch is **refused**: `cannot prove lane availability: unmarked native claude.exe PID 35140` (`start-wd-agent.ps1:873`). | f2adb2ce4842 |
| 15:33:48 | RCO2 records the pre-launch state: WAL 3,525 bytes, marker 257 bytes, canonical log 144,830,401 bytes. It warns that starting a lane runs the accepted-queue drain. | 2cb7f29878bf |
| 15:35:29 | Daemon 35140's exit is detected by a 15 s poll; it exited within the preceding 15 s. | 0f57b928ddce |
| 15:35:36 | The operator starts the Lead launcher (process 35484 created at 15:35:36.416Z). | — |
| 15:37:20 | Lead generation `wd-lane-codex-lead-1-20261012T153537Z` manifests its target state and attempts its append canary. | 77bfa917e614, 0067d2f46db0 |
| 15:37:25 | Lead `codex.exe` 9156 resumes `01a0a654`. | — |
| 15:56:33 | Lead's first decision after the relaunch: BUILD_CONSENSUS_PASS on #1861. | 7afde5d60ad4 |
| 15:56:34 | Lead: WAL 88DF is in the canonical log exactly once and byte-identical; ready, pending and quarantine are empty. The launch drained it, and Lead did not repeat the apply. | 5322870dc010 |
| 18:19:43 | `claude-rco-1` relaunched. The DryRun showed `8abcb653`, the continued conversation, not `b562b69a`. | bebed6af6cd9 |
| 18:35:05 | `claude-rco-1` posts a blocking finding on train #1873 (a startup race in #1871). | f8e7a821d80c |
| 18:36:09 | `claude-rco-2` withdraws its #1873 pass. The train does not merge. | fed3adc3c2bd |
| 18:43:13 | `codex-tools-1` records `restart_ready`: no own claims, no commands in flight. | 877a98f0519a |
| 18:47:25 | `codex-tools-1` relaunched after a clean `-ValidateOnly`; it resumes `01a0a07b`. | 4562b8dcfd2d |
| 18:54:40 | `fable-5` relaunched; it resumes `9f375967`. | 6b49db527294 |
| 19:02:19 | `claude-rco-2` relaunched; it resumes `9d0b4b40`. | c9c6dd01fcdf, 9bacc8d84de9 |

## What held

1. **Launcher ancestry check.** Lead's launch was refused while an unmarked native
   `claude.exe` (PID 35140) without launcher ancestry was alive (`start-wd-agent.ps1:873`,
   reported in event f2adb2ce4842). The refusal was not bypassed. Lead's launcher started
   only after that process had exited: its exit was detected at 15:35:29Z by a 15 s poll,
   and the launcher process was created at 15:35:36.416Z.
2. **No guessing of liveness.** L1 from a Limited window refused (host ownership
   unreadable) instead of assuming which lanes were alive. The fleet `-Auto` path was not
   used at any point.
3. **Exactly-once drain.** E6 was rehearsed as no-apply before the install. The real drain
   happened once, at Lead's session start, and was verified byte-identical.
4. **Pinned bundle and state pointer.** Every wrapper re-hashes the bundle entry point and
   manifest and checks the installed state pointer before starting. The Tools launcher also
   refuses a `-Generation` that is not the bundle's source commit.
5. **Claims are bound to the session generation.** The resumed `fable-5` could not refresh
   its own pre-restart claim (`Claim-AgentTask.ps1:145`, "cannot force-update a claim held
   by another session"). A lane label is not authority:
   only the owner's release or the stale sweep frees such a claim.
6. **Reviews continued during the relaunch.** At 18:35:05Z `claude-rco-1`, relaunched
   16 minutes earlier, posted a blocking finding on train #1873 (event f8e7a821d80c). At
   18:36:09Z `claude-rco-2` withdrew its pass on the same head (event fed3adc3c2bd). #1873
   did not merge at that head.

## What went wrong

1. **RCO1 lost its working context and blocked Lead.** At 15:02:59Z RCO1's session moved
   into a transient, WMI-spawned daemon with no launcher ancestry. The same daemon then
   blocked Lead's launch until it exited at 15:35:29Z.
2. **L1 does not run from a Limited window.** The route changed to the lane-by-lane L2
   relaunch.
3. **Launch preflight alert for the Codex lanes.** Preflight raised an alert-only
   `attention` for both Codex lanes because `gpt-6.1-sol` is not yet in the lane catalog.
   The three Claude lanes were `allowed`.
4. **Stale monitor cursor.** After the relaunch, `fable-5`'s pinned bridge Monitor started
   from a stale default cursor (2026-09-28) and replayed old events until the harness
   stopped it. It was restarted with a fresh `-StatePath`.
5. **Claim not released before the restart.** `fable-5` had not released its claim before
   the restart, as Tools had, so the resumed session had to wait for the stale sweep.

Follow-ups worth a PR:
- add the current Codex model to the lane catalog;
- release a lane's own claims in the pre-restart checkpoint step;
- give the pinned Monitor a documented fresh-baseline option for resumed sessions;
- make the daemon / no-ancestry case visible in the lane DryRun.

## What this is not

- **Not a machine reboot.** The machine stayed up. A full reboot was not attempted and is
  not claimed to be safe.
- **Not uninterrupted operation.** Each lane was down between closing its old window and
  its new start. Per-process helpers (monitors, timers, claim refreshers) had to be
  re-armed by hand.
- **No new authority.** The relaunch itself grants no merge, deploy, signature or runtime
  authority. The reboot pointer that `ops/windows/reboot/Deploy-WdRebootBundle.ps1` writes
  says so explicitly. Code changes inside the new
  bundle arrived through their own reviewed PRs before the install. This record does not
  review them.

## Prior art

The author (`fable-5`) used web search and GitHub search on 2026-10-12. The exact query list
was not saved; the names below are what those searches returned. The building blocks are
common:

- **Messaging buses between agent CLIs**, for example rennerdo30/agent-bridge, agmsg,
  agent-bus and agentbus.
- **Role-based agent teams**, some with append-only logs, for example
  NTCoding/autonomous-claude-agent-team, Claw Orchestrator and Composio
  agent-orchestrator.
- **Review or merge gates for agent PRs**: Orbi merges only the reviewed head; Apron has a
  human gate; also pstack, meridianlabs PR 105 (head-bound approval) and fullsend issue
  7616.
- **Resuming CLI conversations after a reboot**: Herdr/kelpie use `claude --resume`, and
  agentlife keeps a Windows roster. corveil/crow issue 1281 and quiet-choir issue 23 report
  lost conversations.

The closest analogue we found is daniel-ospina/agent-infra issue 1415. It runs about 18
lanes from a single vendor (Claude via pi), with a recovery board in a GitHub issue and an
admin-merge rail.

**We found no public implementation that combines all four of these** (each one is in this
repository, with the evidence named in brackets):

1. persistent named lanes from more than one vendor, running for weeks (the fleet table
   above);
2. a fail-closed three-identity merge gate with an absolute reviewer veto and head-exact
   approvals (`CLAUDE.md` rule 9a and the gate code it names);
3. hash-pinned relaunch bundles with launcher-ancestry proof, and per-lane generations that
   resume each lane's own conversation (the timeline above);
4. an exactly-once drain of the retained write-ahead record (verified here for one record,
   event 5322870dc010).

Searching for that combination returned this repository's own PRs #1689 and #1696 first.
Not finding something in a search does not prove it does not exist. Corrections are
welcome.

## Reproducing the numbers

- **Processes:** `Get-CimInstance Win32_Process` for `claude.exe` / `codex.exe`. The
  resumed id is on the command line (`--resume <id>` / `resume <id>`).
- **Conversation files:** the Claude transcript `<id>.jsonl` and the Codex rollout
  `*<id>*.jsonl`. Size is the file length. "Since" is the first `timestamp` field in the
  file.
- **Model and effort (launch preflight):** rows for 2026-10-12 in the launch-preflight
  shadow log (`lane_profiles/launch-shadow.jsonl`). These are startup resolutions, not
  runtime measurements.
- **Bundle:** `deployment-manifest.json` in the active bundle.
- **Bridge events:** the tables use the first 12 hex characters of each canonical event
  ref as a label only. The pinned reader matches the full 64-hex ref exactly:
  `Read-AgentBridge.ps1 -Compact -EventId <full ref>` returns the event, and a
  12-character label does not (exit 2). The full refs are listed under
  [Event refs](#event-refs).
- **Committed captures:** every value in the fleet table, the Numbers table and the
  timeline is in the files under
  [`evidence/fleet-resurrection-20261012/`](evidence/fleet-resurrection-20261012/):
  - [`measurements.json`](evidence/fleet-resurrection-20261012/measurements.json)
    (sha256 `4dc65dfc8616d84d6854ed1f4329d03978cdb36185b86f9247abff184e973632`): native processes, conversation file lengths at
    19:07:05Z and 19:22:26Z, first-record times and ages, RCO1's predecessor file, the
    totals, the relaunch window, the daemon-to-launcher interval, the bundle and the
    repository counts;
  - [`launch-preflight.json`](evidence/fleet-resurrection-20261012/launch-preflight.json)
    (sha256 `e752f548bc253d751b016b20cb4f6de509084b4b214eea80a6950c092579e485`): the five launch-preflight shadow rows, verbatim, with
    their source line numbers and line hashes;
  - [`bridge-events.json`](evidence/fleet-resurrection-20261012/bridge-events.json)
    (sha256 `9939989243a24671d3cdca5e4230e05623ee40babab13735354dd193c8d83e6d`): every cited event read back by full ref, with sender,
    time, type, status, a message excerpt and the hash of the full message.

  The hashes are of the committed bytes (LF line endings). Check them with
  `git show HEAD:<path> | sha256sum`, because a checkout with `core.autocrlf` can change
  the line endings of the working copy.
- **Lineage and redaction:** the committed files are derived from the author's local raw
  captures in the ignored audit folder `.codex-audit/fable-resurrection-20261012/`. Those
  stay unchanged; each committed file names its sources and their sha256 under `lineage`.
  The raw sources are `facts-20261012.json` (19:07:05Z, sha256
  `70A75596F842A91593916AA0E3EAAACEE7D56E2E52B8EDA8BF5F969F4EE286EC`),
  `growth-20261012.json` (19:22:26Z, sha256
  `003274E844F6D0B3A82AE23378B43EBFB0CB2C8E4912A4CA47E1D7696743A63D`) and a
  re-measurement at 20:23:39Z (`remeasure-20261012T202339Z.json`, sha256
  `4EFA3DD84FD9B19CF40A97558B0B2B87154301C03FF8B0F57AB2C380331BE7F0`), plus the payloads of
  events 2cb7f29878bf and 5322870dc010 (`event-payloads-20261012T202623Z.json`, sha256
  `84729EF70107417C31FCAFF1229DF3F8717E4C22B34481BAACF5D2D3C4908859`). Conversation ids
  are cut to 8 characters, local paths are removed, event messages are cut to 400
  characters, and no transcript content is included.
- **Outside review:** one Grok overclaim check (report sha256 `e9cfe6f2…`) flagged five
  sentences. The continuity, prior-art, authority, ancestry and review-gate wording above
  was tightened in response.

## Event refs

Full canonical refs for the 12-character labels above, in event time order. Each
one was read back with the pinned `Read-AgentBridge.ps1 -Compact -EventId` on 2026-10-12
(exit 0, and the sender and time match the timeline).

| Label | Full ref |
|---|---|
| 03d59c6fbb79 | `03d59c6fbb79e52fe2b89d28075a80c2177ceca4e40161d8b52cdc443b1da197` |
| 8e73b848aeae | `8e73b848aeaee948fa95292339dc6465697e14a8515222540bf58ba2c000c6cb` |
| 6dfeb5310042 | `6dfeb5310042b8597e7f0019381d78da94bb9ee3ebc93fed33db0856710ea9ca` |
| 187547480e76 | `187547480e76928feddc70add024685addc4e1c79b6e2d51670f607706acfffe` |
| 434728209d33 | `434728209d33f55b03b19ba30c841565f663ab7d76f9a473bb8bed0e0758aad0` |
| d0b1e8fed07c | `d0b1e8fed07c87950e1df6d4015036da225f2f39917a7cb15aed78ef2b79b791` |
| f2adb2ce4842 | `f2adb2ce4842a6f4dde0e73746fd91b483bd99766a684c9dd0997efa3134e0ab` |
| 2cb7f29878bf | `2cb7f29878bf07ae0e09e994bb6f75418ccb21b2eb0d7931eee37b5353adb994` |
| 77bfa917e614 | `77bfa917e614590971ea6803f12b694a8ce75d5383fb82e1d849d3e11d25ed67` |
| 0067d2f46db0 | `0067d2f46db038f3124e526cc5520c5ec3c3a27ebfc95909378e171586c9a3cb` |
| 7afde5d60ad4 | `7afde5d60ad43b01e66048f8331646ecde83f81d4e04f63e2a44baf6fb02479a` |
| 5322870dc010 | `5322870dc01064f5c6586a0d52f93284edfaedf7d14e7a1fddebeb525c571dda` |
| bebed6af6cd9 | `bebed6af6cd975e81abaed072a34318b4a4dc291c0181c60e24c1b63964e02f9` |
| f8e7a821d80c | `f8e7a821d80c50b9aa2758e5da208fb533cae9c1203911d0256cf41edeffce2f` |
| fed3adc3c2bd | `fed3adc3c2bd93c61dc53de29e0f68f072d07fd23a3c84f6c87a36530e3f2505` |
| 877a98f0519a | `877a98f0519a0a7986bab9e0512dd36c4c04d8f42363385d5a620768ec6e763e` |
| 4562b8dcfd2d | `4562b8dcfd2dd450bad493bf50e69f42c952f9d3b4cc25080ae2bb1ebb06f474` |
| 6b49db527294 | `6b49db527294094a3d451e5f941c3abc73fc4b46e773df12902bc22237f45517` |
| c9c6dd01fcdf | `c9c6dd01fcdf8cd65b645dc8486d144be2997b974d0597cca0f2fa1f25a97e32` |
| 9bacc8d84de9 | `9bacc8d84de95f1d18261e25dde8e2a26a75d5f17a50ad35138ab26697ffb473` |
| d6fd45cb5a8e | `d6fd45cb5a8e660892b28c7761905a8bb9d3d7852c02e2e9c36a5ce0eea05e8c` |
| 0f57b928ddce | `0f57b928ddce46890cfabc5c6934cf593d3cc53a706130d387fc3ff7b3aeb3db` |
