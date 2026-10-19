"""Tests of the frozen loader of hex-Mind step 1a (lane_view_frozen), run on the loader itself: F-2, there is no default
root (load_manifest and load_frozen take the root from their caller; FROZEN_ROOT stays only the acceptance-1a CLI's
default); FZ-1, every manifest entry is checked before the first file of any cycle is read, so a crafted manifest never
leads a read outside the frozen root and an entry of another shape is a fixed refusal, never a raw error; P4, a cycle
without its publication digest is a fixed refusal, not a raw KeyError. The first twelve entry cases and the P4 cases
are the card lineage's (its test_join_turn.py runs them through the JOIN turn, which this lineage does not have); the
other cases and the positive controls are D15's. Synthetic files in a temporary directory only (test_lane_view's
synthetic frozen root); no data disk path is opened.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/step1/test_lane_view_frozen.py -q -p no:cacheprovider
"""

import hashlib
import inspect
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import lane_view_frozen as fz  # noqa: E402
import test_lane_view as tlv  # noqa: E402  (the synthetic frozen root)

SECRET = b"{}"  # the bytes of a file outside the frozen root that a crafted manifest names
SHAPE = "a manifest cycle entry is not a cycle name and a files map"  # literal: a test never trusts the module's text
CYCLE_NAME = "a manifest cycle name is not one plain path component"
FILE_NAME = "a manifest file name is not plain path components"
DIGEST = "a manifest file digest is not a sha256 or null"
NO_PUBLICATION = "a manifest cycle has no publication digest"
MANIFEST_CASES = [  # the card lineage's twelve (test_join_turn.py, FZ-1)
    ("an entry", SHAPE), ({"cycle": "c"}, SHAPE), ({"files": {}}, SHAPE), ({"cycle": "c", "files": ["x.json"]}, SHAPE),
    ({"cycle": 5, "files": {}}, CYCLE_NAME), ({"cycle": "..", "files": {"secret.json": "@sha"}}, CYCLE_NAME),
    ({"cycle": "nul", "files": {}}, CYCLE_NAME), ({"cycle": "c", "files": {"..\\..\\secret.json": "@sha"}}, FILE_NAME),
    ({"cycle": "c", "files": {"../../secret.json": "@sha"}}, FILE_NAME),
    ({"cycle": "c", "files": {"@abs": "@sha"}}, FILE_NAME), ({"cycle": "c", "files": {"run\\NUL": None}}, FILE_NAME),
    ({"cycle": "c", "files": {"x.json": 5}}, DIGEST),
]
MORE_CASES = [  # D15: every other way out of the root, every device form, every digest edge
    (["c", {}], SHAPE), ({"cycle": "c", "files": None}, SHAPE), ({"cycle": None, "files": {}}, CYCLE_NAME),
    ({"cycle": "", "files": {}}, CYCLE_NAME), ({"cycle": ".c", "files": {}}, CYCLE_NAME),
    ({"cycle": "a\\b", "files": {}}, CYCLE_NAME), ({"cycle": "a/b", "files": {}}, CYCLE_NAME),
    ({"cycle": "C:", "files": {}}, CYCLE_NAME), ({"cycle": "a b", "files": {}}, CYCLE_NAME),
    ({"cycle": "COM1", "files": {}}, CYCLE_NAME), ({"cycle": "lpt9.json", "files": {}}, CYCLE_NAME),
    ({"cycle": "Con.x", "files": {}}, CYCLE_NAME), ({"cycle": "@abs", "files": {}}, CYCLE_NAME),
    ({"cycle": "c", "files": {"run\\": None}}, FILE_NAME), ({"cycle": "c", "files": {"\\\\host\\share\\x": None}}, FILE_NAME),
    ({"cycle": "c", "files": {"x.json:stream": None}}, FILE_NAME), ({"cycle": "c", "files": {"aux.json": None}}, FILE_NAME),
    ({"cycle": "c", "files": {"prn": None}}, FILE_NAME),
    ({"cycle": "c", "files": {"run\\.x": None}}, FILE_NAME), ({"cycle": "c", "files": {"": None}}, FILE_NAME),
    ({"cycle": "c", "files": {"x.json": "@SHA"}}, DIGEST), ({"cycle": "c", "files": {"x.json": "@sha63"}}, DIGEST),
    ({"cycle": "c", "files": {"x.json": "@sha\n"}}, DIGEST), ({"cycle": "c", "files": {"x.json": True}}, DIGEST),
    ({"cycle": "c", "files": {"x.json": ["@sha"]}}, DIGEST),
]


