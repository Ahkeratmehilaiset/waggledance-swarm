"""Read-only frozen hex-memory loader; no corpus builder or rebuild operation."""
import json
from pathlib import Path
from sandbox.mind.step2 import cell as cell_mod
from waggledance.core import hex_cell_topology as topology_mod

SCHEMA = "wd.bridge-mind-hex-memory-step2.v1"

class MemoryError_(ValueError):
    """A memory directory that cannot be used as given."""


def memory_sha256(out_dir):
    return cell_mod.file_sha256(Path(out_dir) / "memory.json")


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
