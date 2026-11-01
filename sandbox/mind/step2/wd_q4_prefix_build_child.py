"""Default-off fixed prefix build child: a closed caller-pinned plan drives the strict-prefix producer with the
fixed E5 encoder path; no caller-supplied encoder callback, identity or dimension.

``build_prefix_generation`` takes the plan bytes with their caller pin (schema ``wd.q4-prefix-build-plan.v1``:
generation name, prior pointer digest or null, prefix budget, lock timeout, the encoder record {model, revision,
model_dir, weights_sha256, dim} and the four source pins), plus the caller-owned ``PublicSourceConfig``, the
``BridgePrefixPin`` the private state continues from, the checkpoint root, the private state root and the
explicitly caller-pinned ``trusted_model_root``. The encoder is never a parameter: the child derives the identity
with ``encode.model_identity`` over the pinned model directory, requires its ``weights_sha256`` to equal the plan
pin, loads the model only through the fixed ``encode.E5`` path (offline, cpu, local files only) and wraps it with
the builder's ``e5_passage_encoder`` (``"passage: "`` prefix, 2000-character truncation, batch 32, normalised
float32, dim 384). The resulting ``PrefixPublicRequest`` goes to
``shared_prefix_public_producer.publish_public_prefix`` unchanged, so prior-pointer and start-prefix continuity, the
private state lock and the pointer CAS stay the producer's; this module adds no publication logic.

Model root binding (v2 finding 1). ``Path.is_absolute`` alone accepts UNC shares and paths through symbolic links
or junctions, so an unbound ``model_dir`` would let ``model_identity`` read remote or escaped data before the
backend's offline controls apply. The plan's ``model_dir`` is therefore bound to ``trusted_model_root`` before any
read: both must be drive-letter paths (no UNC, no ``\\\\?\\`` device form), with no ``.`` or ``..`` component, the
drive must be a fixed local volume (``GetDriveTypeW == DRIVE_FIXED``), ``model_dir`` must be the root or below it
(``os.path.commonpath`` on case-folded strings), and every path component from the drive root down to
``model_dir``, every entry below ``model_dir`` (bounded walk, no link following) and the ``model.safetensors``
weights file (a regular file) must be free of symbolic links and reparse points (``os.lstat``:
``S_ISLNK`` or ``FILE_ATTRIBUTE_REPARSE_POINT``). Only metadata calls are made; a missing component refuses too.
Any failure is ``model_root_refused``. The same policy as ``shared_index_checkpoint._secure`` applies to the roots
inside the producer; this module does not weaken it and exposes ``_drive_type`` so the drive class is testable.

Preflight (v2 finding 2). The checks the producer makes only under its lock that need no live access run here
before identity derivation and backend load, so a mismatched request never loads a model: the start prefix cursor
must equal the source cursor and its offset lie within the plan's prefix budget; ``source.max_bytes`` and
``max_rows`` must lie within the snapshot bounds; the policy, roster and public policy bytes must parse under the
producer's own ``shared_capture_adapter`` parsers (``strict_pins`` then ``public_status_policy``) with the pinned
digests; and the checkpoint root and private state root must both be existing directories that are distinct and
not nested (case-folded ``commonpath`` plus ``os.path.samefile`` over the ancestors). Any failure is
``preflight_refused``. Impossible before live access and therefore left to the producer's locked validation:
the private-state lock and its secure-path checks on the roots, the prior-pointer CAS, the start prefix's
continuity against the private state, the anchor verification against the real bridge log, the snapshot read and
the pointer publication. Preflight is a prefilter; the producer still validates everything itself.

Order: switch (``enabled`` must be the bool ``True``; ``False`` returns ``{"status": "OFF"}`` inspecting nothing),
plan bytes type, byte budget and pin (nothing parsed yet), closed plan shape, request object types including the
trusted root argument (``request_refused``), plan pins against the config digests and bytes (``pins_refused``),
preflight (``preflight_refused``), model root binding (``model_root_refused``), encoder identity derivation and
weights pin (``encoder_refused``, before the backend loads), backend load, producer. Every refusal is
``BuildChildRefused`` with a fixed token: this module's ``REASONS`` or one of the producer's own fixed tokens
passed through; an unknown producer outcome is ``publication_state_unknown``. Refusals are raised outside every
exception handler and never carry a path, digest, plan field or backend message. The report is the producer's
report plus ``encoder_identity_source`` and the verified ``plan_sha256``; it never carries the weights digest, the
model directory, the trusted root or any prefix digest.

Limits (stated, not solved here): the plan pin, the trusted root and the model directory are caller trust; the
child cannot prove the model files are the genuine revision beyond the pinned weights digest, cannot authenticate
the publisher or the source, and does nothing live: no scheduler, launcher, bundle, deployment or installed-runtime
wiring exists for it. The fixed-drive test is Windows only (``os.name == "nt"``), as in the checkpoint module.
Nothing here is operational.
"""
import ctypes
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat

