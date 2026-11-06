"""Default-off capture of one caller-approved, *unverified* snapshot bundle.

The trusted caller must approve the exact digest independently, ensure every
captured byte is permitted for this output location (no secrets), protect the
source and output ACLs and prevent non-cooperating replacement. This helper
cannot prove any of those properties: base64 is not encryption. It delegates
coherent capture to the existing checkpoint-locking helper. A matching digest
is not a verified snapshot, source authenticity, freshness or authorization.

The caller supplies an existing, separate private output directory. Files are
never overwritten. The 0o600 creation mode does not establish private Windows
ACLs; the caller must verify them. Source hardlinks are not excluded and may
alias outside the source root; source isolation remains the caller's duty.
After output creation begins, an error can leave a ``.partial`` or final file:
the caller must reconcile the unknown state without an automatic retry or
deletion. There is no CLI, deployment or live-data access.
"""
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from tools import wd_q4_snapshot_candidate as candidate


_SHA = re.compile(r"[0-9a-f]{64}\Z")


class BundleRunnerRefused(RuntimeError):
    """Sanitized refusal before output creation begins."""


class BundleRunnerOutputStateUnknown(RuntimeError):
    """Output creation started; the caller must reconcile final and partial files."""


def _disjoint(left, right):
    left = os.path.normcase(os.path.abspath(str(left)))
    right = os.path.normcase(os.path.abspath(str(right)))
    try:
        if os.path.commonpath((left, right)) in (left, right):
            return False
    except ValueError:
        pass  # Different local drives cannot be nested.
    for first, second in ((Path(left), Path(right)), (Path(right), Path(left))):
        if any(os.path.samefile(parent, second) for parent in (first, *first.parents)):
            return False
    return True


def run(*, enabled=False, root=None, descriptors=None, output_dir=None, expected_bundle_sha256=None):
    """Write one exact externally pinned candidate; return only its descriptor."""
    if enabled is False:
        return {"status": "OFF"}
    result = None
    output_started = False
    try:
        if (enabled is not True or type(expected_bundle_sha256) is not str
                or _SHA.fullmatch(expected_bundle_sha256) is None):
            raise ValueError()
        source = checkpoint._secure(root)
        output = checkpoint._secure(output_dir)
        if not source.is_dir() or not output.is_dir() or not _disjoint(source, output):
            raise ValueError()
        captured = candidate.capture(root=source, descriptors=descriptors, enabled=True)
        raw = captured["bundle_bytes"]
        digest = hashlib.sha256(raw).hexdigest()
        if (captured["status"] != "CANDIDATE_UNVERIFIED" or type(raw) is not bytes
                or not hmac.compare_digest(digest, expected_bundle_sha256)
                or not hmac.compare_digest(captured["bundle_sha256"], expected_bundle_sha256)):
            raise ValueError()
        name = "q4-snapshot-" + digest + ".bundle"
        final = checkpoint._secure(output / name, missing_leaf=True)
        if os.path.lexists(final):
            raise ValueError()
        staging = checkpoint._secure(output / (name + ".partial-" + secrets.token_hex(16)), missing_leaf=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        output_started = True  # O_CREAT can change the filesystem even on an uncertain failure.
        fd = os.open(staging, flags, 0o600)
        try:
            stream_obj = os.fdopen(fd, "wb")
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        with stream_obj as stream:
            written = stream.write(raw)
            if written != len(raw):
                raise ValueError()
            stream.flush()
            os.fsync(stream.fileno())
        # On the supported Windows filesystem rename fails if final exists.
        os.rename(staging, final)
        result = {"status": "CANDIDATE_UNVERIFIED", "file": name, "sha256": digest, "bytes": len(raw)}
    except BaseException as exc:
        if not output_started and not isinstance(exc, Exception):
            raise
    if result is not None:
        return result
    root = descriptors = output_dir = expected_bundle_sha256 = source = output = captured = raw = None
    final = staging = name = digest = flags = written = stream = stream_obj = fd = None
    if output_started:
        raise BundleRunnerOutputStateUnknown("snapshot_bundle_output_state_unknown") from None
    raise BundleRunnerRefused("snapshot_bundle_runner_refused") from None
