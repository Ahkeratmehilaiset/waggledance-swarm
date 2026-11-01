"""Default-off fixed prefix build child: a closed caller-pinned plan drives the strict-prefix producer with the
fixed E5 encoder path; no caller-supplied encoder callback, identity or dimension.

``build_prefix_generation`` takes the plan bytes with their caller pin (schema ``wd.q4-prefix-build-plan.v1``:
generation name, prior pointer digest or null, prefix budget, lock timeout, the encoder record {model, revision,
model_dir, weights_sha256, dim, assets} and the four source pins), plus the caller-owned ``PublicSourceConfig``, the
``BridgePrefixPin`` the private state continues from, the checkpoint root, the private state root and the
explicitly caller-pinned ``trusted_model_root``. The encoder is never a parameter: the child derives the identity
with ``encode.model_identity`` over the pinned model directory, requires its ``weights_sha256`` to equal the plan
pin, loads the model only through the fixed ``encode.E5`` path (offline, cpu, local files only) and wraps it with
the builder's ``e5_passage_encoder`` (``"passage: "`` prefix, 2000-character truncation, batch 32, normalised
float32, dim 384). The resulting ``PrefixPublicRequest`` goes to
``shared_prefix_public_producer.publish_public_prefix`` unchanged, so prior-pointer and start-prefix continuity, the
private state lock and the pointer CAS stay the producer's; this module adds no publication logic.

The optional, separately pinned ``wd.q4-prefix-build-plan.v2`` adds exactly one field, ``max_windows``. It selects
the producer's bounded catch-up path; incomplete catch-up refuses without publication. Its coverage is only to the
final observed snapshot, not proof of currentness or complete history, and source refusals are not aggregated.
The v1 plan and its single-window route remain unchanged; the caller cannot change the bound without changing the
plan bytes and their independently supplied SHA256.

The separately pinned ``wd.q4-prefix-build-plan.v3`` adds ``max_windows`` and literal ``fenced: true`` and requests
the producer's opt-in fenced API. A missing compatible producer API refuses before model access; there is no fallback.
The API shape check is not proof that the producer implements the promised semantics: caller trust and independent
producer-code pinning remain necessary. Version 1 and 2 do not opt in. V2 deliberately becomes stricter: v2 and v3
both refuse before model access if ``max_windows * (2 * max_prefix_bytes + source.max_bytes)`` exceeds 4 GiB.
This is a static bound on prefix-hash plus window reads, not a wall-clock deadline or a bound on all I/O.

Model root binding (v2 finding 1). ``Path.is_absolute`` alone accepts UNC shares and paths through symbolic links
or junctions, so an unbound ``model_dir`` would let ``model_identity`` read remote or escaped data before the
backend's offline controls apply. The plan's ``model_dir`` is therefore bound to ``trusted_model_root`` before any
read: both must be drive-letter paths (no UNC, no ``\\\\?\\`` device form), with no ``.`` or ``..`` component read
from the raw string, not a bare drive root, the drive must be a fixed local volume (``GetDriveTypeW ==
DRIVE_FIXED``), ``model_dir`` must be the root or below it (``os.path.commonpath`` on case-folded strings), and
every path component from the drive root down to ``model_dir``, every entry below ``model_dir`` (bounded walk, no
link following) and the ``model.safetensors`` weights file (a regular file) must be free of symbolic links and
reparse points (``os.lstat``: ``S_ISLNK`` or ``FILE_ATTRIBUTE_REPARSE_POINT``). Every regular file in the tree must
have exactly one hard link (``st_nlink == 1``; a link count the platform cannot report refuses, fail closed), so a
file aliased from outside the tree cannot pass as a plain file (v3 supplement). Only metadata calls are made; a
missing component refuses too. Any failure is ``model_root_refused``. The same policy as
``shared_index_checkpoint._secure`` applies to the roots inside the producer; this module does not weaken it and
exposes ``_drive_type`` and ``_link_count`` so the drive class and the link count are testable.

Roots versus model root (v3 finding 2). The checkpoint root and the private state root must be disjoint from the
trusted model root and from ``model_dir`` in both directions: equal, nested either way, or the same directory under
another name (junction alias, ``os.path.samefile`` over every ancestor) refuses ``model_root_refused`` before the
backend loads and before the producer writes anything.

Asset manifest (v3 supplement, RCO findings P2). The weights digest alone does not authenticate a model: the
tokenizer, sentencepiece, config, modules, pooling and sentence-bert files under ``model_dir`` also shape the
vectors. The encoder record therefore carries ``assets``, a caller-pinned manifest (relative POSIX path -> sha256)
of EVERY regular file under ``model_dir``, the weights file included; after the binding and before identity
derivation the child lists the plain tree, requires the file set to equal the manifest exactly (an unlisted extra
file or a missing listed file refuses) and streams every file through sha256 against its pin
(``asset_refused``). The per-file byte budget binds the bytes actually read, not only the size seen before the
open: a file that grew in between is refused after at most ``ASSET_BYTES_LIMIT + 1`` bytes, never read to EOF. The
identity reread (v5 finding) is bounded the same way: ``encode.model_identity`` is called with
``max_bytes=ASSET_BYTES_LIMIT`` (an optional keyword; ``None`` keeps the historical unbounded read for the other
consumers), so weights grown or swapped between the manifest pass and the identity pass are refused
(``encoder_refused``) without an unbounded read. The child never self-derives a pin: every digest it compares
against comes from the caller's plan. What remains open and is reported, not claimed: the verify-then-use window
(the backend re-reads the files by path after they were hashed, and that backend reread through safetensors is not
bounded by this module; only an immutable root closes it), the genuineness of the pinned revision (the caller's
manifest is trust, not proof) and the backend running in this process.

Fixed-refusal boundary of the encoder step (v5 supplement). The builder module ``shared_index_builder`` imports
numpy and the FAISS layer at module level; the child imports it lazily inside the encoder step's exception boundary,
so a missing or broken builder, numpy or FAISS import is the fixed ``encoder_refused`` with no chained cause and no
traceback leaking a message. The builder import, the ``encoder_identity`` check, the load and the passage adapter
build all run inside ONE environment restoration scope (v6 finding: the import and the identity check used to run
outside it), and the bounded ``model_identity`` reread runs inside its own; foreign code at any of these points that
alters the environment, then succeeds or raises, leaves the process environment exactly as it was. Stated separately and not
closed here: this module's own module-level import chain (``encode`` imports numpy at module level, the producer
modules follow) means a broken numpy fails at import time, before the default-off entrypoint is ever reached; that
failure surfaces to the importing caller, not as a fixed refusal.

Backend environment (v3 finding 1, v4 inspection). ``encode.E5`` writes the offline cache variables (``CACHE_ENV``,
``HF_HUB_OFFLINE``, ``TRANSFORMERS_OFFLINE``) into the process environment and does not restore them. The child
snapshots the whole environment before the load and restores it exactly afterwards, on success and on failure
(added names removed, overwritten values put back), and wraps every later inference call of the returned encoder
the same way, since a lazy ``model.encode`` may read or alter the environment long after the load. This is scoped
restoration, not OS isolation and not a sandbox: it covers ``os.environ`` only (a direct ``putenv`` or WinAPI
write, or a concurrent writer in another thread, is neither seen nor undone); the backend and its native libraries
still load in this process, the imported modules stay imported and keep the offline settings they read at import
time, and nothing here constrains what the loaded code can do. Reported as ``backend_isolation``.

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
plan bytes type, byte budget and pin (nothing parsed yet), closed plan shape including the manifest keys, request
object types including the trusted root argument (``request_refused``), plan pins against the config digests and
bytes (``pins_refused``), preflight (``preflight_refused``), model root binding with the roots-disjoint relation
(``model_root_refused``), asset manifest (``asset_refused``, the first reads), encoder identity derivation and
weights pin (``encoder_refused``, before the backend loads), backend load under environment restoration, producer.
Every refusal is ``BuildChildRefused`` with a fixed token: this module's ``REASONS`` or one of the producer's own
fixed tokens passed through; an unknown producer outcome is ``publication_state_unknown``. Refusals are raised
outside every exception handler and never carry a path, digest, plan field or backend message. The report is the
producer's report plus ``encoder_identity_source``, the verified ``plan_sha256`` and the two fixed limit fields
``backend_isolation`` and ``model_assets``; it never carries a weights or asset digest, the model directory, the
trusted root or any prefix digest.

Limits (stated, not solved here): the plan pin, the trusted root, the model directory and the asset manifest are
caller trust; the child cannot prove the model files are the genuine revision beyond the pinned digests, cannot
authenticate the publisher or the source, and does nothing live: no scheduler, launcher, bundle, deployment or
installed-runtime wiring exists for it. The fixed-drive and link-count checks are Windows only (``os.name ==
"nt"``), as in the checkpoint module. Nothing here is operational.
"""
import contextlib
import ctypes
from dataclasses import fields
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
           "WEIGHTS_FILE", "MODEL_TREE_LIMIT", "ASSET_LIMIT", "ASSET_BYTES_LIMIT", "BACKEND_ISOLATION", "MODEL_ASSETS"]

