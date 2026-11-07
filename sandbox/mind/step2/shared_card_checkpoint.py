"""Default-OFF validation of one decision-pinned staged card snapshot.

Caller pins are consistency inputs, not authenticated authority or freshness.
This module never publishes a pointer, commits a generation or resolves history.
"""
import hashlib
import json
from pathlib import Path
import re
import stat

_FIELDS = {"schema", "generation", "artifacts", "decision_sha256",
           "policy_sha256", "roster_sha256", "source_prefix"}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _parse(raw):
    return json.loads(raw.decode("ascii"), object_pairs_hook=_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _plain(path):
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError()


def _capture(root, pins):
    if type(pins) is not dict or not 3 <= len(pins) <= 2048:
        raise ValueError()
    captured = {}
    total = 0
    for name, pin in pins.items():
        if (type(name) is not str or type(pin) is not str or not _HEX.fullmatch(pin)
                or "\\" in name or ":" in name or name.startswith("/")
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError()
        path = root / name
        _plain(path)
        if not path.is_file():
            raise ValueError()
        with path.open("rb") as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        total += len(raw)
        if len(raw) > 16 * 1024 * 1024 or total > 256 * 1024 * 1024 or _sha(raw) != pin:
            raise ValueError()
        captured[name] = raw
    actual = set()
    for path in root.rglob("*"):
        _plain(path)
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
            if len(actual) > 2048:
                raise ValueError()
    if actual != set(captured) or not {"memory.json", "descriptor.json", "projection.json"} <= actual:
        raise ValueError()
    return captured


def validate_card_snapshot(*, enabled=False, pointer_bytes=None, expected_pointer_sha256=None,
        generation_dir=None, decision_bytes=None, decision_sha256=None,
        policy_bytes=None, policy_sha256=None, roster_bytes=None, roster_sha256=None,
        card_policy_bytes=None, source_path=None, generation_path=None, end_cursor=None):
    if enabled is False:
        return {"status": "OFF"}
    try:
        if (enabled is not True or type(pointer_bytes) is not bytes or len(pointer_bytes) > 1024 * 1024
                or type(expected_pointer_sha256) is not str or not _HEX.fullmatch(expected_pointer_sha256)
                or _sha(pointer_bytes) != expected_pointer_sha256):
            raise ValueError()
        pointer = _parse(pointer_bytes)
        if (type(pointer) is not dict or set(pointer) != _FIELDS
                or pointer["schema"] != "wd.q4-card-snapshot.v1"
                or type(pointer["generation"]) is not str or not _NAME.fullmatch(pointer["generation"])
                or json.dumps(pointer, sort_keys=True, separators=(",", ":")).encode("ascii") != pointer_bytes):
            raise ValueError()
        for field, value in (("decision_sha256", decision_sha256), ("policy_sha256", policy_sha256),
                             ("roster_sha256", roster_sha256)):
            if type(value) is not str or not _HEX.fullmatch(value) or pointer[field] != value:
                raise ValueError()
        root = Path(generation_dir)
        if not root.is_absolute() or root.name != pointer["generation"]:
            raise ValueError()
        _plain(root)
        captured = _capture(root, pointer["artifacts"])
        from sandbox.mind.step2 import shared_card_projection as cards
        from sandbox.mind.step2 import shared_result_resolver as resolver
        from sandbox.mind.step2 import q4_descriptor, memory
        decision = cards.verify_route_decision(decision_bytes, decision_sha256,
                         policy_sha256=policy_sha256, roster_sha256=roster_sha256)
        if (pointer["source_prefix"] != decision["source_prefix"]
                or end_cursor.offset != decision["source_prefix"]["offset"]
                or end_cursor.generation != decision["source_prefix"]["generation"]
                or type(card_policy_bytes) is not bytes
                or _sha(card_policy_bytes) != decision["card_policy_sha256"]):
            raise ValueError()
        manifest = resolver._parse(captured["memory.json"])
        projection = resolver._parse(captured["projection.json"])
        if manifest.get("projection_schema") != cards.SCHEMA:
            raise ValueError()
        descriptor = q4_descriptor.read_descriptor_bytes(captured["descriptor.json"],
                            _sha(captured["descriptor.json"]), error_type=ValueError)
        if descriptor["memory_sha256"] != _sha(captured["memory.json"]):
            raise ValueError()
        resolver.resolve_results(memory_manifest=captured["memory.json"],
            expected_memory_sha256=_sha(captured["memory.json"]), projection_bytes=captured["projection.json"],
            results=[], query_utc=projection["coverage"]["cutoff_utc"],
            policy_bytes=policy_bytes, policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            card_decision_bytes=decision_bytes, card_decision_sha256=decision_sha256)
        cards.verify_shared_card_sources(projection, enabled=True, source_path=source_path,
            generation_path=generation_path, end_cursor=end_cursor,
            expected_prefix_sha256=decision["source_prefix"]["sha256"],
            nonreview_policy_bytes=policy_bytes, nonreview_policy_sha256=policy_sha256,
            roster_bytes=roster_bytes, roster_sha256=roster_sha256,
            card_policy_bytes=card_policy_bytes, card_policy_sha256=decision["card_policy_sha256"],
            _real_decision=decision if decision["synthetic"] is False else None)
        opened = memory.Memory(root, manifest_bytes=captured["memory.json"])
        if not opened.views or any(not view.healthy for view in opened.views.values()):
            raise ValueError()
        if _capture(root, pointer["artifacts"]) != captured:
            raise ValueError()
        return {"status": "VALIDATED_STAGED", "completeness": "UNKNOWN"}
    except Exception:
        return {"status": "INCOMPLETE"}

