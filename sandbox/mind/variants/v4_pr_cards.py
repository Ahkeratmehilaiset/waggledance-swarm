"""V4: PR cards. Per lane: what is asked of it, a full card for each PR it is involved in, one line per other PR.

A card shows, for one PR, the bridge head and the head the PR context saw, reported CI, the verified votes at the
head, the empty slots (a declared commit-author RCO fills none), the vetoes (and an own later pass), the stale
votes and the votes bound to the context head, the declared class next to the computed one, the attention items
about it, and the first refs. Everything comes from the reflected snapshot and the frozen context through the
workspace's own reading (``_Inputs``); nothing is recomputed differently and nothing is a gate verdict.

A lane is involved in a PR when its branch starts with the lane id, or the lane voted, vetoed or is a declared
commit author, or an attention item about the PR names the lane.

The ready queue (round 3, operator 10:30Z "useful, speeds up current work"; RCO1 10:31Z): every open PR with CI
reported green at its head, no observed veto and no empty vote slot (a declared author or recused RCO fills none),
with the time of its last vote at that head. Nothing in the bridge view is missing for it; whether the merge route
is active is not in the bridge view, so the queue shows how long finished work has waited, never why.

Round 4 (RCO1/RCO2 10:34-10:38Z, Lead 10:37Z): with ``inputs["observed_status"]`` (the read-only GitHub status
capture, ``wd.bridge-mind-gh-status.v1``) a PR whose CI nobody posted on the bridge also enters the queue when
GitHub reported every required check SUCCESS at that same head (labelled "CI observed by GitHub"), and each line
shows GitHub's merge state at that head (else UNKNOWN). The installed merge capability is one attributed line:
the latest structured ``installed_capability`` statement of a declared ``codex-lead-1`` identity, else UNKNOWN;
it is never inferred from votes, CI or a source file.

When the bridge view's head lags the head GitHub reports (the core moves a PR head only on a head-update status,
so a re-push announced as a review request is missed: PR1861, PR1873, PR1875, PR1893 at 10:36Z), the queue also
takes the PR at the GitHub head the context saw: the verified votes bound to that head fill the slots, GitHub's
own required checks at that head stand for CI, and any veto observation keeps it out as before.
"""

from tools.bridge_compact_view import event_id

from tools import bridge_mind_workspace as ws

VARIANT = "V4"
DETAIL_LIMIT = 220
INDEX_LIMIT = 120


def _short(text, limit=DETAIL_LIMIT):
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _h(head):
    return head[:12] if head and head != ws.UNKNOWN else ws.UNKNOWN


def _about(snapshot):
    """Attention items and tensions keyed by PR number (from the item or tension subject)."""
    tensions = {tension["tension_id"]: tension for tension in snapshot["tensions"]}
    items, rules = {}, {}
    for item in snapshot["attention"]:
        subject = item["subject"]
        if subject.startswith("tension:"):
            subject = tensions.get(subject[len("tension:"):], {}).get("subject", "")
        match = ws._PR_SUBJECT.fullmatch(subject)
        if match:
            items.setdefault(int(match.group(1)), []).append(item)
    for tension in snapshot["tensions"]:
        match = ws._PR_SUBJECT.fullmatch(tension["subject"])
        if match:
            rules.setdefault(int(match.group(1)), []).append(tension)
    return items, rules


