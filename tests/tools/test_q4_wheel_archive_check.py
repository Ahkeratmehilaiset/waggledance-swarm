"""Read-only wheel archive checks, exclusively using tiny synthetic ZIPs.

Passing does not prove wheel origin, absence of malicious code, dependency
closure, OS isolation or installation suitability. Never execute or extract
these fixtures; the expected outcome is inspection, not installation.
"""

import base64
import csv
import hashlib
import io
import socket
import subprocess
import warnings
import zipfile

import pytest


DIST = "alpha-1.0.dist-info"
WHEEL = f"{DIST}/WHEEL"
METADATA = f"{DIST}/METADATA"
RECORD = f"{DIST}/RECORD"
MODULE = "alpha/__init__.py"
TARGET = "cp313-cp313-win_amd64"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def record_hash(data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return "sha256=" + digest


def make_archive(*, extra=(), missing_wheel=False, wheel_text=None, record_change=None,
                 duplicate=None, symlink=False, reparse=False, omit_record=False,
                 module_path=MODULE, directories=(), symlink_directory=None,
                 missing_metadata=False, metadata_text=None):
    if wheel_text is None:
        wheel_text = b"Wheel-Version: 1.0\nGenerator: synthetic\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
    entries = [(module_path, b"# synthetic, never execute\n")]
    if not missing_wheel:
        entries.append((WHEEL, wheel_text))
    if not missing_metadata:
        if metadata_text is None:
            metadata_text = b"Metadata-Version: 2.1\nName: alpha\nVersion: 1.0\n\n"
        entries.append((METADATA, metadata_text))
    entries.extend(extra)
    record_rows = [[name, record_hash(data), str(len(data))] for name, data in entries]
    if record_change is not None:
        record_change(record_rows)
    record_rows.append([RECORD, "", ""])
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="\n").writerows(record_rows)
    if not omit_record:
        entries.append((RECORD, output.getvalue().encode("utf-8")))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for directory in directories:
            archive.writestr(directory, b"")
        if symlink_directory is not None:
            info = zipfile.ZipInfo(symlink_directory)
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"")
        for name, data in entries:
            if symlink and name == MODULE:
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = 0o120777 << 16
                archive.writestr(info, data)
            elif reparse and name == MODULE:
                info = zipfile.ZipInfo(name)
                info.create_system = 0
                info.external_attr = 0x400  # Windows FILE_ATTRIBUTE_REPARSE_POINT
                archive.writestr(info, data)
            else:
                archive.writestr(name, data)
        if duplicate is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                archive.writestr(duplicate, b"duplicate")
    return buffer.getvalue()


