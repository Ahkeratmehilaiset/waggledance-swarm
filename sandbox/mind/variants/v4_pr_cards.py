"""V4: PR cards. Per lane: what is asked of it, a full card for each PR it is involved in, one line per other PR.

A card shows, for one PR, the bridge head and the head the PR context saw, reported CI, the verified votes at the
head, the empty slots (a declared commit-author RCO fills none), the vetoes (and an own later pass), the stale
votes and the votes bound to the context head, the declared class next to the computed one, the attention items
about it, and the first refs. Everything comes from the reflected snapshot and the frozen context through the
workspace's own reading (``_Inputs``); nothing is recomputed differently and nothing is a gate verdict.

A lane is involved in a PR when its branch starts with the lane id, or the lane voted, vetoed or is a declared
commit author, or an attention item about the PR names the lane.
"""

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
                        f" (author RCO fills none: {', '.join(sorted(set(ws.RECOGNIZED_RCOS) & authors))})"))
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
    for lane_row in parsed.lanes:
        if lane_row["agent"] == lane:
            for claim in lane_row["claims"]:
                expires = claim["expires"] if isinstance(claim["expires"], str) else claim["expires"].isoformat()
                lines.append(f"  claim {claim['task']} lease {expires}")
    current = [row.get("current_task") for row in snapshot["lanes"].values() if row.get("agent") == lane]
    lines.append(f"  current task (last event): {', '.join(sorted({str(task) for task in current})) or ws.UNKNOWN}")
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