def _card(inputs, pr, items, tensions):
    row = inputs.context_row(pr)
    reason = inputs.suppressed(pr)
    lines = [f"PR{pr['pr']} bridge head {_h(pr['head'])}"
             + ("" if row is None else f"; context {row['state']} head {_h(row['head'])} branch {row['branch']}")
             + ("" if reason is None else f"; NO SUGGESTION ({reason})")]
    lines.append("  CI: " + (f"reported green at {_h(pr['head'])}" if pr["ci_green"] else
                             "not reported green at this head"))
    votes = [f"{vote['agent']} {vote['status']}" + ("" if vote["verified"] else " (unverified)")
             for vote in pr["votes"]]
    lines.append("  votes at head: " + (", ".join(votes) if votes else "none"))
    if reason is None:
        authors = inputs.authors(pr)
        empty = ws._empty_slots(pr, authors)
        lines.append("  empty slots: " + (", ".join(empty) if empty else "none")
                     + ("" if not set(ws.RECOGNIZED_RCOS) & authors else
                        f" (author RCO fills none: {', '.join(sorted(set(ws.RECOGNIZED_RCOS) & authors))})")
                     + ("" if not pr["recused"] else f" (recused at this head: {', '.join(pr['recused'])})"))
    if row is not None:
        lines.append("  commit authors: " + (ws.UNKNOWN if row["authors"] is None else
                                             ", ".join(row["authors"]) or "none lane-shaped"))
    own_later = {tension["subject"] for tension in tensions if tension["rule"] == "veto_own_later_pass"}
    for veto in pr["vetoes"]:
        subject = f"pr:{pr['pr']}:veto:{veto['agent']}:{veto['head'][:12]}"
        lines.append(f"  veto: {veto['agent']} {veto['status']} at {_h(veto['head'])}"
                     + (" (same identity passed later at a current head; still observed)" if subject in own_later
                        else " (observed; activity UNKNOWN)"))
    at_context = inputs.context_head_votes(pr)
    stale = [vote for vote in pr["stale_votes"] if vote not in at_context]
    if at_context:
        lines.append("  votes at the context head (not counted): "
                     + ", ".join(f"{vote['agent']} {vote['status']}" for vote in at_context))
    if stale:
        lines.append(f"  stale votes ({len(stale)}): "
                     + _short(", ".join(sorted({f"{vote['agent']}@{_h(vote['head'])}" for vote in stale}))))
    computed = inputs.computed_class(pr)
    klass = f"  class: declared {pr['class']}, computed {computed or ws.UNKNOWN}"
    if computed is not None:
        path_class = row["path_class"]
        klass += f" (a_hits {len(path_class['a_hits'])}, denylist {len(path_class['denylist_hits'])})"
    lines.append(klass)
    for item in items:
        lines.append(f"  attention #{item['rank']} {item['kind']}: {_short(item['detail'])}")
    lines.append(f"  refs: pr {pr['refs'][0][:16]}" + (f", ci {pr['ci_refs'][0][:16]}" if pr["ci_refs"] else ""))
    return lines


def _index_line(inputs, pr, items):
    row = inputs.context_row(pr)
    state = "?" if row is None else row["state"]
    reason = inputs.suppressed(pr)
    empty = "" if reason is not None else ",".join(ws._empty_slots(pr, inputs.authors(pr))) or "-"
    first = items[0] if items else None
    line = (f"PR{pr['pr']} {state} {_h(pr['head'])} ci={'green' if pr['ci_green'] else 'no'} "
            f"missing={empty or reason} class={pr['class']}/{inputs.computed_class(pr) or '?'}"
            + (f" att=#{first['rank']}" if first else "") + (" veto" if pr["vetoes"] else ""))
    return _short(line, INDEX_LIMIT)


STATUS_SCHEMA = "wd.bridge-mind-gh-status.v1"
CAPABILITY_STATES = ("absent", "present", "unknown")
CAPABILITY_SOURCE = "codex-lead-1"


def _bot_text(observed, head):
    """Bot review threads at this head: open ones by severity (outdated apart), else none, else UNKNOWN."""
    bot = (observed or {}).get("bot_threads")
    if not isinstance(bot, dict) or bot.get("head") != head or not isinstance(bot.get("bot"), dict):
        return "bot review UNKNOWN"
    open_rows = bot["bot"].get("open") or {}
    text = ("bot open " + ", ".join(f"{sev}x{n}" for sev, n in sorted(open_rows.items()))) if open_rows \
        else "bot open none"
    outdated = sum((bot["bot"].get("outdated") or {}).values())
    return text + (f" ({outdated} outdated)" if outdated else "") + ("" if bot.get("complete") else " (partial)")


def _mentioned_by(status, pr):
    """Open PRs whose title or body mentions this PR (a GitHub fact; never read as replacement)."""
    if not status or not status.get("mentions_captured"):
        return None
    return sorted(int(number) for number, row in status["prs"].items()
                  if isinstance(row, dict) and pr["pr"] in (row.get("mentions") or []))