PLAN_SCHEMA = "wd.q4-prefix-build-plan.v1"
PLAN_CATCHUP_SCHEMA = "wd.q4-prefix-build-plan.v2"
PLAN_FENCED_CATCHUP_SCHEMA = "wd.q4-prefix-build-plan.v3"
CATCHUP_READ_BUDGET = 4 * 1024 * 1024 * 1024
PLAN_LIMIT = 1 << 16
PLAN_KEYS = frozenset({"schema", "generation", "expected_pointer_sha256", "max_prefix_bytes", "timeout", "encoder",
                       "pins"})
ENCODER_KEYS = frozenset({"model", "revision", "model_dir", "weights_sha256", "dim", "assets"})
PIN_KEYS = frozenset({"anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"})
ENCODER_MODEL = encode_mod.MODEL
ENCODER_DIM = encode_mod.DIM
IDENTITY_SOURCE = "encode.model_identity"
WEIGHTS_FILE = "model.safetensors"  # the file encode.model_identity hashes; bound and listed before it is read
MODEL_TREE_LIMIT = 4096  # entries below model_dir the bounded reparse walk will inspect before refusing
ASSET_LIMIT = 64  # manifest entries (regular files under model_dir); the e5-small tree has ten
ASSET_BYTES_LIMIT = 1 << 30  # bytes per asset the child will stream through sha256
BACKEND_ISOLATION = "in_process_environment_restored_no_os_isolation"
MODEL_ASSETS = "caller_manifest_verified_before_load_reread_by_backend"
REASONS = frozenset({"disabled", "plan_refused", "request_refused", "pins_refused", "preflight_refused",
                     "model_root_refused", "asset_refused", "encoder_refused", "publication_state_unknown",
                     "catchup_budget_refused", "fenced_api_unavailable"})