def crafted_root(tmp_path, entry):
    """A frozen root whose manifest lists a valid cycle FIRST and then ``entry``, with a file outside the root that a
    crafted name could reach; returns the root and the manifest text."""
    root = tmp_path / "frozen"
    (root / "ok").mkdir(parents=True)
    (root / "ok" / "ok.json").write_bytes(SECRET)
    (tmp_path / "secret.json").write_bytes(SECRET)
    digest = hashlib.sha256(SECRET).hexdigest()
    text = json.dumps({"schema": fz.MANIFEST_SCHEMA, "cycles": [{"cycle": "ok", "files": {"ok.json": "@sha"}}, entry]})
    for token, value in (('"@sha"', digest), ('"@SHA"', digest.upper()), ('"@sha63"', digest[:63]),
                         ('"@sha\\n"', digest + "\n"), ('"@abs"', (tmp_path / "secret.json").as_posix())):
        text = text.replace(token, json.dumps(value))
    (root / "manifest.json").write_text(text, encoding="utf-8")
    return root, text


def recorded_reads(monkeypatch):
    reads, real = [], Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    return reads


def outcome(call):
    try:
        return "returned", call()
    except Exception as error:  # noqa: BLE001 - the exact type is asserted by the caller
        return type(error).__name__, str(error)


@pytest.mark.parametrize("entry, reason", MANIFEST_CASES + MORE_CASES)
def test_a_manifest_entry_of_another_shape_or_an_unsafe_name_is_refused_before_any_file_is_read(tmp_path, monkeypatch,
                                                                                                entry, reason):
    # FZ-1: read_verified refuses the entry before it reads anything, and load_frozen refuses before the first file of
    # the valid cycle listed ahead of it is read: the manifest is the only read.
    root, text = crafted_root(tmp_path, entry)
    reads = recorded_reads(monkeypatch)
    crafted = json.loads(text)["cycles"][1]
    assert (outcome(lambda: fz.read_verified(root, crafted)), reads) == (("FrozenInputError", reason), [])
    assert (outcome(lambda: fz.load_frozen(root)), reads) == (("FrozenInputError", reason), [root / "manifest.json"])


@pytest.mark.parametrize("cycle, name", [("con1", "nulx.json"), ("comA", "lpt.json"), ("A1.b-c_d", "run\\aux1.json"),
                                         ("prn-x", "cons\\a..x"), ("x.nul", "com0x\\y.json")])
def test_plain_names_that_only_resemble_a_device_or_a_path_are_read(tmp_path, cycle, name):
    # Positive controls: a plain name that merely starts like a device name, or has dots inside a component, is one
    # plain component and is read and verified; a null entry is reported and never read.
    (tmp_path / cycle).joinpath(*name.split("\\")).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / cycle).joinpath(*name.split("\\")).write_bytes(SECRET)
    entry = {"cycle": cycle, "files": {name: hashlib.sha256(SECRET).hexdigest(), "pruned.json": None}}
    files, nulls, digests = fz.read_verified(tmp_path, entry)
    assert (files, nulls, digests) == ({name: {}}, ["pruned.json"], {name: hashlib.sha256(SECRET).hexdigest()})


def test_a_listed_file_whose_bytes_differ_from_its_digest_is_refused(tmp_path):
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / "x.json").write_bytes(SECRET)
    entry = {"cycle": "c", "files": {"x.json": hashlib.sha256(b"[]").hexdigest()}}
    assert outcome(lambda: fz.read_verified(tmp_path, entry)) == ("FrozenInputError", "sha256 mismatch: c/x.json")


@pytest.mark.parametrize("listed", ["missing", "null"])
def test_a_cycle_without_its_publication_is_refused_before_any_cycle_file_is_read(tmp_path, monkeypatch, listed):
    # P4 (the card lineage's FZ2 P4): a manifest cycle whose publication is missing or listed as null raised a raw
    # KeyError in load_frozen. Positive control first: the exact v1 backslash key loads.
    root = tlv.write_frozen_root(tmp_path / "frozen", tlv.synthetic_cycles(count=2, captured=1))
    assert fz.PUBLICATION == "run\\reflected.publication.json"
    assert [fz.PUBLICATION in case["files"] for case in fz.load_frozen(root)["cycles"]] == [True, True]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["cycles"][-1]["files"]  # the first cycle stays valid, so a late check would read its files
    if listed == "missing":
        del files[fz.PUBLICATION]
    else:
        files[fz.PUBLICATION] = None
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    reads = recorded_reads(monkeypatch)
    assert (outcome(lambda: fz.load_frozen(root)), reads) == (("FrozenInputError", NO_PUBLICATION),
                                                               [root / "manifest.json"])


def test_the_loader_has_no_default_root_and_reads_nothing_without_one(monkeypatch):
    # F-2: the library defaulted to the private frozen copy on the data disk. FROZEN_ROOT stays only as the
    # acceptance-1a CLI's own default, which names it explicitly.
    reads = recorded_reads(monkeypatch)
    for function in (fz.load_manifest, fz.load_frozen):
        assert inspect.signature(function).parameters["root"].default is inspect.Parameter.empty
        with pytest.raises(TypeError):
            function()
    assert reads == []
    acc = (HERE / "run_acceptance_1a.py").read_text(encoding="utf-8")
    assert acc.count("fz.FROZEN_ROOT") == 1 and "default=fz.FROZEN_ROOT" in acc
