"""Tests for the integrity ratchet (RCO1's R8 proposal; fable-5 adopted, built and decided it, so RCO1 is not
independent for it), written BEFORE the code. ``integrity_ratchet.py`` counts five known bug classes in the non-test
modules of sandbox/mind by AST only. The cases pin each class's own shapes and its look-alikes; the last test holds
this tree's counts in ALLOWED. It compares the tree only with this ALLOWED, so a raised count passes with a raised
ALLOWED in the same change: keeping the counts going down is a review rule for every change to ALLOWED, not
something this test can see (Lead's D13 reconciliation).

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider
     sandbox/mind/test_integrity_ratchet.py
"""

import ast
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import integrity_ratchet as ir  # noqa: E402

# This tree's counts, filled ONCE with ``python -B -I sandbox/mind/integrity_ratchet.py`` and only ever lowered.
ALLOWED: dict[str, dict[str, int]] = {
    "capture/capacity_lens.py": {"json": 1, "int": 3, "write": 3},
    "capture/capture_round.py": {"json": 10, "write": 9},
    "capture/live_mind.py": {"json": 5, "write": 1},
    "capture/overlap_lens.py": {"json": 2, "write": 2},
    "capture/round_loop.py": {"json": 3, "prefix": 1},
    "freeze_inputs.py": {"json": 3},
    "hexmind/bakeoff.py": {"json": 2, "write": 2},
    "hexmind/bakeoff_v2.py": {"json": 2, "write": 1},
    "hexmind/capacity_axis.py": {"json": 1, "write": 1},
    "hexmind/hex_memory.py": {"json": 2, "write": 2},
    "learn_step.py": {"json": 4},
    "lesson_cards.py": {"dollar": 1},
    "measure_answers.py": {"json": 2, "write": 1},
    "mind_capture_producer.py": {"json": 7, "write": 6},
    "probes/sharing_window_probe.py": {"write": 1},
    "remember/corrections.py": {"prefix": 1},
    "remember/reach_audit.py": {"prefix": 1},
    "remember/remember_v2.py": {"json": 13, "write": 1},
    "remember/remember_v21.py": {"json": 9},
    "remember/remember_v3.py": {"json": 1, "write": 2},
    "step1/adapter.py": {"json": 2},
    "step1/run_acceptance_1a.py": {"json": 3, "write": 1},
    "step1/run_parity_1b.py": {"json": 14, "write": 14},
    "step2/calibrate.py": {"json": 2},
    "step2/cell.py": {"json": 6, "write": 4},
    "step2/corpus.py": {"json": 3},
    "step2/encode.py": {"json": 1},
    "step2/memory.py": {"json": 1},
    "step2/run_q4.py": {"json": 2, "write": 3},
    "tournament_driver.py": {"json": 5},
}

