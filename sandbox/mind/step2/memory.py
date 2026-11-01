"""Read-only frozen hex-memory loader; no corpus builder or rebuild operation."""
import hashlib
import json
from pathlib import Path
from sandbox.mind.step2 import cell as cell_mod
from waggledance.core import hex_cell_topology as topology_mod

SCHEMA = "wd.bridge-mind-hex-memory-step2.v1"
MAX_MEMORY_MANIFEST_BYTES = 1024 * 1024
# Opt-in aggregate availability limit, not a native-deserializer memory bound.
MAX_MEMORY_SNAPSHOT_BYTES = 512 * 1024 * 1024

class MemoryError_(ValueError):
    """A memory directory that cannot be used as given."""


def memory_sha256(out_dir):
    with (Path(out_dir) / "memory.json").open("rb") as manifest_file:
        manifest = manifest_file.read(MAX_MEMORY_MANIFEST_BYTES + 1)
    if len(manifest) > MAX_MEMORY_MANIFEST_BYTES:
        raise MemoryError_("memory_over_budget")
    return hashlib.sha256(manifest).hexdigest()


class Memory:
    """Load cells; supplied manifest bytes must be authenticated by the caller."""

    def __init__(self, out_dir, *, manifest_bytes=None):
        self.out_dir = out_dir
        try:
            if manifest_bytes is None:
                with (out_dir / "memory.json").open("rb") as manifest_file:
                    manifest = manifest_file.read(MAX_MEMORY_MANIFEST_BYTES + 1)
            elif type(manifest_bytes) is bytes:
                manifest = manifest_bytes
            else:
                raise MemoryError_("memory_unreadable")
            if len(manifest) > MAX_MEMORY_MANIFEST_BYTES:
                raise MemoryError_("memory_over_budget")
            self.memory = json.loads(manifest)
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

    @classmethod
    def from_snapshots(cls, *, manifest_bytes, expected_sha256, cell_snapshots, enabled=False):
        """Load a bounded byte snapshot; the caller must authenticate the manifest pin.

        No live-pointer freshness, producer identity, filesystem isolation, or
        native-deserializer allocation bound is established by this method.
        """
        captured = parsed = loaded = view = item = bound = artifacts = cell_map = control_receipt = None
        try:
            if (enabled is not True or type(manifest_bytes) is not bytes
                    or len(manifest_bytes) > MAX_MEMORY_MANIFEST_BYTES
                    or type(expected_sha256) is not str or len(expected_sha256) != 64
                    or any(char not in "0123456789abcdef" for char in expected_sha256)
                    or hashlib.sha256(manifest_bytes).hexdigest() != expected_sha256
                    or type(cell_snapshots) is not dict or len(cell_snapshots) > len(topology_mod.ALL_CELLS)):
                raise MemoryError_("snapshot_refused")
            cell_map = dict(cell_snapshots)
            parsed = json.loads(manifest_bytes)
            if (type(parsed) is not dict or parsed.get("schema") != SCHEMA
                    or type(parsed.get("dim")) is not int or parsed["dim"] < 1
                    or type(parsed.get("cells")) is not dict
                    or len(parsed["cells"]) > len(topology_mod.ALL_CELLS)
                    or any(type(key) is not str or key not in topology_mod.ALL_CELLS for key in parsed["cells"])
                    or set(parsed["cells"]) != set(cell_map)):
                raise MemoryError_("snapshot_refused")
            captured = {}
            total = len(manifest_bytes)
            for cell_id in parsed["cells"]:
                item, bound = cell_map[cell_id], parsed["cells"][cell_id]
                if type(item) is not dict or len(item) != 3:
                    raise MemoryError_("snapshot_refused")
                item = dict(item)
                if (set(item) != {"pointer", "log", "artifacts"}
                        or type(item["pointer"]) is not bytes or len(item["pointer"]) > cell_mod.MAX_POINTER_BYTES
                        or type(item["log"]) is not bytes or len(item["log"]) > cell_mod.MAX_LOG_BYTES
                        or type(item["artifacts"]) is not dict or len(item["artifacts"]) not in (4, 5)
                        or type(bound) is not dict or type(bound.get("generation")) is not int
                        or bound["generation"] < 0 or type(bound.get("receipt_sha256")) is not str
                        or len(bound["receipt_sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in bound["receipt_sha256"])):
                    raise MemoryError_("snapshot_refused")
                artifacts = dict(item["artifacts"])
                if (not {"receipt", "index", "meta", "provenance"} <= artifacts.keys()
                        or not artifacts.keys() <= {"receipt", "index", "meta", "provenance", "vectors"}
                        or any(type(key) is not str for key in artifacts)
                        or any(type(value) is not bytes for value in artifacts.values())):
                    raise MemoryError_("snapshot_refused")
                total += len(item["pointer"]) + len(item["log"]) + sum(map(len, artifacts.values()))
                if total > MAX_MEMORY_SNAPSHOT_BYTES:
                    raise MemoryError_("snapshot_refused")
                captured[cell_id] = {"pointer": item["pointer"], "log": item["log"], "artifacts": artifacts}
            loaded = cls.__new__(cls)
            loaded.out_dir = None
            loaded.memory = parsed
            loaded.cell_ids = [cell_id for cell_id in topology_mod.ALL_CELLS if cell_id in parsed["cells"]]
            loaded.topology = topology_mod.HexCellTopology()
            loaded.views = {}
            # Reject any cell's pointer/log/receipt control mismatch before the
            # first cell reaches the native index deserializer.
            for cell_id in loaded.cell_ids:
                item, bound = captured[cell_id], parsed["cells"][cell_id]
                _, control_receipt = cell_mod._preflight_snapshot_controls(
                    cell_id, pointer_bytes=item["pointer"], log_bytes=item["log"],
                    artifact_bytes=item["artifacts"], expected_generation=bound["generation"],
                    expected_receipt_sha256=bound["receipt_sha256"])
                if control_receipt["content"]["dim"] != parsed["dim"]:
                    raise MemoryError_("snapshot_refused")
            for cell_id in loaded.cell_ids:
                item, bound = captured[cell_id], parsed["cells"][cell_id]
                view = cell_mod.load_snapshot(cell_id, pointer_bytes=item["pointer"], log_bytes=item["log"],
                                              artifact_bytes=item["artifacts"],
                                              expected_generation=bound["generation"],
                                              expected_receipt_sha256=bound["receipt_sha256"], enabled=True)
                if (not view.healthy or view.pointer["receipt_sha256"] != bound["receipt_sha256"]
                        or view.pointer["generation"] != bound["generation"]
                        or view.receipt["content"]["dim"] != parsed["dim"]):
                    raise MemoryError_("snapshot_refused")
                loaded.views[cell_id] = view
            return loaded
        except Exception:
            pass
        del manifest_bytes, cell_snapshots, captured, parsed, loaded, view, item, bound, artifacts, cell_map, control_receipt
        raise MemoryError_("snapshot_refused") from None

    def status(self):
        return {cell_id: {"status": view.status, "reason": view.reason,
                          "count": view.receipt["content"]["count"] if view.healthy else None,
                          "rebuild_possible": view.rebuild_possible}
                for cell_id, view in self.views.items()}
