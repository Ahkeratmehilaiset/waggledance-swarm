"""V6: lesson cards plus the palace room of the lane's current tasks (cards-first; no vectors, model or corpus).

Two files per lane:

* ``room.txt``: the lane's rooms in a small bridge memory palace (``waggledance/core/memory_palace``: a wing per
  lane, a room per bridge task). A room is one task: the lane's current task from the snapshot, the tasks of the
  PRs whose attention items name the lane, and the lane's most recent own tasks in the window. Each room has a
  closet (counts by type and status, the last decision per agent with its head, first and last time) and its last
  drawers: event refs with time, agent, type, status and head, never message text (verbatim stays in the pinned
  capture, read on demand by ref). Under each room, the lesson cards that name its PR or share its key words.
* ``lessons.txt``: every frozen lesson card, one line each (id, date, lane, keywords, capped description), newest
  first, meant to be searched (``grep -i <word>``) rather than read whole.

The cards come from ``--side-input`` (``sandbox/mind/lesson_cards.py``, frozen with the round time as as-of).
"""

import re

from tools import bridge_mind_workspace as ws
from waggledance.core.memory_palace.projection import PalaceNode, build_memory_palace_navigation_index

VARIANT = "V6"
ROOM_LIMIT = 6
DRAWER_LIMIT = 10
CARD_LIMIT = 4
LINE_LIMIT = 260
WORD = re.compile(r"[a-z0-9]+")
COMMON = frozenset({"fable", "rco", "codex", "lead", "tools", "claude", "review", "formal", "bridge", "mind", "pr"})


def _short(text, limit=LINE_LIMIT):
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _node_id(prefix, text):
    return (prefix + re.sub(r"[^a-z0-9_.:-]", "-", text.lower().replace("/", ":")))[:128]


def _head(event):
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    head = payload.get("head") or event.get("head") or ""
    return head[:12] if isinstance(head, str) and ws.HEAD.fullmatch(head) else "-"


def _words(text):
    return {word for word in WORD.findall(text.lower()) if len(word) > 3 and not word.isdigit()} - COMMON


def _rooms(inputs, lane):
    """Task ids for the lane, in order: current task, attention PR tasks, own recent tasks."""
    snapshot, context = inputs["snapshot"], inputs["context"]
    tasks = []
    lanes = sorted((row for row in snapshot["lanes"].values() if row.get("agent") == lane),
                   key=lambda row: str(row.get("last_event_utc")), reverse=True)
    tasks += [row["current_task"] for row in lanes if isinstance(row.get("current_task"), str)][:1]
    prs = context["prs"] if context else {}
    for item in snapshot["attention"]:
        match = ws._PR_SUBJECT.fullmatch(item["subject"])
        if lane in item["actors"] and match and match.group(1) in prs:
            tasks.append(prs[match.group(1)]["head_ref_name"])
    for event in reversed(inputs["events"]):
        if event.get("agent") == lane and isinstance(event.get("task_id"), str) and event.get("type") != "claim":
            tasks.append(event["task_id"])
        if len(dict.fromkeys(tasks)) >= ROOM_LIMIT:
            break
    return list(dict.fromkeys(task for task in tasks if ws.TASK.fullmatch(task)))[:ROOM_LIMIT]


def _closet(events):
    counts, last = {}, {}
    for event in events:
        key = f"{event.get('type')}/{event.get('status')}"
        counts[key] = counts.get(key, 0) + 1
        if event.get("type") == "decision":
            last[event.get("agent")] = f"{event.get('status')}@{_head(event)}"
    top = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:6]
    return (f"  closet: {len(events)} events {events[0].get('ts_utc', '?')[:16]}..{events[-1].get('ts_utc', '?')[:16]}; "
            + ", ".join(f"{key} {count}" for key, count in top)
            + ("; last decisions: " + ", ".join(f"{agent} {value}" for agent, value in sorted(last.items()))
               if last else ""))


def _pr_of(task, context):
    for number, row in (context["prs"] if context else {}).items():
        if row["head_ref_name"] == task:
            return int(number)
    return None


def render(inputs, lane):
    from tools.bridge_compact_view import event_id

    cards = (inputs.get("side") or {}).get("cards") or []
    tasks = _rooms(inputs, lane)
    by_task = {task: [] for task in tasks}
    for event in inputs["events"]:
        if event.get("task_id") in by_task:
            by_task[event["task_id"]].append(event)
    wing = _node_id("wing.", lane)
    nodes = [PalaceNode(node_id=wing, kind="wing", label=lane)]
    nodes += [PalaceNode(node_id=_node_id("room.", task), kind="room", label=task, parent_id=wing) for task in tasks]
    paths = build_memory_palace_navigation_index(nodes)["paths_by_node"]
    lines = [f"V6 palace rooms for {lane}; round {inputs['round']}, capture {inputs['capture_sha256'][:16]}, "
             f"{len(cards)} lesson cards; drawers hold refs, never message text; observe only, check canonical tools",
             ""]
    for task in tasks:
        events = by_task[task]
        node = _node_id("room.", task)
        lines.append(f"room {' > '.join(paths[node])} ({task})")
        if events:
            lines.append(_closet(events))
            for event in events[-DRAWER_LIMIT:]:
                lines.append(f"  drawer {event_id(event)[:16]} {event.get('ts_utc', '?')[:19]} {event.get('agent')} "
                             f"{event.get('type')}/{event.get('status')} head {_head(event)}")
        else:
            lines.append("  closet: no event of this task in the window")
        number, words = _pr_of(task, inputs["context"]), _words(task)
        linked = [card for card in cards if (number is not None and number in card["prs"])
                  or len(words & set(card["keywords"])) >= 2][:CARD_LIMIT]
        for card in linked:
            lines.append(_short(f"  lesson {card['id']} {card['modified_utc'][:10]} {card['lane']}: "
                                f"{card['description']}"))
        lines.append("")
    index = [_short(f"{card['id']} {card['modified_utc'][:10]} {card['lane']} [{' '.join(card['keywords'][:8])}] "
                    f"{card['description']}") for card in cards]
    return {"room.txt": "\n".join(lines) + "\n", "lessons.txt": "\n".join(index) + "\n"}
