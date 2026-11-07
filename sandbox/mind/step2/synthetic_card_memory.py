"""HEX MEMORY STEP 2, synthetic closed-card memory: a default-OFF prototype of useful closed-vocabulary facts.

Prototype only (Lead request lead-mind-closed-knowledge-cards-fable-20261107-v1, after the RCO2 closed-card design
and the RCO2 recommendation to keep free text synthetic-only). Instead of free-form message text, every record is
one closed knowledge card validated by the existing ``public_knowledge_card.validate``: an enumerated kind, outcome
and next step, a subject that is a PR number, a pinned component or a pinned allowlisted repo path, pinned tags, an
optional 40-hex head, pinned dependencies, a validity of at most seven days and an optional same-lane supersession.
The stored text is the line that validator rebuilds from those fields; no input string is ever stored as such.

* every function is OFF unless ``enabled is True`` and returns ``{"status": "OFF"}`` without reading anything;
* there is no source path, bridge reader or file input: cards are in-memory dicts from the caller, and ANY card
  whose tags do not contain ``synthetic`` (or that is not a dict with a ``payload.public_knowledge`` dict) refuses
  the whole call (``live_input_refused``); the pinned vocabulary must contain the ``synthetic`` tag too
  (``vocabulary_not_synthetic``). This is not an authorization to index live bridge events;
* by construction a card cannot carry a review verdict or vote (no such kind or outcome), a free-text message,
  a recipient or other routing key, a path outside the pinned allowlist, an unknown tag, an author outside the
  three build lanes (RCO lanes never pass) or any extra key; such a card is refused and only counted
  (``card_refused``); refused cards are never kept, so their content does not reach the projection;
* a card that supersedes another must name, by canonical event id, a card of the same lane and subject in the same
  batch (the validator rechecks the whole chain, at most 15 earlier cards); the superseded card is still stored and
  is answered with ``card_status`` ``SUPERSEDED`` once the newer card is available at the situation time;
* the projection keeps every accepted card under its canonical event id (``tools.bridge_compact_view.event_id``)
  together with the pinned vocabulary and path allowlist and the projection time, so ``validate_card_projection``
  re-derives every record from those cards and refuses any difference;
* the memory is built with the existing ``shared_index_builder`` cell and commit primitives and read with the
  existing ``memory.Memory`` and ``q4.Q4``; it carries its own scope and descriptor schema, so the production Q4
  reader, which pins the shared scope and descriptor schema, does not accept it;
* an answer cites the card by its source event id (``source_ref_sha256``), returns the rebuilt line, the card status
  at the situation time (CURRENT, STALE or SUPERSEDED), the validity end and the coverage cutoff of the memory; an
  ABSTAIN answer carries the same coverage cutoff, so absence is never presented as a negative fact.

A source event id proves only the exact supplied JSON of a synthetic card, not who wrote it, that it was broadcast,
or that a clock is trustworthy (see the validator's own docstring). No live data, policy, launcher, installer or
production path is touched, and nothing here claims production safety.
"""
import hashlib
import json
import os
from pathlib import Path

from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import public_knowledge_card as pkc
from sandbox.mind.step2 import shared_projection as sp
from tools.bridge_compact_view import event_id

SCHEMA = "wd.mind-synthetic-card-projection.v1"
SCOPE = "wd.mind-synthetic-card-events.v1"
DESCRIPTOR_SCHEMA = "wd.mind-synthetic-card-descriptor.v1"
SYNTHETIC_TAG = "synthetic"
MAX_EVENTS = 4096
MAX_CHAIN = 15  # == the validator's earlier_events bound
PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "synthetic", "now_utc", "vocabulary",
                             "vocabulary_sha256", "path_allowlist", "path_allowlist_sha256", "accepted", "refused",
                             "superseded", "records", "sources"})
REFUSAL_TOKENS = frozenset({"card_refused", "duplicate_skipped"})
CARD_META = {"type": "status", "status": "info", "task_id": "mind-public-knowledge-v1"}


class CardMemoryRefused(ValueError):
    """A fixed refusal token; never any input text."""


def _enabled(enabled):
    if enabled is False:
        return False
    if enabled is not True:
        raise CardMemoryRefused("enabled_invalid")
    return True