PRODUCER_REASONS = frozenset({"request_invalid", "pins_mismatch", "private_state_refused", "prepare_refused",
                              "empty_genesis", "build_refused", "publication_state_unknown", "catchup_incomplete",
                              "empty_catchup_unsupported"})
_REPORT_KEYS = frozenset({"status", "pointer_sha256", "generation", "publisher_authentication", "source_authenticity",
                          "freshness"})
_CATCHUP_REPORT_KEYS = _REPORT_KEYS | {"coverage", "source_refusals"}
_PUBLISHED = frozenset({"PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED", "EMPTY_NO_CHANGE"})
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE = re.compile(r"[A-Za-z]:\Z")
_DRIVE_FIXED = 3
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CHUNK = 1 << 20


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


def _asset_key(key):
    """A relative POSIX path below model_dir: no drive, no leading slash, no backslash, no empty or dot component."""
    if type(key) is not str or not key or len(key) > 255 or "\\" in key or "\x00" in key or key.startswith("/"):
        return False
    if os.path.splitdrive(key)[0]:
        return False
    return all(part and part not in (".", "..") and not part.endswith((" ", ".")) for part in key.split("/"))


def _assets_shape(assets):
    if type(assets) is not dict or not 1 <= len(assets) <= ASSET_LIMIT or WEIGHTS_FILE not in assets:
        return False
    if not all(map(_asset_key, assets)) or len({key.lower() for key in assets}) != len(assets):
        return False  # case-colliding names cannot both exist on the fixed volume
    return all(map(_hex64, assets.values()))


def _plan_shape(plan):
    if type(plan) is not dict:
        return False
    schema = plan.get("schema")
    if schema == PLAN_SCHEMA:
        if frozenset(plan) != PLAN_KEYS:
            return False
    elif schema in (PLAN_CATCHUP_SCHEMA, PLAN_FENCED_CATCHUP_SCHEMA):
        extra = {"max_windows"} if schema == PLAN_CATCHUP_SCHEMA else {"max_windows", "fenced"}
        if (frozenset(plan) != PLAN_KEYS | extra or type(plan["max_windows"]) is not int
                or not 1 <= plan["max_windows"] <= snapshot.CATCHUP_MAX_WINDOWS):
            return False
        if schema == PLAN_FENCED_CATCHUP_SCHEMA and plan["fenced"] is not True:
            return False
    else:
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
            or not Path(encoder["model_dir"]).is_absolute() or not _assets_shape(encoder["assets"])):
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