from sandbox.mind.step2 import encode as encode_mod
from sandbox.mind.step2 import shared_bridge_snapshot as snapshot
from sandbox.mind.step2 import shared_capture_adapter as adapter
from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2 import shared_prefix_public_producer as producer
from sandbox.mind.step2 import shared_producer as old
from sandbox.mind.step2 import shared_public_producer as pub
from waggledance.core.bridge_log_reader import BridgeCursor, BridgePrefixPin, MAX_PREFIX_BYTES

__all__ = ["BuildChildRefused", "build_prefix_generation", "PLAN_SCHEMA", "PLAN_LIMIT", "PLAN_KEYS", "ENCODER_KEYS",
           "PIN_KEYS", "ENCODER_MODEL", "ENCODER_DIM", "IDENTITY_SOURCE", "REASONS", "PRODUCER_REASONS",
           "WEIGHTS_FILE", "MODEL_TREE_LIMIT"]

PLAN_SCHEMA = "wd.q4-prefix-build-plan.v1"
PLAN_LIMIT = 1 << 16
PLAN_KEYS = frozenset({"schema", "generation", "expected_pointer_sha256", "max_prefix_bytes", "timeout", "encoder",
                       "pins"})
ENCODER_KEYS = frozenset({"model", "revision", "model_dir", "weights_sha256", "dim"})
PIN_KEYS = frozenset({"anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"})
ENCODER_MODEL = encode_mod.MODEL
ENCODER_DIM = encode_mod.DIM
IDENTITY_SOURCE = "encode.model_identity"
WEIGHTS_FILE = "model.safetensors"  # the file encode.model_identity hashes; bound before it is read
MODEL_TREE_LIMIT = 4096  # entries below model_dir the bounded reparse walk will inspect before refusing
REASONS = frozenset({"disabled", "plan_refused", "request_refused", "pins_refused", "preflight_refused",
                     "model_root_refused", "encoder_refused", "publication_state_unknown"})
PRODUCER_REASONS = frozenset({"request_invalid", "pins_mismatch", "private_state_refused", "prepare_refused",
                              "empty_genesis", "build_refused", "publication_state_unknown"})
_REPORT_KEYS = frozenset({"status", "pointer_sha256", "generation", "publisher_authentication", "source_authenticity",
                          "freshness"})
