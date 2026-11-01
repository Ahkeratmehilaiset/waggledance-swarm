"""Copy an explicitly pinned file inventory into a separate Q4 candidate.

This is not an installer, package manager, dependency resolver, or trust
anchor. It neither executes packages nor proves wheel provenance, dependency
closure, or that a runtime is fit for use. Inputs must be separately pinned by
the caller. Partial output is preserved on failure; nothing is overwritten,
deleted, activated, or switched into service. Same-user races remain possible.
Free space is sampled before each copied block; competing writes and filesystem
overhead can still cross the floor between the check and the write.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil

from sandbox.mind.step2 import runtime_builder, runtime_manifest


_FLOOR_BYTES = 3 * 1024**3
_MANIFEST_RESERVE = runtime_manifest.MAX_MANIFEST_BYTES


def _related(left, right):
    try:
        common = os.path.commonpath((os.fspath(left), os.fspath(right)))
    except ValueError:
        return False
    return os.path.normcase(common) in (os.path.normcase(os.fspath(left)), os.path.normcase(os.fspath(right)))


def _new_path(value):
    supplied = Path(value)
    if not supplied.is_absolute() or '..' in supplied.parts or not supplied.name:
        raise ValueError('output_invalid')
    # Validate the leaf using the same restrictive policy as the verifier.
    runtime_manifest._path_parts('assets/' + supplied.name)
    parent = runtime_manifest._check_chain(supplied.parent)
    output = parent / supplied.name
    if os.path.lexists(output):
        raise ValueError('output_exists')
    return output


def _input_hash(root, relative):
    path = root.joinpath(*runtime_manifest._path_parts(relative))
    runtime_manifest._check_chain(path)
    return runtime_builder._hash_file(path)


def _manifest_matches_pins(path, expected_sha256, generation, revision, files):
    """Bind the actual manifest bytes to the caller's inventory, not just the tree."""
    runtime_manifest._check_chain(path)
    runtime_manifest._check_no_named_streams(path)
    stream, initial = runtime_manifest._open_regular(path, runtime_manifest.MAX_MANIFEST_BYTES)
    with stream:
        raw = stream.read(runtime_manifest.MAX_MANIFEST_BYTES + 1)
        if (len(raw) != initial.st_size or len(raw) > runtime_manifest.MAX_MANIFEST_BYTES
                or not runtime_manifest._same_file(path, stream, initial)
                or hashlib.sha256(raw).hexdigest() != expected_sha256):
            raise ValueError('manifest_changed')
    try:
        document = json.loads(raw.decode('utf-8'), object_pairs_hook=runtime_manifest._unique_object,
                              parse_constant=lambda _: runtime_manifest._refuse('json_constant_invalid'))
        if (type(document) is not dict or set(document) != {'schema', 'generation', 'revision', 'files'}
                or document['schema'] != runtime_manifest.SCHEMA or document['generation'] != generation
                or document['revision'] != revision):
            raise ValueError('manifest_identity_mismatch')
        manifest_files, _ = runtime_manifest._validate_records(document['files'])
    except (UnicodeError, ValueError, RecursionError, TypeError):
        raise ValueError('manifest_invalid') from None
    if manifest_files != files:
        raise ValueError('manifest_pin_mismatch')