def _catchup_budget_valid(max_windows, max_prefix_bytes, max_bytes):
    """A static worst-case prefix+delta byte bound; not a wall-clock or total-I/O bound."""
    return (type(max_windows) is int and 1 <= max_windows <= snapshot.CATCHUP_MAX_WINDOWS
            and type(max_prefix_bytes) is int and 0 <= max_prefix_bytes <= MAX_PREFIX_BYTES
            and type(max_bytes) is int and 1 <= max_bytes <= snapshot.MAX_BYTES
            and max_windows * (2 * max_prefix_bytes + max_bytes) <= CATCHUP_READ_BUDGET)


def _fenced_api_available():
    """Only check the opt-in API shape; code provenance and semantics are caller-owned."""
    try:
        if not callable(producer.publish_public_catchup):
            return False
        fence = next((item for item in fields(producer.PrefixPublicRequest) if item.name == "fenced"), None)
        return fence is not None and fence.init and fence.type is bool and fence.default is False
    except (TypeError, AttributeError):
        return False


# ---------------------------------------------------------------------------------------------------------------
# preflight: the producer's pure validation, run before identity derivation and backend load
# ---------------------------------------------------------------------------------------------------------------


def _disjoint_dirs(left, right):
    """Both existing directories, neither equal to nor nested in the other, not the same directory under two
    names (``samefile`` over every ancestor); a failed probe counts as an overlap."""
    try:
        left, right = Path(left), Path(right)
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
    return _disjoint_dirs(root, private_state_root)


# ---------------------------------------------------------------------------------------------------------------
# model root binding: metadata only, before encode.model_identity reads the weights
# ---------------------------------------------------------------------------------------------------------------


def _drive_type(anchor):
    """``GetDriveTypeW`` of a drive anchor such as ``C:\\``; separate so the drive class is testable."""
    probe = ctypes.WinDLL("kernel32", use_last_error=True).GetDriveTypeW
    probe.argtypes = (ctypes.c_wchar_p,)
    probe.restype = ctypes.c_uint
    return probe(anchor)


def _link_count(path):
    """Hard-link count of a path without following links; 0 where the platform cannot report it (refuses)."""
    return os.lstat(path).st_nlink


def _asset_size(path):
    """Size of an asset before it is opened; the read loop re-enforces the budget on the bytes actually read."""
    return os.lstat(path).st_size


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
    """lstat metadata only: the entry exists, is a plain directory or a single-link regular file, no reparse point."""
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        return False
    if want_dir:
        return stat.S_ISDIR(info.st_mode)
    return stat.S_ISREG(info.st_mode) and _link_count(path) == 1


def _chain_plain(path):
    """Every component from the drive root down to ``path`` is a plain directory."""
    for current in (path, *path.parents):
        if current == current.parent:
            break
        if not _no_reparse(current, want_dir=True):
            return False
    return True


def _tree_files(model_dir):
    """Relative POSIX paths of the regular files below ``model_dir`` after a bounded no-follow walk that refuses
    any link, reparse point or multi-link file; None when the tree is not plain or the weights file is not."""
    seen = 0
    files = []
    pending = [model_dir]
    while pending:
        folder = pending.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                seen += 1
                if seen > MODEL_TREE_LIMIT:
                    return None
                path = Path(entry.path)
                is_dir = entry.is_dir(follow_symlinks=False)
                if not _no_reparse(path, want_dir=is_dir):
                    return None
                if is_dir:
                    pending.append(path)
                else:
                    files.append(path.relative_to(model_dir).as_posix())
    if WEIGHTS_FILE not in files:
        return None
    return files