@pytest.fixture
def wheel_file(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("inspection must not launch a process or open a network socket")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    path = tmp_path / "alpha-1.0-py3-none-any.whl"

    def write(data):
        path.write_bytes(data)
        return path

    return write


def inspect(path, data, **kwargs):
    from tools.q4_wheel_archive_check import inspect_wheel

    args = {"expected_sha256": sha(data), "target_tag": TARGET,
            "max_uncompressed_bytes": 100_000}
    args.update(kwargs)
    return inspect_wheel(path, **args)


def test_valid_wheel_inspected_without_installation_or_output(wheel_file, tmp_path):
    data = make_archive()
    path = wheel_file(data)
    result = inspect(path, data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        expected = sum(item.file_size for item in archive.infolist())
    assert result["uncompressed_bytes"] == expected
    assert result["installation"] == "NOT_INSTALLED"
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


def test_explicit_directory_entries_need_no_record_rows(wheel_file, tmp_path):
    """Wheel RECORD covers files; explicit zero-byte ZIP directory entries need no row."""
    data = make_archive(directories=["alpha/", f"{DIST}/"])
    path = wheel_file(data)
    result = inspect(path, data)
    assert result["installation"] == "NOT_INSTALLED"
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


def test_nested_vendored_dist_info_is_ordinary_recorded_data(wheel_file, tmp_path):
    """A nested vendored dist-info is not the wheel's own root dist-info."""
    data = make_archive(extra=[("alpha/vendor/beta-2.0.dist-info/METADATA", b"Name: beta\nVersion: 2.0\n")])
    path = wheel_file(data)
    result = inspect(path, data)
    assert result["installation"] == "NOT_INSTALLED"
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


def test_symlink_directory_rejected_without_extraction(wheel_file, tmp_path):
    data = make_archive(symlink_directory="alpha/vendor/")
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


@pytest.mark.parametrize("case", [
    "missing_file", "missing_name", "missing_version", "wrong_name", "wrong_version",
    "duplicate_name", "duplicate_version", "duplicate_top_metadata",
])
def test_top_level_metadata_must_bind_name_and_version_once(wheel_file, tmp_path, case):
    fields = {
        "missing_name": b"Metadata-Version: 2.1\nVersion: 1.0\n\n",
        "missing_version": b"Metadata-Version: 2.1\nName: alpha\n\n",
        "wrong_name": b"Metadata-Version: 2.1\nName: beta\nVersion: 1.0\n\n",
        "wrong_version": b"Metadata-Version: 2.1\nName: alpha\nVersion: 2.0\n\n",
        "duplicate_name": b"Metadata-Version: 2.1\nName: alpha\nName: alpha\nVersion: 1.0\n\n",
        "duplicate_version": b"Metadata-Version: 2.1\nName: alpha\nVersion: 1.0\nVersion: 1.0\n\n",
    }
    extra = [("beta-2.0.dist-info/METADATA", b"Name: beta\nVersion: 2.0\n\n")] if case == "duplicate_top_metadata" else []
    data = make_archive(missing_metadata=case == "missing_file", metadata_text=fields.get(case), extra=extra)
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


@pytest.mark.parametrize("value", [None, "TRUE", "yes", "", "true\nRoot-Is-Purelib: true"])
def test_wheel_root_is_purelib_is_exactly_one_boolean(wheel_file, tmp_path, value):
    field = "" if value is None else f"Root-Is-Purelib: {value}\n"
    wheel_text = (f"Wheel-Version: 1.0\n{field}Tag: py3-none-any\n\n").encode("ascii")
    data = make_archive(wheel_text=wheel_text)
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


@pytest.mark.parametrize("entry", [
    "../escape.py", "/absolute.py", "C:/drive.py", "alpha/../../escape.py",
    "alpha/file.py:secret", "alpha\\traversal.py", "alpha//empty.py",
])
def test_unsafe_archive_names_rejected_without_extraction(wheel_file, tmp_path, entry):
    data = make_archive(extra=[(entry, b"synthetic")])
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


@pytest.mark.parametrize("collision", ["duplicate", "case"])
def test_duplicate_or_case_collision_rejected(wheel_file, collision):
    data = (make_archive(duplicate=MODULE) if collision == "duplicate"
            else make_archive(extra=[("ALPHA/__INIT__.PY", b"synthetic-case")]))
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)


def test_windows_casefold_file_directory_prefix_collision_rejected(wheel_file, tmp_path):
    """Distinct ZIP names with correct RECORD collide when materialized on Windows."""
    data = make_archive(module_path="ALPHA/__init__.py", extra=[("alpha", b"plain file")])
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data


def test_symlink_entry_rejected(wheel_file):
    data = make_archive(symlink=True)
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)


def test_windows_reparse_entry_rejected(wheel_file):
    data = make_archive(reparse=True)
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)


@pytest.mark.parametrize("wheel_text", [
    b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\n\n",
    b"Wheel-Version: 1.0\nTag: cp312-cp312-manylinux_x86_64\n\n",
    b"Wheel-Version: 1.0\nTag: invalid-tag\n\n",
    b"Wheel-Version: 1.0\nTag: cp313-cp313-win_amd64\n\n",  # mismatches filename tag
])
def test_missing_invalid_or_incompatible_wheel_metadata_tag_rejected(wheel_file, wheel_text):
    data = make_archive(wheel_text=wheel_text)
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)


@pytest.mark.parametrize("missing", ["wheel", "record"])
def test_required_dist_info_missing_rejected(wheel_file, missing):
    data = make_archive(missing_wheel=missing == "wheel", omit_record=missing == "record")
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)


@pytest.mark.parametrize("mutation", ["wrong_hash", "wrong_size", "missing_row", "extra_row", "duplicate_row"])
def test_record_must_cover_each_entry_once_with_correct_hash_and_size(wheel_file, mutation):
    def alter(rows):
        if mutation == "wrong_hash":
            rows[0][1] = record_hash(b"different")
        elif mutation == "wrong_size":
            rows[0][2] = str(int(rows[0][2]) + 1)
        elif mutation == "missing_row":
            rows.pop(0)
        elif mutation == "extra_row":
            rows.append(["alpha/missing.py", record_hash(b"missing"), "7"])
        elif mutation == "duplicate_row":
            rows.append(list(rows[0]))

    data = make_archive(record_change=alter)
    path = wheel_file(data)
    with pytest.raises(ValueError):
        inspect(path, data)


@pytest.mark.parametrize("problem", ["wrong_archive_sha", "unknown_budget", "zero_budget", "budget_too_small", "invalid_zip"])
def test_bad_input_pins_or_budget_refused_without_output(wheel_file, tmp_path, problem):
    data = b"not a zip" if problem == "invalid_zip" else make_archive()
    path = wheel_file(data)
    kwargs = {}
    if problem == "wrong_archive_sha":
        kwargs["expected_sha256"] = "0" * 64
    elif problem == "unknown_budget":
        kwargs["max_uncompressed_bytes"] = None
    elif problem == "zero_budget":
        kwargs["max_uncompressed_bytes"] = 0
    elif problem == "budget_too_small":
        kwargs["max_uncompressed_bytes"] = 1
    with pytest.raises(ValueError):
        inspect(path, data, **kwargs)
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == data