_PUBLISHED = frozenset({"PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED", "EMPTY_NO_CHANGE"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE = re.compile(r"[A-Za-z]:\Z")
_DRIVE_FIXED = 3
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class BuildChildRefused(ValueError):
    """Fixed token only: this module's ``REASONS`` or a producer token; never a path, digest or backend message."""


def _hex64(value):
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _pairs_without_duplicates(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("duplicate_key")
        document[key] = value
    return document


def _reject_constant(_value):
    raise ValueError("nonstandard_json_constant")


def _parse(plan_bytes):
    try:
        document = json.loads(plan_bytes.decode("utf-8", "strict"), object_pairs_hook=_pairs_without_duplicates,
                              parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        document = None
        failed = True
    else:
        failed = False
    if failed:
        raise BuildChildRefused("plan_refused")
    return document


def _number(value, low, high):
    return (type(value) in (int, float) and math.isfinite(value) and low < value <= high)


def _plan_shape(plan):
    if type(plan) is not dict or frozenset(plan) != PLAN_KEYS or plan["schema"] != PLAN_SCHEMA:
        return False
    generation, pointer, budget, timeout = (plan["generation"], plan["expected_pointer_sha256"],
                                            plan["max_prefix_bytes"], plan["timeout"])
    if type(generation) is not str or checkpoint.NAME.fullmatch(generation) is None:
        return False
    if pointer is not None and not _hex64(pointer):
        return False
    if type(budget) is not int or not 0 <= budget <= MAX_PREFIX_BYTES or not _number(timeout, 0, 30):
        return False
    encoder = plan["encoder"]
    if type(encoder) is not dict or frozenset(encoder) != ENCODER_KEYS:
        return False
    if (encoder["model"] != ENCODER_MODEL or type(encoder["revision"]) is not str
            or _HEX40.fullmatch(encoder["revision"]) is None or not _hex64(encoder["weights_sha256"])
            or type(encoder["dim"]) is not int or encoder["dim"] != ENCODER_DIM
            or type(encoder["model_dir"]) is not str or not encoder["model_dir"]
            or not Path(encoder["model_dir"]).is_absolute()):
        return False
    pins = plan["pins"]
    return type(pins) is dict and frozenset(pins) == PIN_KEYS and all(map(_hex64, pins.values()))


def _absolute_root(value):
    return isinstance(value, (str, Path)) and bool(str(value)) and Path(value).is_absolute()


def _request_shape(config, start_prefix, root, private_state_root, trusted_model_root):
    return (type(config) is old.PublicSourceConfig and type(config.source) is old.SourceConfig
            and type(start_prefix) is BridgePrefixPin and type(start_prefix.cursor) is BridgeCursor
            and _hex64(start_prefix.sha256) and _absolute_root(root) and _absolute_root(private_state_root)
            and _absolute_root(trusted_model_root))


def _pins_bound(plan_pins, config):
    source = config.source
    configured = (("anchor_sha256", source.anchor_sha256, source.anchor_bytes),
                  ("policy_sha256", source.policy_sha256, source.policy_bytes),
                  ("roster_sha256", source.roster_sha256, source.roster_bytes),
                  ("public_policy_sha256", config.public_policy_sha256, config.public_policy_bytes))
    for key, digest, raw in configured:
        expected = plan_pins[key]
        if (not _hex64(digest) or type(raw) is not bytes or not hmac.compare_digest(expected, digest)
                or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected)):
            return False
    return True


# ---------------------------------------------------------------------------------------------------------------
# preflight: the producer's pure validation, run before identity derivation and backend load
# ---------------------------------------------------------------------------------------------------------------


def _roots_distinct(root, private_state_root):
    """Both existing directories, neither equal to nor nested in the other; a failed probe counts as a conflict."""
    try:
        left, right = Path(root), Path(private_state_root)
        if not left.is_dir() or not right.is_dir():
            return False
        folded = (os.path.normcase(os.path.abspath(str(left))), os.path.normcase(os.path.abspath(str(right))))
        try:
            if os.path.commonpath(folded) in folded:
                return False
        except ValueError:  # distinct drives share no path
            pass
        for first, second in ((left, right), (right, left)):
            if any(os.path.samefile(ancestor, second) for ancestor in (first, *first.parents)):
                return False
    except (OSError, ValueError, TypeError):
        return False
    return True


def _preflight_holds(plan, config, start_prefix, root, private_state_root):
    source, cursor = config.source, start_prefix.cursor
    if (cursor != source.cursor or type(cursor.offset) is not int
            or not 0 <= cursor.offset <= plan["max_prefix_bytes"]):
        return False
    if (type(source.max_bytes) is not int or not 1 <= source.max_bytes <= snapshot.MAX_BYTES
            or type(source.max_rows) is not int or not 1 <= source.max_rows <= snapshot.MAX_ROWS):
        return False
    try:
        tasks, _ = adapter.strict_pins(source.policy_bytes, plan["pins"]["policy_sha256"], source.roster_bytes,
                                       plan["pins"]["roster_sha256"])
        adapter.public_status_policy(config.public_policy_bytes, plan["pins"]["public_policy_sha256"], tasks)
    except Exception:  # noqa: BLE001 - the parsers refuse with ValueError or their own refusal types; all one token
        return False
    return _roots_distinct(root, private_state_root)


# ---------------------------------------------------------------------------------------------------------------
# model root binding: metadata only, before encode.model_identity reads the weights
# ---------------------------------------------------------------------------------------------------------------


def _drive_type(anchor):
    """``GetDriveTypeW`` of a drive anchor such as ``C:\\``; separate so the drive class is testable."""
    probe = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    probe.argtypes = (ctypes.c_wchar_p,)
    probe.restype = ctypes.c_uint
    return probe(anchor)


def _plain_local(text):
    """``Path`` of a drive-letter path with no UNC/device prefix and no dot components, else None."""
    if type(text) is not str or not text or "\x00" in text:
        return None
    drive, rest = os.path.splitdrive(text)
    if _DRIVE.fullmatch(drive) is None or not rest or rest[0] not in "\\/":
        return None
    # raw string components: pathlib collapses a single "." and would hide it; a bare drive root is not a pinned root
    parts = [part for part in re.split(r"[\\/]", rest) if part]
    if not parts or any(part in (".", "..") or part.endswith((" ", ".")) for part in parts):
        return None
    path = Path(text)
    return path if path.is_absolute() else None


def _no_reparse(path, *, want_dir):
    """lstat metadata only: the entry exists, is a plain directory or regular file, and is no link or reparse point."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        return False
    return stat.S_ISDIR(info.st_mode) if want_dir else stat.S_ISREG(info.st_mode)


def _chain_plain(path):
    """Every component from the drive root down to ``path`` is a plain directory."""
    for current in (path, *path.parents):
        if current == current.parent:
            break
        if not _no_reparse(current, want_dir=True):
            return False
    return True


def _tree_plain(model_dir):
    """Bounded no-follow walk below ``model_dir``: no link or reparse point anywhere, and the weights file is plain."""
    seen = 0
    pending = [model_dir]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                seen += 1
                if seen > MODEL_TREE_LIMIT:
                    return False
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & _REPARSE:
                    return False
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return _no_reparse(model_dir / WEIGHTS_FILE, want_dir=False)


def _model_root_bound(model_dir_text, trusted_model_root):
    """True when ``model_dir`` is bound to the trusted fixed-local non-reparse root; metadata calls only."""
    try:
        if os.name != "nt":
            return False
        trusted = _plain_local(str(trusted_model_root))
        model_dir = _plain_local(model_dir_text)
        if trusted is None or model_dir is None or model_dir.anchor.lower() != trusted.anchor.lower():
            return False
        if _drive_type(trusted.anchor) != _DRIVE_FIXED:
            return False
        folded = (os.path.normcase(str(trusted)), os.path.normcase(str(model_dir)))
        if os.path.commonpath(folded) != folded[0]:
            return False
        return _chain_plain(trusted) and _chain_plain(model_dir) and _tree_plain(model_dir)
    except Exception:  # noqa: BLE001 - a failed probe (missing path, access error, drive probe) is one refusal
        return False


def _identity(encoder):
    """The three-key encoder identity derived from the pinned model directory; None when it cannot be bound."""
    try:
        derived = encode_mod.model_identity(model_dir=encoder["model_dir"], revision=encoder["revision"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if (type(derived) is not dict or derived.get("model") != ENCODER_MODEL
            or not _hex64(derived.get("weights_sha256"))
            or not hmac.compare_digest(derived["weights_sha256"], encoder["weights_sha256"])):
        return None
    return {"model": ENCODER_MODEL, "revision": encoder["revision"], "weights_sha256": encoder["weights_sha256"]}


def _encoder(encoder, identity):
    """The fixed E5 passage encoder over the pinned directory; None on any load failure."""
    from sandbox.mind.step2 import shared_index_builder as builder
    try:
        if builder.encoder_identity(identity) != identity:
            return None
        loaded = encode_mod.E5(model_dir=encoder["model_dir"], revision=encoder["revision"])
        return builder.e5_passage_encoder(loaded.model)
    except Exception:  # noqa: BLE001 - any backend failure is one fixed refusal without its message
        return None


def _publish(request):
    """``(report, token)``: the producer's validated report, or the fixed token to raise."""
    try:
        report = producer.publish_public_prefix(enabled=True, request=request)
    except producer.PrefixProducerRefused as refused:
        token = refused.args[0] if len(refused.args) == 1 and refused.args[0] in PRODUCER_REASONS else None
        return None, token or "publication_state_unknown"
    except Exception:  # noqa: BLE001 - the producer sanitises its own path; anything else is unknown state
        return None, "publication_state_unknown"
    if (type(report) is not dict or frozenset(report) != _REPORT_KEYS or report["status"] not in _PUBLISHED
            or not _hex64(report["pointer_sha256"])):
        return None, "publication_state_unknown"
    return report, None


def build_prefix_generation(*, enabled=False, plan_bytes=None, expected_plan_sha256=None, config=None,
                            start_prefix=None, root=None, private_state_root=None, trusted_model_root=None):
    """One prefix-bound publication through the fixed encoder path; see the module doc for the order and limits."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        raise BuildChildRefused("disabled")
    if type(plan_bytes) is not bytes or len(plan_bytes) > PLAN_LIMIT or not _hex64(expected_plan_sha256):
        raise BuildChildRefused("plan_refused")
    if not hmac.compare_digest(hashlib.sha256(plan_bytes).hexdigest(), expected_plan_sha256):
        raise BuildChildRefused("plan_refused")
    plan = _parse(plan_bytes)
    if not _plan_shape(plan):
        raise BuildChildRefused("plan_refused")
    if not _request_shape(config, start_prefix, root, private_state_root, trusted_model_root):
        raise BuildChildRefused("request_refused")
    if not _pins_bound(plan["pins"], config):
        raise BuildChildRefused("pins_refused")
    if not _preflight_holds(plan, config, start_prefix, root, private_state_root):
        raise BuildChildRefused("preflight_refused")
    if not _model_root_bound(plan["encoder"]["model_dir"], trusted_model_root):
        raise BuildChildRefused("model_root_refused")
    identity = _identity(plan["encoder"])
    if identity is None:
        raise BuildChildRefused("encoder_refused")
    encode = _encoder(plan["encoder"], identity)
    if encode is None:
        raise BuildChildRefused("encoder_refused")
    request = producer.PrefixPublicRequest(
        config=config, pins=pub.CallerPins(**{key: plan["pins"][key] for key in sorted(PIN_KEYS)}),
        start_prefix=start_prefix, max_prefix_bytes=plan["max_prefix_bytes"], root=root,
        generation=plan["generation"], expected_pointer_sha256=plan["expected_pointer_sha256"], encode=encode,
        identity=identity, dim=ENCODER_DIM, timeout=plan["timeout"], private_state_root=private_state_root)
    report, token = _publish(request)
    if token is not None:
        raise BuildChildRefused(token)
    return {**report, "encoder_identity_source": IDENTITY_SOURCE, "plan_sha256": expected_plan_sha256}
