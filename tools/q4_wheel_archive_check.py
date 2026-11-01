"""Inspect a pinned wheel as data; never extract, execute or install it.

This conservative check validates selected ZIP paths, WHEEL tags and RECORD
SHA-256/size entries. It does not establish publisher identity, package safety,
dependency closure, interpreter ABI, OS isolation, or installation suitability.
Same-user races after the final check remain outside its guarantees.
The ZIP library parses the central directory before our entry-count check, so
the entry limit is not a hard bound on parser memory or CPU use.
"""

import base64
import csv
from email.parser import BytesParser
from email import policy
import hashlib
import io
import os
from pathlib import Path
import re
import stat
import zipfile

from sandbox.mind.step2 import runtime_manifest
from tools.q4_offline_site_candidate import _wheel_name, _name


_MAX_ENTRIES = 100_000
_MAX_METADATA = 8 * 1024 * 1024
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _safe_name(name):
    if (type(name) is not str or not name or len(name) > 4096 or name.startswith('/')
            or '\\' in name or ':' in name or name.endswith('/')):
        raise ValueError('entry_name_invalid')
    parts = name.split('/')
    if (len(parts) > 64 or any(not part or part in ('.', '..') or len(part) > 255
            or part.endswith(('.', ' ')) or runtime_manifest.DEVICE.fullmatch(part)
            or any(ord(char) < 32 for char in part) for part in parts)):
        raise ValueError('entry_name_invalid')
    return name