CASES = [
    # json: a JSON read without object_pairs_hook silently keeps the last of two equal keys
    ("json_loads", r'''
import json
json.loads(s)
''', {"json": 1}),
    ("json_load_file", r'''
import json
json.load(f)
''', {"json": 1}),
    ("json_hook_given", r'''
import json
json.loads(s, object_pairs_hook=pairs)
json.load(f, object_pairs_hook=pairs)
''', {}),
    ("json_module_alias", r'''
import json as j
j.loads(s)
''', {"json": 1}),
    ("json_from_import", r'''
from json import loads
loads(s)
''', {"json": 1}),
    ("json_from_import_alias_with_hook", r'''
from json import load as read
read(f, object_pairs_hook=pairs)
''', {}),
    ("json_not_a_read", r'''
import json
import pickle
json.dumps(x)
reader = json.loads
pickle.loads(b)
''', {}),
    ("json_kwargs_cannot_be_seen", r'''
import json
json.loads(s, **options)
''', {"json": 1}),
    ("json_imported_in_a_function", r'''
def read(s):
    import json
    return json.loads(s)
''', {"json": 1}),
    # dollar: a final unescaped $ lets a trailing newline through match and search
    ("dollar_compiled_match", r'''
import re
LINE = re.compile(r"^a+$")
LINE.match(s)
''', {"dollar": 1}),
    ("dollar_compiled_search", r'''
import re
LINE = re.compile(r"^a+$")
LINE.search(s)
''', {"dollar": 1}),
    ("dollar_fullmatch_is_exact", r'''
import re
LINE = re.compile(r"^a+$")
LINE.fullmatch(s)
re.fullmatch(r"a+$", s)
''', {}),
    ("dollar_direct_match", r'''
import re
re.match(r"^a+$", s)
''', {"dollar": 1}),
    ("dollar_direct_search", r'''
import re
re.search(r"a+$", s)
''', {"dollar": 1}),
    ("dollar_escaped_is_a_literal", r'''
import re
re.match(r"^a\$", s)
''', {"prefix": 1}),
    ("dollar_after_an_escaped_backslash", r'''
import re
re.match(r"^a\\$", s)
''', {"dollar": 1}),
    ("dollar_multiline_flag_scans_lines", r'''
import re
M = re.compile(r"^a$", re.M)
M.search(s)
re.search(r"^a$", s, flags=re.MULTILINE)
''', {}),
    ("dollar_multiline_positional_flag", r'''
import re
re.search(r"^a$", s, re.M)
''', {}),
    ("dollar_inline_multiline", r'''
import re
re.search(r"(?m)^a$", s)
re.search(r"(?im)^a$", s)
''', {}),
    ("dollar_inline_other_flags", r'''
import re
re.search(r"(?i)^a$", s)
''', {"dollar": 1}),
    ("dollar_scoped_flag_group_is_not_global", r'''
import re
re.search(r"(?m:^a)$", s)
''', {"dollar": 1}),
    ("dollar_compile_chain", r'''
import re
re.compile(r"^a$").match(s)
''', {"dollar": 1}),
    ("dollar_attribute_target", r'''
import re
class Reader:
    def __init__(self):
        self.LINE = re.compile(r"^a$")
    def ok(self, s):
        return self.LINE.match(s)
''', {"dollar": 1}),
    ("dollar_module_name_used_in_a_function", r'''
import re
LINE = re.compile(r"^a$")
def ok(s):
    return LINE.match(s)
''', {"dollar": 1}),
    ("dollar_re_module_alias", r'''
import re as regex
regex.match(r"^a$", s)
''', {"dollar": 1}),
    # prefix: a pattern with no end anchor checks only the start under match
    ("prefix_compiled_match", r'''
import re
HEAD = re.compile(r"^a")
HEAD.match(s)
''', {"prefix": 1}),
    ("prefix_annotated_target", r'''
import re
HEAD: re.Pattern = re.compile(r"^a")
HEAD.match(s)
''', {"prefix": 1}),
    ("prefix_multiline_still_prefix", r'''
import re
HEAD = re.compile(r"^a", re.M)
HEAD.match(s)
''', {"prefix": 1}),
    ("prefix_flags_keyword", r'''
import re
HEAD = re.compile(r"^a", flags=re.I)
HEAD.match(s)
''', {"prefix": 1}),
    ("prefix_search_and_fullmatch_are_not_prefix", r'''
import re
HEAD = re.compile(r"^a")
HEAD.search(s)
HEAD.fullmatch(s)
''', {}),
    ("prefix_end_anchor", r'''
import re
END = re.compile(r"^a\Z")
END.match(s)
re.match(r"^a\Z", s)
''', {}),
    ("prefix_escaped_z_is_a_literal", r'''
import re
re.match(r"^a\\Z", s)
''', {"prefix": 1}),
    ("prefix_pattern_not_known", r'''
import re
re.match(pattern, s)
LINE.match(s)
parser.match(s)
''', {}),
    ("prefix_from_import", r'''
from re import match
match(r"^a", s)
''', {"prefix": 1}),
    # int: bool is a subclass of int, so True passes as 1 unless the same subject excludes bool beside it
    ("int_plain", r'''
isinstance(x, int)
''', {"int": 1}),
    ("int_in_a_tuple", r'''
isinstance(x, (int, float))
''', {"int": 1}),
    ("int_with_bool_named", r'''
isinstance(x, (bool, int))
''', {}),
    ("int_and_not_bool", r'''
isinstance(x, int) and not isinstance(x, bool)
''', {}),
    ("int_and_not_bool_first", r'''
not isinstance(x, bool) and isinstance(x, int) and x > 0
''', {}),
    ("int_rejected_or_bool", r'''
not isinstance(x, int) or isinstance(x, bool)
''', {}),
    ("int_same_expression_subject", r'''
isinstance(row.get("n"), int) and not isinstance(row.get("n"), bool)
''', {}),
    ("int_type_is_not_bool", r'''
isinstance(x, int) and type(x) is not bool
''', {}),
    ("int_rejected_or_type_bool", r'''
not isinstance(x, int) or type(x) is bool
''', {}),
    ("int_guard_with_a_bool_tuple", r'''
isinstance(x, int) and not isinstance(x, (bool, str))
''', {}),
    ("int_other_subject", r'''
isinstance(x, int) and not isinstance(y, bool)
''', {"int": 1}),
    ("int_wrong_operator", r'''
isinstance(x, int) or not isinstance(x, bool)
''', {"int": 1}),
    ("int_negated_in_an_and", r'''
not isinstance(x, int) and isinstance(x, bool)
''', {"int": 1}),
    ("int_other_types", r'''
isinstance(x, Point)
isinstance(x, str)
isinstance(x, (str, bytes))
type(x) is int
''', {}),
    # write: an in-place overwrite leaves a cut file under the real name after a crash
    ("write_path_methods", r'''
path.write_text(s)
path.write_bytes(b)
''', {"write": 2}),
    ("write_open_modes", r'''
open(p, "w")
open(p, "wb")
open(p, mode="w", encoding="utf-8")
io.open(p, "w")
''', {"write": 4}),
    ("write_path_open", r'''
path.open("w")
gzip.open(p, "wt")
''', {"write": 2}),
    ("write_safe_modes", r'''
open(p, "x")
open(p, "a")
open(p)
open(p, "rb")
path.open("xb")
path.open()
''', {}),
    ("write_names_are_not_modes", r'''
tarfile.open("new.tar", "r")
open("w.txt")
''', {}),
    ("write_mode_not_known", r'''
open(p, mode)
''', {}),
    ("every_class_once", r'''
import json
import re
json.loads(s)
re.search(r"a$", s)
re.match(r"a", s)
isinstance(x, int)
path.write_text(s)
''', {"json": 1, "dollar": 1, "prefix": 1, "int": 1, "write": 1}),
]