def _status_row(status, pr, head=None):
    """GitHub's observed row for the PR at the given head (default: the bridge head), or None."""
    head = head or pr["head"]
    row = (status or {}).get("prs", {}).get(str(pr["pr"]))
    return row if isinstance(row, dict) and head and row.get("head") == head else None


def _ready(parsed, events, status):
    """(since, pr, ci) for each PR with no vote missing, oldest first; since = its last vote time at this head."""
    times = {event_id(event): event.get("ts_utc") for event in events if isinstance(event.get("ts_utc"), str)}
    ready = []
    for pr in parsed.prs:
        if pr["vetoes"] or pr["veto_unreadable"]:
            continue
        row = parsed.context_row(pr)
        if parsed.suppressed(pr) is None and pr["head"]:
            head, votes, recused = pr["head"], pr["votes"], pr["recused"]
        elif parsed.suppressed(pr) == "head_mismatch" and row is not None and row["state"] == "OPEN":
            # The bridge view lags: the GitHub head the context saw, with the verified votes bound to it.
            head, votes, recused = row["head"], parsed.context_head_votes(pr), []
        else:
            continue
        observed = _status_row(status, pr, head)
        ci = "bridge" if pr["ci_green"] and head == pr["head"] else (
            "GitHub" if observed and observed.get("all_required_success") is True else None)
        if ci and not ws._empty_slots(dict(pr, votes=votes, recused=recused), parsed.authors(pr)):
            stamps = [times.get(ref) for vote in votes for ref in vote["refs"]]
            ready.append((max([stamp for stamp in stamps if stamp] or [ws.UNKNOWN]), pr, ci, head))
    return sorted(ready, key=lambda row: (row[0], row[1]["pr"]))


def _class_at(parsed, pr, head):
    """The computed class at ``head``: the workspace's at the bridge head; at a lagging bridge view, the context
    row's own charter classification, which GitHub computed at exactly that head (else None)."""
    if head == pr["head"]:
        return parsed.computed_class(pr)
    row = parsed.context_row(pr)
    if row is None or row["head"] != head or not parsed.context["charter_ok"] or row["path_class"] is None:
        return None
    return "protected" if row["path_class"]["a_hits"] or row["path_class"]["denylist_hits"] else "not_protected"


def _ready_line(parsed, since, pr, ci, head, status, lane, items):
    computed = _class_at(parsed, pr, head)
    left = "operator signature (computed protected)" if computed == "protected" else "no vote"
    excused = sorted((set(pr["recused"]) if head == pr["head"] else set())
                     | (set(ws.RECOGNIZED_RCOS) & parsed.authors(pr)))
    observed = _status_row(status, pr, head)
    lag = "" if head == pr["head"] else f" (GitHub head; bridge view at {_h(pr['head'])})"
    by = _mentioned_by(status, pr)
    return _short(f"  VOTED PR{pr['pr']} {_h(head)}{lag} since {since[:16]}; "
                  f"gh {observed['merge_state'] if observed else ws.UNKNOWN}; "
                  + ("CI reported on the bridge" if ci == "bridge" else "CI observed by GitHub, not on the bridge")
                  + f"; {_bot_text(observed, head)}"
                  + ("" if not by else "; mentioned by open " + ", ".join(f"PR{n}" for n in by))
                  + f"; left: {left}; class {computed or ws.UNKNOWN}"
                  + (f"; excused {', '.join(excused)}" if excused else "")
                  + (" (yours)" if _involved(parsed, pr, lane, items.get(pr["pr"], [])) else ""), 340)


def _capability_lines(events):
    """The latest structured installed_capability statement per name from a declared codex-lead-1, else UNKNOWN."""
    latest = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        statement = payload.get("installed_capability")
        uuid = event.get("agent_uuid")
        if event.get("agent") != CAPABILITY_SOURCE or not (isinstance(uuid, str) and ws.UUID.fullmatch(uuid)) \
                or not isinstance(statement, dict) or statement.get("state") not in CAPABILITY_STATES \
                or not (isinstance(statement.get("name"), str) and ws.SAFE_ID.fullmatch(statement["name"])):
            continue
        latest[statement["name"]] = (str(event.get("ts_utc"))[:19], statement["state"], event_id(event)[:16])
    if not latest:
        return ["installed merge capability: UNKNOWN (no structured installed_capability statement from "
                f"{CAPABILITY_SOURCE} in this window; never inferred from votes, CI or source files)"]
    return [f"installed capability {name}: {state} per {CAPABILITY_SOURCE} (declared, origin not authenticated) at "
            f"{ts}, ref {ref}" for name, (ts, state, ref) in sorted(latest.items())]


