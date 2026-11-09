"""Validate cooperative observations of append-only native module attributes.

The caller must independently establish the exact manifest-verified native
window and report nesting or concurrency conservatively. The result proves only
the supplied snapshots' consistency, not native allocation, DLL provenance,
same-user authenticity, in-place changes of mutable values, or the absence of
changes between observations.
"""

import types


class NativeAdditionsUnknown(Exception):
    """The available observation does not establish an append-only update."""


def validate_additions(*, name, module, registry, registry_before, registry_after,
                       recorded, before, after,
                       window_verified, nested_or_concurrent):
    """Return a fresh snapshot only for a verified, single, append-only interval."""
    if (type(name) is not str or not name or type(module) is not types.ModuleType
            or any(type(item) is not dict for item in (registry, registry_before, registry_after))
            or any(item.get(name) is not module for item in (registry, registry_before, registry_after))
            or window_verified is not True or nested_or_concurrent is not False
            or any(type(snapshot) is not dict for snapshot in (recorded, before, after))):
        raise NativeAdditionsUnknown()
    current = types.ModuleType.__getattribute__(module, '__dict__')
    if (type(current.get('__name__')) is not str or current['__name__'] != name
            or any(type(key) is not str for snapshot in (recorded, before, after, current)
                   for key in snapshot)):
        raise NativeAdditionsUnknown()

    def same(left, right):
        return left.keys() == right.keys() and all(left[key] is right[key] for key in left)

    if (not same(recorded, before) or not same(after, current)
            or not before.keys() < after.keys()
            or any(before[key] is not after[key] for key in before)):
        raise NativeAdditionsUnknown()
    return dict(after)