def _model_root_bound(model_dir_text, trusted_model_root, root, private_state_root):
    """The plain file list of ``model_dir`` when it is bound to the trusted fixed-local non-reparse root and the
    checkpoint and private roots are disjoint from both; None otherwise. Metadata calls only."""
    try:
        if os.name != "nt":
            return None
        trusted = _plain_local(str(trusted_model_root))
        model_dir = _plain_local(model_dir_text)
        if trusted is None or model_dir is None or model_dir.anchor.lower() != trusted.anchor.lower():
            return None
        if _drive_type(trusted.anchor) != _DRIVE_FIXED:
            return None
        folded = (os.path.normcase(str(trusted)), os.path.normcase(str(model_dir)))
        if os.path.commonpath(folded) != folded[0]:
            return None
        if not (_chain_plain(trusted) and _chain_plain(model_dir)):
            return None
        files = _tree_files(model_dir)
        if files is None:
            return None
        for shared in (root, private_state_root):
            if not (_disjoint_dirs(shared, trusted) and _disjoint_dirs(shared, model_dir)):
                return None
        return files
    except Exception:  # noqa: BLE001 - a failed probe (missing path, access error, drive probe) is one refusal
        return None


# ---------------------------------------------------------------------------------------------------------------
# asset manifest: the first reads, every regular file against the caller's pin
# ---------------------------------------------------------------------------------------------------------------


def _assets_bound(model_dir_text, assets, files):
    """Every regular file under model_dir is listed, every listed file exists, and each hashes to its pin."""
    try:
        if set(files) != set(assets) or len(files) != len(assets):
            return False
        model_dir = Path(model_dir_text)
        for relative, expected in assets.items():
            path = model_dir.joinpath(*relative.split("/"))
            if _asset_size(path) > ASSET_BYTES_LIMIT:
                return False
            digest = hashlib.sha256()
            consumed = 0
            with open(path, "rb") as handle:
                while True:
                    # the budget binds the bytes actually read, not the size seen before the open: a file that
                    # grew in between is refused after at most limit + 1 bytes, never read to EOF
                    chunk = handle.read(min(_CHUNK, ASSET_BYTES_LIMIT + 1 - consumed))
                    if not chunk:
                        break
                    consumed += len(chunk)
                    if consumed > ASSET_BYTES_LIMIT:
                        return False
                    digest.update(chunk)
            if not hmac.compare_digest(digest.hexdigest(), expected):
                return False
    except Exception:  # noqa: BLE001 - any read failure is one refusal without its message
        return False
    return True


@contextlib.contextmanager
def _restored_environment():
    """Restore the exact process environment afterwards: added names removed, overwritten values put back."""
    before = dict(os.environ)
    try:
        yield
    finally:
        environment = os.environ
        for key in [key for key in environment if key not in before]:
            del environment[key]
        for key, value in before.items():
            if environment.get(key) != value:
                environment[key] = value


def _identity(encoder):
    """The three-key encoder identity derived from the pinned model directory; None when it cannot be bound."""
    try:
        # the identity reread is bounded like the manifest pass: a file grown since then refuses, never read to EOF;
        # it runs inside the same environment restoration as every other step that touches the encoder code
        with _restored_environment():
            derived = encode_mod.model_identity(model_dir=encoder["model_dir"], revision=encoder["revision"],
                                                max_bytes=ASSET_BYTES_LIMIT)
    except Exception:
        return None
    if (type(derived) is not dict or derived.get("model") != ENCODER_MODEL
            or not _hex64(derived.get("weights_sha256"))
            or not hmac.compare_digest(derived["weights_sha256"], encoder["weights_sha256"])):
        return None
    return {"model": ENCODER_MODEL, "revision": encoder["revision"], "weights_sha256": encoder["weights_sha256"]}


def _encoder(encoder, identity):
    """The fixed E5 passage encoder over the pinned directory; None on any load or builder-import failure."""
    try:
        # one restoration scope around everything that runs foreign code here: the builder import (module-level
        # numpy and FAISS imports may read or alter the environment), the identity check, the load and the adapter
        with _restored_environment():
            # the builder transitively imports numpy and FAISS: a missing or broken layer is the same fixed refusal
            from sandbox.mind.step2 import shared_index_builder as builder
            if builder.encoder_identity(identity) != identity:
                return None
            loaded = encode_mod.E5(model_dir=encoder["model_dir"], revision=encoder["revision"])
            # building the adapter touches the loaded model lazily; it stays inside the same restoration scope
            inner = builder.e5_passage_encoder(loaded.model)
    except Exception:  # noqa: BLE001 - any backend failure is one fixed refusal without its message
        return None

    def encode(*args, **kwargs):
        # Lazy inference also needs the loader's offline/cache policy; restore caller state afterwards.
        with _restored_environment():
            for name in encode_mod.CACHE_ENV:
                os.environ[name] = os.devnull
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            return inner(*args, **kwargs)
    return encode


