"""Pure descriptor parser. Pins are caller-supplied and do not authenticate data."""
import hashlib
import json

DESCRIPTOR_SCHEMA = "wd.bridge-mind-q4-memory-descriptor.v2"
SHARED_SCOPE = "wd.q4-shared-projected-events.v1"
DESCRIPTOR_BUDGET = 65536
ENCODER_KEYS = ("model", "revision", "weights_sha256")


def _hex64(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def read_descriptor_bytes(raw, expected_sha256, *, error_type):
    """Validate exact bytes, retaining the caller's existing exception contract."""
    try:
        return _read_descriptor_bytes_inner(raw, expected_sha256, error_type=error_type)
    except error_type as exc:
        reason = str(exc)
    except Exception:
        reason = "descriptor_unreadable"
    del raw, expected_sha256
    raise error_type(reason) from None


def _read_descriptor_bytes_inner(raw, expected_sha256, *, error_type):
    if not _hex64(expected_sha256):
        raise error_type("descriptor_pin_invalid")
    if type(raw) is not bytes:
        raise error_type("descriptor_unreadable")
    if len(raw) > DESCRIPTOR_BUDGET:
        raise error_type("descriptor_over_budget")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise error_type("descriptor_pin_mismatch")
    parsed = False
    try:
        descriptor = json.loads(raw.decode("utf-8"))
        parsed = True
    except (ValueError, RecursionError):
        pass
    if not parsed:
        del raw
        raise error_type("descriptor_unreadable") from None
    if isinstance(descriptor, dict) and descriptor.get("scope") != SHARED_SCOPE:
        raise error_type("descriptor_scope_mismatch")
    if (not isinstance(descriptor, dict) or set(descriptor) != {"schema", "scope", "memory_sha256", "dim", "encoder"}
            or descriptor["schema"] != DESCRIPTOR_SCHEMA or not _hex64(descriptor["memory_sha256"])
            or type(descriptor["dim"]) is not int or descriptor["dim"] < 1
            or not isinstance(descriptor["encoder"], dict) or set(descriptor["encoder"]) != set(ENCODER_KEYS)
            or not all(isinstance(descriptor["encoder"][key], str) and descriptor["encoder"][key]
                       for key in ENCODER_KEYS)
            or not _hex64(descriptor["encoder"]["weights_sha256"])):
        raise error_type("descriptor_shape")
    return descriptor
