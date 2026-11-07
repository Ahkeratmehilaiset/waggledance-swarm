"""HEX MEMORY STEP 2, shared-route closed cards (p3-p5): a default-OFF, synthetic-only versioned card projection.

Lead request lead-mind-shared-cards-p3p5-fable-20261107-v1, after the read-only integration map of 01:29Z. It adds a
SEPARATE versioned projection beside the existing shared projection v6; no v1-v6 reader, resolver, builder, policy or
pin is changed, and the existing v6 resolver refuses this projection (other schema and scope).

* every function is OFF unless ``enabled is True`` and returns ``{"status": "OFF"}`` without reading anything;
  input cards are in-memory dicts, and ANY card without the ``synthetic`` tag refuses the whole call
  (``live_input_refused``); this is not an authorization to read or index live bridge events;
* p3 projection: a card is the existing writer envelope (the bare card envelope plus ``agent_uuid``, optionally the
  writer metadata severity, run_id, pid, cwd, role, session_id and capabilities), checked with the envelope rule of
  ``tools/wd_q4_public_card_prefix_verifier.py`` and normalized exactly as that verifier does (only the closed card
  keys, the time floored to the minute, a ``supersedes`` link rewritten to the normalized predecessor), then checked
  by ``public_knowledge_card.validate``; writer metadata enters only the original source id (the canonical
  ``tools.bridge_compact_view.event_id`` of the original envelope) and is never stored; a refused card is counted
  (``card_refused``) and never kept;
* p4 transport-safe line: the stored text is rebuilt from the closed fields only, as
  ``kind outcome agent subject [head:H] next:N tag:T.. dep:D.. until:YYYY-MM-DDTHH:MMZ`` and must match the
  resolved-route rule of ``Invoke-WdQ4Query.ps1`` (letters, digits, dot, underscore, colon, space and hyphen, at most
  320 characters); a repo_path subject or dependency (a slash) is refused on this route;
* p5 truth: ``card_status(line, now)`` gives CURRENT or STALE from the ``until:`` field against the reader's clock,
  and UNKNOWN when the field is missing or malformed; a card superseded by an accepted same-lane successor in the
  same projection is left out of the records (``superseded_excluded``, listed in ``superseded``), which is truthful
  for any query at or after the coverage cutoff; ``coverage`` names the cutoff and completeness ``UNKNOWN``, and every
  answer, ABSTAIN included, carries it;
* ``validate_shared_card_projection`` re-derives every record from the stored normalized cards and pins; the original
  source id of a card is a hash claim that re-derivation cannot check, so ``verify_shared_card_sources`` checks every
  stored card, by that id, with the existing prefix verifier against one pinned synthetic source prefix (non-review
  policy, card policy and roster pins), and requires a match within that prefix for each;
* the memory is built with the existing ``shared_index_builder`` primitives under its own scope, descriptor schema
  and projection schema; ``resolve_shared_card_results`` mirrors the v6 resolver (hash-only Q4 items in, the exact
  five transport keys out, visibility at the query time), and ``ask_shared_cards`` runs Q4, that resolution and the
  status rule in process.

A match within a prefix proves only that the exact envelopes occur in that pinned prefix, not publisher identity,
freshness, later supersession or complete history (completeness UNKNOWN). No live data, policy, launcher, installer
or production path is touched, and nothing here claims production safety.
"""
import copy
import datetime as dt
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import public_knowledge_card as pkc
from sandbox.mind.step2 import shared_projection as sp
from tools import wd_q4_public_card_prefix_verifier as verifier
from tools.bridge_compact_view import event_id

SCHEMA = "wd.bridge-mind-shared-card-projection.v7"
SCOPE = "wd.q4-shared-card-events.v1"
DESCRIPTOR_SCHEMA = "wd.bridge-mind-q4-card-memory-descriptor.v1"
REF_KIND = "sha256_of_projected_metadata"
SYNTHETIC_TAG = "synthetic"
MAX_EVENTS = 4096
MAX_CHAIN = 15  # == the validator's earlier_events bound
MAX_RESULTS = 100
# == the resolved-route stored_text rule in ops/windows/reboot/Invoke-WdQ4Query.ps1 (ConvertFrom-WdQ4ResolvedResult)
TRANSPORT_LINE = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")
UNTIL = re.compile(r"(?:\A| )until:(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})Z\Z")
AGENT_UUID = re.compile(r"[A-Za-z0-9-]{1,64}\Z")
PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "synthetic", "now_utc", "vocabulary",
                             "vocabulary_sha256", "path_allowlist", "path_allowlist_sha256", "accepted", "refused",
                             "superseded", "records", "sources", "coverage"})
