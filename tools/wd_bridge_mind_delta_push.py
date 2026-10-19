# SPDX-License-Identifier: BUSL-1.1
"""Bridge mind delta push v0: typed changes between two reflected ``wd.bridge-mind.v1`` snapshots.

Dormant and unwired. Given the previous and the current reflected snapshot (the projector core's snapshot
with the workspace's reflection fields filled), it emits one JSON line per change, so a reader can follow
the mind without re-reading the bridge log ("PR1895 CI green, the rco vote is missing"). It is the pure part
of an eventual Monitor adapter; it installs nothing, starts nothing, writes no file and writes no bridge
event. Output goes to stdout only.

What it compares is a small *view* of each snapshot, built from identifiers only:

* ``source``: ``as_of_utc``, ``coverage``, ``log_identity``, ``generation`` and ``blocked``.
* ``prs``: per PR (the core's ``prs`` dictionary), the head, the reported CI status and its head, the
  class, and the current votes, stale votes and veto observations as ``agent:status[:head]`` identifiers.
  In each field ``%`` and ``:`` are percent-encoded (``%25``, ``%3A``), so ``:`` only ever separates fields
  and two different observations never share an identifier; plain tokens keep their exact bytes.
  A row whose ``agent_uuid`` is not a declared identity ends in ``:undeclared``, because the workspace
  never lets it fill a slot.
* ``tensions``: per ``tension_id``, rule, severity, subject, actors and refs.
* ``attention``: per kind and subject, rank, actors and refs.
* ``next_question``: the global question and one per lane, with its refs.

Every change line carries the section, the key and the complete new value (or ``removed``). Applying the
changes to the previous view gives exactly the current view; that is the contract the tests hold. The
lines are sorted, so the same two snapshots always give the same bytes.

Refusals, all fail-closed with a fixed message and nothing printed:

* an input that is not one canonical JSON object (the bridge JSON reader: no duplicate key, no NaN);
* a snapshot that is not ``wd.bridge-mind.v1``, whose ``authority`` is not ``none`` or whose ``scope`` is
  not ``bounded_projection_not_gate``;
* a current snapshot older than the previous one (``as_of_utc`` went backwards);
* a section of the wrong shape (``prs``, ``next_question`` and its ``lanes`` are dictionaries, ``tensions``
  and ``attention`` lists), or a tension, attention or question row that cannot be keyed or whose key is not
  unique: a row is never dropped, so a missing row cannot read as ``removed``;
* a guarded private-marker word in either view or in the printed lines, in any letter case or
  compatibility form, before or after each backslash-u unescape, HTML character reference and percent
  decoding, in any order and mix, and in each form's skeleton (format characters and combining marks removed,
  Cyrillic or Greek look-alike letters read as Latin), within the same bounded, fail-closed closure as the
  workspace guard. JSON inside strings is never parsed.

When the log identity or generation differs between the two snapshots, or either side's is not a known
identity (missing, not a string, not the core's token, or ``UNKNOWN``; a null generation is known), the only
change is ``rebuild_required`` with a fixed reason: the previous view cannot be updated across a replaced or
unidentified log, and the rejected value is never printed. The reconstruction contract is at the level of
this identifier view, not the full snapshot. Not on the gate verdict path. The guard is a copy of the
workspace's at 39f6e822 (a dormant tool may not import another dormant tool, so neither becomes wired by the
other); a test keeps every guard constant and function body equal to the workspace's.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
import unicodedata
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The shared guarded-word check, imported so this file carries no marker literal: the charter diff gate
# refuses those literals, and a copied word list could drift from the shared one.
from tools.bridge_diff_privacy import _marker as _guarded_word  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

SNAPSHOT_SCHEMA = "wd.bridge-mind.v1"
SNAPSHOT_SCOPE = "bounded_projection_not_gate"
DELTA_SCHEMA = "wd.bridge-mind.delta.v0"
SECTIONS = ("source", "prs", "tensions", "attention", "next_question")
UNKNOWN = "UNKNOWN"
UNESCAPE_ROUNDS = 4
# The most distinct decoded forms of one text the guard checks; a text with more is refused, never passed unchecked.
FORM_LIMIT = 64
# What a renderer may not show, removed for the skeleton: format characters and every combining mark (Mn, Mc, Me).
SKELETON_DROPPED = frozenset({"Cf", "Mn", "Mc", "Me"})
# Cyrillic and Greek letters a renderer shows like a Latin letter (RCO1's JOIN injection probe passed a Cyrillic one).
# Only letters whose usual glyph matches; a look-alike outside this map is a known limit.
_LOOKALIKE = str.maketrans(
    "\u0410\u0412\u0415\u041a\u041c\u041d\u041e\u0420\u0421\u0422\u0425\u0423\u0406\u0408\u0405"
    "\u0430\u0435\u043e\u0440\u0441\u0443\u0445\u0456\u0458\u0455\u04cf\u0501\u051b\u051d"
    "\u0391\u0392\u0395\u0396\u0397\u0399\u039a\u039c\u039d\u039f\u03a1\u03a4\u03a5\u03a7"
    "\u03bf\u03b9\u03ba\u03c1\u03bd",
    "ABEKMHOPCTXYIJS"
    "aeopcyxijsldqw"
    "ABEZHIKMNOPTYX"
    "oikpv")
_ESCAPED = re.compile(r"\\u([0-9a-fA-F]{4})")
_TIME = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(?:Z|\+00:00)")
_PR_KEY = re.compile(r"[1-9][0-9]{0,9}")
# The core's identity token (``_token``); the core refuses a log identity or generation that is not one.
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}")
_MISSING = object()
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")


class DeltaError(ValueError):
    """Fixed messages that never echo input content."""


def _skeleton(form: str) -> str:
    """The text as a renderer shows it: format characters (Cf: ZWJ, ZWNJ, soft hyphen) and combining marks (Mn, Mc,
    Me) removed after compatibility decomposition, and look-alike letters read as Latin. ASCII is its own skeleton."""
    if form.isascii():  # no format character, mark or look-alike in it
        return form
    return "".join(char for char in unicodedata.normalize("NFKD", form)
                   if unicodedata.category(char) not in SKELETON_DROPPED).translate(_LOOKALIKE)


def _decodings(form: str) -> list[str]:
    """The next forms of the closure: the text's skeleton, and what a reader may decode the text and its skeleton to
    (each ``\\uXXXX`` unescape, HTML character references, percent decoding). The skeleton starts from the
    compatibility decomposition, so a decoding hidden behind a compatibility or invisible character (a fullwidth
    percent sign, a zero-width space inside an escape) is decoded in the same round (Lead review of 57aaad97, F1)."""
    forms = []
    for base in dict.fromkeys((form, _skeleton(form))):
        forms += [base, _ESCAPED.sub(lambda match: chr(int(match.group(1), 16)), base), html.unescape(base),
                  unquote(base, errors="replace")]
    return forms


def _found(form: str) -> bool:
    """The shared check on one form, case-folded first. Compatibility forms (fullwidth letters) are found through the
    skeleton, which starts from the compatibility decomposition and is a form of the closure itself."""
    return _guarded_word(form.casefold().upper()) is not None


def _has_marker(text: str) -> bool:
    """A guarded word in the text in any letter case or compatibility form, before or after each decoding a reader
    may apply (``\\uXXXX`` unescape, HTML character reference, percent decoding), in any order and mix, also when a
    decoding shows only in the skeleton.

    The shared check is case-sensitive, so each form is case-folded and upper-cased first, and each form's skeleton
    (``_skeleton``, from the compatibility decomposition) is a form of the closure too. Decoding repeats until no new
    form appears (at most ``UNESCAPE_ROUNDS`` rounds and ``FORM_LIMIT`` forms), so a word encoded twice, which a
    reader would reveal by decoding twice, is found too; a text still yielding new forms past either bound is
    refused. JSON inside the text is never parsed.
    """
    seen = {text}
    forms = [text]
    for _ in range(UNESCAPE_ROUNDS):
        if any(_found(form) for form in forms):
            return True
        forms = list(dict.fromkeys(decoded for form in forms for decoded in _decodings(form) if decoded not in seen))
        if not forms:
            return False
        seen.update(forms)
        if len(seen) > FORM_LIMIT:
            return True
    return True


def _time(value: Any) -> tuple[datetime, int] | None:
    """A UTC time as (seconds, nanoseconds), or None when the text is not a real calendar time."""
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    try:
        base = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return base, int((match.group(2) or "").ljust(9, "0"))


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _section(value: Any, kind: type, name: str) -> Any:
    if not isinstance(value, kind):
        raise DeltaError(f"snapshot {name} has the wrong shape")
    return value


def _keyed(rows: dict[str, Any], key: Any, row: dict[str, Any], name: str) -> None:
    """A row is keyed by a non-empty text and kept once; it is never dropped or overwritten."""
    if not isinstance(key, str) or not key or key in rows:
        raise DeltaError(f"a {name} row has no key or its key is not unique")
    rows[key] = row


def _text(value: Any) -> str:
    return value if isinstance(value, str) else UNKNOWN


def _identity(value: Any, *, nullable: bool) -> str | None:
    """A known log identity or generation, or UNKNOWN. A null generation is known; a missing one is not."""
    if nullable and value is None:
        return None
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) and value != UNKNOWN else UNKNOWN


def _field(value: str) -> str:
    """One signal field with ``%`` and ``:`` percent-encoded, so ``:`` only separates fields (no collisions)."""
    return value.replace("%", "%25").replace(":", "%3A")


def _signals(rows: Any, with_head: bool) -> list[str]:
    out = set()
    for row in _list(rows):
        row = _map(row)
        signal = f"{_field(_text(row.get('agent')))}:{_field(_text(row.get('status')))}"
        signal += f":{_field(_text(row.get('head'))[:12])}" if with_head else ""
        uuid = row.get("agent_uuid")
        out.add(signal + ("" if isinstance(uuid, str) and _UUID.fullmatch(uuid) else ":undeclared"))
    return sorted(out)


def view(snapshot: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """The identifier-only view of one reflected snapshot that the delta compares."""
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        raise DeltaError("snapshot schema is not wd.bridge-mind.v1")
    if snapshot.get("authority") != "none" or snapshot.get("scope") != SNAPSHOT_SCOPE:
        raise DeltaError("snapshot authority must be none and scope bounded_projection_not_gate")
    source = _map(snapshot.get("source"))
    if _time(source.get("as_of_utc")) is None:
        raise DeltaError("snapshot source needs an explicit as_of_utc")
    out: dict[str, dict[str, Any]] = {section: {} for section in SECTIONS}
    blocked = source.get("blocked")
    out["source"] = {"as_of_utc": source["as_of_utc"], "coverage": _text(source.get("coverage")),
                     "log_identity": _identity(source.get("log_identity", _MISSING), nullable=False),
                     "generation": _identity(source.get("generation", _MISSING), nullable=True),
                     "blocked": None if blocked is None else {
                         "active": _map(blocked).get("active") is True,
                         "offset": _map(blocked).get("offset") if type(_map(blocked).get("offset")) is int else None,
                         "reason": _text(_map(blocked).get("reason"))}}
    for key, pr in _section(snapshot.get("prs"), Mapping, "prs").items():
        pr = _map(pr)
        if not (isinstance(key, str) and _PR_KEY.fullmatch(key) and type(pr.get("pr")) is int and str(pr["pr"]) == key):
            raise DeltaError("a pr entry does not match its key")
        ci = _map(pr.get("reported_ci"))
        out["prs"][key] = {"head": _text(pr.get("head")), "ci": _text(ci.get("status")), "ci_head": _text(ci.get("head")),
                           "class": _text(pr.get("class")), "votes": _signals(pr.get("votes"), False),
                           "stale_votes": _signals(pr.get("stale_votes"), True),
                           "vetoes": _signals(pr.get("veto_observations"), True)}
    for tension in _section(snapshot.get("tensions"), list, "tensions"):
        tension = _map(tension)
        _keyed(out["tensions"], tension.get("tension_id"), {
            "rule": _text(tension.get("rule")), "severity": _text(tension.get("severity")),
            "subject": _text(tension.get("subject")),
            "actors": sorted(_text(actor) for actor in _list(tension.get("actors"))),
            "refs": sorted(_text(ref) for ref in _list(tension.get("refs")))}, "tension")
    for item in _section(snapshot.get("attention"), list, "attention"):
        item = _map(item)
        kind, subject = item.get("kind"), item.get("subject")
        key = f"{kind}|{subject}" if isinstance(kind, str) and kind and isinstance(subject, str) and subject else None
        _keyed(out["attention"], key, {
            "rank": item.get("rank") if type(item.get("rank")) is int else None,
            "actors": sorted(_text(actor) for actor in _list(item.get("actors"))),
            "refs": sorted(_text(ref) for ref in _list(item.get("refs")))}, "attention")
    question = _section(snapshot.get("next_question"), Mapping, "next_question")
    entries = [("global", question.get("global"))]
    entries += [(f"lane:{agent}", entry) for agent, entry in _section(question.get("lanes"), Mapping,
                                                                     "next_question lanes").items()]
    for key, entry in entries:
        if not isinstance(entry, Mapping):
            raise DeltaError("a next_question entry is not an object")
        _keyed(out["next_question"], key, {"question": _text(entry.get("question")),
                                           "refs": sorted(_text(ref) for ref in _list(entry.get("refs")))},
               "next_question")
    if any(_has_marker(text) for text in _strings(out)):
        raise DeltaError("a guarded word is in the output; nothing is printed")
    return out


def delta(previous: Mapping[str, Any], current: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Sorted typed changes that turn the previous view into the current view."""
    old, new = view(previous), view(current)
    if _time(new["source"]["as_of_utc"]) < _time(old["source"]["as_of_utc"]):
        raise DeltaError("the current snapshot is older than the previous one")
    header = {"schema": DELTA_SCHEMA, "type": "header", "from_as_of_utc": old["source"]["as_of_utc"],
              "to_as_of_utc": new["source"]["as_of_utc"]}
    for name in ("log_identity", "generation"):
        # An unknown identity on either side may hide a replaced log: UNKNOWN never equals UNKNOWN here.
        if UNKNOWN in (old["source"][name], new["source"][name]):
            return [header, {"schema": DELTA_SCHEMA, "type": "rebuild_required", "reason": f"{name}_unknown"}]
        if old["source"][name] != new["source"][name]:
            return [header, {"schema": DELTA_SCHEMA, "type": "rebuild_required", "reason": f"{name}_changed"}]
    changes = []
    for section in SECTIONS:
        for key in sorted(set(old[section]) | set(new[section])):
            before, after = old[section].get(key), new[section].get(key)
            if before == after:
                continue
            change = {"schema": DELTA_SCHEMA, "type": "removed" if key not in new[section] else
                      ("added" if key not in old[section] else "changed"), "section": section, "key": key}
            if key in new[section]:
                change["value"] = after
            changes.append(change)
    return [header] + changes


