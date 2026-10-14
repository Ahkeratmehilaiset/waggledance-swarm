"""HEX MEMORY STEP 2, memory: the frozen corpus in WaggleDance's eight hex cells, each cell a verified generation.

usage: python -B sandbox/mind/step2/memory.py build --corpus <dir> --vectors <dir> --out <memory dir>
       python -B sandbox/mind/step2/memory.py status --memory <memory dir>

Placement is the 2026-10-13 bake-off placement over WaggleDance's ``HexCellTopology`` (the winning whole-lesson
variant): a lesson note goes to the ``learning`` cell; an event goes through ``assign_cell(intent, text)`` with the
intent ``constraint`` for a finding, veto, changes-requested or blocked event (the ``safety`` cell) and ``chat``
otherwise (WaggleDance's keyword scan, else ``general``). Each cell is written by ``cell.publish`` from the retained
vectors of ``encode.py``; nothing is re-encoded here. A record has two vectors in its cell, ``<record_id>#w`` (its
text, stored text = the guarded text) and ``<record_id>#d`` (its summary, stored text = the summary).

Per vector the provenance row also carries:

* ``available_utc``: the latest time the stored text is known to have existed in this form (lesson: file mtime; the
  one stripped note: the cutoff; event: ``ts_utc``). The query layer refuses a record whose ``available_utc`` is not
  before the situation time, so a later edit can never answer an earlier situation.
* ``tier``: the MAGMA source type and its trust weight from ``waggledance.core.magma.provenance`` (an event record
  is ``observed``: the bridge logged it; a lesson note is ``learned_from_case``). The tier is read-only: no read
  raises it, and this step applies no decay.

``memory.json`` binds every cell to its current receipt sha256, so a cell rolled back consistently inside its own
directory still fails against the memory file; a writer that rewrites both is caught only by the freeze hash posted
outside the machine (stated limitation). Writes only under ``--out``. No network, no bridge event, no gate code.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[2]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(HERE))
import cell as cell_mod  # noqa: E402
import corpus as corpus_mod  # noqa: E402
import encode as encode_mod  # noqa: E402
from waggledance.core import hex_cell_topology as topology_mod  # noqa: E402
from waggledance.core.magma.provenance import ProvenanceRecord, VALID_SOURCE_TYPES  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-memory-step2.v1"
TOPOLOGY_FILE = SRC / "waggledance" / "core" / "hex_cell_topology.py"
TIER = {"event": "observed", "lesson": "learned_from_case"}
PLACEMENT = "bakeoff-2026-10-13: lesson->learning; event->assign_cell(constraint|chat, text.lower())"


class MemoryError_(ValueError):
    """A memory directory that cannot be used as given."""


def place(record, topology):
    if record["kind"] == "lesson":
        return topology_mod.CELL_LEARNING
    return topology.assign_cell(record["intent"], record["text"].lower()).cell_id


def tier(kind):
    source_type = TIER[kind]
    if source_type not in VALID_SOURCE_TYPES:
        raise MemoryError_("tier_unknown")
    return {"source_type": source_type,
            "trust_weight": ProvenanceRecord(fact_id="-", source_type=source_type).trust_weight()}


def available_utc(record, cutoff_utc):
    provenance = record["provenance"]
    if record["kind"] == "event":
        return record["time_utc"]
    if provenance.get("transform"):
        return cutoff_utc
    return provenance["mtime_utc"]


def vector_rows(record, cutoff):
    """One cell row per view of a record: ``<record_id>#w`` (its text) and ``<record_id>#d`` (its summary)."""
    provenance = dict(record["provenance"], available_utc=available_utc(record, cutoff), tier=tier(record["kind"]),
                      record=record["record_id"])
    if provenance["available_utc"] > cutoff:
        raise MemoryError_("available_after_cutoff")
    for view in encode_mod.VIEWS:
        yield view, {"record_id": f"{record['record_id']}#{view}", "kind": record["kind"],
                     "time_utc": record["time_utc"], "meta": dict(record["meta"], view=view),
                     "stored_text": record["stored_text"] if view == "w" else record["summary"],
                     "provenance": dict(provenance, view=view)}