def _inspect_archive(archive, wheel_path, target_tag, max_uncompressed_bytes):
    distribution, version = _wheel_name(wheel_path, target_tag)
    entries = archive.infolist()
    if not entries or len(entries) > _MAX_ENTRIES:
        raise ValueError('entry_count_invalid')
    files, folded, explicit_directories, total = {}, set(), set(), 0
    for entry in entries:
        directory = entry.is_dir()
        name = _safe_name(entry.filename[:-1] if directory else entry.filename)
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        allowed_kinds = (0, stat.S_IFDIR) if directory else (0, stat.S_IFREG)
        if (kind not in allowed_kinds or entry.external_attr & 0x400 or entry.flag_bits & 1
                or entry.file_size < 0 or (directory and entry.file_size != 0)):
            raise ValueError('entry_invalid')
        folded_name = name.casefold()
        if directory:
            if folded_name in explicit_directories:
                raise ValueError('entry_invalid')
            explicit_directories.add(folded_name)
        else:
            if folded_name in folded:
                raise ValueError('entry_invalid')
            folded.add(folded_name)
        total += entry.file_size
        if total > max_uncompressed_bytes:
            raise ValueError('budget_exceeded')
        if not directory:
            files[name] = entry

    # Windows cannot represent one case-insensitive path as both a file and
    # a parent directory, even when each complete member name is distinct.
    if folded & explicit_directories:
        raise ValueError('file_directory_collision')
    for name in folded | explicit_directories:
        parts = name.split('/')
        if any('/'.join(parts[:index]) in folded for index in range(1, len(parts))):
            raise ValueError('file_directory_collision')

    info_roots = {name.split('/')[0] for name in files
                  if name.split('/')[0].endswith('.dist-info')}
    if len(info_roots) != 1:
        raise ValueError('dist_info_invalid')
    info = next(iter(info_roots))
    if not info.endswith('.dist-info') or '-' not in info[:-10]:
        raise ValueError('dist_info_invalid')
    info_dist, info_version = info[:-10].rsplit('-', 1)
    if _name(info_dist) != distribution or info_version != version:
        raise ValueError('dist_info_invalid')
    wheel_name, record_name, metadata_name = info + '/WHEEL', info + '/RECORD', info + '/METADATA'
    if (wheel_name not in files or record_name not in files or metadata_name not in files
            or any(files[name].file_size > _MAX_METADATA for name in (wheel_name, record_name, metadata_name))):
        raise ValueError('metadata_missing')

    message = BytesParser(policy=policy.default).parsebytes(archive.read(files[wheel_name]))
    tags = message.get_all('Tag', [])
    if (message.defects or message.get_all('Wheel-Version', []) != ['1.0']
            or message.get_all('Root-Is-Purelib', []) not in (['true'], ['false'])
            or not tags or len(tags) != len(set(tags))):
        raise ValueError('wheel_metadata_invalid')
    package = BytesParser(policy=policy.default).parsebytes(archive.read(files[metadata_name]))
    package_names, package_versions = package.get_all('Name', []), package.get_all('Version', [])
    if (package.defects or len(package_names) != 1 or len(package_versions) != 1
            or _name(package_names[0]) != distribution or package_versions[0] != version):
        raise ValueError('package_metadata_invalid')
    filename_parts = wheel_path.name[:-4].split('-')
    py_tags, abi_tags, platforms = [set(part.split('.')) for part in filename_parts[-3:]]
    compatible = False
    for tag in tags:
        components = tag.split('-')
        if (len(components) != 3 or components[0] not in py_tags
                or components[1] not in abi_tags or components[2] not in platforms):
            raise ValueError('wheel_metadata_invalid')
        try:
            _wheel_name(Path('synthetic-1.0-' + tag + '.whl'), target_tag)
            compatible = True
        except ValueError:
            pass
    if not compatible:
        raise ValueError('wheel_tag_incompatible')

    try:
        raw_record = archive.read(files[record_name]).decode('utf-8')
        rows = list(csv.reader(io.StringIO(raw_record, newline=''), strict=True))
    except (UnicodeError, csv.Error):
        raise ValueError('record_invalid') from None
    records = {}
    for row in rows:
        if len(row) != 3 or _safe_name(row[0]) in records:
            raise ValueError('record_invalid')
        records[row[0]] = (row[1], row[2])
    if set(records) != set(files) or records.get(record_name) != ('', ''):
        raise ValueError('record_membership_mismatch')
    for name, entry in files.items():
        if name == record_name:
            continue
        claimed_hash, claimed_size = records[name]
        if claimed_size != str(entry.file_size) or not claimed_hash.startswith('sha256='):
            raise ValueError('record_invalid')
        digest = hashlib.sha256()
        count = 0
        with archive.open(entry, 'r') as content:
            while True:
                block = content.read(1024 * 1024)
                if not block:
                    break
                count += len(block)
                if count > entry.file_size:
                    raise ValueError('entry_size_invalid')
                digest.update(block)
        encoded = base64.urlsafe_b64encode(digest.digest()).rstrip(b'=').decode('ascii')
        if count != entry.file_size or claimed_hash != 'sha256=' + encoded:
            raise ValueError('record_digest_mismatch')
    return {'uncompressed_bytes': total, 'entry_count': len(files), 'installation': 'NOT_INSTALLED',
            'publisher_authentication': 'NOT_VERIFIED', 'dependency_closure': 'NOT_VERIFIED'}


def inspect_wheel(path, *, expected_sha256, target_tag, max_uncompressed_bytes):
    """Inspect one hash-pinned wheel without writing output or loading its code."""
    if (type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256)
            or type(max_uncompressed_bytes) is not int or max_uncompressed_bytes <= 0
            or max_uncompressed_bytes > runtime_manifest.MAX_TOTAL_BYTES):
        raise ValueError('input_invalid')
    try:
        wheel_path = runtime_manifest._check_chain(path)
        runtime_manifest._check_no_named_streams(wheel_path)
        stream, initial = runtime_manifest._open_regular(wheel_path, runtime_manifest.MAX_FILE_BYTES)
        with stream:
            digest = hashlib.sha256()
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
            if digest.hexdigest() != expected_sha256 or not runtime_manifest._same_file(wheel_path, stream, initial):
                raise ValueError('wheel_pin_mismatch')
            stream.seek(0)
            with zipfile.ZipFile(stream, 'r') as archive:
                result = _inspect_archive(archive, Path(wheel_path), target_tag, max_uncompressed_bytes)
            if not runtime_manifest._same_file(wheel_path, stream, initial):
                raise ValueError('wheel_changed')
            return result
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, EOFError, OverflowError):
        raise ValueError('wheel_inspection_refused') from None