def apply_delta(previous_view: Mapping[str, Mapping[str, Any]], changes: Sequence[Mapping[str, Any]]
                ) -> dict[str, dict[str, Any]]:
    """Apply typed changes to a view; the result equals the view of the current snapshot."""
    out = {section: dict(previous_view[section]) for section in SECTIONS}
    for change in changes:
        if change.get("type") == "rebuild_required":
            raise DeltaError("rebuild required: the previous view cannot be updated")
        if change.get("type") == "header":
            continue
        section, key = change["section"], change["key"]
        if change["type"] == "removed":
            out[section].pop(key, None)
        else:
            out[section][key] = change["value"]
    return out


def render_lines(changes: Sequence[Mapping[str, Any]]) -> str:
    """The exact printed lines. Every string in the changes is guarded before serialization, because JSON doubles a
    backslash and the printed text may then no longer decode to the word (Tools DV1); the printed lines are still
    guarded as a whole before they are returned."""
    changes = list(changes)
    if any(_has_marker(text) for text in _strings(changes)):
        raise DeltaError("a guarded word is in the output; nothing is printed")
    text = "".join(json.dumps(change, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
                   for change in changes)
    if _has_marker(text):
        raise DeltaError("a guarded word is in the output; nothing is printed")
    return text


def _read_object(path: Path) -> dict[str, Any]:
    """One JSON object under the canonical bridge JSON contract (no duplicate keys, no NaN, safe numbers)."""
    try:
        return parse_bridge_json_object(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        raise DeltaError("an input file is not one canonical JSON object") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bridge mind delta push v0 (read-only, dormant).")
    parser.add_argument("--previous", type=Path, required=True, help="The previous reflected snapshot JSON.")
    parser.add_argument("--current", type=Path, required=True, help="The current reflected snapshot JSON.")
    args = parser.parse_args(argv)
    try:
        text = render_lines(delta(_read_object(args.previous), _read_object(args.current)))
    except DeltaError as exc:
        sys.stderr.write(f"bridge mind delta refused: {exc}\n")
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