def build(corpus_dir, vectors_dir, out_dir):
    corpus_manifest, records = corpus_mod.read_corpus(corpus_dir)
    vectors_manifest, vectors = encode_mod.read_vectors(vectors_dir, corpus_manifest)
    cutoff = corpus_manifest["cutoff_utc"]
    topology = topology_mod.HexCellTopology()
    cells = {}
    for index, record in enumerate(records):
        bucket = cells.setdefault(place(record, topology), [])
        for view, row in vector_rows(record, cutoff):
            bucket.append((row, vectors[view][index]))
    encoder = {key: vectors_manifest["encoder"][key] for key in sorted(vectors_manifest["encoder"])}
    pointers = {}
    for cell_id in topology_mod.ALL_CELLS:
        if cell_id not in cells:
            continue
        rows = cells[cell_id]
        pointer = cell_mod.publish(out_dir / "cells" / cell_id, cell_id, [row for row, _ in rows],
                                   np.vstack([vector for _, vector in rows]), dim=vectors_manifest["dim"],
                                   cutoff_utc=cutoff, encoder=encoder, corpus_sha256=corpus_manifest["corpus_sha256"])
        pointers[cell_id] = dict(pointer, count=len(rows))
    memory = {
        "schema": SCHEMA, "authority": "none", "scope": "shadow_memory_not_gate", "cutoff_utc": cutoff,
        "corpus_sha256": corpus_manifest["corpus_sha256"], "record_ids_sha256": corpus_manifest["record_ids_sha256"],
        "vectors_sha256": vectors_manifest["vectors_sha256"], "encoder": encoder, "dim": vectors_manifest["dim"],
        "placement": PLACEMENT, "topology_source_sha256": cell_mod.file_sha256(TOPOLOGY_FILE),
        "cells": pointers, "records": len(records),
    }
    write_memory(out_dir, memory)
    return memory


def write_memory(out_dir, memory):
    corpus_mod.ws.guard_object(memory)
    cell_mod.write_atomic(out_dir / "memory.json", cell_mod.canonical(memory).encode("utf-8"))


def memory_sha256(out_dir):
    return cell_mod.file_sha256(out_dir / "memory.json")


class Memory:
    """All cells of one memory directory; every cell HEALTHY or QUARANTINED, none silently empty."""

    def __init__(self, out_dir):
        self.out_dir = out_dir
        try:
            self.memory = json.loads((out_dir / "memory.json").read_bytes())
            if self.memory.get("schema") != SCHEMA:
                raise MemoryError_("memory_schema")
            self.cell_ids = [cell_id for cell_id in topology_mod.ALL_CELLS if cell_id in self.memory["cells"]]
        except (OSError, ValueError, KeyError, TypeError):
            raise MemoryError_("memory_unreadable") from None
        self.topology = topology_mod.HexCellTopology()
        self.views = {}
        for cell_id in self.cell_ids:
            view = cell_mod.load(out_dir / "cells" / cell_id, cell_id)
            bound = self.memory["cells"][cell_id]
            if view.status == "ABSENT":
                view = cell_mod.CellView(cell_id, "QUARANTINED", "cell_dir_missing")
            elif view.healthy and (view.pointer["receipt_sha256"] != bound["receipt_sha256"]
                                   or view.pointer["generation"] != bound["generation"]):
                view = cell_mod.CellView(cell_id, "QUARANTINED", "memory_binding_mismatch")
            elif view.healthy and view.receipt["content"]["dim"] != self.memory["dim"]:
                view = cell_mod.CellView(cell_id, "QUARANTINED", "dim_mismatch")
            self.views[cell_id] = view

    def status(self):
        return {cell_id: {"status": view.status, "reason": view.reason,
                          "count": view.receipt["content"]["count"] if view.healthy else None,
                          "rebuild_possible": view.rebuild_possible}
                for cell_id, view in self.views.items()}

    def rebuild(self, cell_id):
        """Rebuild one cell from its retained vectors as a new generation, then rebind memory.json."""
        pointer = cell_mod.rebuild_from_retained(self.out_dir / "cells" / cell_id, cell_id)
        memory = dict(self.memory, cells=dict(self.memory["cells"]))
        memory["cells"][cell_id] = dict(pointer, count=memory["cells"][cell_id]["count"])
        write_memory(self.out_dir, memory)
        return pointer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("build")
    one.add_argument("--corpus", type=Path, required=True)
    one.add_argument("--vectors", type=Path, required=True)
    one.add_argument("--out", type=Path, required=True)
    two = sub.add_parser("status")
    two.add_argument("--memory", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "build":
        if (args.out / "memory.json").exists():
            raise MemoryError_("memory_exists")
        memory = build(args.corpus, args.vectors, args.out)
        print(json.dumps({"memory_sha256": memory_sha256(args.out),
                          "cells": {cell_id: row["count"] for cell_id, row in memory["cells"].items()}}))
        return 0
    loaded = Memory(args.memory)
    print(json.dumps({"memory_sha256": memory_sha256(args.memory), "cells": loaded.status()}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