REFUSAL_TOKENS = frozenset({"card_refused", "duplicate_skipped", "superseded_excluded"})
ITEM_KEYS = frozenset({"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"})
CARD_META = {"type": "status", "status": "info", "task_id": "mind-public-knowledge-v1"}


class SharedCardRefused(ValueError):
    """A fixed refusal token; never any input text."""


def _enabled(enabled):
    if enabled is False:
        return False
    if enabled is not True:
        raise SharedCardRefused("enabled_invalid")
    return True


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _synthetic(event):
    if type(event) is not dict or type(event.get("payload")) is not dict:
        return False
    card = event["payload"].get("public_knowledge")
    if type(card) is not dict or type(card.get("tags")) is not list:
        return False
    return any(type(tag) is str and tag == SYNTHETIC_TAG for tag in card["tags"])


def _pinned_text(raw, pin):
    if type(raw) is not bytes or type(pin) is not str or sp.HEX64.fullmatch(pin) is None:
        raise SharedCardRefused("pin_invalid")
    if _sha(raw) != pin:
        raise SharedCardRefused("pin_mismatch")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        raise SharedCardRefused("pin_invalid") from None


def _vocabulary_is_synthetic(text):
    try:
        data = json.loads(text)
        return type(data) is dict and type(data.get("tags")) is list and SYNTHETIC_TAG in data["tags"]
    except ValueError:
        return False


def _normalize_one(event):
    """The verifier's normalization of one writer envelope: closed card keys only, time floored to the minute."""
    if type(event) is not dict or not (pkc._EVENT | {"agent_uuid"}) <= set(event):
        raise ValueError()
    if type(event["agent_uuid"]) is not str or AGENT_UUID.fullmatch(event["agent_uuid"]) is None:
        raise ValueError()
    verifier._envelope(event)
    normalized = {key: copy.deepcopy(event[key]) for key in pkc._EVENT}
    normalized["ts_utc"] = verifier._minute(verifier._utc(event["ts_utc"]))
    return normalized


def _render_subject(value):
    if type(value) is not dict or len(value) != 1:
        raise ValueError()
    key, item = next(iter(value.items()))
    if key == "pr" and type(item) is int:
        return "pr:" + str(item)
    if key == "component" and type(item) is str:
        return "component:" + item
    raise ValueError()  # repo_path (a slash) is not carried on the shared route


def transport_line(normalized):
    """The p4 line of one normalized, validated card, rebuilt from the closed fields only."""
    data = normalized["payload"]["public_knowledge"]
    parts = [data["kind"], data["outcome"], normalized["agent"], _render_subject(data["subject"])]
    if "head" in data:
        parts.append("head:" + data["head"])
    parts.append("next:" + data["next"])
    parts.extend("tag:" + tag for tag in sorted(data["tags"]))
    parts.extend("dep:" + dep for dep in sorted(_render_subject(item) for item in data.get("depends_on", [])))
    parts.append("until:" + data["valid_until_utc"][:16] + "Z")
    line = " ".join(parts)
    if TRANSPORT_LINE.fullmatch(line) is None:
        raise ValueError()
    return line


def card_status(line, now_utc):
    """CURRENT or STALE from the line's ``until:`` field against ``now_utc``; UNKNOWN when either is unusable."""
    try:
        match = UNTIL.search(line) if isinstance(line, str) else None
        now = corpus_mod.parse_utc(now_utc) if isinstance(now_utc, str) else None
        if match is None or now is None:
            return "UNKNOWN"
        until = dt.datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M").replace(tzinfo=dt.timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return "UNKNOWN"
    return "CURRENT" if now < until else "STALE"


