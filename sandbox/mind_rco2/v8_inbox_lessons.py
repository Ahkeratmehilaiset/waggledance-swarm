# SPDX-License-Identifier: BUSL-1.1
"""V8 (claude-rco-2): the V3 lane inbox plus the V6 lesson cards (operator 10:17Z via RCO1: a V3 plus
V6-lessons hybrid). Sandbox code only; the tournament driver loads it like any variant.

Two files per lane:

* ``inbox.txt``: the V3 view (asks, empty slots, own PRs, dead and old items, claims, by identity and
  currentness), then SEEN lines: for each live PR line of the lane (ACT, WAIT or OWN) up to two frozen lesson
  cards that name that PR, newest first, each with its card id and a capped description. A card is advisory memory
  written by a lane; it never decides a vote.
* ``lessons.txt``: every frozen lesson card, one line each, the same lines as V6, to be searched with
  ``grep -i <word>`` for a seen-before question rather than read whole.

The cards are the driver's ``--side-input`` (``sandbox/mind/lesson_cards.py``). Without them, inbox.txt says so
and lessons.txt is empty. No gate code, no process, no network, no bridge write; refs on every inbox item.
"""
from __future__ import annotations

from typing import Any, Mapping

from sandbox.mind_rco2 import v3_lane_inbox as v3

VARIANT = "V8"
SEEN_PER_PR = 2
LINE_LIMIT = 260


def _short(text: str, limit: int = LINE_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _card_line(card: Mapping[str, Any]) -> str:
    return _short(f"{card['id']} {card['modified_utc'][:10]} {card['lane']} [{' '.join(card['keywords'][:8])}] "
                  f"{card['description']}")


def render(inputs: Mapping[str, Any], lane: str) -> dict[str, str]:
    inbox = v3.inbox_for(inputs, lane)
    cards = [card for card in ((inputs.get("side") or {}).get("cards") or []) if isinstance(card, Mapping)]
    newest = sorted(cards, key=lambda card: (str(card.get("modified_utc")), str(card.get("id"))), reverse=True)
    seen = []
    live = [item for item in inbox["act"] + inbox["wait"] + inbox["own"] if item.get("pr") is not None]
    for number in dict.fromkeys(item["pr"] for item in live):
        linked = [card for card in newest if number in (card.get("prs") or [])][:SEEN_PER_PR]
        for card in linked:
            seen.append(_short(f"SEEN PR{number} {card['id']} {card['modified_utc'][:10]} {card['lane']}: "
                               f"{card['description']}"))
    text = v3.render_text(inbox).rstrip("\n").split("\n")
    if not cards:
        seen = ["SEEN no lesson cards in the driver inputs (no --side-input), so seen-before is UNKNOWN here"]
    elif not seen:
        seen = [f"SEEN no lesson card names a live PR of this lane; search lessons.txt ({len(cards)} cards)"]
    else:
        seen.append(f"SEEN search lessons.txt for anything else ({len(cards)} cards, grep -i <word>)")
    lessons = [_card_line(card) for card in newest]
    # SEEN lines follow the live lines (ACT, WAIT, OWN), before the dead, old and summary lines.
    cut = next((index for index, line in enumerate(text) if index >= 2 and not line.startswith(("ACT", "WAIT", "OWN"))),
               len(text))
    return {"inbox.txt": "\n".join(text[:cut] + seen + text[cut:]) + "\n",
            "lessons.txt": ("\n".join(lessons) + "\n") if lessons else ""}