def _synthetic(event):
    """True when the card is a dict whose ``payload.public_knowledge.tags`` list contains the synthetic tag."""
    if type(event) is not dict or type(event.get("payload")) is not dict:
        return False
    card = event["payload"].get("public_knowledge")
    if type(card) is not dict or type(card.get("tags")) is not list:
        return False
    return any(type(tag) is str and tag == SYNTHETIC_TAG for tag in card["tags"])


def _pinned_text(raw, pin):
    if type(raw) is not bytes or type(pin) is not str or sp.HEX64.fullmatch(pin) is None:
        raise CardMemoryRefused("pin_invalid")
    if hashlib.sha256(raw).hexdigest() != pin:
        raise CardMemoryRefused("pin_mismatch")
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        raise CardMemoryRefused("pin_invalid") from None


def _vocabulary_is_synthetic(text):
    try:
        data = json.loads(text)
        return type(data) is dict and type(data.get("tags")) is list and SYNTHETIC_TAG in data["tags"]
    except ValueError:
        return False


def record_ref(line, source_ref):
    """sha256 of the rebuilt card line and its canonical source event id (canonical JSON)."""
    raw = json.dumps({"text": line, "source": source_ref}, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def _record(event, checked):
    line, source_ref = checked["stored_text"], checked["source_ref_sha256"]
    time_utc = corpus_mod.utc_text(corpus_mod.parse_utc(event["ts_utc"]))
    available = sp._available(time_utc)
    ref = record_ref(line, source_ref)
    return {"record_id": "card:" + ref, "kind": "event", "time_utc": time_utc, "available_utc": available,
            "stored_text": line, "summary": line, "meta": dict(CARD_META, agent=event["agent"]),
            "provenance": {"source_kind": "synthetic_closed_card", "event_ref": ref,
                           "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
                           "tier": {"source_type": "bridge_event"}, "available_utc": available,
                           "source_event_ref": source_ref, "valid_until_utc": checked["valid_until_utc"],
                           "visibility": checked["visibility"], "provenance": checked["provenance"]}}


def _chain(event, by_ref):
    """(previous_event, earlier_events) for the validator from the same batch, or None when a link is missing."""
    chain, current, seen = [], event, set()
    while "supersedes" in current["payload"]["public_knowledge"]:
        prior = current["payload"]["public_knowledge"]["supersedes"]
        if type(prior) is not str or prior not in by_ref or prior in seen or len(chain) > MAX_CHAIN:
            return None
        seen.add(prior)
        current = by_ref[prior]
        if type(current) is not dict or type(current.get("payload")) is not dict:
            return None
        chain.append(current)
    if not chain:
        return None, []
    return chain[0], chain[1:]


def _project(events, vocabulary, vocabulary_sha256, path_allowlist, path_allowlist_sha256, now_utc):
    if type(events) is not list or len(events) > MAX_EVENTS:
        raise CardMemoryRefused("events_invalid")
    if not all(_synthetic(event) for event in events):
        raise CardMemoryRefused("live_input_refused")
    vocabulary_text = _pinned_text(vocabulary, vocabulary_sha256)
    paths_text = _pinned_text(path_allowlist, path_allowlist_sha256)
    if not _vocabulary_is_synthetic(vocabulary_text):
        raise CardMemoryRefused("vocabulary_not_synthetic")
    if type(now_utc) is not str:
        raise CardMemoryRefused("now_invalid")
    refs = []
    for event in events:
        try:
            refs.append(event_id(event))
        except (TypeError, ValueError):
            refs.append(None)
    by_ref = {}
    for ref, event in zip(refs, events):
        if ref is not None:
            by_ref.setdefault(ref, event)
    records, sources, superseded, refused, seen = [], {}, set(), {}, set()

    def refuse(token):
        refused[token] = refused.get(token, 0) + 1

    for ref, event in zip(refs, events):
        if ref is not None and ref in seen:
            refuse("duplicate_skipped")
            continue
        try:
            chain = None if ref is None else _chain(event, by_ref)
            if chain is None:
                raise pkc.CardRefused("card_refused")
            previous, earlier = chain
            checked = pkc.validate(enabled=True, event=event, source_ref=ref, vocabulary_bytes=vocabulary,
                                   expected_vocabulary_sha256=vocabulary_sha256, path_allowlist_bytes=path_allowlist,
                                   expected_path_allowlist_sha256=path_allowlist_sha256, now_utc=now_utc,
                                   previous_event=previous, earlier_events=earlier)
            record = _record(event, checked)
        except (pkc.CardRefused, sp.ProjectionRefused, KeyError, TypeError, ValueError):
            refuse("card_refused")
            continue
        seen.add(ref)
        records.append(record)
        sources[ref] = json.loads(json.dumps(event))
        if "supersedes" in event["payload"]["public_knowledge"]:
            superseded.add(event["payload"]["public_knowledge"]["supersedes"])
    out = {"schema": SCHEMA, "authority": "none", "scope": SCOPE, "synthetic": True, "now_utc": now_utc,
           "vocabulary": vocabulary_text, "vocabulary_sha256": vocabulary_sha256,
           "path_allowlist": paths_text, "path_allowlist_sha256": path_allowlist_sha256,
           "accepted": len(records), "refused": dict(sorted(refused.items())),
           "superseded": sorted(superseded & set(sources)), "records": records, "sources": sources}
    try:
        corpus_mod.ws.guard_object(out)
    except corpus_mod.ws.WorkspaceError:
        raise CardMemoryRefused("guarded_word") from None
    return out


def project_synthetic_cards(events=None, *, vocabulary_bytes=None, vocabulary_sha256=None,
                            path_allowlist_bytes=None, path_allowlist_sha256=None, now_utc=None, enabled=False):
    """The closed-card projection of in-memory synthetic cards, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    return _project(events, vocabulary_bytes, vocabulary_sha256, path_allowlist_bytes, path_allowlist_sha256, now_utc)


def validate_card_projection(projection):
    """Re-derive every record from the stored cards and pins; ``projection_invalid`` on any difference."""
    try:
        if (type(projection) is not dict or set(projection) != PROJECTION_KEYS or projection["schema"] != SCHEMA
                or projection["scope"] != SCOPE or projection["authority"] != "none"
                or projection["synthetic"] is not True or type(projection["sources"]) is not dict
                or type(projection["refused"]) is not dict
                or any(key not in REFUSAL_TOKENS or type(count) is not int or count < 1
                       for key, count in projection["refused"].items())):
            raise ValueError()
        sources = projection["sources"]
        if (type(projection["records"]) is not list or len(projection["records"]) != len(sources)
                or any(event_id(event) != ref for ref, event in sources.items())):
            raise ValueError()
        # record order, not key order: a stored projection is written with sorted keys
        ordered = [sources[record["provenance"]["source_event_ref"]] for record in projection["records"]]
        again = _project(ordered, projection["vocabulary"].encode("ascii"),
                         projection["vocabulary_sha256"], projection["path_allowlist"].encode("ascii"),
                         projection["path_allowlist_sha256"], projection["now_utc"])
        if (again["refused"] or again["records"] != projection["records"] or again["sources"] != sources
                or again["superseded"] != projection["superseded"]
                or again["accepted"] != projection["accepted"]):
            raise ValueError()
    except (CardMemoryRefused, ValueError, KeyError, TypeError, AttributeError, corpus_mod.ws.WorkspaceError):
        raise CardMemoryRefused("projection_invalid") from None
    return projection


def build_synthetic_card_memory(projection, encode, identity, dim, root, name, *, enabled=False):
    """One frozen synthetic card memory ``root/name`` from a validated projection, or ``{"status": "OFF"}``."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    validate_card_projection(projection)
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
        cutoff_utc = max(record["available_utc"] for record in records)
        texts = sorted({row["stored_text"] for rows in cells.values() for row in rows})
        by_text = dict(zip(texts, sib._vectors(encode, texts, dim)))
    except (sib.BuildRefused, sp.ProjectionRefused) as refusal:
        raise CardMemoryRefused(str(refusal)) from None
    projection_bytes = json.dumps(projection, sort_keys=True, indent=1).encode("utf-8")
    projection_sha256 = hashlib.sha256(projection_bytes).hexdigest()
    memory_dir = root / name
    try:
        os.mkdir(memory_dir)
    except FileExistsError:
        raise CardMemoryRefused("memory_exists") from None
    except OSError:
        raise CardMemoryRefused("write_failed") from None
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
        memory_sha256 = hashlib.sha256(memory_bytes).hexdigest()
        descriptor = {"schema": DESCRIPTOR_SCHEMA, "scope": SCOPE, "memory_sha256": memory_sha256, "dim": dim,
                      "encoder": encoder}
        descriptor_bytes = cell_mod.canonical(descriptor).encode("utf-8")
        stage = "descriptor"
        sib._write_new(memory_dir / "descriptor.json", descriptor_bytes)
        stage = "memory"
        sib._commit_memory(memory_dir, memory_bytes)
    except sib.BuildRefused as refusal:
        sib._report_incomplete(memory_dir, name, str(refusal), stage)
        raise CardMemoryRefused(str(refusal)) from None
    except Exception:  # noqa: BLE001 - one fixed token after the directory exists; nothing is removed
        sib._report_incomplete(memory_dir, name, "write_failed", stage)
        raise CardMemoryRefused("write_failed") from None
    return {"memory_sha256": memory_sha256, "descriptor_sha256": hashlib.sha256(descriptor_bytes).hexdigest(),
            "descriptor": descriptor, "records": len(records), "cutoff_utc": cutoff_utc}


def _read_bounded(path, limit):
    with open(path, "rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise CardMemoryRefused("file_over_bound")
    return raw


def _card_status(record, projection, when):
    """SUPERSEDED once a newer accepted card naming this one is available at ``when``; else CURRENT or STALE."""
    source_ref = record["provenance"]["source_event_ref"]
    for newer in projection["records"]:
        card = projection["sources"][newer["provenance"]["source_event_ref"]]["payload"]["public_knowledge"]
        if card.get("supersedes") == source_ref and corpus_mod.parse_utc(newer["available_utc"]) <= when:
            return "SUPERSEDED"
    expires = corpus_mod.parse_utc(record["provenance"]["valid_until_utc"])
    return "CURRENT" if when < expires else "STALE"


def ask_synthetic_cards(memory_dir, expected_memory_sha256, encoder, situation, situation_time_utc, *,
                        enabled=False):
    """Q4 over a pinned synthetic card memory; every result cites its source card and carries the rebuilt line."""
    if not _enabled(enabled):
        return {"status": "OFF"}
    from sandbox.mind.step2 import memory as memory_mod
    from sandbox.mind.step2 import q4 as q4_mod
    memory_dir = Path(memory_dir)
    try:
        raw = _read_bounded(memory_dir / "memory.json", memory_mod.MAX_MEMORY_MANIFEST_BYTES)
    except OSError:
        raise CardMemoryRefused("memory_unreadable") from None
    if (not isinstance(expected_memory_sha256, str) or sp.HEX64.fullmatch(expected_memory_sha256) is None
            or hashlib.sha256(raw).hexdigest() != expected_memory_sha256):
        raise CardMemoryRefused("memory_mismatch")
    try:
        manifest = json.loads(raw)
        if manifest.get("scope") != SCOPE or manifest.get("synthetic") is not True:
            raise ValueError()
        projection_raw = _read_bounded(memory_dir / manifest["projection_file"], 64 * 1024 * 1024)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise CardMemoryRefused("memory_invalid") from None
    if hashlib.sha256(projection_raw).hexdigest() != manifest["projection_sha256"]:
        raise CardMemoryRefused("projection_mismatch")
    try:
        projection = validate_card_projection(json.loads(projection_raw))
    except ValueError:
        raise CardMemoryRefused("projection_invalid") from None
    when = corpus_mod.parse_utc(situation_time_utc) if isinstance(situation_time_utc, str) else None
    if when is None:
        raise CardMemoryRefused("situation_time_invalid")
    by_ref = {record["provenance"]["event_ref"]: record for record in projection["records"]}
    try:
        memory = memory_mod.Memory(memory_dir, manifest_bytes=raw)
        out = q4_mod.Q4(memory, encoder).ask(situation, situation_time_utc)
    except memory_mod.MemoryError_:
        raise CardMemoryRefused("memory_unreadable") from None
    results = []
    for result in out["results"]:
        record = by_ref.get(result["ref"])
        if record is None or record["record_id"] != result["doc_id"]:
            raise CardMemoryRefused("result_unresolved")
        provenance = record["provenance"]
        results.append({"ref": result["ref"], "text": record["stored_text"], "time_utc": record["time_utc"],
                        "cell": result["cell"], "source_ref_sha256": provenance["source_event_ref"],
                        "card_status": _card_status(record, projection, when),
                        "valid_until_utc": provenance["valid_until_utc"], "visibility": provenance["visibility"],
                        "provenance": provenance["provenance"]})
    answer = {"status": out["status"], "reason": out["reason"], "results": results,
              "coverage": {"cutoff_utc": manifest["cutoff_utc"]}}
    corpus_mod.ws.guard_object(answer)
    return answer
