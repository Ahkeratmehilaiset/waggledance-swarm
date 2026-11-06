"""Default-off, no-I/O composition of caller-supplied session declarations.

The caller must independently authenticate the two documents, their expected
digests, the entire runtime tuple, the actual launcher identity and the code
used to call this function, and must prevent concurrent mutation. This helper
does not attest any of them, prove freshness, access the filesystem or launch a
process. A lane is declared, not authenticated. There is no separate query parameter;
the caller must keep query out of tuple fields and must not log argv without
first checking its contents. Tuple values can contain arbitrary caller text.

It cross-checks the fleet/binding documents with their worker pins, the lane,
the caller-provided bundle/wrapper/plan pins and all eight expected snapshot
pins before using the existing strict session argv builder. The argv is still
only a construction candidate, not permission or proof that it is safe to run.
"""

from tools.wd_q4_lane_binding import resolve_lane_binding
from tools.wd_q4_session_tuple import build_session_argv

_TUPLE_KEYS = frozenset({"worker", "outer", "expected_binding", "bundle_commit", "manifest_sha256",
                         "wrapper_sha256", "public_plan_sha256"})


def resolve_attributed_session_tuple(*, fleet_bytes, expected_fleet_sha256, binding_bytes,
                                     expected_binding_sha256, lane, runtime_tuple, enabled=False):
    """Return an attributed candidate only when all independent declarations agree."""
    if enabled is False:
        return {"status": "OFF"}
    pins = worker = outer = expected = argv = None
    try:
        if enabled is not True or type(runtime_tuple) is not dict or set(runtime_tuple) != _TUPLE_KEYS:
            raise ValueError()
        if any(type(runtime_tuple[key]) is not dict for key in ("worker", "outer", "expected_binding")):
            raise ValueError()
        if any(type(runtime_tuple[key]) is not str for key in ("bundle_commit", "manifest_sha256",
                                                              "wrapper_sha256", "public_plan_sha256")):
            raise ValueError()
        worker, outer, expected = (dict(runtime_tuple[key]) for key in ("worker", "outer", "expected_binding"))
        pins = resolve_lane_binding(fleet_bytes, expected_fleet_sha256, binding_bytes, expected_binding_sha256,
                                    lane=lane, enabled=True)
        if (worker.get("lane") != lane or pins["lane"] != lane
                or worker.get("fleet-sha256") != expected_fleet_sha256
                or worker.get("binding-sha256") != expected_binding_sha256
                or runtime_tuple["bundle_commit"] != pins["expected_commit"]
                or runtime_tuple["manifest_sha256"] != pins["expected_manifest_sha256"]
                or runtime_tuple["wrapper_sha256"] != pins["expected_wrapper_sha256"]
                or runtime_tuple["public_plan_sha256"] != pins["expected_public_plan_sha256"]
                or expected != pins["expected_binding"]):
            raise ValueError()
        argv = build_session_argv(worker, outer, expected, enabled=True)
        return {"status": "TUPLE_CONSISTENT", "attribution": "DECLARED_NOT_AUTHENTICATED",
                "freshness": "FRESHNESS_UNKNOWN", "argv": argv}
    except Exception:
        pass
    # Do not retain inputs in this refusal frame; caller frames are outside
    # this guarantee and must not be logged as if they were scrubbed.
    fleet_bytes = expected_fleet_sha256 = binding_bytes = expected_binding_sha256 = None
    lane = runtime_tuple = enabled = pins = worker = outer = expected = argv = None
    raise ValueError("attributed_tuple_refused") from None
