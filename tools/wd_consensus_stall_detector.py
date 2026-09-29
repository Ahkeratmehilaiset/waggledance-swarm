#!/usr/bin/env python3
"""WaggleDance consensus-stall detector (Layer-2 visibility).

Makes SILENT merge stalls visible. A PR can be CI-green + CLEAN and still never
auto-merge because the consensus signals are fragmented across task_ids, posted
with a non-canonical token, or stuck at a stale head. The merge driver then finds
nothing to merge and stays quiet -- looking identical to "an agent won't review".

This scans open, non-draft, CI-green PRs and, for each, dumps the build/RCO
signals grouped BY task_id at the PR head, then DIAGNOSES why it is not merging:
  * MISSING   -- a required slot has no signal anywhere at head
  * FRAGMENTED -- the slot's signal is at head but on a NON-canonical task_id
                  (the gate keys consensus on the branch-name task_id)
  * STALE_HEAD -- the agent's only signal is at an older head
  * READY      -- canonical task carries every slot at head (driver should merge)

Read-only. Writes findings to C:\\Python\\wd_consensus_stall.log and prints a
summary. Exit code is always 0.

Installed as C:\\Python\\wd_consensus_stall_detector.py and run by the
WD-ConsensusStallDetector task. With --alert it posts through the Write-AgentEvent.ps1
of a deployed reboot bundle, pinned by --bridge-bundle and --bridge-manifest-sha256 and
verified file by file against that manifest (as wd_agent_value_metric.py does). Without
a pin it never posts: the July runtime-root writer is no longer used.
It also never posts on a PR it cannot classify: the (a)-class classifier is not in any
bundle, so it is not imported, and every stall is reported locally only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO = "Ahkeratmehilaiset/waggledance-swarm"
RUNTIME_ROOT = Path(r"C:\Python\project2-master\.agent-bridge")
BRIDGE = RUNTIME_ROOT / "shared" / "events.jsonl"
LOG = Path(r"C:\Python\wd_consensus_stall.log")
STATE = Path(r"C:\Python\wd_consensus_stall_state.json")
BUILD_AGENTS = ("codex-lead-1", "codex-tools-1")
RCO_AGENTS = ("claude-rco-1", "claude-rco-2")
TAIL_LINES = 6000
MONITOR_AGENT = "wd-stall-monitor"   # neutral, non-RCO -> a finding/message here is never a veto
STALL_ALERT_MIN = 15.0               # only alert after a stall persists this long (not freshly-opened)
REALERT_MIN = 60.0                   # re-alert the same stall at most this often


def verified_writer(bundle, manifest_sha256, name='Write-AgentEvent.ps1'):
    root = Path(bundle).resolve(strict=True)
    manifest_path = root / 'deployment-manifest.json'
    raw = manifest_path.read_bytes()
    if not re.fullmatch(r'[a-fA-F0-9]{64}', manifest_sha256 or '') or hashlib.sha256(raw).hexdigest().lower() != manifest_sha256.lower():
        raise ValueError('Bridge manifest pin missing or changed')
    manifest = json.loads(raw)
    for relative, expected in manifest['files'].items():
        target = (root / relative.replace('\\', '/')).resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError('Bridge manifest path escaped bundle')
        if hashlib.sha256(target.read_bytes()).hexdigest().lower() != expected.lower():
            raise ValueError('Bridge bundle file changed: ' + relative)
    relative = 'tools-bootstrap/.agent-bridge/bin/' + name
    if relative not in {key.replace('\\', '/') for key in manifest['files']}:
        raise ValueError('Bridge helper is not pinned')
    return root / relative


def reporting_env(bridge_root):
    # Never inherit the calling Lead/RCO's UUID, session or capabilities.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('AGENT_BRIDGE_')}
    env['AGENT_BRIDGE_RUNTIME_ROOT'] = str(bridge_root)
    return env


def gh_json(args: list[str]):
    # gh writes UTF-8; the Windows locale default would misdecode it, or raise on
    # a byte it leaves undefined (0x81 in 'Á').
    out = subprocess.run(
        ["gh", *args], check=False, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=60,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except Exception:
        return None


def ci_all_green(number: int) -> bool | None:
    out = subprocess.run(
        ["gh", "pr", "checks", str(number), "--repo", REPO],
        check=False, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=60,
    )
    lines = [ln for ln in out.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    return all("\tpass\t" in ln for ln in lines)


def load_recent_events() -> list[dict]:
    try:
        with BRIDGE.open(encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-TAIL_LINES:]
    except Exception:
        return []
    evs = []
    for ln in lines:
        try:
            evs.append(json.loads(ln))
        except Exception:
            continue
    return evs


def is_build_pass(status: str) -> bool:
    s = (status or "").lower()
    return "build_consensus" in s and "block" not in s and "request" not in s


def is_rco_pass(typ: str, status: str) -> bool:
    return typ == "decision" and "rco_pass" in (status or "").lower()


def is_veto(agent: str, typ: str, status: str) -> bool:
    s = (status or "").lower()
    return agent in RCO_AGENTS and (
        typ == "finding" or "changes_requested" in s or "blocked" in s
    )


def collect_signals(events: list[dict], head: str) -> tuple[dict, list]:
    """Return ({task_id: {agent: kind}}, [vetoes]) for signals mentioning head."""
    h = head[:12]
    by_task: dict[str, dict[str, str]] = {}
    vetoes = []
    for e in events:
        msg = e.get("message") or ""
        if h not in msg:
            continue
        ag = e.get("agent", "")
        typ = e.get("type", "")
        st = e.get("status") or ""
        tid = e.get("task_id") or ""
        if ag in BUILD_AGENTS and is_build_pass(st):
            by_task.setdefault(tid, {})[ag] = "build"
        elif ag in RCO_AGENTS and is_rco_pass(typ, st):
            by_task.setdefault(tid, {})[ag] = "rco_pass"
        if is_veto(ag, typ, st):
            vetoes.append(f"{ag}:{typ}/{st}")
    return by_task, vetoes


RECOGNIZED_HOLD_IDENTITIES = {
    "codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "operator",
}


def has_operator_signature_hold(events: list[dict], branch: str) -> bool:
    """True when a recognized identity marked this task operator-signature-held.

    An (a)-EXPLICIT carve-out PR (CLAUDE.md / gate code) with complete
    consensus intentionally does NOT merge until the operator signs; alerting
    on it every cycle is a false positive (2026-07-02: #1480 alerted 4x).
    """
    for e in events:
        if not isinstance(e, dict):
            continue
        if str(e.get("task_id") or "") != branch:
            continue
        if str(e.get("agent") or "") not in RECOGNIZED_HOLD_IDENTITIES:
            continue
        status = str(e.get("status") or "").lower()
        HOLD_MARKERS = (
            "signature_required",
            "intentional_hold",
            "operator_review_required",   # lead merge-preflight fail-closed
            "review_required",
            "operator_explicit",
            "operator_signature",
            "operator_decision",
        )
        if any(m in status for m in HOLD_MARKERS):
            return True
    return False


def is_operator_signature_class(pr: dict) -> bool | None:
    """Whether the PR's changed paths are (a)-class, which never auto-merges and
    always waits for an explicit operator signature; None when it cannot be told.

    classify_ab lives in the WD repository and imports the charter loader, which
    no reboot bundle carries. Importing it from a checkout would run unpinned
    code, so there is no trusted classifier here and the answer is always None.
    diagnose() then withholds the bridge alert: an (a)-class PR waiting on its
    signature must not be reported as a stall.
    """
    return None


def diagnose(pr: dict, events: list[dict]) -> dict | None:
    branch = pr["headRefName"]
    head = pr["headRefOid"]
    if has_operator_signature_hold(events, branch):
        print(f"[hold] PR #{pr['number']} ({branch}) is operator-signature-held; not a stall")
        return None
    signature_class = is_operator_signature_class(pr)
    if signature_class:
        print(f"[hold] PR #{pr['number']} ({branch}) is (a)-class -> operator signature required; not a stall")
        return None
    by_task, vetoes = collect_signals(events, head)
    canonical = by_task.get(branch, {})
    other_tasks = {t: a for t, a in by_task.items() if t != branch}

    slots = {
        "lead_build": ("codex-lead-1", "build"),
        "tools_build": ("codex-tools-1", "build"),
        "rco1": ("claude-rco-1", "rco_pass"),
        "rco2": ("claude-rco-2", "rco_pass"),
    }
    status = {}
    for slot, (agent, kind) in slots.items():
        if canonical.get(agent) == kind:
            status[slot] = "READY"
        elif any(sig.get(agent) == kind for sig in other_tasks.values()):
            where = [t for t, sig in other_tasks.items() if sig.get(agent) == kind]
            status[slot] = f"FRAGMENTED@{where[0]}"
        else:
            status[slot] = "MISSING"

    # charter-clean needs lead+tools+>=1 RCO; off-allowlist needs both RCO.
    problems = [s for s, v in status.items() if v != "READY"]
    return {
        "pr": pr["number"],
        "branch": branch,
        "head": head[:12],
        "head_full": head,
        "merge_state": pr.get("mergeStateStatus"),
        "vetoes": vetoes,
        "slots": status,
        "problems": problems,
        # Alert only on a PR known not to be (a)-class; unknown fails closed.
        "alert_eligible": signature_class is False,
    }


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    try:
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


SLOT_AGENTS = {
    "lead_build": "codex-lead-1",
    "tools_build": "codex-tools-1",
    "rco1": "claude-rco-1",
    "rco2": "claude-rco-2",
}


def post_alert(branch: str, message: str, do_post: bool,
               recipients: list[str] | None = None,
               payload_head: str | None = None,
               pin: tuple[str, str] | None = None) -> str:
    """Emit a bridge message (never a veto: neutral identity + type=message).

    Re-consensus targeting (roadmap slice 2, 2026-07-02): address the alert
    directly TO the agents whose slots are stale/missing so it lands as
    actionable incoming in their next-action/watcher loops, and carry the
    exact head in the payload so a re-post can bind it without a lookup.
    The writer is the pinned bundle's Write-AgentEvent.ps1; no pin, no post.
    """
    if not do_post:
        return "DRY-ALERT"
    if not pin:
        return "ALERT-SKIPPED:unpinned-writer"
    to = ",".join(recipients) if recipients else "codex-lead-1"
    try:
        writer = verified_writer(pin[0], pin[1])
    except Exception as exc:
        return f"ALERT-FAILED:{type(exc).__name__}"
    cmd = ["pwsh", "-NoProfile", "-NonInteractive", "-File", str(writer),
           "-Agent", MONITOR_AGENT, "-Role", "monitor", "-Type", "message",
           "-TaskId", branch, "-Status", "consensus_stall_detected",
           "-To", to, "-Message", message, "-ReceiptJson"]
    if payload_head:
        cmd += ["-PayloadJson", json.dumps({"head": payload_head})]
    try:
        # Captured as bytes: the console code page is not UTF-8, and a decode in
        # subprocess's reader thread would lose the receipt after a durable write.
        result = subprocess.run(
            cmd,
            check=False, capture_output=True, timeout=60,
            env=reporting_env(RUNTIME_ROOT),
        )
    except Exception as exc:
        return f"ALERT-FAILED:{type(exc).__name__}"
    if result.returncode != 0:
        return f"ALERT-FAILED:exit-{result.returncode}"
    return delivery_note(result.stdout, to)


def delivery_note(stdout: bytes, to: str) -> str:
    """ALERTED only when the writer's -ReceiptJson receipt says the event is durable
    in the canonical log this detector reads. Exit 0 alone can mean queued."""
    lines = [ln for ln in stdout.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    try:
        delivery = json.loads(lines[-1])["_bridge_delivery"]
    except Exception:
        return "ALERT-UNCONFIRMED:no-receipt"
    if not isinstance(delivery, dict):
        return "ALERT-UNCONFIRMED:no-receipt"
    status = delivery.get("delivery_status")
    if (delivery.get("accepted") is not True or delivery.get("canonical_durable") is not True
            or status != "canonical"):
        return f"ALERT-UNCONFIRMED:{status if status in ('queued', 'suppressed') else 'not-canonical'}"
    path = delivery.get("events_path")
    if not isinstance(path, str) or os.path.normcase(os.path.normpath(path)) != os.path.normcase(str(BRIDGE)):
        return "ALERT-UNCONFIRMED:other-log"
    return f"ALERTED->{to}"


def maybe_alert(d: dict, state: dict, now: datetime, do_post: bool,
                pin: tuple[str, str] | None = None) -> str | None:
    """Dedup + persistence gate: alert only after a stall lasts STALL_ALERT_MIN,
    and re-alert at most every REALERT_MIN. Returns an action note or None."""
    key = f"{d['pr']}@{d['head']}"
    rec = state.get(key) or {"first_seen": now.isoformat()}
    state[key] = rec
    first = datetime.fromisoformat(rec["first_seen"])
    age = (now - first).total_seconds() / 60.0
    if age < STALL_ALERT_MIN:
        return f"watching ({age:.0f}min<{STALL_ALERT_MIN:.0f})"
    last = rec.get("last_alert")
    if last and (now - datetime.fromisoformat(last)).total_seconds() / 60.0 < REALERT_MIN:
        return "recently-alerted"
    if d.get("alert_eligible") is not True:
        return "ALERT-WITHHELD:unclassified"
    frag = [s for s, v in d["slots"].items() if str(v).startswith("FRAGMENTED")]
    miss = [s for s, v in d["slots"].items() if v == "MISSING"]
    fix = []
    if frag:
        fix.append(f"re-post {frag} on canonical task '{d['branch']}'")
    if miss:
        fix.append(f"obtain {miss} at head {d['head']}")
    stale_agents = sorted({
        SLOT_AGENTS[s] for s in (frag + miss) if s in SLOT_AGENTS
    })
    full_head = d.get("head_full") or d["head"]
    msg = (f"[STALL-ALERT] PR #{d['pr']} is CI-green + {d['merge_state']} at head "
           f"{full_head} but not merging for ~{age:.0f}min. Slots: {d['slots']}. "
           f"FIX: {'; '.join(fix) or 'investigate veto/'+str(d['vetoes'])}. "
           f"Addressed to the stale slots - please re-post at this exact head.")
    note = post_alert(d["branch"], msg, do_post,
                      recipients=(stale_agents or None), payload_head=full_head, pin=pin)
    # Only an alert whose receipt proves it durable in the canonical log starts the
    # re-alert quiet period; a dry, skipped, failed or unconfirmed one must not
    # hold back the next run that can post.
    if note.startswith("ALERTED->"):
        rec["last_alert"] = now.isoformat()
    return note


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="WD consensus-stall detector")
    ap.add_argument("--alert", action="store_true",
                    help="emit a bridge message for stalls that persist past the threshold")
    ap.add_argument("--bridge-bundle", default=None,
                    help="deployed reboot bundle whose pinned Write-AgentEvent.ps1 posts alerts")
    ap.add_argument("--bridge-manifest-sha256", default=None,
                    help="SHA-256 of that bundle's deployment-manifest.json")
    args = ap.parse_args(argv)
    if (args.bridge_bundle is None) != (args.bridge_manifest_sha256 is None):
        ap.error("--bridge-bundle and --bridge-manifest-sha256 go together")
    pin = (args.bridge_bundle, args.bridge_manifest_sha256) if args.bridge_bundle else None
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat(timespec="seconds")
    prs = gh_json([
        "pr", "list", "--repo", REPO, "--state", "open",
        "--json", "number,title,headRefName,headRefOid,mergeStateStatus,isDraft",
    ]) or []
    events = load_recent_events()
    state = load_state()
    findings = []
    for pr in prs:
        if pr.get("isDraft"):
            continue
        if pr.get("mergeStateStatus") not in ("CLEAN", "UNSTABLE", "BLOCKED"):
            continue
        green = ci_all_green(pr["number"])
        if green is not True:
            continue  # CI not (yet) green -> not a consensus stall
        d = diagnose(pr, events)
        if d and (d["problems"] or d["vetoes"]):
            findings.append(d)

    lines = [f"[{now}] scanned {len(prs)} open PRs; {len(findings)} consensus-stalled"]
    for d in findings:
        lines.append(
            f"  PR #{d['pr']} head={d['head']} {d['merge_state']} "
            f"branch={d['branch']}"
        )
        for slot, st in d["slots"].items():
            mark = "OK " if st == "READY" else "!! "
            lines.append(f"      {mark}{slot:12} {st}")
        if d["vetoes"]:
            lines.append(f"      VETO {d['vetoes']}")
        frag = [s for s, v in d["slots"].items() if str(v).startswith("FRAGMENTED")]
        miss = [s for s, v in d["slots"].items() if v == "MISSING"]
        hint = []
        if frag:
            hint.append(f"re-post {frag} on canonical task '{d['branch']}'")
        if miss:
            hint.append(f"obtain {miss} at head {d['head']}")
        if hint:
            lines.append(f"      FIX: {'; '.join(hint)}")
        note = maybe_alert(d, state, now_dt, args.alert, pin=pin)
        if note:
            lines.append(f"      ALERT: {note}")

    # prune state for PRs that are no longer stalled (merged/closed/fixed)
    live_keys = {f"{d['pr']}@{d['head']}" for d in findings}
    for k in list(state.keys()):
        if k not in live_keys:
            del state[k]
    save_state(state)

    report = "\n".join(lines)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(report + "\n")
    except Exception:
        pass
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