def _publish(request, *, max_windows=None):
    """``(report, token)``: the producer's validated report, or the fixed token to raise."""
    try:
        if max_windows is None:
            report = producer.publish_public_prefix(enabled=True, request=request)
        else:
            report = producer.publish_public_catchup(enabled=True, request=request, max_windows=max_windows)
    except producer.PrefixProducerRefused as refused:
        token = (refused.args[0] if len(refused.args) == 1 and type(refused.args[0]) is str
                 and refused.args[0] in PRODUCER_REASONS else None)
        return None, token or "publication_state_unknown"
    except Exception:  # noqa: BLE001 - the producer sanitises its own path; anything else is unknown state
        return None, "publication_state_unknown"
    expected_keys = _REPORT_KEYS if max_windows is None else _CATCHUP_REPORT_KEYS
    if (type(report) is not dict or frozenset(report) != expected_keys
            or type(report["status"]) is not str or report["status"] not in _PUBLISHED
            or not _hex64(report["pointer_sha256"])
            or any(type(report[key]) is not str or report[key] != "UNKNOWN"
                   for key in ("publisher_authentication", "source_authenticity", "freshness"))
            or (report["status"] in ("PUBLISHED_GENESIS", "PUBLISHED_HISTORY")
                and (type(report["generation"]) is not str or report["generation"] != request.generation))
            or (report["status"] in ("EMPTY_ADVANCED", "EMPTY_NO_CHANGE") and report["generation"] is not None)
            or (request.expected_pointer_sha256 is None and report["status"] != "PUBLISHED_GENESIS")
            or (request.expected_pointer_sha256 is not None and report["status"] == "PUBLISHED_GENESIS")
            or (report["status"] == "EMPTY_NO_CHANGE"
                and report["pointer_sha256"] != request.expected_pointer_sha256)
            or (report["status"] in ("EMPTY_ADVANCED", "PUBLISHED_HISTORY")
                and report["pointer_sha256"] == request.expected_pointer_sha256)
            or (max_windows is not None and
                (report["status"] not in ("PUBLISHED_GENESIS", "PUBLISHED_HISTORY")
                 or report["coverage"] != "caught_up_to_final_snapshot_not_history"
                 or report["source_refusals"] != "NOT_AGGREGATED"))):
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
    catchup = plan["schema"] in (PLAN_CATCHUP_SCHEMA, PLAN_FENCED_CATCHUP_SCHEMA)
    if catchup and not _catchup_budget_valid(plan["max_windows"], plan["max_prefix_bytes"], config.source.max_bytes):
        raise BuildChildRefused("catchup_budget_refused")
    if not _preflight_holds(plan, config, start_prefix, root, private_state_root):
        raise BuildChildRefused("preflight_refused")
    fenced = plan["schema"] == PLAN_FENCED_CATCHUP_SCHEMA
    if fenced and not _fenced_api_available():
        raise BuildChildRefused("fenced_api_unavailable")
    encoder = plan["encoder"]
    files = _model_root_bound(encoder["model_dir"], trusted_model_root, root, private_state_root)
    if files is None:
        raise BuildChildRefused("model_root_refused")
    if not _assets_bound(encoder["model_dir"], encoder["assets"], files):
        raise BuildChildRefused("asset_refused")
    identity = _identity(encoder)
    if identity is None:
        raise BuildChildRefused("encoder_refused")
    encode = _encoder(encoder, identity)
    if encode is None:
        raise BuildChildRefused("encoder_refused")
    request = producer.PrefixPublicRequest(
        config=config, pins=pub.CallerPins(**{key: plan["pins"][key] for key in sorted(PIN_KEYS)}),
        start_prefix=start_prefix, max_prefix_bytes=plan["max_prefix_bytes"], root=root,
        generation=plan["generation"], expected_pointer_sha256=plan["expected_pointer_sha256"], encode=encode,
        identity=identity, dim=ENCODER_DIM, timeout=plan["timeout"], private_state_root=private_state_root,
        **({"fenced": True} if fenced else {}))
    max_windows = plan["max_windows"] if catchup else None
    report, token = _publish(request, max_windows=max_windows)
    if token is not None:
        raise BuildChildRefused(token)
    return {**report, "encoder_identity_source": IDENTITY_SOURCE, "plan_sha256": expected_plan_sha256,
            "backend_isolation": BACKEND_ISOLATION, "model_assets": MODEL_ASSETS}