def build_candidate(source_root, candidate_root, manifest_path, *, pins, generation, revision, max_bytes):
    """Build an unactivated candidate and an external manifest from pinned files.

    ``pins`` maps POSIX relative runtime paths to exact ``size`` and ``sha256``.
    ``max_bytes`` bounds the sum of the selected file sizes, not total build
    peak. Disk free space must retain the separate 3 GiB floor plus room for
    the candidate bytes and maximum manifest. The caller remains responsible
    for pin provenance and a realistic end-to-end budget. Failure after output
    creation can leave partial files, which must not be treated as a receipt.
    """
    if (not isinstance(generation, str) or not runtime_manifest.IDENTITY.fullmatch(generation)
            or not isinstance(revision, str) or not runtime_manifest.IDENTITY.fullmatch(revision)):
        raise ValueError('identity_invalid')
    if type(max_bytes) is not int or max_bytes <= 0 or type(pins) is not dict:
        raise ValueError('inputs_invalid')
    source = runtime_manifest._check_chain(source_root)
    if not source.is_dir() or source == Path(source.anchor):
        raise ValueError('source_invalid')
    candidate, manifest = _new_path(candidate_root), _new_path(manifest_path)
    if _related(source, candidate) or _related(source, manifest) or _related(candidate, manifest):
        raise ValueError('output_overlap')
    records = []
    for path, pin in pins.items():
        if type(path) is not str or type(pin) is not dict or set(pin) != {'size', 'sha256'}:
            raise ValueError('pin_invalid')
        records.append({'path': path, 'size': pin['size'], 'sha256': pin['sha256']})
    files, directories = runtime_manifest._validate_records(records)
    total = sum(size for size, _ in files.values())
    if total > max_bytes:
        raise ValueError('budget_insufficient')
    if shutil.disk_usage(candidate.parent).free < _FLOOR_BYTES + total + _MANIFEST_RESERVE:
        raise ValueError('disk_floor')
    if os.path.splitdrive(str(candidate))[0].casefold() != os.path.splitdrive(str(manifest))[0].casefold():
        if shutil.disk_usage(manifest.parent).free < _FLOOR_BYTES + _MANIFEST_RESERVE:
            raise ValueError('disk_floor')

    # Validate every named input before making any output path.
    identities = {}
    for relative, (size, digest) in files.items():
        observed_size, observed_digest, identity = _input_hash(source, relative)
        if observed_size != size or observed_digest != digest:
            raise ValueError('input_pin_mismatch')
        identities[relative] = identity

    os.mkdir(candidate)
    for relative, (size, digest) in files.items():
        source_path = source.joinpath(*relative.split('/'))
        target = candidate.joinpath(*relative.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        runtime_manifest._check_chain(target.parent)
        runtime_manifest._check_chain(source_path)
        runtime_manifest._check_no_named_streams(source_path)
        stream, initial = runtime_manifest._open_regular(source_path, runtime_manifest.MAX_FILE_BYTES)
        if runtime_manifest._stable_identity(initial) != identities[relative]:
            stream.close()
            raise ValueError('input_changed')
        digest_copy = hashlib.sha256()
        count = 0
        with stream, target.open('xb') as output:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > size:
                    raise ValueError('input_changed')
                if shutil.disk_usage(target.parent).free < _FLOOR_BYTES + len(block):
                    raise ValueError('disk_floor')
                output.write(block)
                digest_copy.update(block)
            output.flush()
            os.fsync(output.fileno())
            if (count != size or digest_copy.hexdigest() != digest
                    or not runtime_manifest._same_file(source_path, stream, initial)):
                raise ValueError('input_changed')

    result = runtime_builder.build_runtime_manifest(candidate, manifest, generation=generation, revision=revision)
    _manifest_matches_pins(manifest, result['manifest_sha256'], generation, revision, files)
    if runtime_manifest._tree(candidate) != (set(files), directories):
        raise ValueError('candidate_membership_mismatch')
    for relative, (size, digest) in files.items():
        copied_size, copied_digest, _ = _input_hash(candidate, relative)
        if (copied_size, copied_digest) != (size, digest):
            raise ValueError('candidate_pin_mismatch')
        current_size, current_digest, identity = _input_hash(source, relative)
        if (current_size, current_digest, identity) != (size, digest, identities[relative]):
            raise ValueError('input_changed')
    runtime_manifest.verify_runtime(candidate, manifest, result['manifest_sha256'],
                                    expected_generation=generation, expected_revision=revision)
    return result


def build_candidate_from_sources(source_roots, candidate_root, manifest_path, *, pins,
                                 generation, revision, max_bytes):
    """Copy pinned site/model/assets files once from three disjoint source roots.

    A relative runtime path such as ``site/pkg/a.py`` selects the ``site``
    root plus ``pkg/a.py``. This is an audit-only candidate, not an installer.
    The disk reservation is a preflight check, not a guarantee against other
    writers or filesystem overhead. Same-user races and pin provenance remain
    outside the guarantees; output is preserved on failure and not activated.
    """
    if (not isinstance(generation, str) or not runtime_manifest.IDENTITY.fullmatch(generation)
            or not isinstance(revision, str) or not runtime_manifest.IDENTITY.fullmatch(revision)
            or type(max_bytes) is not int or max_bytes <= 0 or type(pins) is not dict
            or type(source_roots) is not dict or set(source_roots) != {'site', 'model', 'assets'}):
        raise ValueError('inputs_invalid')
    roots = {name: runtime_manifest._check_chain(path) for name, path in source_roots.items()}
    if any(not path.is_dir() or path == Path(path.anchor) for path in roots.values()):
        raise ValueError('source_invalid')
    paths = list(roots.values())
    if any(_related(left, right) for index, left in enumerate(paths) for right in paths[index + 1:]):
        raise ValueError('source_overlap')
    candidate, manifest = _new_path(candidate_root), _new_path(manifest_path)
    if ('.codex-audit' not in (part.casefold() for part in candidate.parts)
            or '.codex-audit' not in (part.casefold() for part in manifest.parts)):
        raise ValueError('audit_root_required')
    if (_related(candidate, manifest)
            or any(_related(source, output) for source in paths for output in (candidate, manifest))):
        raise ValueError('output_overlap')
    records = []
    for relative, pin in pins.items():
        if type(relative) is not str or type(pin) is not dict or set(pin) != {'size', 'sha256'}:
            raise ValueError('pin_invalid')
        records.append({'path': relative, 'size': pin['size'], 'sha256': pin['sha256']})
    files, directories = runtime_manifest._validate_records(records)
    if {relative.split('/')[0] for relative in files} != set(roots):
        raise ValueError('source_membership_invalid')
    total = sum(size for size, _ in files.values())
    if total > max_bytes:
        raise ValueError('budget_insufficient')
    if shutil.disk_usage(candidate.parent).free < _FLOOR_BYTES + total + _MANIFEST_RESERVE:
        raise ValueError('disk_floor')
    if os.path.splitdrive(str(candidate))[0].casefold() != os.path.splitdrive(str(manifest))[0].casefold():
        if shutil.disk_usage(manifest.parent).free < _FLOOR_BYTES + _MANIFEST_RESERVE:
            raise ValueError('disk_floor')

    def source_path(relative):
        parts = runtime_manifest._path_parts(relative)
        return roots[parts[0]].joinpath(*parts[1:])

    # All exact caller pins are checked before creating any output.
    identities = {}
    for relative, (size, digest) in files.items():
        actual_size, actual_digest, identity = runtime_builder._hash_file(source_path(relative))
        if (actual_size, actual_digest) != (size, digest):
            raise ValueError('input_pin_mismatch')
        identities[relative] = identity

    os.mkdir(candidate)
    for relative, (size, digest) in files.items():
        source = source_path(relative)
        target = candidate.joinpath(*relative.split('/'))
        target.parent.mkdir(parents=True, exist_ok=True)
        runtime_manifest._check_chain(target.parent)
        runtime_manifest._check_chain(source)
        runtime_manifest._check_no_named_streams(source)
        stream, initial = runtime_manifest._open_regular(source, runtime_manifest.MAX_FILE_BYTES)
        if runtime_manifest._stable_identity(initial) != identities[relative]:
            stream.close()
            raise ValueError('input_changed')
        digest_copy = hashlib.sha256()
        count = 0
        with stream, target.open('xb') as output:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > size:
                    raise ValueError('input_changed')
                if shutil.disk_usage(target.parent).free < _FLOOR_BYTES + len(block):
                    raise ValueError('disk_floor')
                output.write(block)
                digest_copy.update(block)
            output.flush()
            os.fsync(output.fileno())
            if (count != size or digest_copy.hexdigest() != digest
                    or not runtime_manifest._same_file(source, stream, initial)):
                raise ValueError('input_changed')

    result = runtime_builder.build_runtime_manifest(candidate, manifest, generation=generation, revision=revision)
    _manifest_matches_pins(manifest, result['manifest_sha256'], generation, revision, files)
    if runtime_manifest._tree(candidate) != (set(files), directories):
        raise ValueError('candidate_membership_mismatch')
    for relative, (size, digest) in files.items():
        copied_size, copied_digest, _ = runtime_builder._hash_file(candidate.joinpath(*relative.split('/')))
        if (copied_size, copied_digest) != (size, digest):
            raise ValueError('candidate_pin_mismatch')
        final_size, final_digest, identity = runtime_builder._hash_file(source_path(relative))
        if (final_size, final_digest, identity) != (size, digest, identities[relative]):
            raise ValueError('input_changed')
    runtime_manifest.verify_runtime(candidate, manifest, result['manifest_sha256'],
                                    expected_generation=generation, expected_revision=revision)
    return result