def record_ref(line, source_ref):
    raw = json.dumps({"text": line, "source": source_ref}, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("ascii")
    return _sha(raw)


def _record(source_ref, normalized, line, checked):
    time_utc = corpus_mod.utc_text(corpus_mod.parse_utc(normalized["ts_utc"]))
    available = sp._available(time_utc)
    ref = record_ref(line, source_ref)
    return {"record_id": "card:" + ref, "kind": "event", "time_utc": time_utc, "available_utc": available,
            "stored_text": line, "summary": line, "meta": dict(CARD_META, agent=normalized["agent"]),
            "provenance": {"source_kind": "shared_closed_card", "event_ref": ref, "ref_kind": REF_KIND,
                           "canonical_event": False, "tier": {"source_type": "bridge_event"},
                           "available_utc": available, "source_event_ref": source_ref,
                           "valid_until_utc": checked["valid_until_utc"]}}


def _derive(entries, vocabulary, vocabulary_sha256, path_allowlist, path_allowlist_sha256, now_utc):
    """Records from (original source id, normalized card) pairs; supersession is resolved in normalized ids."""
    by_norm = {}
    for source_ref, normalized in entries:
        by_norm.setdefault(event_id(normalized), (source_ref, normalized))
    accepted, refused = [], {}

    def refuse(token):
        refused[token] = refused.get(token, 0) + 1

    for source_ref, normalized in entries:
        try:
            chain, current, seen = [], normalized, set()
            while "supersedes" in current["payload"]["public_knowledge"]:
                prior = current["payload"]["public_knowledge"]["supersedes"]
                if type(prior) is not str or prior not in by_norm or prior in seen or len(chain) > MAX_CHAIN:
                    raise ValueError()
                seen.add(prior)
                current = by_norm[prior][1]
                chain.append(current)
            checked = pkc.validate(enabled=True, event=normalized, source_ref=event_id(normalized),
                                   vocabulary_bytes=vocabulary, expected_vocabulary_sha256=vocabulary_sha256,
                                   path_allowlist_bytes=path_allowlist,
                                   expected_path_allowlist_sha256=path_allowlist_sha256, now_utc=now_utc,
                                   previous_event=chain[0] if chain else None, earlier_events=chain[1:])
            line = transport_line(normalized)
        except (pkc.CardRefused, KeyError, TypeError, ValueError):
            refuse("card_refused")
            continue
        accepted.append((source_ref, normalized, line, checked))
    while True:  # a successor stays only while its predecessor stays, so every kept chain re-derives from sources
        kept = {event_id(normalized) for _, normalized, _, _ in accepted}
        orphans = []
        for entry in accepted:
            data = entry[1]["payload"]["public_knowledge"]
            if "supersedes" in data and data["supersedes"] not in kept:
                orphans.append(entry)
        if not orphans:
            break
        for entry in orphans:
            accepted.remove(entry)
            refuse("card_refused")
    replaced = {normalized["payload"]["public_knowledge"]["supersedes"]
                for _, normalized, _, _ in accepted if "supersedes" in normalized["payload"]["public_knowledge"]}
    superseded, records = [], []
    for source_ref, normalized, line, checked in accepted:
        if event_id(normalized) in replaced:
            superseded.append(source_ref)
            refuse("superseded_excluded")
            continue
        records.append(_record(source_ref, normalized, line, checked))
    cutoff = max((record["available_utc"] for record in records), default=None)
    return {"records": records, "refused": refused, "superseded": sorted(superseded),
            "sources": {source_ref: normalized for source_ref, normalized, _, _ in accepted},
            "coverage": {"cutoff_utc": cutoff, "completeness": "UNKNOWN"}}


def _assemble(derived, refused, vocabulary_text, vocabulary_sha256, paths_text, path_allowlist_sha256, now_utc):
    out = {"schema": SCHEMA, "authority": "none", "scope": SCOPE, "synthetic": True, "now_utc": now_utc,
           "vocabulary": vocabulary_text, "vocabulary_sha256": vocabulary_sha256,
           "path_allowlist": paths_text, "path_allowlist_sha256": path_allowlist_sha256,
           "accepted": len(derived["records"]), "refused": dict(sorted(refused.items())),
           "superseded": derived["superseded"], "records": derived["records"], "sources": derived["sources"],
           "coverage": derived["coverage"]}
    try:
        corpus_mod.ws.guard_object(out)
    except corpus_mod.ws.WorkspaceError:
        raise SharedCardRefused("guarded_word") from None
    return out


def project_shared_cards(events=None, *, vocabulary_bytes=None, vocabulary_sha256=None, path_allowlist_bytes=None,
                         path_allowlist_sha256=None, now_utc=None, enabled=False):
    """The v7 shared card projection of in-memory synthetic writer envelopes, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise SharedCardRefused("events_invalid")
    if not all(_synthetic(event) for event in events):
        raise SharedCardRefused("live_input_refused")
    vocabulary_text = _pinned_text(vocabulary_bytes, vocabulary_sha256)
    paths_text = _pinned_text(path_allowlist_bytes, path_allowlist_sha256)
    if not _vocabulary_is_synthetic(vocabulary_text):
        raise SharedCardRefused("vocabulary_not_synthetic")
    if type(now_utc) is not str:
        raise SharedCardRefused("now_invalid")
    originals, refused = {}, {}
    order = []
    for event in events:
        try:
            source_ref = event_id(event)
        except (TypeError, ValueError):
            refused["card_refused"] = refused.get("card_refused", 0) + 1
            continue
        if source_ref in originals:
            refused["duplicate_skipped"] = refused.get("duplicate_skipped", 0) + 1
            continue
        originals[source_ref] = event
        order.append(source_ref)
    normalized, failed = {}, set()

    def normalize(source_ref, depth=0):
        if source_ref in normalized:
            return normalized[source_ref]
        if source_ref in failed or depth > MAX_CHAIN:
            raise ValueError()
        result = _normalize_one(originals[source_ref])
        data = result["payload"]["public_knowledge"]
        if type(data) is dict and "supersedes" in data:
            prior = data["supersedes"]
            if type(prior) is not str or prior not in originals or prior == source_ref:
                raise ValueError()
            data["supersedes"] = event_id(normalize(prior, depth + 1))
        normalized[source_ref] = result
        return result

    entries, seen_norm = [], set()
    for source_ref in order:
        try:
            entry = normalize(source_ref)
            norm_ref = event_id(entry)
        except (KeyError, TypeError, ValueError, RecursionError):
            failed.add(source_ref)
            refused["card_refused"] = refused.get("card_refused", 0) + 1
            continue
        if norm_ref in seen_norm:  # same card, other writer metadata or second: one record, no ambiguous link
            refused["duplicate_skipped"] = refused.get("duplicate_skipped", 0) + 1
            continue
        seen_norm.add(norm_ref)
        entries.append((source_ref, entry))
    derived = _derive(entries, vocabulary_bytes, vocabulary_sha256, path_allowlist_bytes, path_allowlist_sha256,
                      now_utc)
    for token, count in derived["refused"].items():
        refused[token] = refused.get(token, 0) + count
    return _assemble(derived, refused, vocabulary_text, vocabulary_sha256, paths_text, path_allowlist_sha256, now_utc)


def validate_shared_card_projection(projection):
    """Re-derive every record from the stored normalized cards and pins; ``projection_invalid`` on any difference.

    The original source id of each card is a hash claim that this cannot check; ``verify_shared_card_sources`` does.
    """
    try:
        if (type(projection) is not dict or set(projection) != PROJECTION_KEYS or projection["schema"] != SCHEMA
                or projection["scope"] != SCOPE or projection["authority"] != "none"
                or projection["synthetic"] is not True or type(projection["sources"]) is not dict
                or type(projection["records"]) is not list or type(projection["refused"]) is not dict
                or any(key not in REFUSAL_TOKENS or type(count) is not int or count < 1
                       for key, count in projection["refused"].items())
                or any(type(ref) is not str or sp.HEX64.fullmatch(ref) is None for ref in projection["sources"])):
            raise ValueError()
        vocabulary = projection["vocabulary"].encode("ascii")
        paths = projection["path_allowlist"].encode("ascii")
        _pinned_text(vocabulary, projection["vocabulary_sha256"])
        _pinned_text(paths, projection["path_allowlist_sha256"])
        if not _vocabulary_is_synthetic(projection["vocabulary"]):
            raise ValueError()
        sources = projection["sources"]
        if (any(type(event) is not dict or set(event) != pkc._EVENT or not _synthetic(event)
                for event in sources.values())
                or len({event_id(event) for event in sources.values()}) != len(sources)):
            raise ValueError()
        # record order first, then the left-out predecessors in their stored order
        ordered = [record["provenance"]["source_event_ref"] for record in projection["records"]]
        ordered += [ref for ref in projection["superseded"] if ref not in ordered]
        if sorted(ordered) != sorted(sources) or len(set(ordered)) != len(ordered):
            raise ValueError()
        derived = _derive([(ref, sources[ref]) for ref in ordered], vocabulary, projection["vocabulary_sha256"],
                          paths, projection["path_allowlist_sha256"], projection["now_utc"])
        stored = projection["refused"]
        if (derived["refused"].get("card_refused") or derived["records"] != projection["records"]
                or derived["superseded"] != projection["superseded"] or derived["sources"] != sources
                or derived["coverage"] != projection["coverage"]
                or derived["refused"].get("superseded_excluded", 0) != stored.get("superseded_excluded", 0)
                or projection["accepted"] != len(derived["records"])):
            raise ValueError()
        corpus_mod.ws.guard_object(projection)
    except (SharedCardRefused, ValueError, KeyError, TypeError, AttributeError, UnicodeError,
            corpus_mod.ws.WorkspaceError):
        raise SharedCardRefused("projection_invalid") from None
    return projection


def verify_shared_card_sources(projection=None, *, enabled=False, source_path=None, generation_path=None,
                               end_cursor=None, expected_prefix_sha256=None, nonreview_policy_bytes=None,
                               nonreview_policy_sha256=None, card_policy_bytes=None, card_policy_sha256=None,
                               roster_bytes=None, roster_sha256=None):
    """Every stored card, by its original source id, must match within one pinned synthetic source prefix."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    validate_shared_card_projection(projection)
    sources = projection["sources"]
    norm_to_source = {event_id(normalized): ref for ref, normalized in sources.items()}
    for ref, normalized in sources.items():
        # the card's own minute, end of minute: the verifier then checks existence, not today's validity
        moment = normalized["ts_utc"][:17] + "59.9999999Z"
        out = verifier.verify(enabled=True, source_path=source_path, generation_path=generation_path,
                              end_cursor=end_cursor, expected_prefix_sha256=expected_prefix_sha256,
                              target_event_ref=ref, nonreview_policy_bytes=nonreview_policy_bytes,
                              nonreview_policy_sha256=nonreview_policy_sha256, card_policy_bytes=card_policy_bytes,
                              card_policy_sha256=card_policy_sha256, roster_bytes=roster_bytes,
                              roster_sha256=roster_sha256, vocabulary_bytes=projection["vocabulary"].encode("ascii"),
                              vocabulary_sha256=projection["vocabulary_sha256"],
                              path_allowlist_bytes=projection["path_allowlist"].encode("ascii"),
                              path_allowlist_sha256=projection["path_allowlist_sha256"], now_utc=moment)
        prior = normalized["payload"]["public_knowledge"].get("supersedes")
        expected_prior = [norm_to_source.get(prior)] if prior is not None else []
        if (out.get("status") != "MATCH_WITHIN_PREFIX" or out.get("canonical_event_ref") != ref
                or out.get("predecessor_refs", [])[:1] != expected_prior):
            raise SharedCardRefused("source_unverified")
    return {"status": "MATCH_WITHIN_PREFIX", "cards": len(sources), "completeness": "UNKNOWN"}


def build_shared_card_memory(projection, encode, identity, dim, root, name, *, enabled=False):
    """One frozen card memory ``root/name`` from a validated v7 projection, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    validate_shared_card_projection(projection)
    from sandbox.mind.step2 import cell as cell_mod
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import shared_index_builder as sib
    from waggledance.core import hex_cell_topology as topology_mod
    import numpy as np
    try:
        encoder = sib.encoder_identity(identity)
        dim = sib.check_dim(dim)
        if not callable(encode):
            raise sib.BuildRefused("encoder_invalid")
        if not isinstance(name, str) or sib.MEMORY_NAME.fullmatch(name) is None:
            raise sib.BuildRefused("memory_name_invalid")
        sib._publication_supported()
        root = sp._output_root(root)
        records, duplicates = sib.distinct_records(projection["records"])
        if not records:
            raise sib.BuildRefused("projection_empty")
        sib._check_bounds(records)
        cells = {}
        for record in records:
            cell_id = sib.place(record)
            cells.setdefault(cell_id, []).extend(sib.cell_rows(record, cell_id))
        cutoff_utc = projection["coverage"]["cutoff_utc"]
        texts = sorted({row["stored_text"] for rows in cells.values() for row in rows})
        by_text = dict(zip(texts, sib._vectors(encode, texts, dim)))
    except (sib.BuildRefused, sp.ProjectionRefused) as refusal:
        raise SharedCardRefused(str(refusal)) from None
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    projection_sha256 = _sha(projection_bytes)
    memory_dir = root / name
    try:
        os.mkdir(memory_dir)
    except FileExistsError:
        raise SharedCardRefused("memory_exists") from None
    except OSError:
        raise SharedCardRefused("write_failed") from None
    stage = "projection"
    try:
        sib._write_new(memory_dir / "projection.json", projection_bytes)
        stage = "cells"
        pointers = {}
        for cell_id in topology_mod.ALL_CELLS:
            if cell_id not in cells:
                continue
            rows = cells[cell_id]
            vectors = np.vstack([by_text[row["stored_text"]] for row in rows])
            (memory_dir / "cells").mkdir(exist_ok=True)
            pointer = sib._publish_cell(memory_dir / "cells" / cell_id, cell_id, rows, vectors, dim=dim,
                                        cutoff_utc=cutoff_utc, encoder=encoder, projection_sha256=projection_sha256)
            pointers[cell_id] = dict(pointer, count=len(rows))
        stage = "finalize"
        memory = {"schema": memory_mod.SCHEMA, "authority": "none", "scope": SCOPE, "synthetic": True,
                  "cutoff_utc": cutoff_utc, "projection_sha256": projection_sha256, "projection_schema": SCHEMA,
                  "projection_file": "projection.json", "encoder": encoder, "dim": dim,
                  "placement": sib.PLACEMENT_RULE, "topology_source_sha256": cell_mod.file_sha256(sib.TOPOLOGY_FILE),
                  "cells": pointers, "records": len(records), "duplicates": duplicates, "views": list(sib.VIEWS),
                  "independent_views": False}
        corpus_mod.ws.guard_object(memory)
        memory_bytes = cell_mod.canonical(memory).encode("utf-8")
        memory_sha256 = _sha(memory_bytes)
        descriptor = {"schema": DESCRIPTOR_SCHEMA, "scope": SCOPE, "memory_sha256": memory_sha256, "dim": dim,
                      "encoder": encoder}
        descriptor_bytes = cell_mod.canonical(descriptor).encode("utf-8")
        stage = "descriptor"
        sib._write_new(memory_dir / "descriptor.json", descriptor_bytes)
        stage = "memory"
        sib._commit_memory(memory_dir, memory_bytes)
    except sib.BuildRefused as refusal:
        sib._report_incomplete(memory_dir, name, str(refusal), stage)
        raise SharedCardRefused(str(refusal)) from None
    except Exception:  # noqa: BLE001 - one fixed token after the directory exists; nothing is removed
        sib._report_incomplete(memory_dir, name, "write_failed", stage)
        raise SharedCardRefused("write_failed") from None
    return {"memory_sha256": memory_sha256, "descriptor_sha256": _sha(descriptor_bytes), "descriptor": descriptor,
            "records": len(records), "coverage": dict(projection["coverage"])}


def resolve_shared_card_results(projection=None, results=None, query_utc=None, *, enabled=False):
    """Hash-only Q4 items to the exact resolved transport items, like the v6 resolver; ``resolution_refused``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    try:
        validate_shared_card_projection(projection)
    except SharedCardRefused:
        raise SharedCardRefused("resolution_refused") from None
    if (not isinstance(results, list) or len(results) > MAX_RESULTS or not isinstance(query_utc, str)
            or corpus_mod.parse_utc(query_utc) is None):
        raise SharedCardRefused("resolution_refused")
    records = {}
    for record in projection["records"]:
        key = (_sha(record["provenance"]["event_ref"].encode("utf-8")), _sha(record["record_id"].encode("utf-8")))
        records.setdefault(key, record)
    resolved, seen = [], set()
    for item in results:
        if (not isinstance(item, dict) or set(item) != ITEM_KEYS or item["ref_kind"] != REF_KIND
                or item["canonical_event"] is not False
                or any(not isinstance(item[k], str) or sp.HEX64.fullmatch(item[k]) is None
                       for k in ("ref_sha256", "doc_id_sha256"))):
            raise SharedCardRefused("resolution_refused")
        key = (item["ref_sha256"], item["doc_id_sha256"])
        record = records.get(key)
        if (record is None or not sp.visible_at(record, query_utc, guard_band_s=1.0)
                or not hmac.compare_digest(record["stored_text"], record["summary"])):
            raise SharedCardRefused("resolution_refused")
        if key not in seen:
            resolved.append({"ref_sha256": key[0], "doc_id_sha256": key[1], "stored_text": record["stored_text"],
                             "ref_kind": REF_KIND, "canonical_event": False})
            seen.add(key)
    return resolved


def _read_bounded(path, limit):
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise SharedCardRefused("file_over_bound")
    return raw


def ask_shared_cards(memory_dir, expected_memory_sha256, encoder, situation, situation_time_utc, *, enabled=False):
    """Q4 over a pinned card memory, resolved to the transport items plus ``card_status``, with coverage."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import q4 as q4_mod
    memory_dir = Path(memory_dir)
    try:
        raw = _read_bounded(memory_dir / "memory.json", memory_mod.MAX_MEMORY_MANIFEST_BYTES)
    except OSError:
        raise SharedCardRefused("memory_unreadable") from None
    if (not isinstance(expected_memory_sha256, str) or sp.HEX64.fullmatch(expected_memory_sha256) is None
            or _sha(raw) != expected_memory_sha256):
        raise SharedCardRefused("memory_mismatch")
    try:
        manifest = json.loads(raw)
        if (manifest.get("scope") != SCOPE or manifest.get("synthetic") is not True
                or manifest.get("projection_schema") != SCHEMA):
            raise ValueError()
        projection_raw = _read_bounded(memory_dir / manifest["projection_file"], 64 * 1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise SharedCardRefused("memory_invalid") from None
    if _sha(projection_raw) != manifest["projection_sha256"]:
        raise SharedCardRefused("projection_mismatch")
    try:
        projection = validate_shared_card_projection(json.loads(projection_raw))
    except ValueError:
        raise SharedCardRefused("projection_invalid") from None
    if manifest["cutoff_utc"] != projection["coverage"]["cutoff_utc"]:
        raise SharedCardRefused("memory_invalid")
    try:
        memory = memory_mod.Memory(memory_dir, manifest_bytes=raw)
        out = q4_mod.Q4(memory, encoder).ask(situation, situation_time_utc)
    except memory_mod.MemoryError_:
        raise SharedCardRefused("memory_unreadable") from None
    items = [{"ref_sha256": _sha(result["ref"].encode("utf-8")), "doc_id_sha256": _sha(result["doc_id"].encode("utf-8")),
              "ref_kind": REF_KIND, "canonical_event": False} for result in out["results"]]
    resolved = resolve_shared_card_results(projection, items, situation_time_utc, enabled=True) if items else []
    results = [dict(item, card_status=card_status(item["stored_text"], situation_time_utc)) for item in resolved]
    answer = {"status": out["status"], "reason": out["reason"], "results": results,
              "coverage": dict(projection["coverage"])}
    corpus_mod.ws.guard_object(answer)
    return answer
