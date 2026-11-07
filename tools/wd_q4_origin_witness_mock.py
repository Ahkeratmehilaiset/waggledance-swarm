"""Data-only synthetic origin witness, never a production provenance guard.

The caller controls the registry and mock world. REGISTERED means these exact
objects and their primitive metadata still match the sealed mock snapshot; it
does not authenticate their creator, loader, package, code, or real imports.
Callables, real loader/spec objects, subclasses and mutable metadata are outside
this model and remain INCOMPLETE. Concurrent mutation and registry tampering by
the caller are outside the model. No lifecycle operation imports or performs I/O.
"""

from types import ModuleType

INCOMPLETE = "INCOMPLETE"
REGISTERED = "REGISTERED"


class Incomplete(ValueError):
    """A mock registration or seal could not establish its closed contract."""


def _name(value):
    return (
        type(value) is str
        and 0 < len(value) <= 160
        and all(part.isidentifier() for part in value.split("."))
    )


def _snapshot(module):
    if type(module) is not ModuleType:
        raise Incomplete("unsupported module type")
    raw = ModuleType.__getattribute__(module, "__dict__")
    if type(raw) is not dict or len(raw) > 64:
        raise Incomplete("unsupported metadata shape or budget")
    entries = tuple(raw.items())
    # Inspect key types before lookup, sorting, hashing or comparing input keys.
    if any(type(key) is not str for key, _ in entries):
        raise Incomplete("unsupported metadata key")
    for key, value in entries:
        if key in ("__spec__", "__loader__") and value is not None:
            raise Incomplete("real specs and loaders are outside the mock model")
        # Class equality can execute a custom metaclass; compare identities only.
        value_type = type(value)
        if not any(value_type is allowed for allowed in (type(None), bool, int, str, bytes, ModuleType)):
            raise Incomplete("unsupported metadata value")
    return tuple(sorted(entries, key=lambda entry: entry[0]))


def _same_snapshot(left, right):
    return len(left) == len(right) and all(
        old_key == new_key and old_value is new_value
        for (old_key, old_value), (new_key, new_value) in zip(left, right)
    )


class MockOriginWitness:
    """Collect once, seal once, then check a small explicit mock world.

    A validation failure is terminal. Unknown classification targets cannot
    enroll themselves; they leave an otherwise unchanged sealed world intact.
    Parent depth counts edges, with at most max_entries registered objects.
    """

    def __init__(self, *, max_entries=64, max_depth=8):
        if type(max_entries) is not int or not 1 <= max_entries <= 128:
            raise ValueError("max_entries must be an integer from 1 to 128")
        if type(max_depth) is not int or not 1 <= max_depth <= 64:
            raise ValueError("max_depth must be an integer from 1 to 64")
        self._max_entries = max_entries
        self._max_depth = max_depth
        self._records = {}
        self._state = "collecting"

    def register(self, name, module, *, parent=None):
        try:
            if self._state != "collecting":
                raise Incomplete("registration is closed")
            if not _name(name) or name in self._records:
                raise Incomplete("invalid or duplicate registration name")
            if len(self._records) >= self._max_entries:
                raise Incomplete("registration budget exceeded")
            if parent is not None and (
                type(parent) is not tuple
                or len(parent) != 2
                or not _name(parent[0])
                or type(parent[1]) is not str
                or len(parent[1]) > 160
                or not parent[1].isidentifier()
            ):
                raise Incomplete("unsupported parent binding")
            metadata = _snapshot(module)
            actual_name = dict(metadata).get("__name__")
            if type(actual_name) is not str or actual_name != name:
                raise Incomplete("module name does not match registration")
            self._records[name] = (module, metadata, parent)
        except Incomplete:
            self._state = INCOMPLETE
            raise

    def _validate_world(self, world):
        if not self._records:
            raise Incomplete("empty registration has no authority")
        if type(world) is not dict or len(world) > self._max_entries:
            raise Incomplete("unsupported world shape or budget")
        entries = tuple(world.items())
        if any(type(name) is not str for name, _ in entries):
            raise Incomplete("unsupported world key")
        if set(world) != set(self._records):
            raise Incomplete("world membership changed")
        known_ids = {id(record[0]) for record in self._records.values()}
        for name, module in entries:
            original, metadata, parent = self._records[name]
            if module is not original or not _same_snapshot(metadata, _snapshot(module)):
                raise Incomplete("registered object or metadata changed")
            for _, value in metadata:
                if type(value) is ModuleType and id(value) not in known_ids:
                    raise Incomplete("unregistered module reference")
            if parent is not None:
                parent_name, attribute = parent
                if parent_name not in self._records:
                    raise Incomplete("unregistered parent")
                parent_metadata = self._records[parent_name][1]
                if dict(parent_metadata).get(attribute) is not module:
                    raise Incomplete("parent binding does not match child")
        for name in self._records:
            visited = set()
            cursor = name
            for depth in range(self._max_depth + 1):
                if cursor in visited:
                    raise Incomplete("parent cycle")
                visited.add(cursor)
                parent = self._records[cursor][2]
                if parent is None:
                    break
                if depth == self._max_depth:
                    raise Incomplete("parent depth budget exceeded")
                cursor = parent[0]

    def seal(self, world):
        try:
            if self._state != "collecting":
                raise Incomplete("seal is closed")
            self._validate_world(world)
            self._state = "sealed"
        except Incomplete:
            self._state = INCOMPLETE
            raise

    def classify(self, name, module, world):
        if self._state != "sealed":
            return INCOMPLETE
        try:
            self._validate_world(world)
        except Incomplete:
            self._state = INCOMPLETE
            return INCOMPLETE
        if not _name(name) or name not in self._records:
            return INCOMPLETE
        if module is not self._records[name][0]:
            return INCOMPLETE
        return REGISTERED