def _involved(inputs, pr, lane, items):
    row = inputs.context_row(pr)
    if row is not None and row["branch"].startswith(lane + "/"):
        return True
    agents = {vote["agent"] for vote in pr["votes"] + pr["stale_votes"] + pr["vetoes"]}
    authors = set() if row is None or row["authors"] is None else set(row["authors"])
    return lane in agents | authors or any(lane in item["actors"] for item in items)


def render(inputs, lane):
    snapshot = inputs["snapshot"]
    parsed = ws._Inputs(snapshot, inputs["context"])
    items, tensions = _about(snapshot)
    source = snapshot["source"]
    lines = [f"V4 PR cards for {lane}; round {inputs['round']}, snapshot {inputs['snapshot_sha256'][:16]}, capture "
             f"{inputs['capture_sha256'][:16]}, context {inputs['context_sha256'][:16]}, as of {source['as_of_utc']}",
             "observe only; before any vote or pass check the canonical tools", ""]
    question = snapshot["next_question"]["lanes"].get(lane, {"question": ws.UNKNOWN})
    lines.append(f"next question for {lane}: {question['question']}")
    mine = [item for item in snapshot["attention"] if lane in item["actors"]]
    for item in mine:
        lines.append(f"  attention #{item['rank']} {item['kind']}: {_short(item['detail'])}")
    # Claims are left out: the core v0 still lists claims the log already released (RCO2 09:29Z), and bridge claim
    # events carry no lease, so a claim list here would be stale noise.
    current =[row.get("current_task") for row in snapshot["lanes"].values() if row.get("agent") == lane]
    lines.append(f"  current task (last event): {', '.join(sorted({str(task) for task in current})) or ws.UNKNOWN}")
    status = inputs.get("observed_status")
    status = status if isinstance(status, dict) and status.get("schema") == STATUS_SCHEMA else None
    ready = _ready(parsed, inputs["events"], status)
    lines += [""] + _capability_lines(inputs["events"])
    lines += [f"vote-complete queue, votes only, NOT merge eligibility ({len(ready)} PRs" + (f", oldest since {ready[0][0][:16]}" if ready else "")
              + "; GitHub status " + (f"observed {status['observed_utc']}" if status else "UNKNOWN") + "):"]
    lines += [_ready_line(parsed, since, pr, ci, head, status, lane, items) for since, pr, ci, head in ready]
    involved = [pr for pr in parsed.prs if _involved(parsed, pr, lane, items.get(pr["pr"], []))
                and parsed.suppressed(pr) not in ("MERGED", "CLOSED")]
    # A full card only where something is live: an attention item, a reported-green head, a vote or a veto.
    live = [pr for pr in involved if items.get(pr["pr"]) or pr["ci_green"] or pr["votes"] or pr["vetoes"]]
    lines += ["", f"your PRs with something live ({len(live)} cards, first attention rank first):"]
    rank = {pr["pr"]: min([item["rank"] for item in items.get(pr["pr"], [])] or [999]) for pr in live}
    for pr in sorted(live, key=lambda pr: (rank[pr["pr"]], -pr["pr"])):
        lines += _card(parsed, pr, items.get(pr["pr"], []), tensions.get(pr["pr"], []))
    quiet = [pr for pr in involved if pr not in live]
    lines += ["", f"your other open PRs ({len(quiet)}, nothing live in this window):"]
    lines += [_index_line(parsed, pr, []) for pr in sorted(quiet, key=lambda pr: -pr["pr"])]
    others = [pr for pr in parsed.prs if pr not in involved and parsed.suppressed(pr) not in ("MERGED", "CLOSED")]
    lines += ["", f"other open or unknown-state PRs in the bridge view ({len(others)}):"]
    lines += [_index_line(parsed, pr, items.get(pr["pr"], [])) for pr in sorted(others, key=lambda pr: -pr["pr"])]
    return {"cards.txt": "\n".join(lines) + "\n"}