@pytest.mark.parametrize("source, expected", [case[1:] for case in CASES], ids=[case[0] for case in CASES])
def test_each_class_counts_its_own_shapes_only(source, expected):
    assert ir.count_source(source) == expected


def test_count_source_reads_bytes_with_a_bom_and_crlf():
    assert ir.count_source(b"\xef\xbb\xbfimport json\r\njson.loads(s)\r\n") == {"json": 1}


def test_hits_give_line_numbers_in_order():
    source = "import json\nrows = [\n    json.loads(s),\n]\nopen(p, 'w')\n"  # the tree walk meets line 5 first
    assert ir.hits(source) == [(3, "json"), (5, "write")]


def test_collect_reads_nested_modules_and_skips_tests(tmp_path):
    files = {
        "b.py": "import json\njson.loads(s)\n",
        "a/inner.py": "path.write_text(s)\n",
        "a/deeper/c.py": "isinstance(x, int)\n",
        "test_skipped.py": "import json\njson.loads(s)\n",
        "a/test_also.py": "path.write_text(s)\n",
        "conftest.py": "path.write_text(s)\n",
        "__pycache__/d.py": "path.write_text(s)\n",
        ".hidden/e.py": "path.write_text(s)\n",
        "clean.py": "x = 1\n",
        "notes.txt": "json.loads(s)\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text, encoding="utf-8")
    found = ir.collect(tmp_path)
    assert found == {"a/deeper/c.py": {"int": 1}, "a/inner.py": {"write": 1}, "b.py": {"json": 1}}
    assert list(found) == sorted(found)


def test_collect_never_imports_what_it_reads(tmp_path):
    (tmp_path / "loud.py").write_text("raise SystemExit('imported')\nopen(p, 'w')\n", encoding="utf-8")
    assert ir.collect(tmp_path) == {"loud.py": {"write": 1}}


@pytest.mark.parametrize("current, allowed, grown, stale", [
    ({"a.py": {"json": 2}}, {"a.py": {"json": 2}}, {}, {}),
    ({"a.py": {"json": 3}}, {"a.py": {"json": 2}}, {"a.py": {"json": (3, 2)}}, {}),
    ({"a.py": {"json": 1}}, {"a.py": {"json": 2}}, {}, {"a.py": {"json": (1, 2)}}),
    ({"a.py": {"json": 2}, "new.py": {"int": 1}}, {"a.py": {"json": 2}}, {"new.py": {"int": (1, 0)}}, {}),
    ({}, {"gone.py": {"write": 1}}, {}, {"gone.py": {"write": (0, 1)}}),
    ({"a.py": {"json": 2}}, {"a.py": {"json": 2, "jsn": 1}}, {}, {"a.py": {"jsn": (0, 1)}}),
    ({"a.py": {"json": 1, "int": 2}}, {"a.py": {"json": 2, "int": 1}}, {"a.py": {"int": (2, 1)}},
     {"a.py": {"json": (1, 2)}}),
], ids=["equal", "grown", "lowered", "new_file", "vanished_file", "unknown_class", "both_ways"])
def test_compare_names_each_change_with_now_and_allowed(current, allowed, grown, stale):
    assert ir.compare(current, allowed) == (grown, stale)


def test_allowed_literal_round_trips_in_class_order():
    current = {"z/b.py": {"write": 3, "int": 2, "json": 1}, "a.py": {"int": 2}}  # class order is not abc order
    text = ir.allowed_literal(current)
    assert ast.literal_eval(text) == current
    rows = ['    "a.py": {"int": 2},', '    "z/b.py": {"json": 1, "int": 2, "write": 3},']
    assert text.splitlines() == ["{", *rows, "}"]
    assert ir.allowed_literal({}) == "{}"


@pytest.mark.parametrize("bad", [{"a.py": {"jsn": 1}}, {"a.py": {"json": 0}}, {"a.py": {"json": True}},
                                 {'a"b.py': {"json": 1}}],
                         ids=["unknown_class", "zero", "bool_count", "quote_in_path"])
def test_allowed_literal_refuses_what_it_cannot_write_back(bad):
    with pytest.raises(ValueError):
        ir.allowed_literal(bad)


def test_main_prints_the_literal_or_the_hits(capsys):
    assert ir.main([]) == 0
    assert capsys.readouterr().out == ir.allowed_literal(ir.collect()) + "\n"
    assert ir.main(["--hits"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == sum(sum(row.values()) for row in ir.collect().values())
    assert all(line.split(" ")[1] in ir.CLASSES for line in lines)
    assert ir.main(["--other"]) == 2


def test_the_ratchet_module_holds_no_instance():
    assert ir.count_source((HERE / "integrity_ratchet.py").read_bytes()) == {}


def test_allowed_is_canonical():
    assert list(ALLOWED) == sorted(ALLOWED)
    assert ast.literal_eval(ir.allowed_literal(ALLOWED)) == ALLOWED
    for rel in ALLOWED:
        assert (HERE / rel).is_file() and not Path(rel).name.startswith("test_"), rel


def test_integrity_classes_only_ratchet_down():
    grown, stale = ir.compare(ir.collect(), ALLOWED)
    lines = {rel: [line for line, cls in ir.hits((HERE / rel).read_bytes()) if cls in classes]
             for rel, classes in grown.items()}
    assert not grown, f"new instances of a known class (file: {{class: (now, allowed)}}): {grown}; lines: {lines}"
    assert not stale, f"fixed instances: lower ALLOWED in this change (file: {{class: (now, allowed)}}): {stale}"
